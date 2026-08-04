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

# Test: Event-Loop Concurrency And Per-Service Cap
# Description: With the NIM stub gate closed, prove that (a) in-flight VLM
#              concurrency exceeds the worker/dispatch thread count while the
#              Kafka consumer keeps draining (lag stays 0 and no documents are
#              published until the gate opens), and (b) max_vlm_concurrent is
#              a hard cap — the stub's peak in-flight never exceeds it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"
BASE_CONFIG="${P1_ROOT}/shared/config_base.yaml"
TEST_NAME="event_loop_concurrency"
CONSUMER_GROUP="${CONSUMER_GROUP:-alert-bridge-vlm-group-p1}"

BURST_COUNT="${BURST_COUNT:-12}"
CAP_BURST_COUNT="${CAP_BURST_COUNT:-10}"
VLM_CAP="${VLM_CAP:-3}"

echo "=== P1: Event-Loop Concurrency And Per-Service Cap ==="
mkdir -p "$PID_DIR"

if [ ! -f "$PID_DIR/nim_sim.pid" ]; then
    print_status "info" "SKIP: $PID_DIR/nim_sim.pid not found - NIM sim not managed by this harness"
    exit 0
fi

stop_nim_sim() {
    if [ -f "$PID_DIR/nim_sim.pid" ]; then
        local pid
        pid=$(cat "$PID_DIR/nim_sim.pid")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            sleep 1
            kill -9 "$pid" 2>/dev/null || true
        fi
    fi
}

start_nim_sim() {
    NIM_STUB_DELAY_SECONDS="0" \
        python3 "$REPO_ROOT/test/sim_scripts/nim/nim_stub_server.py" > "$PID_DIR/nim_sim.log" 2>&1 &
    echo $! > "$PID_DIR/nim_sim.pid"
    sleep 2
}

cleanup() {
    nim_stub_gate open 2>/dev/null || true
    stop_nim_sim
    start_nim_sim
}
trap cleanup EXIT

if [ -x "$REPO_ROOT/venv/bin/python3" ]; then
    export PATH="$REPO_ROOT/venv/bin:$PATH"
fi

# Fresh stub so counters/gate start clean
stop_nim_sim
start_nim_sim

