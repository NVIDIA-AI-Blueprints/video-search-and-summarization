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

# Shared helpers for P1 functional tests

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_status() {
    local status=$1 message=$2
    case "$status" in
        ok)   echo -e "${GREEN}✓${NC} $message" ;;
        fail) echo -e "${RED}✗${NC} $message" ;;
        wait) echo -e "${YELLOW}⏳${NC} $message" ;;
        info) echo -e "ℹ $message" ;;
        *)    echo "  $message" ;;
    esac
}

# Patch payload timestamps to today — from P0 step3 lines 72-84
# CRITICAL: Without this, ES index date won't match today and tests fail
patch_timestamps() {
    local input="$1" output="$2"
    python3 -c "
import json
from datetime import datetime, timezone
with open('$input') as f: data = json.load(f)
now = datetime.now(timezone.utc)
data['timestamp'] = now.strftime('%Y-%m-%dT%H:%M:%S.000Z')
data['end'] = data['timestamp']
with open('$output', 'w') as f: json.dump(data, f)
"
}

# Produce incident to Kafka
# Usage: produce_incident REPO_ROOT BOOTSTRAP TOPIC PAYLOAD ID_SUFFIX [--no-patch]
# By default, auto-patches timestamps to today so ES daily index matches.
# Pass --no-patch as 6th arg to preserve original timestamps (for segment tests, etc.)
produce_incident() {
    local repo_root="$1" bootstrap="$2" topic="$3" payload="$4" id_suffix="$5"
    local no_patch="${6:-}"
    local pid_dir="${PID_DIR:-/tmp/alert_agent_p1_functional}"
    local patched="$pid_dir/.patched_$(basename "$payload")_$$"

    if [ "$no_patch" = "--no-patch" ]; then
        # Use payload as-is without patching timestamps
        patched="$payload"
    else
        # Auto-patch timestamps to today so ES daily index matches
        patch_timestamps "$payload" "$patched"
    fi

    python3 "$repo_root/test/protobuf/produce_incident.py" \
        --bootstrap "$bootstrap" --topic "$topic" \
        --payload "$patched" --id-suffix "$id_suffix"

    # Only remove temp file if we created one
    if [ "$no_patch" != "--no-patch" ]; then
        rm -f "$patched"
    fi
}

# ─── Redis Streams transport (optional source/sink) ─────────────────────────
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_CONTAINER="${REDIS_CONTAINER:-alert-agent-redis-test}"

redis_available() {
    nc -z "$REDIS_HOST" "$REDIS_PORT" 2>/dev/null
}

# Gate a redisStream test on Redis being there, and decide what its absence
# means. Call as: require_redis "<test name>" || exit $?
#
# A suite that reports success when the transport under test was never
# exercised proves nothing, and the skip used to be unconditional — so a run
# where Redis failed to start passed with every redisStream test skipped and
# nothing to distinguish it from a run that tested them.
#
# run_p1.sh starts a Redis, so REDIS_REQUIRED defaults to 1 there: a missing
# broker means the container did not come up, which is a failure. SKIP_REDIS=1
# is the way to run without it, and turns these back into skips. The default
# here stays 0 so a test invoked directly, outside the orchestrator, behaves as
# it always did.
require_redis() {
    local test_name="${1:-redisStream test}"
    if redis_available; then
        print_status "ok" "Redis reachable at $REDIS_HOST:$REDIS_PORT"
        return 0
    fi
    if [ "${REDIS_REQUIRED:-0}" = "1" ]; then
        print_status "fail" \
            "FAIL: $test_name needs Redis on $REDIS_HOST:$REDIS_PORT and REDIS_REQUIRED=1. Run with SKIP_REDIS=1 to skip the redisStream tests instead."
        return 1
    fi
    print_status "info" \
        "SKIP: no Redis on $REDIS_HOST:$REDIS_PORT (set REDIS_REQUIRED=1 to fail instead)"
    return "${EXIT_SKIP:-66}"
}

