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

# Test: Kafka source + Redis Streams VLM-enhanced sink
# Description: Verify the two transports are selected independently. An incident
#              produced to Kafka must be consumed on the Kafka source and its
#              VLM-enhanced result published to a Redis Stream.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
export REPO_ROOT
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
INPUT_TOPIC="${TOPIC:-mdx-incidents}"
OUTPUT_STREAM="mdx-vlm-incidents"
SENSOR_ID="MIXED_TRANSPORT_TEST_SENSOR"
TEST_NAME="redis_sink_kafka_source"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"
AB_LOG="$PID_DIR/alert_bridge.log"

echo "=== P1: Kafka source + Redis Streams sink ==="

mkdir -p "$PID_DIR"

if ! redis_available; then
    print_status "info" "SKIP: no Redis on $REDIS_HOST:$REDIS_PORT"
    exit 0
fi

# 1. The source must still be Kafka. If the Redis sink selection leaked into
#    the source, this catches it before the test times out on an empty stream.
if grep -q "Creating source of type: kafka" "$AB_LOG" 2>/dev/null; then
    print_status "ok" "AB log confirms the Kafka source"
else
    print_status "fail" "FAIL: AB did not select the Kafka source"
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

# 3. Build and produce the incident to Kafka
PAYLOAD="$PID_DIR/incident_mixed_transport.json"
cat > "$PAYLOAD" << EOF
{
  "id": "test-mixed-transport-$ID_SUFFIX",
  "sensorId": "$SENSOR_ID",
  "timestamp": "2025-01-01T00:00:00.000Z",
  "end": "2025-01-01T00:01:00.000Z",
  "objectIds": ["5001"],
  "place": {
    "name": "Mixed Transport Test Location",
    "id": "loc-005",
    "type": "intersection",
    "info": {}
  },
  "analyticsModule": {
    "id": "Mixed Transport Test",
    "description": "Kafka source with a Redis Streams sink",
    "info": {},
    "source": "test",
    "version": "1.0"
  },
  "category": "collision",
  "isAnomaly": true,
  "info": {
    "location": "37.7749,-122.4194,0.0",
    "primaryObjectId": "5001",
    "video_path": "$MOCK_VIDEO_PATH"
  },
  "frameIds": [],
  "embeddings": []
}
EOF

patch_timestamps "$PAYLOAD" "$PAYLOAD"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$INPUT_TOPIC" "$PAYLOAD" "$ID_SUFFIX"
print_status "info" "Sent incident to Kafka topic '$INPUT_TOPIC' (id-suffix: $ID_SUFFIX)"

# 4. The result must land on the Redis output stream, not in Elasticsearch.
print_status "wait" "Polling Redis stream '$OUTPUT_STREAM' (up to 60s)..."
DOC=$(poll_redis_stream_for_sensor "$OUTPUT_STREAM" "$SENSOR_ID" 60 3 || echo "")

if [ -z "$DOC" ]; then
    print_status "fail" "FAIL: no enhanced incident for $SENSOR_ID on '$OUTPUT_STREAM'"
    print_status "info" "Output stream length: $(redis_stream_len "$OUTPUT_STREAM")"
    print_status "info" "Last 20 lines of the AB log:"
    tail -20 "$AB_LOG" 2>/dev/null || true
    exit 1
fi

print_status "ok" "Enhanced incident published to the Redis stream"
print_status "info" "  document: $DOC"
print_status "ok" "PASS: source and sink transports were selected independently"
exit 0
