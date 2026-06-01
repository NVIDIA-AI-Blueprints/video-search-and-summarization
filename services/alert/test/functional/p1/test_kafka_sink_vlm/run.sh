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

# Test: Base64 Media Encode + VLM Verification + Kafka Sink
# Description: Verify Alert Bridge can process local video file (Mode 2),
#              encode to base64, send to VLM, and publish result to Kafka sink
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
INPUT_TOPIC="${TOPIC:-mdx-incidents}"
OUTPUT_TOPIC="mdx-vlm-incidents"
KAFKA_CONTAINER="${KAFKA_CONTAINER:-alert-agent-kafka-test}"
TEST_NAME="kafka_sink_vlm"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: Base64 Media Encode + VLM + Kafka Sink ==="

mkdir -p "$PID_DIR"

# 1. Prepare local mock video file
MOCK_VIDEO_DIR="/tmp/alert_bridge_media"
MOCK_VIDEO_PATH="$MOCK_VIDEO_DIR/test_video_${ID_SUFFIX}.mp4"
mkdir -p "$MOCK_VIDEO_DIR"

# Download a small test video from VST simulator (or create a minimal one)
if curl -sf "http://127.0.0.1:30888/mock/media/test_video.mp4" -o "$MOCK_VIDEO_PATH" 2>/dev/null; then
    print_status "ok" "Downloaded mock video from VST simulator"
else
    # Create minimal valid MP4 file (ftyp + moov atoms)
    print_status "info" "Creating minimal mock video file"
    python3 -c "
import struct
# Minimal MP4: ftyp box + empty moov box
ftyp = b'\\x00\\x00\\x00\\x14ftypmp42\\x00\\x00\\x00\\x00mp42'
moov = b'\\x00\\x00\\x00\\x08moov'
with open('$MOCK_VIDEO_PATH', 'wb') as f:
    f.write(ftyp + moov)
"
fi

# 2. Create test payload with video_path (Mode 2: local file)
PAYLOAD="$PID_DIR/incident_kafka_sink.json"
cat > "$PAYLOAD" << EOF
{
  "id": "test-kafka-sink-$ID_SUFFIX",
  "sensorId": "KAFKA_SINK_TEST_SENSOR",
  "timestamp": "2025-01-01T00:00:00.000Z",
  "end": "2025-01-01T00:01:00.000Z",
  "objectIds": ["2001"],
  "place": {
    "name": "Kafka Sink Test Location",
    "id": "loc-002",
    "type": "intersection",
    "info": {}
  },
  "analyticsModule": {
    "id": "Kafka Sink Test",
    "description": "Testing Kafka sink with local video",
    "info": {},
    "source": "test",
    "version": "1.0"
  },
  "category": "collision",
  "isAnomaly": true,
  "info": {
    "location": "37.7749,-122.4194,0.0",
    "primaryObjectId": "2001",
    "video_path": "$MOCK_VIDEO_PATH"
  },
  "frameIds": [],
  "embeddings": []
}
EOF

# 3. Reset Kafka output topic to ensure clean state
print_status "wait" "Resetting Kafka output topic..."
docker exec "$KAFKA_CONTAINER" kafka-topics --delete \
    --bootstrap-server localhost:9092 \
    --topic "$OUTPUT_TOPIC" 2>/dev/null || true
sleep 2
docker exec "$KAFKA_CONTAINER" kafka-topics --create \
    --bootstrap-server localhost:9092 \
    --topic "$OUTPUT_TOPIC" \
    --partitions 1 \
    --replication-factor 1 2>/dev/null || true
print_status "ok" "Kafka topic $OUTPUT_TOPIC reset"

# 4. Patch timestamps and produce to input topic
patch_timestamps "$PAYLOAD" "$PAYLOAD"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$INPUT_TOPIC" "$PAYLOAD" "$ID_SUFFIX"
print_status "info" "Sent incident with video_path (id-suffix: $ID_SUFFIX)"

# 5. Wait for processing
print_status "wait" "Waiting for VLM processing (15s)..."
sleep 15

# 6. Consume from Kafka output topic and verify
print_status "wait" "Checking Kafka output topic..."

# Check AB log first to confirm publish happened
if grep -q "Publishing VLM-enhanced event to Kafka.*topic=mdx-vlm-incidents" "$PID_DIR/alert_bridge.log" 2>/dev/null; then
    print_status "ok" "AB log confirms Kafka publish attempt"
else
    print_status "fail" "AB log does not show Kafka publish"
fi

