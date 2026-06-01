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

# Test: Verdict Protection
# Description: Verify protect_confirmed_verdicts prevents re-processing a confirmed incident
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
TEST_NAME="verdict_protection"

echo "=== P1: Verdict Protection ==="

mkdir -p "$PID_DIR"

# 1. Record baseline doc count
BEFORE=$(count_es_docs "$ES_HOST")

# 2. Patch timestamps + send first incident
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "p1_protect_1" --no-patch
print_status "info" "Sent incident #1 (id-suffix: p1_protect_1)"

# 3. Wait + poll for first doc
sleep 10
DOC1=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc from incident #1 in 60s"; exit 1; }

# 4. Check first verdict
VERDICT1=$(echo "$DOC1" | python3 -c "
import sys, json
data = json.load(sys.stdin)
info = data.get('info', {})
print(info.get('verdict', data.get('verdict', '')))
" 2>/dev/null || echo "")

print_status "info" "First verdict: $VERDICT1"

if [ "$VERDICT1" != "confirmed" ]; then
    print_status "info" "SKIP: First verdict is '$VERDICT1' (not 'confirmed') — protection test not applicable with this sim"
    exit 0
fi

AFTER_FIRST=$(count_es_docs "$ES_HOST")

# 5. Wait for dedup TTL to expire, then resend SAME payload (same fingerprint)
#    Fingerprint = primaryObjectId + category + sensorId + timestamp
#    Dedup TTL = 3s (from config) → wait 5s to be safe
#    Protection TTL = 600s → still active
#    So: dedup key expired → passes dedup → hits verdict protection → blocked
print_status "wait" "Waiting 5s for dedup TTL (3s) to expire..."
sleep 5

produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "p1_protect_2" --no-patch
print_status "info" "Sent incident #2 (same payload/fingerprint — dedup expired, protection should block)"

# 6. Wait for potential processing
sleep 10

# 7. Count docs — protection should block the second write
AFTER_SECOND=$(count_es_docs "$ES_HOST")
ADDED=$((AFTER_SECOND - AFTER_FIRST))

print_status "info" "Docs added after second send: $ADDED"

if [ "$ADDED" -eq 0 ]; then
    print_status "ok" "PASS: Second incident blocked by verdict protection (doc count unchanged)"
    exit 0
elif [ "$ADDED" -ge 1 ]; then
    # If a second doc appeared, check it has no new VLM response (protection fired but still indexed)
    ALL_DOCS=$(get_all_es_docs "$ES_HOST")
    HAS_NEW_VLM_RESPONSE=$(echo "$ALL_DOCS" | python3 -c "
import sys, json
docs = json.load(sys.stdin)
if len(docs) < 2:
    print('no')
else:
    last = docs[-1]
    info = last.get('info', {})
    vlm_response = info.get('vlm_response', last.get('vlm_response', ''))
    print('yes' if vlm_response else 'no')
" 2>/dev/null || echo "yes")

    if [ "$HAS_NEW_VLM_RESPONSE" = "no" ]; then
        print_status "ok" "PASS: Second doc has no new VLM response — verdict protection preserved"
        exit 0
    else
        print_status "fail" "FAIL: Second incident was re-processed with new VLM verdict — protection did not fire"
        exit 1
    fi
fi
