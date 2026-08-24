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

# Test: End Time Delta Filter
# Description: Incidents with small end-time changes are filtered out when the
#              delta filter is enabled; first incident passes, second (end +2s) is blocked.
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
TEST_NAME="end_time_delta_filter"

echo "=== P1: End Time Delta Filter ==="

mkdir -p "$PID_DIR"

# 1. Record baseline doc count
BEFORE=$(count_es_docs "$ES_HOST")

# 2. Patch incident #1 — establishes the delta baseline in Redis
PATCHED1="$PID_DIR/patched_${TEST_NAME}_1.json"
patch_timestamps "$PAYLOAD" "$PATCHED1"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED1" "p1_etd_1" --no-patch
print_status "info" "Sent incident #1 (end=T, establishes delta baseline)"

# 3. Wait 10s — allows AB to process #1 AND the dedup TTL (3s) to expire.
#    After dedup TTL expires, incident #2 will pass dedup but must be caught
#    by the end time delta filter (delta < threshold_seconds=5).
print_status "wait" "Waiting 10s for processing + dedup TTL (3s) to expire..."
sleep 10

AFTER_FIRST=$(count_es_docs "$ES_HOST")
print_status "info" "Docs after incident #1: $AFTER_FIRST (baseline was $BEFORE)"

# 4. Build incident #2: same payload (same sensorId, timestamp, objectIds, category)
#    but with end = T+2s (delta < 5s threshold → should be filtered by delta filter)
PATCHED2="$PID_DIR/patched_${TEST_NAME}_2.json"
python3 -c "
import json, sys
from datetime import datetime, timezone, timedelta

with open('$PATCHED1') as f:
    data = json.load(f)

# Advance end by 2 seconds — below the 5s threshold
current_end = data.get('end', data.get('timestamp'))
try:
    dt = datetime.fromisoformat(current_end.replace('Z', '+00:00'))
    dt_plus2 = dt + timedelta(seconds=2)
    data['end'] = dt_plus2.strftime('%Y-%m-%dT%H:%M:%S.000Z')
except Exception as e:
    print(f'ERROR patching end timestamp: {e}', file=sys.stderr)
    sys.exit(1)

with open('$PATCHED2', 'w') as f:
    json.dump(data, f)
"

produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED2" "p1_etd_2" --no-patch
print_status "info" "Sent incident #2 (end=T+2s, delta < 5s threshold — should be filtered)"

# 5. Wait for any potential processing
sleep 10

# 6. Count docs — delta filter should have blocked #2
AFTER_SECOND=$(count_es_docs "$ES_HOST")
ADDED=$((AFTER_SECOND - AFTER_FIRST))

print_status "info" "Docs added after incident #2: $ADDED"

if [ "$ADDED" -eq 0 ]; then
    print_status "ok" "PASS: Incident #2 filtered by end time delta (doc count unchanged after second send)"
    exit 0
else
    print_status "fail" "FAIL: $ADDED new doc(s) appeared — incident #2 was not filtered (delta filter may not have fired)"
    exit 1
fi
