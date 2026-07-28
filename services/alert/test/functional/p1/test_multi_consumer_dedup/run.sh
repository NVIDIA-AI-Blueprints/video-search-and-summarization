#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# P1: Multi-consumer dedup — validate the post-Redis assumption that in-process
# dedup stays correct when MORE THAN ONE Alert MS instance consumes from Kafka.
#
# Two Alert MS instances join ONE consumer group ("alert-bridge-mc-group") on a
# 2-partition topic (mdx-incidents-mc). Kafka's group protocol assigns 1
# partition to each (confirmed: get_consumer uses subscribe()+group.id).
#
#   Scenario A (positive)  — duplicate incident (same sensorId key + same
#     fingerprint) → same partition → same instance → in-process dedup drops it.
#     Assert: exactly 1 ES doc AND exactly 1 "Publishing to Elastic" across both.
#   Scenario B (fan-out)   — M unique incidents (distinct sensorId keys) →
#     spread across partitions/instances. Assert: M ES docs (no double-index)
#     AND both instances published at least once (load actually shared).
#   Scenario D (negative)  — SAME fingerprint forced onto partition 0 AND 1
#     (i.e. onto both instances), simulating a producer that does NOT honour the
#     sensorId partition-key contract. In-process dedup CANNOT span containers,
#     so both instances process it — but the Elastic sink keys the doc by
#     fingerprint (document["Id"]), so ES still holds exactly 1 doc.
#     Assert: exactly 1 ES doc (ES doc-id idempotency is the safety net);
#     report the cross-container processing count as evidence.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="mdx-incidents-mc"
CONFIG="$SCRIPT_DIR/config.yaml"
KAFKA_CONTAINER="${KAFKA_CONTAINER:-alert-agent-kafka-test}"
GROUP_ID="alert-bridge-mc-group"

# Publish signal — pinned to services/alert/src/mdx/sink/sink_elastic.py:129:
#   log.info("Publishing to Elastic [sensor=%s category=%s ...]", ...)
# In pub_in_log the trailing space after the sensor value disambiguates an exact
# sensor id from one that is merely a prefix of another. If that log line's
# format changes, update PUB_LOG_PREFIX and re-verify both call sites.
PUB_LOG_PREFIX='Publishing to Elastic \[sensor='

AB1_PORT=9101
AB2_PORT=9102
AB1_LOG="$PID_DIR/mc_ab1.log"; AB1_PID="$PID_DIR/mc_ab1.pid"
AB2_LOG="$PID_DIR/mc_ab2.log"; AB2_PID="$PID_DIR/mc_ab2.pid"

RUN=$(date +%s)
PRODUCE=(python3 "$SCRIPT_DIR/mc_produce.py" --bootstrap "$BOOTSTRAP" --topic "$TOPIC")

echo "=== P1: Multi-consumer dedup (2 Alert MS instances, 1 group, 2 partitions) ==="

# ── Cleanup ──────────────────────────────────────────────────────────────────
stop_instance() {
    local pidfile="$1"
    [ -f "$pidfile" ] || return 0
    local pid; pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        local w=0
        while [ $w -lt 12 ] && kill -0 "$pid" 2>/dev/null; do sleep 1; w=$((w+1)); done
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
}
cleanup() {
    local rc=$?
    stop_instance "$AB1_PID"
    stop_instance "$AB2_PID"
    # Release the two REST ports before the orchestrator's next stop_alert_bridge.
    fuser -k "${AB1_PORT}/tcp" "${AB2_PORT}/tcp" 2>/dev/null || true
    if [ $rc -ne 0 ]; then
        print_status "info" "AB1 last 40 log lines:"; tail -40 "$AB1_LOG" 2>/dev/null || true
        print_status "info" "AB2 last 40 log lines:"; tail -40 "$AB2_LOG" 2>/dev/null || true
    fi
    exit $rc
}
trap cleanup EXIT

