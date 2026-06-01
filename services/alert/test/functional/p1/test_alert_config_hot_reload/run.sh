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

# Test: Alert Config Hot-Reload VLM Params
# Description: POST a config with vlm_params via API, then send an incident
#              and verify the VLM call uses the override (no restart needed).
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
TEST_NAME="alert_config_hot_reload"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: Alert Config Hot-Reload VLM Params ==="

mkdir -p "$PID_DIR"

# 1. POST config for collision (sample incident has category=collision)
print_status "wait" "POST config for 'collision' with hot-reload params"
POST_BODY='{
  "alert_type": "collision",
  "prompt": "Hot-reload test prompt",
  "vlm_params": {"max_tokens": 333, "num_frames": 3}
}'
HTTP_CODE=$(curl -s -o /tmp/hot_post.json -w "%{http_code}" \
    -X POST "$BASE" -H "Content-Type: application/json" -d "$POST_BODY")
# Accept 201 (new) or 409 (already exists from prior tests) — fall through to PUT if needed
if [ "$HTTP_CODE" = "409" ]; then
    print_status "info" "Config exists, using PUT instead"
    PUT_BODY='{"prompt": "Hot-reload test prompt", "vlm_params": {"max_tokens": 333, "num_frames": 3}}'
    HTTP_CODE=$(curl -s -o /tmp/hot_post.json -w "%{http_code}" \
        -X PUT "$BASE/collision" -H "Content-Type: application/json" -d "$PUT_BODY")
fi
if [ "$HTTP_CODE" != "200" ] && [ "$HTTP_CODE" != "201" ]; then
    print_status "fail" "POST/PUT expected 200/201, got $HTTP_CODE"
    exit 1
fi
print_status "ok" "Config persisted via API ($HTTP_CODE)"

# 2. Trigger incident
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX" --no-patch
print_status "info" "Sent collision incident"

# 3. Wait for processing
sleep 12

# 4. Verify ES doc (sanity check — ensures pipeline reached VLM)
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES"; exit 1; }
print_status "info" "Pipeline reached VLM"

# 5. Check NIM sim log for max_tokens=333 (the override)
NIM_LOG="$PID_DIR/nim_sim.log"
if [ ! -f "$NIM_LOG" ]; then
    print_status "fail" "FAIL: NIM sim log missing — setup error, cannot verify hot-reload"
    curl -s -o /dev/null -X DELETE "$BASE/collision"
    exit 1
fi
CALL_COUNT=$(tail -500 "$NIM_LOG" | grep -c '"max_tokens": *333' || echo "0")
if [ "$CALL_COUNT" -ge 1 ]; then
    print_status "ok" "PASS: VLM called with hot-reloaded max_tokens=333 ($CALL_COUNT call(s))"
    # Cleanup: DELETE the config so subsequent tests start clean
    curl -s -o /dev/null -X DELETE "$BASE/collision"
    exit 0
else
    LAST_TOKENS=$(tail -200 "$NIM_LOG" | grep -o '"max_tokens": *[0-9]*' | tail -3 || echo "none")
    print_status "fail" "FAIL: max_tokens=333 not seen. Recent: $LAST_TOKENS"
    curl -s -o /dev/null -X DELETE "$BASE/collision"
    exit 1
fi
