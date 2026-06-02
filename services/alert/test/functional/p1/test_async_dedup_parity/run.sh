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

# Test: Async Redis Dedup Parity
# Description: Compare sync vs async Redis dedup behavior (same duplicate input should index exactly one document).
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
TEST_NAME="async_dedup_parity"

echo "=== P1: Async Redis Dedup Parity ==="
mkdir -p "$PID_DIR"

ASYNC_CONFIG="$PID_DIR/${TEST_NAME}_async_config.yaml"
build_async_external_io_config "$BASE_CONFIG" "$ASYNC_CONFIG"

if [ -x "$REPO_ROOT/venv/bin/python3" ]; then
    export PATH="$REPO_ROOT/venv/bin:$PATH"
fi

reset_mode_state() {
    local today
    today=$(date -u +%Y-%m-%d)

    redis-cli -h 127.0.0.1 -p 6379 FLUSHALL >/dev/null 2>&1 || true
    docker exec alert-agent-redis-test redis-cli FLUSHALL >/dev/null 2>&1 || true

    curl -sf -X DELETE "$ES_HOST/mdx-vlm-incidents-$today" >/dev/null 2>&1 || true
    curl -sf -X DELETE "$ES_HOST/mdx-vlm-alerts-$today" >/dev/null 2>&1 || true
}

count_docs_by_sensor() {
    local sensor_id="$1"
    local all_docs
    all_docs=$(get_all_es_docs "$ES_HOST")
    SENSOR_ID="$sensor_id" python3 -c "
import json
import os
import sys

sensor_id = os.environ.get('SENSOR_ID', '')
docs = json.load(sys.stdin)
count = 0
for doc in docs:
    sid = str(doc.get('sensorId', doc.get('sensor_id', '')))
    if sid == sensor_id:
        count += 1
print(count)
" <<< "$all_docs" 2>/dev/null || echo "0"
}

wait_for_alert_bridge_processing_loop() {
    local timeout="${1:-90}"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        if grep -q "Starting anomaly processing loop" "$PID_DIR/alert_bridge.log" 2>/dev/null; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

run_dedup_mode() {
    local mode_label="$1"
    local config_file="$2"
    local sensor_id="$3"
    local id_suffix="$4"
    local result_var="$5"

    stop_alert_bridge_local "$PID_DIR"
    reset_mode_state
    start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$config_file"
    if ! wait_for_alert_bridge_processing_loop 90; then
        print_status "fail" "[$mode_label] FAIL: Alert Bridge did not reach processing loop"
        return 1
    fi

    local payload_file="$PID_DIR/${TEST_NAME}_${mode_label}.json"
    python3 - "$PAYLOAD" "$payload_file" "$sensor_id" <<'PY'
import json
import sys
from datetime import datetime, timezone

src, dst, sensor_id = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, "r", encoding="utf-8") as f:
    data = json.load(f)
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
data["sensorId"] = sensor_id
data["timestamp"] = now
data["end"] = now
with open(dst, "w", encoding="utf-8") as f:
    json.dump(data, f)
PY

    local before after added
    before=$(count_docs_by_sensor "$sensor_id")
    produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$payload_file" "$id_suffix" --no-patch
    produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$payload_file" "$id_suffix" --no-patch
    print_status "info" "[$mode_label] Sent duplicate incidents (id-suffix=$id_suffix)"

    if ! poll_es_doc_by_sensor "$ES_HOST" "$sensor_id" 90 2 >/dev/null; then
        print_status "fail" "[$mode_label] FAIL: No document found for sensorId=$sensor_id within timeout"
        return 1
    fi

    # Allow brief settle window for duplicate to appear if dedup failed.
    sleep 6
    after=$(count_docs_by_sensor "$sensor_id")
    added=$((after - before))
    print_status "info" "[$mode_label] Docs added for sensor=$sensor_id: $added (before=$before after=$after)"

    printf -v "$result_var" '%s' "$added"
}

SYNC_ADDED=0
ASYNC_ADDED=0

run_dedup_mode "sync" "$BASE_CONFIG" "ASYNC_DEDUP_SYNC_SENSOR" "p1_${TEST_NAME}_sync_fixed" SYNC_ADDED
run_dedup_mode "async" "$ASYNC_CONFIG" "ASYNC_DEDUP_ASYNC_SENSOR" "p1_${TEST_NAME}_async_fixed" ASYNC_ADDED

if [ "$SYNC_ADDED" -ne 1 ]; then
    print_status "fail" "FAIL: Sync baseline expected 1 doc, got $SYNC_ADDED"
    exit 1
fi

if [ "$ASYNC_ADDED" -ne 1 ]; then
    print_status "fail" "FAIL: Async mode expected 1 doc, got $ASYNC_ADDED"
    exit 1
fi

if [ "$SYNC_ADDED" -ne "$ASYNC_ADDED" ]; then
    print_status "fail" "FAIL: Dedup parity mismatch (sync=$SYNC_ADDED async=$ASYNC_ADDED)"
    exit 1
fi

print_status "ok" "PASS: Redis dedup parity confirmed (sync=$SYNC_ADDED async=$ASYNC_ADDED)"
exit 0
