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

# Test: Document Parity
# Description: Verify sensorId from input payload is preserved in ES output document
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
TEST_NAME="document_parity"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: Document Parity ==="

mkdir -p "$PID_DIR"

# 1. Read expected sensorId from input payload
EXPECTED_SENSOR_ID=$(python3 -c "
import json
with open('$PAYLOAD') as f: data = json.load(f)
print(data.get('sensorId', data.get('sensor_id', '')))
")
if [ -z "$EXPECTED_SENSOR_ID" ]; then
    print_status "fail" "FAIL: Could not read sensorId from input payload"
    exit 1
fi
print_status "info" "Expected sensorId: $EXPECTED_SENSOR_ID"

# 2. Patch timestamps + produce
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX"

# 3. Wait + poll
sleep 10
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES within 60s"; exit 1; }

# 4. Validate sensorId matches
ACTUAL_SENSOR_ID=$(echo "$DOC" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('sensorId', data.get('sensor_id', '')))
" 2>/dev/null || echo "")

if [ -z "$ACTUAL_SENSOR_ID" ]; then
    print_status "fail" "FAIL: sensorId missing from output document"
    exit 1
fi

if [ "$ACTUAL_SENSOR_ID" = "$EXPECTED_SENSOR_ID" ]; then
    print_status "ok" "PASS: sensorId preserved ($ACTUAL_SENSOR_ID)"
else
    print_status "fail" "FAIL: sensorId mismatch: expected=$EXPECTED_SENSOR_ID actual=$ACTUAL_SENSOR_ID"
    exit 1
fi

# 5. Validate index name
TODAY=$(date -u +%Y-%m-%d)
EXPECTED_INDEX="mdx-vlm-incidents-$TODAY"
RESPONSE=$(curl -sf "$ES_HOST/$EXPECTED_INDEX/_all" 2>/dev/null || echo "")
if [ -n "$RESPONSE" ]; then
    DOC_COUNT=$(echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(len(data.get('documents', [])))
" 2>/dev/null || echo "0")
    if [ "$DOC_COUNT" -gt 0 ]; then
        print_status "ok" "PASS: Document in correct index ($EXPECTED_INDEX)"
        exit 0
    fi
fi

print_status "fail" "FAIL: Document not found in expected index $EXPECTED_INDEX"
exit 1
