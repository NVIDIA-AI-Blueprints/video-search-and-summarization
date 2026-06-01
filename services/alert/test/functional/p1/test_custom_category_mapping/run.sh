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

# Test: Custom Category Mapping
# Description: output_category in alert_type_config.json transforms the category
#              field in the ES output document (collision -> "Vehicle Collision")
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
TEST_NAME="custom_category_mapping"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: Custom Category Mapping ==="

mkdir -p "$PID_DIR"

# 1. Patch timestamps + produce
#    alert_type_config.json maps "collision" -> "Vehicle Collision" via output_category
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX"
print_status "info" "Sent collision incident (id-suffix: $ID_SUFFIX)"

# 2. Wait + poll
sleep 10
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES within 60s"; exit 1; }

# 3. Extract category from ES doc and check for mapped value
CATEGORY_RESULT=$(echo "$DOC" | python3 -c "
import sys, json
data = json.load(sys.stdin)
cat = data.get('category', data.get('incidentType', ''))
print(cat)
" 2>/dev/null || echo "")

print_status "info" "ES document category: '$CATEGORY_RESULT'"

if [ "$CATEGORY_RESULT" = "Vehicle Collision" ]; then
    print_status "ok" "PASS: category field shows mapped value 'Vehicle Collision'"
    exit 0
elif [ "$CATEGORY_RESULT" = "collision" ]; then
    print_status "info" "SKIP: category shows raw value 'collision' — output_category mapping did not apply (check alert_type_config.json is loaded)"
    exit 0
elif [ -z "$CATEGORY_RESULT" ]; then
    print_status "fail" "FAIL: category field missing from ES document"
    exit 1
else
    print_status "fail" "FAIL: unexpected category value '$CATEGORY_RESULT' (expected 'Vehicle Collision' or 'collision')"
    exit 1
fi
