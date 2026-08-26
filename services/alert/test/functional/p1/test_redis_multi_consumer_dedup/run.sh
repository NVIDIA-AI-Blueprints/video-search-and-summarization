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
# P1: Redis multi-consumer dedup — the Redis counterpart of
# test_multi_consumer_dedup, and a deliberately NEGATIVE test.
#
# In-process dedup is only sound because a sensorId is always handled by the same
# instance. Kafka guarantees that structurally: mdx-incidents is partitioned by
# sensorId, cohort keys are sensorId-prefixed, and a consumer owns whole
# partitions. A Redis Streams consumer group gives no such guarantee — XREADGROUP
# hands each entry to whichever consumer asks first — so two Alert MS instances
# sharing one group interleave a single sensor's events.
#
# This test exists to keep that difference honest and visible, so nobody scales
# the Redis source to >1 replica assuming Kafka's affinity carries over.
#
#   Scenario A (the constraint)  — N events, ONE sensorId, distinct fingerprints.
#     Under Kafka all N would land on a single instance. Assert BOTH instances
#     processed some, i.e. the cohort really did split. Also assert all N are
#     indexed (the split costs affinity, not data).
#   Scenario B (the consequence) — one fingerprint published K times. The
#     instance that cached it dedups its own copies; the other has never seen it
#     and publishes again. Hard-assert the ES doc-id idempotency net (exactly 1
#     doc) and report the cross-instance publish count as evidence.
#   Scenario C (the remedy)      — kill one instance, leaving one consumer in the
#     group, then send a duplicate pair. Assert exactly 1 publish: dedup is
#     correct when the documented one-replica-per-group constraint is honoured.
#
# Scenario A is the assertion that would break if someone "fixed" the transport
# to fake affinity, and Scenario C is the assertion that would break if dedup
# itself regressed. Read them together.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"
STREAM="mdx-incidents-mc"
GROUP="alert-bridge-mc-redis-group"
CONFIG="$SCRIPT_DIR/config.yaml"

# Publish signal — pinned to
# services/alert/src/mdx/sink/vlm_enhanced_sink/sink_elastic.py:
#   self._logger.info("Publishing to Elastic [sensor=%s category=%s ...]", ...)
# The trailing space after the sensor value keeps an exact sensor id from
# matching one it is merely a prefix of.
PUB_LOG_PREFIX='Publishing to Elastic \[sensor='

AB1_PORT=9111
AB2_PORT=9112
AB1_LOG="$PID_DIR/mc_redis_ab1.log"; AB1_PID="$PID_DIR/mc_redis_ab1.pid"
AB2_LOG="$PID_DIR/mc_redis_ab2.log"; AB2_PID="$PID_DIR/mc_redis_ab2.pid"

RUN=$(date +%s)
PRODUCE=(python3 "$SCRIPT_DIR/mc_produce_redis.py"
         --host "$REDIS_HOST" --port "$REDIS_PORT" --stream "$STREAM")

echo "=== P1: Redis multi-consumer dedup (2 Alert MS instances, 1 consumer group) ==="

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
    fuser -k "${AB1_PORT}/tcp" "${AB2_PORT}/tcp" 2>/dev/null || true
    if [ $rc -ne 0 ]; then
        print_status "info" "AB1 last 40 log lines:"; tail -40 "$AB1_LOG" 2>/dev/null || true
        print_status "info" "AB2 last 40 log lines:"; tail -40 "$AB2_LOG" 2>/dev/null || true
    fi
    exit $rc
}
trap cleanup EXIT

redis_cli() {
    docker exec "${REDIS_CONTAINER:-alert-agent-redis-test}" redis-cli "$@" 2>/dev/null
}