# ── Helpers ──────────────────────────────────────────────────────────────────
# Count ES docs whose sensorId exactly equals $1.
es_count_sensor() {
    get_all_es_docs "$ES_HOST" | SENSOR="$1" python3 -c "
import os, sys, json
s = os.environ['SENSOR']
docs = json.load(sys.stdin)
print(sum(1 for d in docs if str(d.get('sensorId','')) == s))
" 2>/dev/null || echo 0
}
# Count ES docs whose sensorId starts with $1.
es_count_prefix() {
    get_all_es_docs "$ES_HOST" | PFX="$1" python3 -c "
import os, sys, json
p = os.environ['PFX']
docs = json.load(sys.stdin)
print(sum(1 for d in docs if str(d.get('sensorId','')).startswith(p)))
" 2>/dev/null || echo 0
}
# Count exact-sensor publish lines in one log (trailing space = exact match).
pub_in_log() {
    local sensor="$1" log="$2" n
    n=$(grep -c "${PUB_LOG_PREFIX}${sensor} " "$log" 2>/dev/null || true)
    echo "${n:-0}"
}
# Total publishes for an exact sensor across both instance logs.
pub_total() {
    local a b; a=$(pub_in_log "$1" "$AB1_LOG"); b=$(pub_in_log "$1" "$AB2_LOG")
    echo $((a + b))
}
# Count publish lines matching a sensor PREFIX in one log.
pub_prefix_in_log() {
    local prefix="$1" log="$2" n
    n=$(grep -c "${PUB_LOG_PREFIX}${prefix}" "$log" 2>/dev/null || true)
    echo "${n:-0}"
}

# ── Poll helpers (replace fixed processing/rebalance sleeps) ─────────────────
# Each always exits 0 and echoes the best value seen, so callers assert on the
# echoed value under `set -e` without a failing poll aborting the script.

# Poll until es_count_sensor "$1" reaches >= target $2, or timeout $3s.
poll_sensor_count() {
    local sensor="$1" target="$2" timeout="${3:-60}" interval="${4:-3}" elapsed=0 n=0
    while [ "$elapsed" -lt "$timeout" ]; do
        n=$(es_count_sensor "$sensor")
        [ "$n" -ge "$target" ] && break
        sleep "$interval"; elapsed=$((elapsed + interval))
    done
    echo "$n"
}
# Poll until es_count_prefix "$1" reaches >= target $2, or timeout $3s.
poll_prefix_count() {
    local prefix="$1" target="$2" timeout="${3:-90}" interval="${4:-3}" elapsed=0 n=0
    while [ "$elapsed" -lt "$timeout" ]; do
        n=$(es_count_prefix "$prefix")
        [ "$n" -ge "$target" ] && break
        sleep "$interval"; elapsed=$((elapsed + interval))
    done
    echo "$n"
}
# Poll until log $1 contains >= target $3 lines matching pattern $2, or timeout.
poll_pub_in_log() {
    local log="$1" pattern="$2" target="${3:-1}" timeout="${4:-60}" interval="${5:-2}" elapsed=0 n=0
    while [ "$elapsed" -lt "$timeout" ]; do
        n=$(grep -c "$pattern" "$log" 2>/dev/null || true); n="${n:-0}"
        [ "$n" -ge "$target" ] && break
        sleep "$interval"; elapsed=$((elapsed + interval))
    done
    echo "$n"
}
# Distinct consumers that currently OWN a partition of the incident topic.
# NOTE: each Alert MS instance subscribes to BOTH the incident and the alert
# topic, so it registers TWO consumers (=two members) in the group. Counting all
# group members therefore yields 2x the instance count (4 for two instances).
# What this test actually cares about is how many distinct consumers split the
# incident topic's partitions, so we filter kafka-consumer-groups --describe rows
# to TOPIC == $TOPIC (column 2) and count distinct CONSUMER-IDs (column 7).
# Result: 2 while both instances are up (1 partition each), 1 after one is killed.
group_member_count() {
    docker exec "$KAFKA_CONTAINER" kafka-consumer-groups \
        --bootstrap-server localhost:9092 --describe --group "$GROUP_ID" 2>/dev/null \
        | awk -v t="$TOPIC" 'NR>1 && $2==t && NF>=7 && $7 != "-" {print $7}' | sort -u | grep -c . || echo 0
}
# Poll until the group has exactly $1 members, or timeout $2s. Echoes final count.
poll_group_members() {
    local want="$1" timeout="${2:-90}" interval="${3:-3}" elapsed=0 n=0
    while [ "$elapsed" -lt "$timeout" ]; do
        n=$(group_member_count)
        [ "$n" = "$want" ] && break
        sleep "$interval"; elapsed=$((elapsed + interval))
    done
    echo "$n"
}

