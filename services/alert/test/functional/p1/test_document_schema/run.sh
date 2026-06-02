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

# Test: Document Schema
# Description: Verify output document contains all 7 required fields
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
TEST_NAME="document_schema"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: Document Schema ==="

mkdir -p "$PID_DIR"

# 1. Patch timestamps + produce
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX"

# 2. Wait + poll
sleep 10
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES within 60s"; exit 1; }

# 3. Check 7 required fields
SCHEMA_RESULT=$(echo "$DOC" | python3 -c "
import sys, json
data = json.load(sys.stdin)
info = data.get('info', {})

# Option B: default path emits info.reasoning,
# pluggable-parser path emits info.vlm_response; accept either for the
# narrative slot.
narrative = (
    info.get('reasoning')
    or info.get('vlm_response')
    or data.get('reasoning')
    or data.get('vlm_response')
)

required = {
    'sensorId':                         data.get('sensorId', data.get('sensor_id')),
    'category':                         data.get('category', data.get('incidentType')),
    'timestamp':                        data.get('timestamp'),
    'info.verdict':                     info.get('verdict', data.get('verdict')),
    'info.reasoning|vlm_response':      narrative,
    'info.verificationResponseCode':    info.get('verificationResponseCode', data.get('verificationResponseCode')),
    'info.verificationResponseStatus':  info.get('verificationResponseStatus', data.get('verificationResponseStatus')),
}

missing = [k for k, v in required.items() if v is None or v == '']
if missing:
    print('MISSING:' + '|'.join(missing))
else:
    print('OK')
" 2>/dev/null || echo "ERROR:python3 failed")

if [ "$SCHEMA_RESULT" = "OK" ]; then
    print_status "ok" "PASS: All 7 required fields present"
    exit 0
else
    MISSING_FIELDS="${SCHEMA_RESULT#MISSING:}"
    print_status "fail" "FAIL: Missing fields: $(echo "$MISSING_FIELDS" | tr '|' ', ')"
    exit 1
fi
