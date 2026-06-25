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

# Test: Async Kafka Non-Blocking Under Slow VLM
# Description: Verify Kafka consume/dispatch continues while VLM is waiting
#              by injecting fixed NIM delay and asserting dispatch queueing
#              activity before the first delayed VLM response returns.
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
TEST_NAME="async_kafka_non_blocking"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"
SENSOR_PREFIX="ASYNC_NONBLOCK_${ID_SUFFIX}"

BURST_COUNT="${BURST_COUNT:-6}"
NIM_DELAY_SECONDS="${NIM_DELAY_SECONDS:-3.0}"
MIN_QUEUE_DURING_WAIT="${MIN_QUEUE_DURING_WAIT:-2}"

echo "=== P1: Async Kafka Non-Blocking ==="
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
    local delay_seconds="$1"
    if [ "$delay_seconds" = "0" ] || [ "$delay_seconds" = "0.0" ]; then
        NIM_STUB_DELAY_SECONDS="0" \
            python3 "$REPO_ROOT/test/sim_scripts/nim/nim_stub_server.py" > "$PID_DIR/nim_sim.log" 2>&1 &
    else
        NIM_STUB_DELAY_SECONDS="$delay_seconds" \
            python3 "$REPO_ROOT/test/sim_scripts/nim/nim_stub_server.py" > "$PID_DIR/nim_sim.log" 2>&1 &
    fi
    echo $! > "$PID_DIR/nim_sim.pid"
    sleep 2
}

restore_default_nim() {
    stop_nim_sim
    start_nim_sim "0"
}
trap restore_default_nim EXIT

ASYNC_CONFIG="$PID_DIR/${TEST_NAME}_config.yaml"
build_async_external_io_config "$BASE_CONFIG" "$ASYNC_CONFIG"
python3 - "$ASYNC_CONFIG" <<'PY'
import sys
import yaml

config_path = sys.argv[1]
with open(config_path, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

alert_agent = cfg.setdefault("alert_agent", {})
alert_agent["num_workers"] = 1
alert_agent["chunk_size"] = 1
alert_agent["include_latency_info"] = True
alert_agent["async_dispatch_workers"] = 1
alert_agent["async_dispatch_max_in_flight"] = 20

kafka_cfg = cfg.setdefault("kafka", {})
kafka_cfg["max_poll_records"] = 1
kafka_cfg["poll_timeout"] = 100

logging_cfg = cfg.setdefault("logging", {})
logging_cfg["level"] = "DEBUG"
logging_cfg["format"] = "%(asctime)s - %(threadName)s - %(name)s - %(levelname)s - %(message)s"

with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)
PY

if [ -x "$REPO_ROOT/venv/bin/python3" ]; then
    export PATH="$REPO_ROOT/venv/bin:$PATH"
fi

stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$ASYNC_CONFIG"

BASE_LOG_LINE=$(python3 - "$PID_DIR/alert_bridge.log" <<'PY'
import sys
path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        print(sum(1 for _ in f))
except FileNotFoundError:
    print(0)
PY
)

stop_nim_sim
start_nim_sim "$NIM_DELAY_SECONDS"
print_status "info" "NIM simulator restarted with fixed response delay=${NIM_DELAY_SECONDS}s"

for i in $(seq 1 "$BURST_COUNT"); do
    payload_i="$PID_DIR/${TEST_NAME}_payload_${i}.json"
    sensor_id="${SENSOR_PREFIX}_${i}"
    python3 - "$PAYLOAD" "$payload_i" "$sensor_id" "$i" <<'PY'
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

    produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$payload_i" "${ID_SUFFIX}_${i}" --no-patch
done
print_status "info" "Produced burst incidents: count=${BURST_COUNT} sensor_prefix=${SENSOR_PREFIX}_*"

first_response_seen=0
for _ in $(seq 1 90); do
    first_response_seen=$(python3 - "$PID_DIR/alert_bridge.log" "$SENSOR_PREFIX" "$BASE_LOG_LINE" <<'PY'
import re
import sys

log_path, sensor_prefix, start_line = sys.argv[1], sys.argv[2], int(sys.argv[3])
count = 0
with open(log_path, "r", encoding="utf-8", errors="replace") as f:
    for idx, line in enumerate(f, start=1):
        if idx <= start_line:
            continue
        if "VLM response received" in line and sensor_prefix in line:
            count += 1
print(count)
PY
)
    if [ "${first_response_seen}" -gt 0 ]; then
        break
    fi
    sleep 1