start_instance() {
    local port="$1" log="$2" pidfile="$3"
    cd "$REPO_ROOT"
    FASTAPI_PORT="$port" PROMETHEUS_METRICS_ENABLED="false" PROMETHEUS_PORT="$((port + 10))" \
        python3 "$REPO_ROOT/enhance_alert_with_vlm.py" --config "$CONFIG" > "$log" 2>&1 &
    echo $! > "$pidfile"
}

# ── 0. Prerequisites ─────────────────────────────────────────────────────────
print_status "wait" "Checking prerequisites"
curl -fsS "$ES_HOST/health" >/dev/null || { print_status "fail" "ES sim unreachable"; exit 2; }

# Stop the orchestrator-started single AB; this test manages its own instances.
stop_alert_bridge_local "$PID_DIR"
fuser -k "${AB1_PORT}/tcp" "${AB2_PORT}/tcp" 2>/dev/null || true
sleep 2

# ── 1. Start two instances in one consumer group ─────────────────────────────
print_status "wait" "Starting 2 Alert MS instances (ports $AB1_PORT, $AB2_PORT) in group $GROUP_ID"
start_instance "$AB1_PORT" "$AB1_LOG" "$AB1_PID"
start_instance "$AB2_PORT" "$AB2_LOG" "$AB2_PID"

# Poll each instance to healthy (bounded) instead of a blind sleep; abort early
# if a process dies during startup.
print_status "wait" "Waiting for both instances to become healthy"
for pair in "$AB1_PID:$AB1_PORT" "$AB2_PID:$AB2_PORT"; do
    pid=$(cat "${pair%%:*}"); port="${pair##*:}"; hw=0
    until curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; do
        if ! kill -0 "$pid" 2>/dev/null; then
            print_status "fail" "Instance on port $port died during startup"; exit 1
        fi
        if [ "$hw" -ge 60 ]; then
            print_status "fail" "Instance on port $port not healthy after 60s"; exit 1
        fi
        sleep 2; hw=$((hw + 2))
    done
done

# Gate on the consumer group actually reaching 2 joined+assigned members, so
# partition ownership is settled (1 partition each) before we assert on it.
# Replaces the blind rebalance sleep and makes the multi-consumer assertions real.
print_status "wait" "Waiting for consumer-group join/rebalance (expect 2 members)"
MEMBERS=$(poll_group_members 2 90)
if [ "$MEMBERS" != "2" ]; then
    print_status "fail" "Consumer group $GROUP_ID did not reach 2 members (got $MEMBERS) — cannot validate multi-consumer behavior"; exit 1
fi
print_status "ok" "Both instances healthy and joined the group (2 members)"

# ── Scenario A: dedup holds across the fleet ─────────────────────────────────
echo ""
print_status "wait" "Scenario A: duplicate incident (same key + fingerprint) must dedup to 1"
SENSOR_A="mc_dedup_${RUN}"
TS_A=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
"${PRODUCE[@]}" --sensor-id "$SENSOR_A" --timestamp "$TS_A"
"${PRODUCE[@]}" --sensor-id "$SENSOR_A" --timestamp "$TS_A"
# Both copies go to the same partition/instance in order, so once the first is
# indexed the duplicate has already been consumed. Poll for the doc, then a short
# settle so a (wrongful) second index would have time to appear.
poll_sensor_count "$SENSOR_A" 1 60 >/dev/null
sleep 3
A_DOCS=$(es_count_sensor "$SENSOR_A")
A_PUB=$(pub_total "$SENSOR_A")
print_status "info" "Scenario A: ES docs=$A_DOCS, publishes across both instances=$A_PUB"
if [ "$A_DOCS" != "1" ]; then
    print_status "fail" "Scenario A: expected exactly 1 ES doc, got $A_DOCS"; exit 1
fi
if [ "$A_PUB" != "1" ]; then
    print_status "fail" "Scenario A: expected exactly 1 publish (dedup), got $A_PUB"; exit 1
fi
print_status "ok" "Scenario A PASS: duplicate deduped fleet-wide (1 doc, 1 publish)"

