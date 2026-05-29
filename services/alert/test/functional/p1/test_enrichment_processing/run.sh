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

# Test: Enrichment Processing
# Description: Verify post-verification enrichment VLM call runs when enabled
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
TEST_NAME="enrichment_processing"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: Enrichment Processing ==="

mkdir -p "$PID_DIR"

# 1. Patch timestamps + produce
#    Uses collision incident which has an enrichment prompt in alert_type_config.json
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX"
print_status "info" "Sent collision incident (id-suffix: $ID_SUFFIX)"

# 2. Wait 20s — enrichment is async post-publish, needs extra time beyond verification
print_status "wait" "Waiting 20s for verification + async enrichment to complete..."
sleep 20

# 3. Poll ES for the document
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES within 60s"; exit 1; }

# 4. Check info.enrichment is a JSON string that parses to a dict (map<string,string>)
ENRICHMENT_RESULT=$(echo "$DOC" | python3 -c "
import sys, json
data = json.load(sys.stdin)
info = data.get('info', {})
enrichment = info.get('enrichment')
if enrichment is None:
    print('ABSENT')
elif isinstance(enrichment, str):
    try:
        parsed = json.loads(enrichment)
        if isinstance(parsed, dict):
            print('STRING_DICT')
        else:
            print('STRING_OTHER')
    except json.JSONDecodeError:
        print('STRING_INVALID')
elif isinstance(enrichment, dict):
    print('RAW_DICT')
else:
    print('UNEXPECTED')
" 2>/dev/null || echo "ERROR")

if [ "$ENRICHMENT_RESULT" = "STRING_DICT" ]; then
    print_status "ok" "PASS: info.enrichment is a JSON string containing a dict"
    exit 0
elif [ "$ENRICHMENT_RESULT" = "RAW_DICT" ]; then
    print_status "fail" "FAIL: info.enrichment is a raw dict (expected JSON string for map<string,string>)"
    exit 1
elif [ "$ENRICHMENT_RESULT" = "ABSENT" ]; then
    print_status "fail" "FAIL: info.enrichment absent (enrichment.enabled=true but no enrichment written)"
    exit 1
else
    print_status "fail" "FAIL: info.enrichment unexpected type: $ENRICHMENT_RESULT"
    exit 1
fi
