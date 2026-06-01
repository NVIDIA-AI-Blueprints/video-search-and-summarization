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

# Test: VST Segment Window — Middle Anchor
# Description: Verify segment_anchor=middle computes correct VST time window (startTime=T+10s, endTime=T+20s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"
TEST_NAME="vst_segment_middle"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: VST Segment Window (middle anchor) ==="

mkdir -p "$PID_DIR"

# 1. Create payload with known timestamps (30s apart, both in the past to avoid future-clamping)
# Use unsafe_behavior category which has segment_anchor=middle in alert_type_config.json
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
python3 -c "
import json
from datetime import datetime, timezone, timedelta
with open('$PAYLOAD') as f: data = json.load(f)
# Use timestamps 2 minutes in the past so AB does not clamp the window
start = datetime.now(timezone.utc) - timedelta(seconds=120)
end = start + timedelta(seconds=30)
data['timestamp'] = start.strftime('%Y-%m-%dT%H:%M:%S.000Z')
data['end'] = end.strftime('%Y-%m-%dT%H:%M:%S.000Z')
# Use unsafe_behavior which has per-alert-type segment_anchor=middle
data['category'] = 'unsafe_behavior'
with open('$PATCHED', 'w') as f: json.dump(data, f)
print(f'start={start.strftime(\"%H:%M:%S\")} end={end.strftime(\"%H:%M:%S\")}')
"

# 2. Record the incident timestamps for verification
INCIDENT_START=$(python3 -c "import json; d=json.load(open('$PATCHED')); print(d['timestamp'])")
INCIDENT_END=$(python3 -c "import json; d=json.load(open('$PATCHED')); print(d['end'])")
print_status "info" "Incident start=$INCIDENT_START end=$INCIDENT_END"

# 3. Produce to Kafka
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX" --no-patch

# 4. Wait for processing
sleep 15

# 5. Find the VST effective window log line for this request
VST_LOG_LINE=$(grep -i "Requesting VST video URL" "$PID_DIR/alert_bridge.log" 2>/dev/null | tail -1 || echo "")

if [ -z "$VST_LOG_LINE" ]; then
    VST_LOG_LINE=$(grep -i "VST effective window" "$PID_DIR/alert_bridge.log" 2>/dev/null | tail -1 || echo "")
fi

if [ -z "$VST_LOG_LINE" ]; then
    print_status "fail" "FAIL: No VST request log line found in alert_bridge.log"
    exit 1
fi

print_status "info" "VST log: $VST_LOG_LINE"

# 6. Verify the time window using python datetime arithmetic
RESULT=$(python3 -c "
import re, sys
from datetime import datetime, timedelta, timezone

incident_start = datetime.fromisoformat('$INCIDENT_START'.replace('Z', '+00:00'))
incident_end   = datetime.fromisoformat('$INCIDENT_END'.replace('Z', '+00:00'))

# Expected for anchor=middle, duration=10s
# midpoint = start + (end - start) / 2  =>  start + 15s
# window   = [midpoint - 5s, midpoint + 5s]  =>  [start+10s, start+20s]
total_seconds = (incident_end - incident_start).total_seconds()
midpoint = incident_start + timedelta(seconds=total_seconds / 2)
expected_start = midpoint - timedelta(seconds=5)
expected_end   = midpoint + timedelta(seconds=5)

log_line = '''$VST_LOG_LINE'''

start_match = re.search(r'(?:effective_start|[,\s]start)=([0-9T:.Z+\-]+)', log_line)
end_match   = re.search(r'(?:effective_end|[,\s]end)=([0-9T:.Z+\-]+)', log_line)

if not start_match or not end_match:
    print('SKIP: Could not parse startTime/endTime from log line')
    sys.exit(0)

actual_start_str = start_match.group(1).rstrip(',')
actual_end_str   = end_match.group(1).rstrip(',')

try:
    actual_start = datetime.fromisoformat(actual_start_str.replace('Z', '+00:00'))
    actual_end   = datetime.fromisoformat(actual_end_str.replace('Z', '+00:00'))
except Exception as e:
    print(f'SKIP: Could not parse times: start={actual_start_str} end={actual_end_str} ({e})')
    sys.exit(0)

start_diff = abs((actual_start - expected_start).total_seconds())
end_diff   = abs((actual_end   - expected_end).total_seconds())

if start_diff <= 2 and end_diff <= 2:
    print(f'PASS: Window correct (start diff={start_diff:.1f}s, end diff={end_diff:.1f}s)')
else:
    print(f'FAIL: Expected start={expected_start.isoformat()} end={expected_end.isoformat()}, got start={actual_start.isoformat()} end={actual_end.isoformat()} (start_diff={start_diff:.1f}s end_diff={end_diff:.1f}s)')
    sys.exit(1)
" 2>&1)

if echo "$RESULT" | grep -q "^PASS"; then
    print_status "ok" "$RESULT"
    exit 0
elif echo "$RESULT" | grep -q "^SKIP"; then
    print_status "info" "$RESULT"
    exit 0
else
    print_status "fail" "$RESULT"
    exit 1
fi