# ── Scenario B: fan-out, no double-processing, load shared ───────────────────
echo ""
print_status "wait" "Scenario B: 8 unique incidents split deterministically across both partitions"
UNIQ_PFX="mc_uniq_${RUN}_"
# Explicit 4/4 split across partition 0 and 1 so each instance (one partition
# each) deterministically receives work — no reliance on key-hash luck.
for i in $(seq 1 8); do
    "${PRODUCE[@]}" --sensor-id "${UNIQ_PFX}${i}" --partition "$(( i % 2 ))"
done
# Poll until all 8 unique docs are indexed (bounded), then a short settle to let
# any (erroneous) 9th/double index surface before we assert exactly 8.
poll_prefix_count "$UNIQ_PFX" 8 120 >/dev/null
sleep 2
B_DOCS=$(es_count_prefix "$UNIQ_PFX")
B_AB1=$(pub_prefix_in_log "$UNIQ_PFX" "$AB1_LOG")
B_AB2=$(pub_prefix_in_log "$UNIQ_PFX" "$AB2_LOG")
print_status "info" "Scenario B: unique ES docs=$B_DOCS (expect 8); publishes ab1=$B_AB1 ab2=$B_AB2"
if [ "$B_DOCS" != "8" ]; then
    print_status "fail" "Scenario B: expected 8 unique ES docs, got $B_DOCS (double-index or loss)"; exit 1
fi
if [ "$B_AB1" -lt 1 ] || [ "$B_AB2" -lt 1 ]; then
    print_status "fail" "Scenario B: load not shared — ab1=$B_AB1 ab2=$B_AB2 (one instance idle)"; exit 1
fi
print_status "ok" "Scenario B PASS: 8 docs, both instances active (partitions disjoint, no double-index)"

# ── Scenario D: negative — mis-colocated duplicate + ES idempotency net ──────
echo ""
print_status "wait" "Scenario D: same fingerprint forced onto BOTH partitions (both instances)"
SENSOR_D="mc_split_${RUN}"
TS_D=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
"${PRODUCE[@]}" --sensor-id "$SENSOR_D" --timestamp "$TS_D" --partition 0
"${PRODUCE[@]}" --sensor-id "$SENSOR_D" --timestamp "$TS_D" --partition 1
# Each partition is owned by a distinct instance (2 members verified at startup),
# so BOTH instances must PROCESS this fingerprint — that is the negative property
# under test: in-process dedup cannot span containers. Poll each instance's log
# for its publish (bounded), then a short settle before reading ES.
D_AB1=$(poll_pub_in_log "$AB1_LOG" "${PUB_LOG_PREFIX}${SENSOR_D} " 1 60)
D_AB2=$(poll_pub_in_log "$AB2_LOG" "${PUB_LOG_PREFIX}${SENSOR_D} " 1 60)
sleep 3
D_DOCS=$(es_count_sensor "$SENSOR_D")
D_PUB=$(pub_total "$SENSOR_D")
print_status "info" "Scenario D: ES docs=$D_DOCS, publishes ab1=$D_AB1 ab2=$D_AB2 (total=$D_PUB)"
# Hard assertion #1 — cross-container processing actually happened. Without this
# the scenario could pass via in-process dedup and never exercise the ES doc-id
# idempotency path it exists to prove.
if [ "$D_AB1" -lt 1 ] || [ "$D_AB2" -lt 1 ]; then
    print_status "fail" "Scenario D: expected BOTH instances to process the cross-partition duplicate (ab1=$D_AB1 ab2=$D_AB2); in-process dedup must not span containers"; exit 1
fi
# Hard assertion #2 — ES doc-id (fingerprint) idempotency collapses the two
# cross-container publishes to a single document.
if [ "$D_DOCS" != "1" ]; then
    print_status "fail" "Scenario D: expected exactly 1 ES doc (idempotent doc-id), got $D_DOCS"; exit 1
fi
print_status "ok" "Scenario D PASS: both instances processed (dedup does not span containers); ES doc-id idempotency held (1 doc)"

