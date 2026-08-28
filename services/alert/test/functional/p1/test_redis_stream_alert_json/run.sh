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

# Test: alerts over Redis Streams, published in the JSON envelope
#
# What this covers that the incident test does not:
#
#   1. an alert rather than an incident. The two take different protobuf
#      schemas and different output streams, and the kind is decided by the
#      stream the entry arrived on -- so an alert reaching the incident stream
#      is a routing bug that raises nothing anywhere.
#   2. the data/timestamp/metadata JSON envelope, with a populated 'metadata'
#      sidecar. An entry carrying both a body and a sidecar has exactly one
#      correct reading, and decoding the sidecar produces a payload that parses
#      cleanly and describes nothing.
#   3. that trimming is off by default: the input entry is still there after
#      being consumed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
export REPO_ROOT
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
INPUT_STREAM="mdx-alerts"
ALERT_OUTPUT_STREAM="mdx-vlm-alerts"
INCIDENT_OUTPUT_STREAM="mdx-vlm-incidents"
CONSUMER_GROUP="alert-bridge-vlm-group-p1-alert-json"
SENSOR_ID="REDIS_JSON_ALERT_SENSOR"
TEST_NAME="redis_stream_alert_json"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: Redis Streams alert in the JSON envelope ==="

mkdir -p "$PID_DIR"

require_redis "$TEST_NAME" || exit $?

# 1. Confirm the redisStream source was actually selected. Without this a
#    silent fallback to Kafka would make everything below pass or time out for
#    a reason that has nothing to do with what is being tested.
if grep -qE "Creating source: .*resolved to 'redisStream'" "$PID_DIR/alert_bridge.log" 2>/dev/null; then
    print_status "ok" "AB log confirms the redisStream source was selected"
else
    print_status "fail" "FAIL: AB did not select the redisStream source"
    tail -20 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
    exit 1
fi

# 2. Local mock video so VLM verification has media, matching the sibling test.
MOCK_VIDEO_DIR="/tmp/alert_bridge_media"
MOCK_VIDEO_PATH="$MOCK_VIDEO_DIR/test_video_${ID_SUFFIX}.mp4"
mkdir -p "$MOCK_VIDEO_DIR"
if ! curl -sf "http://127.0.0.1:30888/mock/media/test_video.mp4" -o "$MOCK_VIDEO_PATH" 2>/dev/null; then
    print_status "info" "Creating minimal mock video file"
    python3 -c "
ftyp = b'\\x00\\x00\\x00\\x14ftypmp42\\x00\\x00\\x00\\x00mp42'
moov = b'\\x00\\x00\\x00\\x08moov'
with open('$MOCK_VIDEO_PATH', 'wb') as f:
    f.write(ftyp + moov)
"
fi

# 3. Build an alert (Behavior) payload. Note there is no notification_type
#    field: an alert whose kind is only implied by its stream is exactly the
#    case that used to be published to the incident stream, because terminal
#    routing re-derived the kind from the payload and found nothing.
PAYLOAD="$PID_DIR/alert_redis_json.json"
cat > "$PAYLOAD" << EOF
{
  "id": "test-redis-json-alert-$ID_SUFFIX",
  "timestamp": "2025-01-01T00:00:00.000Z",
  "end": "2025-01-01T00:01:00.000Z",
  "sensor": {
    "id": "$SENSOR_ID",
    "type": "traffic_camera",
    "description": "Redis JSON envelope alert test camera"
  },
  "analyticsModule": {
    "id": "Redis JSON Envelope Test",
    "description": "Testing the JSON envelope on the alert route",
    "source": "test",
    "version": "1.0",
    "info": {}
  },
  "event": {
    "id": "test-redis-json-alert-$ID_SUFFIX",
    "type": "vehicle_waiting",
    "description": "Vehicle stopped in a travel lane"
  },
  "place": { "name": "Redis JSON Test Location" },
  "videoPath": "$MOCK_VIDEO_PATH",
  "info": {
    "video_path": "$MOCK_VIDEO_PATH",
    "scene_summary": "Vehicle stopped in travel lane"
  }
}
EOF