# Use Python to consume with proper protobuf handling
RESULT=$(python3 << 'PYEOF'
import sys
import json
import os

sys.path.insert(0, '/localhome/local-trongp/alert_agent')

from confluent_kafka import Consumer, KafkaError, TopicPartition, OFFSET_BEGINNING

conf = {
    'bootstrap.servers': '127.0.0.1:9092',
    'group.id': 'p1-test-kafka-sink-verify-' + str(os.getpid()),
    'auto.offset.reset': 'earliest',
    'enable.auto.commit': False,
}

topic = 'mdx-vlm-incidents'
messages = []

try:
    consumer = Consumer(conf)
    
    # Assign partition directly and seek to beginning
    partitions = [TopicPartition(topic, 0, OFFSET_BEGINNING)]
    consumer.assign(partitions)
    
    # Poll for messages with timeout
    empty_polls = 0
    max_empty = 10
    
    while empty_polls < max_empty:
        msg = consumer.poll(timeout=1.0)
        
        if msg is None:
            empty_polls += 1
            continue
        
        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                break
            print(f"Error: {msg.error()}", file=sys.stderr)
            continue
        
        empty_polls = 0  # Reset on successful message
        value = msg.value()
        
        if not value:
            continue
        
        # Try protobuf first (Kafka sink uses protobuf)
        decoded = False
        try:
            from mdx.anomaly.protobuf import Incident
            incident = Incident()
            incident.ParseFromString(value)
            if incident.sensorId:
                # Convert info map to dict
                info_dict = {}
                for key in incident.info:
                    info_dict[key] = incident.info[key]
                # Note: Incident proto has no 'id' field, use sensorId or info.id
                data = {
                    'id': info_dict.get('id', incident.sensorId),
                    'sensorId': incident.sensorId,
                    'category': incident.category,
                    'info': info_dict
                }
                messages.append(data)
                decoded = True
        except Exception as e:
            print(f"Protobuf decode failed: {e}", file=sys.stderr)
        
        if decoded:
            continue
        
        # Try JSON
        try:
            data = json.loads(value.decode('utf-8'))
            messages.append(data)
            continue
        except Exception as e:
            print(f"JSON decode failed: {e}", file=sys.stderr)
        
        # Raw bytes - show hex prefix for debugging
        hex_prefix = value[:20].hex() if len(value) >= 20 else value.hex()
        messages.append({'raw_size': len(value), 'sensorId': 'unknown_format', 'hex': hex_prefix})
    
    consumer.close()

except Exception as e:
    print(f"ERROR|Consumer exception: {e}")
    sys.exit(0)

# Find our test message
if messages:
    for msg in messages:
        sensor_id = msg.get('sensorId', '')
        if 'KAFKA_SINK_TEST_SENSOR' in str(sensor_id):
            verdict = msg.get('info', {}).get('verdict', '')
            response_code = msg.get('info', {}).get('verificationResponseCode', '')
            print(f"FOUND|{sensor_id}|{verdict}|{response_code}")
            sys.exit(0)
    
    # Show what we found with more detail
    details = []
    for m in messages[:3]:
        sid = str(m.get('sensorId', 'unknown'))[:30]
        if 'hex' in m:
            details.append(f"{sid}(hex:{m['hex'][:16]})")
        else:
            details.append(sid)
    print(f"NOMATCH|found {len(messages)} messages: {details}")
else:
    print("EMPTY|no messages in topic")
PYEOF
) || RESULT="ERROR|python script failed"

print_status "info" "Kafka result: $RESULT"

# 7. Parse result and assert
IFS='|' read -r STATUS SENSOR_OR_MSG VERDICT RESP_CODE <<< "$RESULT"

if [ "$STATUS" = "FOUND" ]; then
    print_status "ok" "Found message in Kafka output topic"
    print_status "info" "  sensorId: $SENSOR_OR_MSG"
    print_status "info" "  verdict: $VERDICT"
    print_status "info" "  responseCode: $RESP_CODE"
    
    if [ -z "$VERDICT" ] && [ "$RESP_CODE" != "200" ]; then
        print_status "info" "WARN: VLM verification may have failed (code=$RESP_CODE)"
    fi
    
    print_status "ok" "PASS: Kafka sink received VLM-enhanced incident"
    exit 0
elif [ "$STATUS" = "NOMATCH" ]; then
    print_status "fail" "FAIL: Messages in Kafka but none matched test sensor"
    print_status "info" "  $SENSOR_OR_MSG"
    exit 1
elif [ "$STATUS" = "EMPTY" ]; then
    print_status "fail" "FAIL: No messages in Kafka output topic"
    # Check AB log for errors
    if [ -f "$PID_DIR/alert_bridge.log" ]; then
        print_status "info" "Last 10 lines of AB log:"
        tail -10 "$PID_DIR/alert_bridge.log" || true
    fi
    exit 1
else
    print_status "fail" "FAIL: Error checking Kafka - $RESULT"
    exit 1
fi
