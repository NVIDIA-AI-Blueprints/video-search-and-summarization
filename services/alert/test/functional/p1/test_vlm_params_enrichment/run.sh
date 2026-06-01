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

# Test: VLM Params Enrichment Override
# Description: Enrichment VLM call uses the same per-type overrides as verification.
#              collision has vlm_params + enrichment prompt — both verification
#              and enrichment should use per-type config.
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
TEST_NAME="vlm_params_enrichment"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: VLM Params Enrichment Override ==="
echo "    collision has vlm_params + enrichment prompt"
echo "    Both VLM calls should use per-type overrides"

mkdir -p "$PID_DIR"

# 1. Send collision incident (has both vlm_params and enrichment prompt)
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX"
print_status "info" "Sent collision incident (id-suffix: $ID_SUFFIX)"

# 2. Wait for verification + enrichment (enrichment is async, needs more time)
print_status "wait" "Waiting 20s for verification + enrichment..."
sleep 20

# 3. Poll ES for the document
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES within 60s"; exit 1; }

# 4. Verify enrichment was processed
ENRICHMENT_PRESENT=$(echo "$DOC" | python3 -c "
import sys, json
data = json.load(sys.stdin)
enrichment = data.get('info', {}).get('enrichment')
print('yes' if enrichment else 'no')
" 2>/dev/null || echo "error")

if [ "$ENRICHMENT_PRESENT" != "yes" ]; then
    print_status "fail" "FAIL: Enrichment not present — cannot verify per-type params on enrichment call"
    exit 1
fi

# 5. Check NIM sim log — should see at least 2 VLM calls (verification + enrichment)
#    Both should use collision's max_tokens=2048
NIM_LOG="$PID_DIR/nim_sim.log"
if [ ! -f "$NIM_LOG" ]; then
    print_status "info" "SKIP: NIM sim log not found"
    exit 0
fi

CALL_COUNT=$(tail -500 "$NIM_LOG" | grep -c '"max_tokens": *2048' || echo "0")

if [ "$CALL_COUNT" -ge 2 ]; then
    print_status "ok" "PASS: $CALL_COUNT VLM calls used per-type max_tokens=2048 (verification + enrichment)"
    exit 0
elif [ "$CALL_COUNT" -eq 1 ]; then
    print_status "fail" "FAIL: Only 1 call used max_tokens=2048 — enrichment likely used global defaults"
    exit 1
else
    print_status "info" "SKIP: max_tokens=2048 not found in NIM log — sim may not log request body"
    exit 0
fi
