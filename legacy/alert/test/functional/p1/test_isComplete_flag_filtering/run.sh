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

# Test: isComplete Flag Filtering
# Description: When verify_only_finished_events=true, incidents with
#              info.isComplete=false are skipped; info.isComplete=true are processed.
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
TEST_NAME="isComplete_flag_filtering"

echo "=== P1: isComplete Flag Filtering ==="

mkdir -p "$PID_DIR"

# 1. Record baseline doc count
BEFORE=$(count_es_docs "$ES_HOST")

# 2. Build incident #1: isComplete=false with timestamp T1
#    This should be filtered out (verify_only_finished_events=true skips non-finished events)
PATCHED1="$PID_DIR/patched_${TEST_NAME}_1.json"
python3 -c "
import json
from datetime import datetime, timezone, timedelta

with open('$PAYLOAD') as f:
    data = json.load(f)

now = datetime.now(timezone.utc)
# T1: base time
data['timestamp'] = now.strftime('%Y-%m-%dT%H:%M:%S.000Z')
data['end'] = data['timestamp']
# Explicitly set isComplete=false — filter_new_events skips this
# Note: protobuf info map requires string values, so use "false" not False
data.setdefault('info', {})['isComplete'] = 'false'

with open('$PATCHED1', 'w') as f:
    json.dump(data, f)
"

produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED1" "p1_iscomplete_1" --no-patch
print_status "info" "Sent incident #1 (isComplete=false — should be skipped)"

# 3. Wait for any potential (incorrect) processing
sleep 8

AFTER_INCOMPLETE=$(count_es_docs "$ES_HOST")
print_status "info" "Docs after incomplete incident: $AFTER_INCOMPLETE (baseline was $BEFORE)"

# 4. Build incident #2: isComplete=true with timestamp T2 (different fingerprint)
#    This should be processed.
PATCHED2="$PID_DIR/patched_${TEST_NAME}_2.json"
python3 -c "
import json
from datetime import datetime, timezone, timedelta

with open('$PAYLOAD') as f:
    data = json.load(f)

now = datetime.now(timezone.utc)
# T2: offset by 60s to ensure a distinct fingerprint from incident #1
t2 = now + timedelta(seconds=60)
data['timestamp'] = t2.strftime('%Y-%m-%dT%H:%M:%S.000Z')
data['end'] = data['timestamp']
# Explicitly set isComplete=true — filter_new_events processes this
# Note: protobuf info map requires string values, so use "true" not True
data.setdefault('info', {})['isComplete'] = 'true'

with open('$PATCHED2', 'w') as f:
    json.dump(data, f)
"

produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED2" "p1_iscomplete_2" --no-patch
print_status "info" "Sent incident #2 (isComplete=true — should be processed)"

# 5. Wait + poll for the complete incident
sleep 10
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES within 60s (isComplete=true incident not processed)"; exit 1; }

AFTER_COMPLETE=$(count_es_docs "$ES_HOST")
print_status "info" "Docs after complete incident: $AFTER_COMPLETE"

# 6. Verify: incomplete incident must not have added a doc; complete incident must have
ADDED_BY_INCOMPLETE=$((AFTER_INCOMPLETE - BEFORE))
ADDED_BY_COMPLETE=$((AFTER_COMPLETE - AFTER_INCOMPLETE))

print_status "info" "Docs added by incomplete incident: $ADDED_BY_INCOMPLETE"
print_status "info" "Docs added by complete incident: $ADDED_BY_COMPLETE"

if [ "$ADDED_BY_INCOMPLETE" -eq 0 ] && [ "$ADDED_BY_COMPLETE" -ge 1 ]; then
    print_status "ok" "PASS: Incomplete incident filtered; complete incident processed"
    exit 0
elif [ "$ADDED_BY_INCOMPLETE" -ge 1 ]; then
    print_status "fail" "FAIL: Incomplete incident was NOT filtered (verify_only_finished_events did not apply)"
    exit 1
else
    print_status "fail" "FAIL: Complete incident was not processed (expected at least 1 new doc)"
    exit 1
fi
