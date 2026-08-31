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

# Test: Console sink (local development extension)
# Description: With sinkType/vlm_enhanced_sink.type set to console, a Kafka
#              incident must still be processed end to end and its verdict
#              rendered to the log. Also covers independent transport
#              selection: the source stays on Kafka while the sink does not.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
export REPO_ROOT
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
INPUT_TOPIC="${TOPIC:-mdx-incidents}"
SENSOR_ID="CONSOLE_SINK_TEST_SENSOR"
TEST_NAME="console_sink"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"
AB_LOG="$PID_DIR/alert_bridge.log"

echo "=== P1: Console sink ==="

mkdir -p "$PID_DIR"

# 1. Both sinks must have announced themselves as console-only, and the source
#    must still be Kafka — that pairing is the independence claim.
if grep -qE "Creating source: .*resolved to 'kafka'" "$AB_LOG" 2>/dev/null; then
    print_status "ok" "AB log confirms the Kafka source is still in use"
else
    print_status "fail" "FAIL: AB did not select the Kafka source"
    tail -20 "$AB_LOG" 2>/dev/null || true
    exit 1
fi

if grep -q "Console sink selected" "$AB_LOG" 2>/dev/null; then
    print_status "ok" "Event bridge console sink announced itself"
else
    print_status "fail" "FAIL: event bridge console sink did not start"
    tail -20 "$AB_LOG" 2>/dev/null || true
    exit 1
fi

if grep -q "Console VLM enhanced sink selected" "$AB_LOG" 2>/dev/null; then
    print_status "ok" "VLM enhanced console sink announced itself"
else
    print_status "fail" "FAIL: VLM enhanced console sink did not start"
    tail -20 "$AB_LOG" 2>/dev/null || true
    exit 1
fi

# 2. Prepare mock media (Mode 2: local file)
MOCK_VIDEO_DIR="/tmp/alert_bridge_media"
MOCK_VIDEO_PATH="$MOCK_VIDEO_DIR/test_video_${ID_SUFFIX}.mp4"
mkdir -p "$MOCK_VIDEO_DIR"
if ! curl -sf "http://127.0.0.1:30888/mock/media/test_video.mp4" -o "$MOCK_VIDEO_PATH" 2>/dev/null; then
    python3 -c "
ftyp = b'\\x00\\x00\\x00\\x14ftypmp42\\x00\\x00\\x00\\x00mp42'
moov = b'\\x00\\x00\\x00\\x08moov'
with open('$MOCK_VIDEO_PATH', 'wb') as f:
    f.write(ftyp + moov)
"
fi

# 3. Build and produce the incident
PAYLOAD="$PID_DIR/incident_console_sink.json"
cat > "$PAYLOAD" << EOF
{
  "id": "test-console-sink-$ID_SUFFIX",
  "sensorId": "$SENSOR_ID",
  "timestamp": "2025-01-01T00:00:00.000Z",
  "end": "2025-01-01T00:01:00.000Z",
  "objectIds": ["4001"],
  "place": {
    "name": "Console Sink Test Location",
    "id": "loc-004",
    "type": "intersection",
    "info": {}
  },
  "analyticsModule": {
    "id": "Console Sink Test",
    "description": "Testing the console sink",
    "info": {},
    "source": "test",
    "version": "1.0"
  },
  "category": "collision",
  "isAnomaly": true,
  "info": {
    "location": "37.7749,-122.4194,0.0",
    "primaryObjectId": "4001",
    "video_path": "$MOCK_VIDEO_PATH"
  },
  "frameIds": [],
  "embeddings": []
}
EOF

patch_timestamps "$PAYLOAD" "$PAYLOAD"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$INPUT_TOPIC" "$PAYLOAD" "$ID_SUFFIX"
print_status "info" "Sent incident with video_path (id-suffix: $ID_SUFFIX)"

# 4. Poll the log for OUR rendered verdict. The console sink is the only output,
#    so the log line IS the deliverable.
#
#    Marker and sensorId have to match on the same line. The consumer group is
#    static, so a rerun resumes from its committed offset and replays every
#    incident earlier tests produced to mdx-incidents — waiting for the first
#    console-sink line of any kind would latch onto one of those instead of
#    ours. Alert MS logs through _SingleLineFormatter, which collapses the
#    embedded newlines of the rendered JSON, so the whole document is
#    guaranteed to sit on the marker's own line.
print_status "wait" "Polling the AB log for the rendered verdict for $SENSOR_ID (up to 90s)..."
FOUND=0
ELAPSED=0
while [ "$ELAPSED" -lt 90 ]; do
    if grep "\[console-sink\] vlm-enhanced incident" "$AB_LOG" 2>/dev/null \
            | grep -q "$SENSOR_ID"; then
        FOUND=1
        break
    fi
    sleep 3
    ELAPSED=$((ELAPSED + 3))
done

if [ "$FOUND" -ne 1 ]; then
    print_status "fail" "FAIL: no console-sink verdict rendered for $SENSOR_ID within 90s"
    RENDERED=$(grep -c "\[console-sink\] vlm-enhanced incident" "$AB_LOG" 2>/dev/null || echo 0)
    print_status "info" "  console-sink incidents rendered for other sensors: $RENDERED"
    print_status "info" "Last 20 lines of the AB log:"
    tail -20 "$AB_LOG" 2>/dev/null || true
    exit 1
fi
print_status "ok" "Console sink rendered the vlm-enhanced incident for $SENSOR_ID"

# 5. The rendered document must carry the verdict, not just the envelope.
if grep "\[console-sink\] vlm-enhanced incident" "$AB_LOG" 2>/dev/null \
        | grep "$SENSOR_ID" | grep -q '"verdict"'; then
    print_status "ok" "Rendered document carries a verdict"
else
    print_status "fail" "FAIL: rendered document for $SENSOR_ID has no verdict field"
    exit 1
fi

print_status "ok" "PASS: console sink emitted the processed result"
exit 0
