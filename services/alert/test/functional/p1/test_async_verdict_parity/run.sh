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

# Test: Async Verdict Parity
# Description: Compare sync vs async runs for the same incident shape and assert verdict/status parity.
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
TEST_NAME="async_verdict_parity"
SYNC_SENSOR_ID="ASYNC_PARITY_SYNC_SENSOR"
ASYNC_SENSOR_ID="ASYNC_PARITY_ASYNC_SENSOR"

echo "=== P1: Async Verdict Parity ==="
mkdir -p "$PID_DIR"

ASYNC_CONFIG="$PID_DIR/${TEST_NAME}_async_config.yaml"
build_async_external_io_config "$BASE_CONFIG" "$ASYNC_CONFIG"

FIXED_TS=$(python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
PY
)

build_payload() {
    local sensor_id="$1"
    local output="$2"
    python3 - "$PAYLOAD" "$output" "$sensor_id" "$FIXED_TS" <<'PY'
import json
import sys

src, dst, sensor_id, fixed_ts = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(src, "r", encoding="utf-8") as f:
    data = json.load(f)
data["sensorId"] = sensor_id
data["timestamp"] = fixed_ts
data["end"] = fixed_ts
with open(dst, "w", encoding="utf-8") as f:
    json.dump(data, f)
PY
}

extract_signature() {
    local doc_json="$1"
    echo "$doc_json" | python3 -c "
import sys, json
doc = json.load(sys.stdin)
info = doc.get('info', {})
sig = {
    'verdict': info.get('verdict'),
    'verificationResponseCode': info.get('verificationResponseCode'),
    'verificationResponseStatus': info.get('verificationResponseStatus'),
}
print(json.dumps(sig, sort_keys=True))
" 2>/dev/null
}

# --- Sync baseline ---
stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$BASE_CONFIG"

SYNC_PAYLOAD="$PID_DIR/${TEST_NAME}_sync_payload.json"
build_payload "$SYNC_SENSOR_ID" "$SYNC_PAYLOAD"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$SYNC_PAYLOAD" "p1_${TEST_NAME}_sync" --no-patch
print_status "info" "Sync incident produced for sensorId=$SYNC_SENSOR_ID"

sleep 10
SYNC_DOC=$(poll_es_doc_by_sensor "$ES_HOST" "$SYNC_SENSOR_ID" 60 5) || {
    print_status "fail" "FAIL: No sync parity document found"
    exit 1
}
SYNC_SIG=$(extract_signature "$SYNC_DOC")

# --- Async run ---
stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$ASYNC_CONFIG"

ASYNC_PAYLOAD="$PID_DIR/${TEST_NAME}_async_payload.json"
build_payload "$ASYNC_SENSOR_ID" "$ASYNC_PAYLOAD"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$ASYNC_PAYLOAD" "p1_${TEST_NAME}_async" --no-patch
print_status "info" "Async incident produced for sensorId=$ASYNC_SENSOR_ID"

sleep 10
ASYNC_DOC=$(poll_es_doc_by_sensor "$ES_HOST" "$ASYNC_SENSOR_ID" 60 5) || {
    print_status "fail" "FAIL: No async parity document found"
    exit 1
}
ASYNC_SIG=$(extract_signature "$ASYNC_DOC")

COMPARE_RESULT=$(SYNC_SIG="$SYNC_SIG" ASYNC_SIG="$ASYNC_SIG" python3 - <<'PY'
import json
import os

sync_sig = json.loads(os.environ["SYNC_SIG"])
async_sig = json.loads(os.environ["ASYNC_SIG"])

mismatches = []
for key in ("verdict", "verificationResponseCode", "verificationResponseStatus"):
    if sync_sig.get(key) != async_sig.get(key):
        mismatches.append(f"{key}: sync={sync_sig.get(key)!r} async={async_sig.get(key)!r}")

if mismatches:
    print("FAIL:" + "; ".join(mismatches))
else:
    print("OK")
PY
)

if [ "$COMPARE_RESULT" != "OK" ]; then
    print_status "fail" "FAIL: $COMPARE_RESULT"
    print_status "info" "sync_signature=$SYNC_SIG"
    print_status "info" "async_signature=$ASYNC_SIG"
    exit 1
fi

print_status "ok" "PASS: Sync and async verdict/status signatures match"
exit 0