# Publish an alert (Behavior) into a Redis Stream as a JSON payload.
#
# The two envelope formats are a real interop contract: the MDX envelope keeps
# the body in `value`, while RT-VLM and the pre-MDX prototype use a `data` /
# `metadata` JSON envelope. `--envelope json` publishes the latter, so the
# source's field precedence is exercised by a producer rather than only by a
# unit test.
# Usage: produce_alert_redis REPO_ROOT STREAM PAYLOAD ID_SUFFIX [--envelope json]
produce_alert_redis() {
    local repo_root="$1" stream="$2" payload="$3" id_suffix="$4"
    shift 4
    local pid_dir="${PID_DIR:-/tmp/alert_agent_p1_functional}"
    local patched="$pid_dir/.patched_redis_alert_$(basename "$payload")_$$"

    patch_timestamps "$payload" "$patched"
    python3 "$repo_root/test/protobuf/produce_incident_redis_stream.py" \
        --host "$REDIS_HOST" --port "$REDIS_PORT" --stream "$stream" \
        --payload "$patched" --id-suffix "$id_suffix" --json "$@"
    local rc=$?
    rm -f "$patched"
    return $rc
}

# XPENDING count for a group, or 0 when the stream or group is absent.
# An un-acked entry is replayed on every restart, so this is how a test proves
# the ack lifecycle actually completed rather than merely not erroring.
redis_pending_count() {
    local stream="$1" group="$2"
    docker exec "$REDIS_CONTAINER" redis-cli XPENDING "$stream" "$group" 2>/dev/null \
        | head -1 | tr -d '\r' || echo 0
}

# Publish an Incident protobuf into a Redis Stream using the MDX envelope,
# mirroring produce_incident() but for the redisStream source.
# Usage: produce_incident_redis REPO_ROOT STREAM PAYLOAD ID_SUFFIX [--json]
produce_incident_redis() {
    local repo_root="$1" stream="$2" payload="$3" id_suffix="$4"
    local encoding="${5:-}"
    local pid_dir="${PID_DIR:-/tmp/alert_agent_p1_functional}"
    local patched="$pid_dir/.patched_redis_$(basename "$payload")_$$"

    patch_timestamps "$payload" "$patched"
    python3 "$repo_root/test/protobuf/produce_incident_redis_stream.py" \
        --host "$REDIS_HOST" --port "$REDIS_PORT" --stream "$stream" \
        --payload "$patched" --id-suffix "$id_suffix" $encoding
    local rc=$?
    rm -f "$patched"
    return $rc
}

# Number of entries currently in a Redis Stream (0 when the stream is absent).
redis_stream_len() {
    docker exec "$REDIS_CONTAINER" redis-cli XLEN "$1" 2>/dev/null | tr -d '\r' || echo 0
}

# Poll a Redis Stream for an entry whose payload mentions SENSOR_ID, and echo
# the decoded document. Handles both payload encodings the sink can emit:
# protobuf (the default, what Logstash consumes) and JSON.
# Usage: poll_redis_stream_for_sensor STREAM SENSOR_ID [TIMEOUT] [INTERVAL]
poll_redis_stream_for_sensor() {
    local stream="$1" sensor_id="$2" timeout="${3:-60}" interval="${4:-3}"
    local repo_root="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
    local elapsed=0

    while [ "$elapsed" -lt "$timeout" ]; do
        local doc
        doc=$(AB_SRC="$repo_root/src" REDIS_HOST="$REDIS_HOST" REDIS_PORT="$REDIS_PORT" \
              STREAM="$stream" SENSOR_ID="$sensor_id" python3 - <<'PYEOF'
import json
import os
import sys

sys.path.insert(0, os.environ["AB_SRC"])

import redis

from mdx.redis_stream_broker import extract_envelope

client = redis.Redis(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    decode_responses=False,
)
sensor_id = os.environ["SENSOR_ID"]

def as_document(payload):
    """Decode an entry body to a dict, whichever shape it is in.

    Three shapes reach here: JSON text, an Incident protobuf, and a Behavior
    protobuf — the last one because the alert route publishes Behavior, so a
    poller that only tried Incident could not see an alert at all and would
    report a timeout instead of the wrong-schema bug it actually hit.
    """
    try:
        return json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        pass

    from mdx.protobuf import Behavior, Incident

    incident = Incident()
    try:
        incident.ParseFromString(payload)
        if incident.sensorId:
            return {
                "kind": "incident",
                "sensorId": incident.sensorId,
                "category": incident.category,
                "info": dict(incident.info),
            }
    except Exception:
        pass

    behavior = Behavior()
    try:
        behavior.ParseFromString(payload)
    except Exception:
        return None
    return {
        "kind": "alert",
        "sensorId": behavior.sensor.id,
        "eventType": behavior.event.type,
        "info": dict(behavior.info),
    }


for _entry_id, fields in client.xrange(os.environ["STREAM"]) or []:
    payload, _key, _headers = extract_envelope(fields)
    if payload is None:
        continue
    document = as_document(payload)
    if not document:
        continue
    found = str(document.get("sensorId") or (document.get("sensor") or {}).get("id") or "")
    if sensor_id in found:
        print(json.dumps(document))
        break
PYEOF
        ) || doc=""

        if [ -n "$doc" ] && [ "$doc" != "{}" ]; then
            echo "$doc"
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    return 1
}