# 4. Publish in the JSON envelope: body in 'data', sidecar in 'metadata'.
produce_alert_redis "$REPO_ROOT" "$INPUT_STREAM" "$PAYLOAD" "$ID_SUFFIX" --envelope json
print_status "info" "Published alert to '$INPUT_STREAM' in the JSON envelope (id-suffix: $ID_SUFFIX)"

# 5. Wait for the enhanced alert on the alert output stream.
print_status "wait" "Polling '$ALERT_OUTPUT_STREAM' for the enhanced alert (up to 60s)..."
DOC=$(poll_redis_stream_for_sensor "$ALERT_OUTPUT_STREAM" "$SENSOR_ID" 60 3 || echo "")

if [ -z "$DOC" ]; then
    print_status "fail" "FAIL: no enhanced alert for $SENSOR_ID on '$ALERT_OUTPUT_STREAM'"
    print_status "info" "Input stream length:  $(redis_stream_len "$INPUT_STREAM")"
    print_status "info" "Alert output length:  $(redis_stream_len "$ALERT_OUTPUT_STREAM")"
    # Named explicitly because this is the failure mode worth telling apart: an
    # alert on the incident stream means the kind was re-derived from the
    # payload instead of taken from the stream it arrived on.
    print_status "info" "Incident output length: $(redis_stream_len "$INCIDENT_OUTPUT_STREAM") (an alert here would be a cross-route)"
    print_status "info" "Last 30 lines of the AB log:"
    tail -30 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
    exit 1
fi

print_status "ok" "Found the enhanced alert on '$ALERT_OUTPUT_STREAM'"
print_status "info" "  document: $DOC"

# 6. The body was read from 'data', not from the 'metadata' sidecar. The
#    sidecar carries no event fields at all, so a document that came from it
#    would be missing the ones asserted here.
EVENT_TYPE=$(echo "$DOC" | python3 -c "
import json, sys
doc = json.load(sys.stdin)
print(doc.get('eventType') or (doc.get('event') or {}).get('type', ''))
" 2>/dev/null || echo "")

if [ "$EVENT_TYPE" != "vehicle_waiting" ]; then
    print_status "fail" "FAIL: expected eventType 'vehicle_waiting' from the 'data' field, got '$EVENT_TYPE'"
    print_status "info" "A blank value here means the 'metadata' sidecar was decoded as the event body"
    exit 1
fi
print_status "ok" "Body was decoded from 'data' (eventType=$EVENT_TYPE), not from 'metadata'"

# 7. The alert must NOT also have been published to the incident stream. This
#    is the cross-route the kind-authority fix exists to prevent, and it is
#    silent: both writes succeed.
INCIDENT_HIT=$(poll_redis_stream_for_sensor "$INCIDENT_OUTPUT_STREAM" "$SENSOR_ID" 5 2 || echo "")
if [ -n "$INCIDENT_HIT" ]; then
    print_status "fail" "FAIL: the alert was also published to '$INCIDENT_OUTPUT_STREAM'"
    print_status "info" "  cross-routed document: $INCIDENT_HIT"
    exit 1
fi
print_status "ok" "The alert did not cross-route to the incident stream"

# 8. Entries must be acked, or a restart replays the whole stream.
PENDING=$(redis_pending_count "$INPUT_STREAM" "$CONSUMER_GROUP")
if [ "${PENDING:-0}" = "0" ]; then
    print_status "ok" "Consumed entries were acked (no pending entries)"
else
    print_status "fail" "FAIL: $PENDING entries still pending on '$INPUT_STREAM'"
    exit 1
fi

# 9. Trimming is opt-in, and this config does not opt in: the entry Alert MS
#    consumed is still in the customer's stream. Asserted because the previous
#    default trimmed on every publish, which made ordinary output delete
#    entries nobody asked it to touch.
INPUT_LEN=$(redis_stream_len "$INPUT_STREAM")
if [ "${INPUT_LEN:-0}" -lt 1 ]; then
    print_status "fail" "FAIL: '$INPUT_STREAM' is empty; consumption must not remove entries"
    exit 1
fi
print_status "ok" "Input stream still holds its $INPUT_LEN entry(ies) — nothing was trimmed"

print_status "ok" "PASS: alert carried end to end over Redis Streams in the JSON envelope"
exit 0
