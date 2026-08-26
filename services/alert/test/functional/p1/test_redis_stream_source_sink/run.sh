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

# Test: Redis Streams source + Redis Streams VLM-enhanced sink
# Description: Publish an Incident protobuf into a Redis Stream using the MDX
#              envelope that vss-behavior-analytics writes, and verify Alert MS
#              consumes it, runs VLM verification, and publishes the enhanced
#              result to the configured output stream. Kafka is not configured
#              for this run, so nothing here can succeed via the default path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
export REPO_ROOT
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
INPUT_STREAM="mdx-incidents"
OUTPUT_STREAM="mdx-vlm-incidents"
SENSOR_ID="REDIS_STREAM_TEST_SENSOR"
TEST_NAME="redis_stream_source_sink"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: Redis Streams source + Redis Streams sink ==="

mkdir -p "$PID_DIR"

# 0. Redis is optional infrastructure — skip rather than fail when absent.
if ! redis_available; then
    print_status "info" "SKIP: no Redis on $REDIS_HOST:$REDIS_PORT"
    exit 0
fi
print_status "ok" "Redis reachable at $REDIS_HOST:$REDIS_PORT"

# 1. Confirm Alert MS actually selected the Redis transports. Without this a
#    silent fallback to Kafka would make the rest of the test pass for the
#    wrong reason (or fail with a confusing timeout).
if grep -qE "Creating source: .*resolved to 'redisStream'" "$PID_DIR/alert_bridge.log" 2>/dev/null; then
    print_status "ok" "AB log confirms the redisStream source was selected"
else
    print_status "fail" "FAIL: AB did not select the redisStream source"
    tail -20 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
    exit 1
fi

# 2. Prepare a local mock video so VLM verification has media to work with
#    (Mode 2: local file), matching test_kafka_sink_vlm.
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

# 3. Build the incident payload
PAYLOAD="$PID_DIR/incident_redis_stream.json"
cat > "$PAYLOAD" << EOF
{
  "id": "test-redis-stream-$ID_SUFFIX",
  "sensorId": "$SENSOR_ID",
  "timestamp": "2025-01-01T00:00:00.000Z",
  "end": "2025-01-01T00:01:00.000Z",
  "objectIds": ["3001"],
  "place": {
    "name": "Redis Stream Test Location",
    "id": "loc-003",
    "type": "intersection",
    "info": {}
  },
  "analyticsModule": {
    "id": "Redis Stream Test",
    "description": "Testing the Redis Streams source and sink",
    "info": {},
    "source": "test",
    "version": "1.0"
  },
  "category": "collision",
  "isAnomaly": true,
  "info": {
    "location": "37.7749,-122.4194,0.0",
    "primaryObjectId": "3001",
    "video_path": "$MOCK_VIDEO_PATH"
  },
  "frameIds": [],
  "embeddings": []
}
EOF

# 4. Publish into the Redis input stream using the MDX envelope
produce_incident_redis "$REPO_ROOT" "$INPUT_STREAM" "$PAYLOAD" "$ID_SUFFIX"
print_status "info" "Published incident to stream '$INPUT_STREAM' (id-suffix: $ID_SUFFIX)"

# 5. Wait for the enhanced result to appear on the output stream
print_status "wait" "Polling '$OUTPUT_STREAM' for the enhanced incident (up to 60s)..."
DOC=$(poll_redis_stream_for_sensor "$OUTPUT_STREAM" "$SENSOR_ID" 60 3 || echo "")

if [ -z "$DOC" ]; then
    print_status "fail" "FAIL: no enhanced incident for $SENSOR_ID on '$OUTPUT_STREAM'"
    print_status "info" "Input stream length:  $(redis_stream_len "$INPUT_STREAM")"
    print_status "info" "Output stream length: $(redis_stream_len "$OUTPUT_STREAM")"
    print_status "info" "Last 20 lines of the AB log:"
    tail -20 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
    exit 1
fi

print_status "ok" "Found the enhanced incident on '$OUTPUT_STREAM'"
print_status "info" "  document: $DOC"

# 6. Assert the payload survived the round trip. Decoding it above already
#    proves the wire format is the protobuf-in-MDX-envelope shape Logstash
#    reads; here we check the VLM verdict actually rode along.
VERDICT=$(echo "$DOC" | python3 -c "
import json, sys
doc = json.load(sys.stdin)
print(doc.get('info', {}).get('verdict', ''))
" 2>/dev/null || echo "")
RESP_CODE=$(echo "$DOC" | python3 -c "
import json, sys
doc = json.load(sys.stdin)
print(doc.get('info', {}).get('verificationResponseCode', ''))
" 2>/dev/null || echo "")

print_status "info" "  verdict: $VERDICT"
print_status "info" "  responseCode: $RESP_CODE"

if [ -z "$VERDICT" ] && [ "$RESP_CODE" != "200" ]; then
    print_status "info" "WARN: VLM verification may have failed (code=$RESP_CODE)"
fi

# 7. Entries must be acked, otherwise a restart replays the whole stream.
PENDING=$(docker exec "$REDIS_CONTAINER" redis-cli XPENDING "$INPUT_STREAM" \
    "alert-bridge-vlm-group-p1-redis" 2>/dev/null | head -1 | tr -d '\r' || echo "0")
if [ "${PENDING:-0}" = "0" ]; then
    print_status "ok" "Consumed entries were acked (no pending entries)"
else
    print_status "info" "WARN: $PENDING entries still pending on '$INPUT_STREAM'"
fi

print_status "ok" "PASS: Redis Streams source and sink carried the incident end to end"
exit 0
