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

# Test: JSON Response Format
# Description: Verify response_format="json" parses flat JSON VLM output into
#              correct info.verdict and info.reasoning in Elasticsearch.
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
TEST_NAME="json_response_format"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: JSON Response Format ==="

mkdir -p "$PID_DIR"

if [ ! -f "$PID_DIR/nim_sim.pid" ]; then
    print_status "info" "SKIP: $PID_DIR/nim_sim.pid not found — NIM sim not managed by this harness"
    exit 0
fi

NIM_PID=$(cat "$PID_DIR/nim_sim.pid")

restart_nim() {
    print_status "wait" "Restarting NIM simulator (default CR2 mode)..."
    pkill -f "nim_stub_server.py" 2>/dev/null || true
    sleep 1
    python3 "$REPO_ROOT/test/sim_scripts/nim/nim_stub_server.py" \
        > "$PID_DIR/nim_sim.log" 2>&1 &
    echo $! > "$PID_DIR/nim_sim.pid"
    sleep 2
    print_status "ok" "NIM simulator restarted"
}
trap restart_nim EXIT

# 1. Kill existing NIM stub
if kill -0 "$NIM_PID" 2>/dev/null; then
    kill "$NIM_PID" 2>/dev/null || true
    sleep 1
    kill -9 "$NIM_PID" 2>/dev/null || true
    print_status "info" "NIM simulator stopped (PID $NIM_PID)"
else
    print_status "info" "NIM simulator was already stopped"
fi

# 2. Restart NIM with JSON response file
NIM_RESPONSE_FILE="$SCRIPT_DIR/json_response.txt" \
    python3 "$REPO_ROOT/test/sim_scripts/nim/nim_stub_server.py" \
    > "$PID_DIR/nim_sim.log" 2>&1 &
echo $! > "$PID_DIR/nim_sim.pid"
sleep 2
print_status "ok" "NIM simulator started with JSON response file"

# 3. Record current ES doc count before producing
COUNT_BEFORE=$(count_es_docs "$ES_HOST")

# 4. Patch timestamps and produce incident
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX"
print_status "info" "Incident produced (id-suffix: $ID_SUFFIX)"

# 5. Poll ES for the new document
sleep 10
DOC=$(poll_es_sim "$ES_HOST" 60 5) || {
    print_status "fail" "FAIL: No document appeared in ES within 60s"
    exit 1
}

# 6. Verify verdict and reasoning
RESULT=$(echo "$DOC" | python3 -c "
import sys, json
doc = json.load(sys.stdin)
info = doc.get('info', {})
verdict = info.get('verdict', '')
reasoning = info.get('reasoning', '')
errors = []
if verdict != 'confirmed':
    errors.append(f'verdict={verdict!r}, expected \"confirmed\"')
if 'forklift' not in reasoning.lower():
    errors.append(f'reasoning missing \"forklift\": {reasoning!r}')
if errors:
    print('FAIL:' + '; '.join(errors))
else:
    print('OK')
" 2>/dev/null || echo "FAIL:python error")

if [ "$RESULT" = "OK" ]; then
    print_status "ok" "PASS: JSON response parsed correctly — verdict=confirmed, reasoning contains 'forklift'"
    exit 0
else
    print_status "fail" "FAIL: $RESULT"
    exit 1
fi
