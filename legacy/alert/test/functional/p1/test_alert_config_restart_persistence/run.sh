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

# Test: Alert Config Restart Persistence (AC-11)
# Description: Configs written via the API must survive an Alert Bridge
#              restart. Verifies (1) config is still served by GET after
#              the bridge restarts, and (2) the restart-persisted
#              vlm_params reach the next VLM call.
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
BASE_CONFIG="${BASE_CONFIG:-$P1_ROOT/shared/config_base.yaml}"
BASE="$AB_HOST/api/v1/verification/config"
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"
TEST_NAME="alert_config_restart_persistence"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"
DISTINCTIVE_TOKENS=777
DISTINCTIVE_FRAMES=4

echo "=== P1: Alert Config Restart Persistence ==="

mkdir -p "$PID_DIR"

# 1. POST a config with distinctive vlm_params for collision
print_status "wait" "POST config for 'collision' with distinctive values"
POST_BODY=$(cat <<EOF
{
  "alert_type": "collision",
  "prompt": "Restart-persistence test prompt",
  "vlm_params": {"max_tokens": $DISTINCTIVE_TOKENS, "num_frames": $DISTINCTIVE_FRAMES}
}
EOF
)
HTTP_CODE=$(curl -s -o /tmp/restart_post.json -w "%{http_code}" \
    -X POST "$BASE" -H "Content-Type: application/json" -d "$POST_BODY")
if [ "$HTTP_CODE" = "409" ]; then
    print_status "info" "Config already exists, switching to PUT"
    PUT_BODY=$(cat <<EOF
{"prompt": "Restart-persistence test prompt", "vlm_params": {"max_tokens": $DISTINCTIVE_TOKENS, "num_frames": $DISTINCTIVE_FRAMES}}
EOF
)
    HTTP_CODE=$(curl -s -o /tmp/restart_post.json -w "%{http_code}" \
        -X PUT "$BASE/collision" -H "Content-Type: application/json" -d "$PUT_BODY")
fi
if [ "$HTTP_CODE" != "200" ] && [ "$HTTP_CODE" != "201" ]; then
    print_status "fail" "POST/PUT expected 200/201, got $HTTP_CODE"
    exit 1
fi
print_status "ok" "Config persisted via API ($HTTP_CODE)"

# 2. Restart Alert Bridge in-place
print_status "wait" "Restarting Alert Bridge..."
stop_alert_bridge_local "$PID_DIR"
sleep 2
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$BASE_CONFIG" 15

# 3. GET back the config via API after restart — config must survive
HTTP_CODE=$(curl -s -o /tmp/restart_get.json -w "%{http_code}" "$BASE/collision")
if [ "$HTTP_CODE" != "200" ]; then
    print_status "fail" "FAIL: GET after restart expected 200, got $HTTP_CODE — config did NOT survive"
    curl -s -o /dev/null -X DELETE "$BASE/collision"
    exit 1
fi
RESULT=$(python3 -c "
import json
d = json.load(open('/tmp/restart_get.json'))
ok = d.get('vlm_params', {}).get('max_tokens') == $DISTINCTIVE_TOKENS
print('OK' if ok else 'FAIL: ' + json.dumps(d.get('vlm_params')))
")
if [ "$RESULT" != "OK" ]; then
    print_status "fail" "FAIL: vlm_params lost across restart: $RESULT"
    curl -s -o /dev/null -X DELETE "$BASE/collision"
    exit 1
fi
print_status "ok" "Config still present after restart with original values"

# 4. Trigger an incident and verify VLM receives the persisted overrides
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX" --no-patch
print_status "info" "Sent collision incident"
sleep 12

DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES"; curl -s -o /dev/null -X DELETE "$BASE/collision"; exit 1; }
print_status "info" "Pipeline reached VLM"

NIM_LOG="$PID_DIR/nim_sim.log"
if [ ! -f "$NIM_LOG" ]; then
    print_status "fail" "FAIL: NIM sim log missing — setup error, cannot verify hot-reload after restart"
    curl -s -o /dev/null -X DELETE "$BASE/collision"
    exit 1
fi
CALL_COUNT=$(tail -500 "$NIM_LOG" | grep -c "\"max_tokens\": *$DISTINCTIVE_TOKENS" || echo "0")
if [ "$CALL_COUNT" -ge 1 ]; then
    print_status "ok" "PASS: VLM called with persisted max_tokens=$DISTINCTIVE_TOKENS after restart ($CALL_COUNT call(s))"
    curl -s -o /dev/null -X DELETE "$BASE/collision"
    exit 0
else
    LAST_TOKENS=$(tail -200 "$NIM_LOG" | grep -o '"max_tokens": *[0-9]*' | tail -3 || echo "none")
    print_status "fail" "FAIL: max_tokens=$DISTINCTIVE_TOKENS not seen post-restart. Recent: $LAST_TOKENS"
    curl -s -o /dev/null -X DELETE "$BASE/collision"
    exit 1
fi
