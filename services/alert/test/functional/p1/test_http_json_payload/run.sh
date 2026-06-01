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

# Test: HTTP JSON Payload
# Description: POST JSON incident to REST API, verify document in ES with matching sensorId
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
AB_HOST="${AB_HOST:-http://localhost:9080}"
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"
TEST_NAME="http_json_payload"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: HTTP JSON Payload ==="

mkdir -p "$PID_DIR"

# 1. Patch timestamps
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"

SENSOR_ID=$(python3 -c "import json; print(json.load(open('$PATCHED')).get('sensorId',''))")
print_status "info" "sensorId: $SENSOR_ID"

# 2. POST JSON to REST API
print_status "wait" "POSTing JSON incident to $AB_HOST/api/v1/incidents ..."
HTTP_CODE=$(curl -s -o /tmp/http_json_response.json -w "%{http_code}" \
    -X POST "$AB_HOST/api/v1/incidents" \
    -H "Content-Type: application/json" \
    -d @"$PATCHED")

print_status "info" "HTTP response code: $HTTP_CODE"

# 3. Verify 202
if [ "$HTTP_CODE" != "202" ]; then
    print_status "fail" "FAIL: Expected HTTP 202, got $HTTP_CODE"
    exit 1
fi
print_status "ok" "HTTP 202 accepted"

# 4. Wait for processing
sleep 10

# 5. Poll ES for document
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES within 60s"; exit 1; }

# 6. Verify sensorId matches
RESULT=$(echo "$DOC" | python3 -c "
import sys, json
doc = json.load(sys.stdin)
got = doc.get('sensorId', doc.get('sensor_id', ''))
want = '$SENSOR_ID'
if got == want:
    print('OK')
else:
    print('MISMATCH:got=' + str(got) + ':want=' + str(want))
" 2>/dev/null || echo "ERROR:python3 failed")

if [ "$RESULT" = "OK" ]; then
    print_status "ok" "PASS: Document found in ES with matching sensorId ($SENSOR_ID)"
    exit 0
else
    DETAIL="${RESULT#MISMATCH:}"
    print_status "fail" "FAIL: sensorId mismatch — $DETAIL"
    exit 1
fi
