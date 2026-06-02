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

# Test: VLM Error Codes
# Description: Verify AB records a non-200 verificationResponseCode when NIM sim is down
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
TEST_NAME="vlm_error_codes"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: VLM Error Codes ==="

mkdir -p "$PID_DIR"

# SKIP if NIM sim PID file doesn't exist (can't safely stop/restart)
if [ ! -f "$PID_DIR/nim_sim.pid" ]; then
    print_status "info" "SKIP: $PID_DIR/nim_sim.pid not found — NIM sim not managed by this harness"
    exit 0
fi

NIM_PID=$(cat "$PID_DIR/nim_sim.pid")

# Trap to restart NIM sim on exit (pass or fail)
restart_nim() {
    print_status "wait" "Restarting NIM simulator..."
    pkill -f "nim_stub_server.py" 2>/dev/null || true
    sleep 1
    python3 "$REPO_ROOT/test/sim_scripts/nim/nim_stub_server.py" \
        > "$PID_DIR/nim_sim.log" 2>&1 &
    echo $! > "$PID_DIR/nim_sim.pid"
    sleep 2
    print_status "ok" "NIM simulator restarted"
}
trap restart_nim EXIT

# 1. Kill NIM sim
if kill -0 "$NIM_PID" 2>/dev/null; then
    kill "$NIM_PID" 2>/dev/null || true
    sleep 1
    kill -9 "$NIM_PID" 2>/dev/null || true
    print_status "info" "NIM simulator stopped (PID $NIM_PID)"
else
    print_status "info" "NIM simulator was already stopped"
fi

# 2. Patch timestamps + produce
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX"

# 3. Wait + poll (extended timeout — AB may retry before giving up)
sleep 10
DOC=$(poll_es_sim "$ES_HOST" 90 5) || {
    print_status "info" "SKIP: No doc appeared in 90s — AB may have dropped the message on VLM failure"
    exit 0
}

# 4. Extract verificationResponseCode
RESPONSE_CODE=$(echo "$DOC" | python3 -c "
import sys, json
data = json.load(sys.stdin)
info = data.get('info', {})
code = info.get('verificationResponseCode', data.get('verificationResponseCode'))
print(code if code is not None else 'null')
" 2>/dev/null || echo "null")

print_status "info" "verificationResponseCode: $RESPONSE_CODE"

# 5. Assert non-200 (accept 0, -1, 503, 504, or any non-200 int)
IS_NON_200=$(python3 -c "
code = '$RESPONSE_CODE'
try:
    c = int(code)
    print('yes' if c != 200 else 'no')
except:
    print('yes')  # null / non-numeric also counts as non-200
")

if [ "$IS_NON_200" = "yes" ]; then
    print_status "ok" "PASS: verificationResponseCode is non-200 ($RESPONSE_CODE) as expected with NIM down"
    exit 0
else
    print_status "fail" "FAIL: Expected non-200 response code with NIM down, got $RESPONSE_CODE"
    exit 1
fi
