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

# Test: Alert Config Prompt Sync
# Description: PUT a new prompt via the verification config API, then send
#              an incident and verify the VLM call uses the updated prompt
#              (read from alert_config:{alert_type}).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"
AB_HOST="${AB_HOST:-http://localhost:9080}"
BASE="$AB_HOST/api/v1/verification/config"
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"
TEST_NAME="alert_config_prompt_sync"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"
SENTINEL="HOTRELOAD_SENTINEL_$(date +%s)"

echo "=== P1: Alert Config Prompt Sync ==="
print_status "info" "sentinel: $SENTINEL"

mkdir -p "$PID_DIR"

# 1. POST/PUT config with a sentinel string in the prompt
POST_BODY=$(cat <<EOF
{
  "alert_type": "collision",
  "prompt": "$SENTINEL — Detect any collision in the scene.",
  "system_prompt": "Answer yes or no"
}
EOF
)
HTTP_CODE=$(curl -s -o /tmp/sync_post.json -w "%{http_code}" \
    -X POST "$BASE" -H "Content-Type: application/json" -d "$POST_BODY")
if [ "$HTTP_CODE" = "409" ]; then
    PUT_BODY=$(cat <<EOF
{"prompt": "$SENTINEL — Detect any collision in the scene.", "system_prompt": "Answer yes or no"}
EOF
)
    HTTP_CODE=$(curl -s -o /tmp/sync_post.json -w "%{http_code}" \
        -X PUT "$BASE/collision" -H "Content-Type: application/json" -d "$PUT_BODY")
fi
if [ "$HTTP_CODE" != "200" ] && [ "$HTTP_CODE" != "201" ]; then
    print_status "fail" "POST/PUT expected 200/201, got $HTTP_CODE"
    exit 1
fi
print_status "ok" "Prompt persisted via API ($HTTP_CODE)"

# 2. Trigger incident
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX" --no-patch
print_status "info" "Sent collision incident"

# 3. Wait for processing
sleep 12

# 4. Verify ES doc (sanity)
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES"; exit 1; }
print_status "info" "Pipeline reached VLM"

# 5. Check NIM sim log — body should include sentinel
NIM_LOG="$PID_DIR/nim_sim.log"
if [ ! -f "$NIM_LOG" ]; then
    print_status "fail" "FAIL: NIM sim log missing — setup error, cannot verify prompt sync"
    curl -s -o /dev/null -X DELETE "$BASE/collision"
    exit 1
fi
SEEN=$(tail -2000 "$NIM_LOG" | grep -c "$SENTINEL" || echo "0")
if [ "$SEEN" -ge 1 ]; then
    print_status "ok" "PASS: NIM received prompt containing sentinel ($SEEN occurrence(s))"
    # Cleanup
    curl -s -o /dev/null -X DELETE "$BASE/collision"
    exit 0
else
    print_status "fail" "FAIL: Sentinel '$SENTINEL' not seen in NIM log — prompt update via alert_config:* may not have applied"
    curl -s -o /dev/null -X DELETE "$BASE/collision"
    exit 1
fi