# Poll ES sim for documents — from P0 step4 pattern
# Uses ES simulator's /_all endpoint (NOT standard ES _search)
poll_es_sim() {
    local es_host="$1" timeout="${2:-60}" interval="${3:-5}"
    local today=$(date -u +%Y-%m-%d)
    local index="mdx-vlm-incidents-$today"
    local elapsed=0

    while [ $elapsed -lt $timeout ]; do
        local response=$(curl -sf "$es_host/$index/_all" 2>/dev/null || echo "")
        if [ -n "$response" ]; then
            local doc=$(echo "$response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
docs = data.get('documents', [])
if docs:
    print(json.dumps(docs[-1].get('_source', docs[-1])))
" 2>/dev/null || echo "")
            if [ -n "$doc" ] && [ "$doc" != "{}" ] && [ "$doc" != "null" ]; then
                echo "$doc"
                return 0
            fi
        fi
        sleep $interval; elapsed=$((elapsed + interval))
    done
    return 1
}

# Poll ES sim for latest document by sensorId
# Usage: poll_es_doc_by_sensor ES_HOST SENSOR_ID [TIMEOUT] [INTERVAL]
poll_es_doc_by_sensor() {
    local es_host="$1" sensor_id="$2" timeout="${3:-60}" interval="${4:-5}"
    local elapsed=0

    while [ $elapsed -lt $timeout ]; do
        local all_docs
        all_docs=$(get_all_es_docs "$es_host")
        local doc
        doc=$(SENSOR_ID="$sensor_id" python3 -c "
import os, sys, json
sensor_id = os.environ.get('SENSOR_ID', '')
docs = json.load(sys.stdin)
matches = []
for doc in docs:
    sid = str(doc.get('sensorId', doc.get('sensor_id', '')))
    if sid == sensor_id:
        matches.append(doc)
if matches:
    print(json.dumps(matches[-1]))
" <<< "$all_docs" 2>/dev/null || echo "")

        if [ -n "$doc" ] && [ "$doc" != "{}" ] && [ "$doc" != "null" ]; then
            echo "$doc"
            return 0
        fi

        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    return 1
}

# Build async-enabled config from a source config
# Usage: build_async_external_io_config SRC_CONFIG DST_CONFIG
build_async_external_io_config() {
    local src_config="$1" dst_config="$2"
    python3 - "$src_config" "$dst_config" <<'PY'
import sys
import yaml

src, dst = sys.argv[1], sys.argv[2]
with open(src, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

alert_agent = cfg.setdefault("alert_agent", {})
async_io = alert_agent.setdefault("async_io", {})
async_io["enabled"] = True
async_io["vst_enabled"] = True
async_io["elastic_enabled"] = True
async_io["dedup_enabled"] = True
async_io["external_timeout_seconds"] = 30
async_io["sink_warn_in_flight"] = 20

with open(dst, "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)
PY
}

# Build event_loop-mode config from a source config
# Usage: build_event_loop_config SRC_CONFIG DST_CONFIG [MAX_IN_FLIGHT] [MAX_VLM] [MAX_VST]
build_event_loop_config() {
    local src_config="$1" dst_config="$2"
    local max_in_flight="${3:-20}" max_vlm="${4:-20}" max_vst="${5:-20}"
    python3 - "$src_config" "$dst_config" "$max_in_flight" "$max_vlm" "$max_vst" <<'PY'
import sys
import yaml

src, dst = sys.argv[1], sys.argv[2]
max_in_flight, max_vlm, max_vst = int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
with open(src, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

alert_agent = cfg.setdefault("alert_agent", {})
alert_agent["pipeline_mode"] = "event_loop"
alert_agent["async_dispatch_max_in_flight"] = max_in_flight
async_io = alert_agent.setdefault("async_io", {})
async_io["max_vlm_concurrent"] = max_vlm
async_io["max_vst_concurrent"] = max_vst

# Gated/held stub responses must not trip the VLM client timeout.
vlm = cfg.setdefault("vlm", {})
vlm["request_timeout"] = max(int(vlm.get("request_timeout", 0) or 0), 150)

with open(dst, "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)
PY
}

# ─── NIM stub control (concurrency stats + response gate) ───────────────────
NIM_STUB_URL="${NIM_STUB_URL:-http://127.0.0.1:18081}"

nim_stub_stats() {
    curl -sf "$NIM_STUB_URL/stub/stats" 2>/dev/null || echo "{}"
}

nim_stub_stat() {
    local field="$1"
    nim_stub_stats | python3 -c "import sys, json; print(json.load(sys.stdin).get('$field', 0))" 2>/dev/null || echo 0
}

nim_stub_gate() {
    curl -sf -X POST "$NIM_STUB_URL/stub/gate/$1" >/dev/null
}

nim_stub_reset() {
    curl -sf -X POST "$NIM_STUB_URL/stub/reset" >/dev/null
}

# Poll until the stub reports the expected number of in-flight VLM requests.
# Usage: nim_stub_wait_in_flight EXPECTED [TIMEOUT_SECONDS]
nim_stub_wait_in_flight() {
    local expected="$1" timeout="${2:-60}"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        local current
        current=$(nim_stub_stat in_flight)
        if [ "$current" -ge "$expected" ]; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

# Total consumer-group lag across partitions (requires the p1 Kafka container).
# Usage: kafka_consumer_lag GROUP_ID
kafka_consumer_lag() {
    local group_id="$1"
    docker exec alert-agent-kafka-test kafka-consumer-groups \
        --bootstrap-server localhost:9092 --describe --group "$group_id" 2>/dev/null \
        | awk 'NR>1 && $6 ~ /^[0-9]+$/ {sum += $6} END {print sum + 0}'
}

# Stop Alert Bridge process managed by PID file
# Usage: stop_alert_bridge_local PID_DIR
stop_alert_bridge_local() {
    local pid_dir="$1"
    if [ -f "$pid_dir/alert_bridge.pid" ]; then
        local pid
        pid=$(cat "$pid_dir/alert_bridge.pid")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            sleep 1
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$pid_dir/alert_bridge.pid"
    fi
    pkill -f "enhance_alert_with_vlm.py" 2>/dev/null || true
}

# Start Alert Bridge with config and wait for bootstrap
# Usage: start_alert_bridge_local REPO_ROOT PID_DIR CONFIG_FILE [WAIT_SECONDS]
start_alert_bridge_local() {
    local repo_root="$1" pid_dir="$2" config_file="$3" wait_seconds="${4:-15}"
    local config_dir
    config_dir="$(cd "$(dirname "$config_file")" && pwd)"
    ALERT_AGENT_CONFIG_DIR="$config_dir" python3 "$repo_root/enhance_alert_with_vlm.py" --config "$config_file" > "$pid_dir/alert_bridge.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$pid_dir/alert_bridge.pid"
    sleep "$wait_seconds"
    if ! kill -0 "$pid" 2>/dev/null; then
        print_status "fail" "Alert Bridge failed to start with config: $config_file"
        tail -20 "$pid_dir/alert_bridge.log" 2>/dev/null || true
        return 1
    fi
    print_status "ok" "Alert Bridge running (PID $pid)"
}

# Count documents in ES sim
count_es_docs() {
    local es_host="$1"
    local today=$(date -u +%Y-%m-%d)
    local index="mdx-vlm-incidents-$today"

    # A real Elasticsearch first: it serves _count and, being near-real-time,
    # needs the refresh or the last second of writes is missing. The simulator
    # answers neither, so a run against it falls through to its own /_all.
    curl -sf -X POST "$es_host/$index/_refresh" >/dev/null 2>&1
    local counted=$(curl -sf "$es_host/$index/_count" 2>/dev/null | python3 -c "
import sys, json
try:
    print(int(json.load(sys.stdin)['count']))
except Exception:
    pass
" 2>/dev/null)
    if [ -n "$counted" ]; then
        echo "$counted"
        return
    fi

    local response=$(curl -sf "$es_host/$index/_all" 2>/dev/null || echo "")
    if [ -n "$response" ]; then
        echo "$response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
docs = data.get('documents', [])
print(len(docs))
" 2>/dev/null || echo "0"
    else
        echo "0"
    fi
}

# Get all documents from ES sim as JSON array
get_all_es_docs() {
    local es_host="$1"
    local today=$(date -u +%Y-%m-%d)
    local index="mdx-vlm-incidents-$today"
    local response=$(curl -sf "$es_host/$index/_all" 2>/dev/null || echo "")
    if [ -n "$response" ]; then
        echo "$response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
docs = data.get('documents', [])
result = [d.get('_source', d) for d in docs]
print(json.dumps(result))
" 2>/dev/null || echo "[]"
    else
        echo "[]"
    fi
}
