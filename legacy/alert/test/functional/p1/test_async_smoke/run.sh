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

# Test: Async External I/O Smoke
# Description: Enable async external I/O guardrails and verify one incident is processed end-to-end.
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
TEST_NAME="async_smoke"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"
SENSOR_ID="ASYNC_SMOKE_SENSOR_${ID_SUFFIX}"

echo "=== P1: Async External I/O Smoke ==="
mkdir -p "$PID_DIR"

ASYNC_CONFIG="$PID_DIR/${TEST_NAME}_config.yaml"
build_async_external_io_config "$BASE_CONFIG" "$ASYNC_CONFIG"
print_status "info" "Generated async config: $ASYNC_CONFIG"

stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$ASYNC_CONFIG"

PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
python3 - "$PAYLOAD" "$PATCHED" "$SENSOR_ID" <<'PY'
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

produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX" --no-patch
print_status "info" "Incident produced for sensorId=$SENSOR_ID"

sleep 10
DOC=$(poll_es_doc_by_sensor "$ES_HOST" "$SENSOR_ID" 60 5) || {
    print_status "fail" "FAIL: No async smoke document found for sensorId=$SENSOR_ID"
    exit 1
}

VALIDATION=$(echo "$DOC" | python3 -c "
import sys, json
doc = json.load(sys.stdin)
info = doc.get('info', {})
verdict = info.get('verdict')
code = info.get('verificationResponseCode')
status = info.get('verificationResponseStatus')
errors = []
if verdict in (None, '', 'null'):
    errors.append('missing verdict')
if code in (None, '', 'null'):
    errors.append('missing verificationResponseCode')
if status in (None, '', 'null'):
    errors.append('missing verificationResponseStatus')
if errors:
    print('FAIL:' + '; '.join(errors))
else:
    print('OK')
" 2>/dev/null || echo "FAIL:python_error")

if [ "$VALIDATION" != "OK" ]; then
    print_status "fail" "FAIL: $VALIDATION"
    exit 1
fi

if grep -q "Async external I/O guardrail is enabled" "$PID_DIR/alert_bridge.log" 2>/dev/null; then
    print_status "ok" "Async guardrail enabled log detected"
else
    print_status "fail" "FAIL: Async guardrail enable log not found"
    exit 1
fi

print_status "ok" "PASS: Async smoke path processed incident successfully"
exit 0