done

if [ "${first_response_seen}" -eq 0 ]; then
    print_status "fail" "FAIL: Timed out waiting for first delayed VLM response"
    exit 1
fi

ANALYSIS_OUTPUT=$(python3 - "$PID_DIR/alert_bridge.log" "$SENSOR_PREFIX" "$BASE_LOG_LINE" "$NIM_DELAY_SECONDS" "$MIN_QUEUE_DURING_WAIT" <<'PY'
import datetime as dt
import re
import sys

log_path = sys.argv[1]
sensor_prefix = sys.argv[2]
start_line = int(sys.argv[3])
expected_delay = float(sys.argv[4])
min_queue = int(sys.argv[5])

ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
ts_format = "%Y-%m-%d %H:%M:%S,%f"

requests = []
responses = []
queue_events = []

with open(log_path, "r", encoding="utf-8", errors="replace") as f:
    for idx, line in enumerate(f, start=1):
        if idx <= start_line:
            continue
        ts_match = ts_pattern.match(line)
        if not ts_match:
            continue
        timestamp = dt.datetime.strptime(ts_match.group(1), ts_format)
        stripped = line.rstrip("\n")

        if "VLM request sent" in stripped and sensor_prefix in stripped:
            requests.append((timestamp, stripped))
        if "VLM response received" in stripped and sensor_prefix in stripped:
            responses.append((timestamp, stripped))
        if (
            "Queueing message to async dispatch pipeline" in stripped
            or "Message queued for async dispatch" in stripped
        ):
            queue_events.append((timestamp, stripped))

if not requests:
    print("FAIL: No VLM request logs found for test sensor prefix")
    sys.exit(1)
if not responses:
    print("FAIL: No VLM response logs found for test sensor prefix")
    sys.exit(1)

first_request_time, _ = requests[0]
first_response_time = None
for ts, line in responses:
    if ts >= first_request_time:
        first_response_time = ts
        break
if first_response_time is None:
    print("FAIL: Could not find response after first request")
    sys.exit(1)

queue_during_wait = [
    evt for evt in queue_events if first_request_time <= evt[0] < first_response_time
]
request_to_response_seconds = (first_response_time - first_request_time).total_seconds()

if request_to_response_seconds < max(1.0, expected_delay * 0.6):
    print(
        "FAIL: First request->response duration too short for delayed VLM "
        f"(observed={request_to_response_seconds:.3f}s expected_delay={expected_delay:.3f}s)"
    )
    sys.exit(1)

if len(queue_during_wait) < min_queue:
    print(
        "FAIL: Dispatch queue activity did not continue while first VLM call waited "
        f"(queue_events_during_wait={len(queue_during_wait)} min_required={min_queue})"
    )
    sys.exit(1)

print(
    "OK: request_to_response={:.3f}s queue_events_during_wait={} requests={} responses={}".format(
        request_to_response_seconds,
        len(queue_during_wait),
        len(requests),
        len(responses),
    )
)
PY
)

print_status "info" "$ANALYSIS_OUTPUT"

DOC_COUNT=0
for _ in $(seq 1 120); do
    all_docs=$(get_all_es_docs "$ES_HOST")
    DOC_COUNT=$(SENSOR_PREFIX="$SENSOR_PREFIX" python3 -c "
import json
import os
import sys

sensor_prefix = os.environ.get('SENSOR_PREFIX', '')
docs = json.load(sys.stdin)
count = 0
for doc in docs:
    sid = str(doc.get('sensorId', doc.get('sensor_id', '')))
    if sid.startswith(sensor_prefix):
        count += 1
print(count)
" <<< "$all_docs" 2>/dev/null || echo "0")

    if [ "$DOC_COUNT" -ge "$BURST_COUNT" ]; then
        break
    fi
    sleep 1
done

if [ "$DOC_COUNT" -lt "$BURST_COUNT" ]; then
    print_status "fail" "FAIL: Expected >=${BURST_COUNT} docs for ${SENSOR_PREFIX}_* but got ${DOC_COUNT}"
    exit 1
fi

print_status "ok" "PASS: Kafka consume/dispatch continued while delayed VLM was in-flight"
exit 0