# ── Scenario E: rebalance safety — survivor takes over, ES idempotency holds ──
echo ""
print_status "wait" "Scenario E: kill one instance mid-run; survivor must take over its partition"
SENSOR_E="mc_rebal_${RUN}"
TS_E=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
# 1) Send to partition 1 while both instances are alive; its owner processes it
#    (and records the dedup key in that instance's in-process cache).
"${PRODUCE[@]}" --sensor-id "$SENSOR_E" --timestamp "$TS_E" --partition 1
E_BEFORE=$(poll_sensor_count "$SENSOR_E" 1 60)
if [ "$E_BEFORE" != "1" ]; then
    print_status "fail" "Scenario E: pre-kill incident not processed (docs=$E_BEFORE)"; exit 1
fi
# 2) Kill instance 2 -> the group rebalances -> instance 1 (sole consumer) owns
#    BOTH partitions. Gate on the group dropping to exactly 1 member so ownership
#    has actually moved before we probe takeover (replaces a blind rebalance sleep).
print_status "info" "Killing instance 2 (PID $(cat "$AB2_PID"))"
stop_instance "$AB2_PID"
print_status "wait" "Waiting for consumer-group rebalance onto the survivor (expect 1 member)"
E_MEMBERS=$(poll_group_members 1 90)
if [ "$E_MEMBERS" != "1" ]; then
    print_status "fail" "Scenario E: group did not rebalance to 1 member (got $E_MEMBERS)"; exit 1
fi
if ! kill -0 "$(cat "$AB1_PID")" 2>/dev/null; then
    print_status "fail" "Scenario E: survivor (instance 1) died"; exit 1
fi
if ! curl -fsS "http://127.0.0.1:$AB1_PORT/health" >/dev/null 2>&1; then
    print_status "fail" "Scenario E: survivor unhealthy after rebalance"; exit 1
fi
# 3) DETERMINISTIC takeover proof: send a NEW incident to partition 1, which the
#    survivor now necessarily owns. The survivor MUST process it — this proves
#    takeover regardless of which instance owned partition 1 before the kill (the
#    previous version accepted "no reprocess", so it could pass with no hand-off).
SENSOR_ET="mc_rebal_takeover_${RUN}"
TS_ET=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
"${PRODUCE[@]}" --sensor-id "$SENSOR_ET" --timestamp "$TS_ET" --partition 1
ET_AB1=$(poll_pub_in_log "$AB1_LOG" "${PUB_LOG_PREFIX}${SENSOR_ET} " 1 60)
ET_DOCS=$(poll_sensor_count "$SENSOR_ET" 1 60)
if [ "$ET_AB1" -lt 1 ]; then
    print_status "fail" "Scenario E: survivor did NOT process a new partition-1 message after rebalance — takeover not proven (ab1 pub=$ET_AB1)"; exit 1
fi
if [ "$ET_DOCS" != "1" ]; then
    print_status "fail" "Scenario E: takeover message produced $ET_DOCS ES docs (expected 1)"; exit 1
fi
print_status "ok" "Scenario E: survivor took over partition 1 (processed a new post-rebalance message)"
# 4) Idempotency through rebalance: resend the ORIGINAL fingerprint to partition 1
#    (now the survivor's). Whether the survivor's cache is warm (dedup) or cold
#    (reprocess), ES doc-id idempotency must keep SENSOR_E at exactly 1 doc.
"${PRODUCE[@]}" --sensor-id "$SENSOR_E" --timestamp "$TS_E" --partition 1
sleep 5
E_DOCS=$(es_count_sensor "$SENSOR_E")
E_PUB=$(pub_total "$SENSOR_E")
print_status "info" "Scenario E: ES docs=$E_DOCS, publishes across instances=$E_PUB"
if [ "$E_DOCS" != "1" ]; then
    print_status "fail" "Scenario E: expected exactly 1 ES doc after rebalance, got $E_DOCS"; exit 1
fi
if [ "$E_PUB" -ge 2 ]; then
    print_status "info" "Scenario E: survivor reprocessed the resent fingerprint (cold in-process cache after hand-off, $E_PUB publishes) — ES doc-id idempotency still yields 1 doc."
else
    print_status "info" "Scenario E: resent fingerprint deduped by the survivor's warm cache ($E_PUB publish)."
fi
print_status "ok" "Scenario E PASS: survivor took over the partition and kept processing; 1 doc preserved through rebalance"

echo ""
print_status "ok" "PASS: multi-consumer dedup correct — partition-key contract, fan-out, ES doc-id idempotency, and rebalance safety validated"
