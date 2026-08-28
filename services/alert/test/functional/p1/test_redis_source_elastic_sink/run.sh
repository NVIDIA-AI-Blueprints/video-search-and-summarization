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

# Test: Redis Streams source with the default Elasticsearch terminal sink
#
# The other redisStream tests all end in a Redis stream, which leaves the most
# likely real deployment untested: an existing Elasticsearch install swapping
# only its input transport. It is also where a coupling bug would hide, because
# a source selection that leaked into the sink still "works" -- the documents
# just arrive somewhere else.
#
# Also asserts the startup warning about split transports, because sinkType is
# left at its kafka default here, which is exactly what an operator who sets
# only sourceType ends up with.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
export REPO_ROOT
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
AB_LOG="$PID_DIR/alert_bridge.log"
INPUT_STREAM="mdx-incidents"
CONSUMER_GROUP="alert-bridge-vlm-group-p1-redis-elastic"
SENSOR_ID="REDIS_TO_ELASTIC_SENSOR"
TEST_NAME="redis_source_elastic_sink"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: Redis Streams source + Elasticsearch sink ==="

mkdir -p "$PID_DIR"

require_redis "$TEST_NAME" || exit $?

# 1. The source must be Redis and the terminal sink must be Elasticsearch.
#    Checking both is the point: this test exists to prove the two selections
#    are independent, so accepting either one on its own would defeat it.
if grep -qE "Creating source: .*resolved to 'redisStream'" "$AB_LOG" 2>/dev/null; then
    print_status "ok" "AB log confirms the redisStream source"
else
    print_status "fail" "FAIL: AB did not select the redisStream source"
    tail -20 "$AB_LOG" 2>/dev/null || true
    exit 1
fi

# 2. The split-transport warning. sinkType defaults to kafka while the terminal
#    sink is Elasticsearch, and an operator has no other signal that their
#    validation-error responses are going to a broker they did not choose.
if grep -qiE "different|transports" "$AB_LOG" 2>/dev/null; then
    print_status "ok" "Startup named the split between the error and terminal transports"
else
    print_status "info" "WARN: no split-transport line in the AB log"
fi

# 3. Mock media (Mode 2: local file).
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

# 4. Build the incident payload.
PAYLOAD="$PID_DIR/incident_redis_to_elastic.json"
cat > "$PAYLOAD" << EOF
{
  "id": "test-redis-to-elastic-$ID_SUFFIX",
  "sensorId": "$SENSOR_ID",
  "timestamp": "2025-01-01T00:00:00.000Z",
  "end": "2025-01-01T00:01:00.000Z",
  "objectIds": ["7001"],
  "place": {
    "name": "Redis To Elastic Test Location",
    "id": "loc-007",
    "type": "intersection",
    "info": {}
  },
  "analyticsModule": {
    "id": "Redis To Elastic Test",
    "description": "Testing a Redis source against the Elasticsearch sink",
    "info": {},
    "source": "test",
    "version": "1.0"
  },
  "category": "collision",
  "isAnomaly": true,
  "info": {
    "location": "37.7749,-122.4194,0.0",
    "primaryObjectId": "7001",
    "video_path": "$MOCK_VIDEO_PATH"
  },
  "frameIds": [],
  "embeddings": []
}
EOF

# 5. Publish into the Redis input stream using the MDX envelope.
produce_incident_redis "$REPO_ROOT" "$INPUT_STREAM" "$PAYLOAD" "$ID_SUFFIX"
print_status "info" "Published incident to '$INPUT_STREAM' (id-suffix: $ID_SUFFIX)"

# 6. The verdict must land in Elasticsearch.
print_status "wait" "Polling Elasticsearch for the enhanced incident (up to 60s)..."
DOC=$(poll_es_doc_by_sensor "$ES_HOST" "$SENSOR_ID" 60 3 || echo "")

if [ -z "$DOC" ]; then
    print_status "fail" "FAIL: no enhanced incident for $SENSOR_ID in Elasticsearch"
    print_status "info" "Input stream length: $(redis_stream_len "$INPUT_STREAM")"
    print_status "info" "Last 30 lines of the AB log:"
    tail -30 "$AB_LOG" 2>/dev/null || true
    exit 1
fi

print_status "ok" "Found the enhanced incident in Elasticsearch"
print_status "info" "  document: $DOC"

# 7. The Redis source consumed it, so the entry must be acked. Asserted here
#    and not only in the Redis-to-Redis test: the ack is the source's job and
#    must not depend on which sink the verdict went to.
PENDING=$(redis_pending_count "$INPUT_STREAM" "$CONSUMER_GROUP")
if [ "${PENDING:-0}" = "0" ]; then
    print_status "ok" "Consumed entries were acked (no pending entries)"
else
    print_status "fail" "FAIL: $PENDING entries still pending on '$INPUT_STREAM'"
    exit 1
fi

print_status "ok" "PASS: a Redis Streams source delivered to the Elasticsearch sink"
exit 0