produce_burst() {
    local sensor_prefix="$1" count="$2" id_suffix="$3"
    local i
    for i in $(seq 1 "$count"); do
        local payload_i="$PID_DIR/${TEST_NAME}_payload_${sensor_prefix}_${i}.json"
        python3 - "$PAYLOAD" "$payload_i" "${sensor_prefix}_${i}" "$i" <<'PY'
import json
import sys
from datetime import datetime, timedelta, timezone

src, dst, sensor_id, offset = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
with open(src, "r", encoding="utf-8") as f:
    data = json.load(f)
ts = (datetime.now(timezone.utc) + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
data["sensorId"] = sensor_id
data["timestamp"] = ts
data["end"] = ts
with open(dst, "w", encoding="utf-8") as f:
    json.dump(data, f)
PY
        produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$payload_i" "${id_suffix}_${i}" --no-patch
    done
}

count_docs_by_prefix() {
    local sensor_prefix="$1"
    local all_docs
    all_docs=$(get_all_es_docs "$ES_HOST")
    SENSOR_PREFIX="$sensor_prefix" python3 -c "
import json
import os
import sys

sensor_prefix = os.environ.get('SENSOR_PREFIX', '')
docs = json.load(sys.stdin)
print(sum(1 for d in docs if str(d.get('sensorId', '')).startswith(sensor_prefix)))
" <<< "$all_docs" 2>/dev/null || echo "0"
}

wait_docs_by_prefix() {
    local sensor_prefix="$1" expected="$2" timeout="${3:-120}"
    local elapsed=0 count=0
    while [ "$elapsed" -lt "$timeout" ]; do
        count=$(count_docs_by_prefix "$sensor_prefix")
        if [ "$count" -ge "$expected" ]; then
            echo "$count"
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    echo "$count"
    return 1
}

# ─── Phase A: concurrency exceeds worker/thread count ────────────────────────
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"
SENSOR_PREFIX_A="EL_CONC_${ID_SUFFIX}"

CONFIG_A="$PID_DIR/${TEST_NAME}_config_a.yaml"
build_event_loop_config "$BASE_CONFIG" "$CONFIG_A" 32 "$BURST_COUNT" "$BURST_COUNT"
python3 - "$CONFIG_A" "$BURST_COUNT" <<'PY'
import sys
import yaml

config_path, burst = sys.argv[1], int(sys.argv[2])
with open(config_path, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

alert_agent = cfg.setdefault("alert_agent", {})
alert_agent["num_workers"] = 1
alert_agent["chunk_size"] = 1
alert_agent["async_dispatch_workers"] = 1

kafka_cfg = cfg.setdefault("kafka", {})
kafka_cfg["max_poll_records"] = burst
kafka_cfg["poll_timeout"] = 100

logging_cfg = cfg.setdefault("logging", {})
logging_cfg["level"] = "DEBUG"
logging_cfg["format"] = "%(asctime)s - %(threadName)s - %(name)s - %(levelname)s - %(message)s"

with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)
PY

stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$CONFIG_A"

nim_stub_reset
nim_stub_gate close
print_status "info" "[phase A] NIM stub gate closed; producing burst of $BURST_COUNT"

produce_burst "$SENSOR_PREFIX_A" "$BURST_COUNT" "${ID_SUFFIX}_a"

if ! nim_stub_wait_in_flight "$BURST_COUNT" 90; then
    print_status "fail" "FAIL: [phase A] stub never reached $BURST_COUNT concurrent VLM requests (in_flight=$(nim_stub_stat in_flight))"
    exit 1
fi
print_status "ok" "[phase A] $BURST_COUNT VLM requests in flight concurrently (num_workers=1, async_dispatch_workers=1)"

DOCS_WHILE_HELD=$(count_docs_by_prefix "$SENSOR_PREFIX_A")
if [ "$DOCS_WHILE_HELD" -ne 0 ]; then
    print_status "fail" "FAIL: [phase A] $DOCS_WHILE_HELD documents published while all VLM responses were held"
    exit 1
fi
print_status "ok" "[phase A] No documents published while gate closed"

LAG_OK=0
for _ in $(seq 1 30); do
    LAG=$(kafka_consumer_lag "$CONSUMER_GROUP")
    if [ -n "$LAG" ] && [ "$LAG" -eq 0 ] 2>/dev/null; then
        LAG_OK=1
        break
    fi
    sleep 1
done
if [ "$LAG_OK" -ne 1 ]; then
    print_status "fail" "FAIL: [phase A] consumer lag did not drain to 0 while VLM was held (lag=${LAG:-unknown})"
    exit 1
fi
print_status "ok" "[phase A] Kafka consumer lag drained to 0 while all $BURST_COUNT VLM calls were held"

nim_stub_gate open
FINAL_COUNT=$(wait_docs_by_prefix "$SENSOR_PREFIX_A" "$BURST_COUNT" 120) || {
    print_status "fail" "FAIL: [phase A] expected $BURST_COUNT docs after gate open, got $FINAL_COUNT"
    exit 1
}

PEAK_A=$(nim_stub_stat peak_in_flight)
if [ "$PEAK_A" -lt "$BURST_COUNT" ]; then
    print_status "fail" "FAIL: [phase A] peak in-flight $PEAK_A < $BURST_COUNT"
    exit 1
fi
print_status "ok" "[phase A] All $FINAL_COUNT docs published after gate open (peak_in_flight=$PEAK_A)"

# ─── Phase B: max_vlm_concurrent is a hard cap ────────────────────────────────
SENSOR_PREFIX_B="EL_CAP_${ID_SUFFIX}"

CONFIG_B="$PID_DIR/${TEST_NAME}_config_b.yaml"
build_event_loop_config "$BASE_CONFIG" "$CONFIG_B" 32 "$VLM_CAP" "$CAP_BURST_COUNT"
python3 - "$CONFIG_B" "$CAP_BURST_COUNT" <<'PY'
import sys
import yaml

config_path, burst = sys.argv[1], int(sys.argv[2])
with open(config_path, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

alert_agent = cfg.setdefault("alert_agent", {})
alert_agent["num_workers"] = 1
alert_agent["chunk_size"] = 1
alert_agent["async_dispatch_workers"] = 1

kafka_cfg = cfg.setdefault("kafka", {})
kafka_cfg["max_poll_records"] = burst
kafka_cfg["poll_timeout"] = 100

with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)
PY

stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$CONFIG_B"

nim_stub_reset
nim_stub_gate close
print_status "info" "[phase B] NIM stub gate closed; producing burst of $CAP_BURST_COUNT with max_vlm_concurrent=$VLM_CAP"

produce_burst "$SENSOR_PREFIX_B" "$CAP_BURST_COUNT" "${ID_SUFFIX}_b"

if ! nim_stub_wait_in_flight "$VLM_CAP" 90; then
    print_status "fail" "FAIL: [phase B] stub never reached $VLM_CAP concurrent VLM requests (in_flight=$(nim_stub_stat in_flight))"
    exit 1
fi

nim_stub_gate open
FINAL_COUNT_B=$(wait_docs_by_prefix "$SENSOR_PREFIX_B" "$CAP_BURST_COUNT" 120) || {
    print_status "fail" "FAIL: [phase B] expected $CAP_BURST_COUNT docs after gate open, got $FINAL_COUNT_B"
    exit 1
}

PEAK_B=$(nim_stub_stat peak_in_flight)
if [ "$PEAK_B" -ne "$VLM_CAP" ]; then
    print_status "fail" "FAIL: [phase B] peak in-flight $PEAK_B != cap $VLM_CAP across $CAP_BURST_COUNT messages"
    exit 1
fi
print_status "ok" "[phase B] Cap held: peak_in_flight=$PEAK_B == max_vlm_concurrent=$VLM_CAP for all $FINAL_COUNT_B docs"

print_status "ok" "PASS: Event-loop concurrency exceeds thread count and per-service cap is enforced"
exit 0