# ── Helpers ──────────────────────────────────────────────────────────────────
es_count_sensor() {
    get_all_es_docs "$ES_HOST" | SENSOR="$1" python3 -c "
import os, sys, json
s = os.environ['SENSOR']
docs = json.load(sys.stdin)
print(sum(1 for d in docs if str(d.get('sensorId','')) == s))
" 2>/dev/null || echo 0
}
pub_in_log() {
    local sensor="$1" log="$2" n
    n=$(grep -c "${PUB_LOG_PREFIX}${sensor} " "$log" 2>/dev/null || true)
    echo "${n:-0}"
}
pub_total() {
    local a b; a=$(pub_in_log "$1" "$AB1_LOG"); b=$(pub_in_log "$1" "$AB2_LOG")
    echo $((a + b))
}
poll_sensor_count() {
    local sensor="$1" target="$2" timeout="${3:-90}" interval="${4:-3}" elapsed=0 n=0
    while [ "$elapsed" -lt "$timeout" ]; do
        n=$(es_count_sensor "$sensor")
        [ "$n" -ge "$target" ] && break
        sleep "$interval"; elapsed=$((elapsed + interval))
    done
    echo "$n"
}
poll_pub_total() {
    local sensor="$1" target="$2" timeout="${3:-90}" interval="${4:-3}" elapsed=0 n=0
    while [ "$elapsed" -lt "$timeout" ]; do
        n=$(pub_total "$sensor")
        [ "$n" -ge "$target" ] && break
        sleep "$interval"; elapsed=$((elapsed + interval))
    done
    echo "$n"
}
# Consumers currently registered in the group (XINFO CONSUMERS lists one row per
# consumer name; each instance registers exactly one).
group_consumer_count() {
    redis_cli XINFO CONSUMERS "$STREAM" "$GROUP" \
        | grep -c '^alert-bridge-' || echo 0
}
poll_group_consumers() {
    local want="$1" timeout="${2:-60}" interval="${3:-3}" elapsed=0 n=0
    while [ "$elapsed" -lt "$timeout" ]; do
        n=$(group_consumer_count)
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
if ! redis_available; then
    print_status "info" "Redis not available; skipping (transport is optional)"
    exit 0
fi

stop_alert_bridge_local "$PID_DIR"
fuser -k "${AB1_PORT}/tcp" "${AB2_PORT}/tcp" 2>/dev/null || true
sleep 2

# Start from a clean stream so a previous run's entries cannot be replayed into
# this one by start_id 0-0.
redis_cli DEL "$STREAM" >/dev/null || true

# ── 1. Start two instances in one consumer group ─────────────────────────────
print_status "wait" "Starting 2 Alert MS instances (ports $AB1_PORT, $AB2_PORT) in group $GROUP"
start_instance "$AB1_PORT" "$AB1_LOG" "$AB1_PID"
start_instance "$AB2_PORT" "$AB2_LOG" "$AB2_PID"

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

print_status "wait" "Waiting for both consumers to register in the group (expect 2)"
CONSUMERS=$(poll_group_consumers 2 60)
if [ "$CONSUMERS" != "2" ]; then
    print_status "fail" "Group $GROUP did not reach 2 consumers (got $CONSUMERS) — cannot validate multi-consumer behavior"; exit 1
fi
print_status "ok" "Both instances healthy and registered in the group (2 consumers)"

# ── Scenario A: one sensor's cohort splits across instances ──────────────────
echo ""
print_status "wait" "Scenario A: 12 events for ONE sensorId must split across both instances"
SENSOR_A="mcr_split_${RUN}"
# Distinct end timestamps => distinct dedup fingerprints (the config puts the end
# timestamp in the key for this category), so every event is processed and can be
# attributed to an instance. Spaced so both blocking consumers get a turn at the
# head of the stream rather than one XREADGROUP claiming the whole batch.
NOW=$(date +%s)
for i in $(seq 1 12); do
    TS_I=$(date -u -d "@$(( NOW - i ))" +%Y-%m-%dT%H:%M:%S.000Z)
    "${PRODUCE[@]}" --sensor-id "$SENSOR_A" --timestamp "$TS_I" >/dev/null
    sleep 0.3
done
A_DOCS=$(poll_sensor_count "$SENSOR_A" 12 150)
A_AB1=$(pub_in_log "$SENSOR_A" "$AB1_LOG")
A_AB2=$(pub_in_log "$SENSOR_A" "$AB2_LOG")
print_status "info" "Scenario A: ES docs=$A_DOCS (expect 12); publishes ab1=$A_AB1 ab2=$A_AB2"
if [ "$A_DOCS" != "12" ]; then
    print_status "fail" "Scenario A: expected 12 ES docs, got $A_DOCS (loss or double-index)"; exit 1
fi
if [ "$A_AB1" -lt 1 ] || [ "$A_AB2" -lt 1 ]; then
    print_status "fail" "Scenario A: one sensor's cohort did NOT split (ab1=$A_AB1 ab2=$A_AB2). Either XREADGROUP gained affinity or one instance is idle — re-check the premise before trusting this suite"; exit 1
fi
print_status "ok" "Scenario A PASS: cohort for a single sensorId was split ab1=$A_AB1 / ab2=$A_AB2 — Redis provides no per-sensor affinity (Kafka would have sent all 12 to one instance)"

# ── Scenario B: the consequence, and the idempotency net ─────────────────────
echo ""
print_status "wait" "Scenario B: one fingerprint published 6 times across two instances"
SENSOR_B="mcr_dup_${RUN}"
TS_B=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
for _ in $(seq 1 6); do
    "${PRODUCE[@]}" --sensor-id "$SENSOR_B" --timestamp "$TS_B" >/dev/null
    sleep 0.5
done
poll_sensor_count "$SENSOR_B" 1 90 >/dev/null
sleep 5
B_DOCS=$(es_count_sensor "$SENSOR_B")
B_AB1=$(pub_in_log "$SENSOR_B" "$AB1_LOG")
B_AB2=$(pub_in_log "$SENSOR_B" "$AB2_LOG")
B_PUB=$((B_AB1 + B_AB2))
print_status "info" "Scenario B: ES docs=$B_DOCS, publishes ab1=$B_AB1 ab2=$B_AB2 (total=$B_PUB)"
# The only hard guarantee: the Elastic sink keys the document by fingerprint, so
# however many instances processed it, ES holds one doc.
if [ "$B_DOCS" != "1" ]; then
    print_status "fail" "Scenario B: expected exactly 1 ES doc (idempotent doc-id), got $B_DOCS"; exit 1
fi
# Whether both instances saw this particular fingerprint is a race (XREADGROUP
# may hand every copy to the same consumer), so it is reported, not asserted.
# Scenario A already proves the affinity loss deterministically.
if [ "$B_PUB" -ge 2 ]; then
    print_status "ok" "Scenario B PASS: duplicate escaped in-process dedup ($B_PUB publishes across instances) and was collapsed by ES doc-id idempotency (1 doc) — this is the cost the README documents"
else
    print_status "ok" "Scenario B PASS: 1 doc. This run happened to route every copy to one instance, so in-process dedup caught them ($B_PUB publish); the idempotency net was not needed"
fi

# ── Scenario C: the documented remedy — one consumer per group ───────────────
echo ""
print_status "wait" "Scenario C: kill one instance, then a duplicate pair must dedup to 1"
print_status "info" "Killing instance 2 (PID $(cat "$AB2_PID"))"
stop_instance "$AB2_PID"
# Consumers linger in XINFO after the process dies, so gate on the survivor being
# healthy rather than on the consumer count dropping.
if ! curl -fsS "http://127.0.0.1:$AB1_PORT/health" >/dev/null 2>&1; then
    print_status "fail" "Scenario C: survivor unhealthy after the kill"; exit 1
fi
sleep 3
SENSOR_C="mcr_single_${RUN}"
TS_C=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
"${PRODUCE[@]}" --sensor-id "$SENSOR_C" --timestamp "$TS_C" >/dev/null
"${PRODUCE[@]}" --sensor-id "$SENSOR_C" --timestamp "$TS_C" >/dev/null
C_DOCS=$(poll_sensor_count "$SENSOR_C" 1 90)
sleep 4
C_PUB=$(pub_total "$SENSOR_C")
C_DOCS=$(es_count_sensor "$SENSOR_C")
print_status "info" "Scenario C: ES docs=$C_DOCS, publishes=$C_PUB"
if [ "$C_DOCS" != "1" ]; then
    print_status "fail" "Scenario C: expected exactly 1 ES doc, got $C_DOCS"; exit 1
fi
if [ "$C_PUB" != "1" ]; then
    print_status "fail" "Scenario C: expected exactly 1 publish with a single consumer in the group, got $C_PUB — in-process dedup is broken even WITH affinity"; exit 1
fi
print_status "ok" "Scenario C PASS: with one consumer per group the duplicate deduped in-process (1 publish) — the documented remedy holds"

echo ""
print_status "ok" "PASS: Redis consumer groups provide no per-sensor affinity (A); duplicates escape in-process dedup and rely on ES doc-id idempotency (B); one replica per group restores correct dedup (C)"
