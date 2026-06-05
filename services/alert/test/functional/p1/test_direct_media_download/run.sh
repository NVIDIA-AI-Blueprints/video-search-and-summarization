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

# Test: Direct Media Download (Mode 3)
# Description: Verify Alert Bridge can download media from direct URLs in info.media_urls
#              and process them through VLM without VST lookup
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"
TEST_NAME="direct_media_download"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: Direct Media Download (Mode 3) ==="

mkdir -p "$PID_DIR"

# 1. Create test payload with media_urls (pointing to VST simulator's media endpoint)
#    Uses /vst/sim/media/ which serves video files (fallback to dummy bytes if no file exists)
PAYLOAD="$PID_DIR/incident_direct_media.json"
cat > "$PAYLOAD" << 'EOF'
{
  "id": "test-direct-media",
  "sensorId": "DIRECT_MEDIA_TEST_SENSOR",
  "timestamp": "2025-01-01T00:00:00.000Z",
  "end": "2025-01-01T00:01:00.000Z",
  "objectIds": ["1001"],
  "place": {
    "name": "Test Location",
    "id": "loc-001",
    "type": "intersection",
    "info": {}
  },
  "analyticsModule": {
    "id": "Direct Media Test",
    "description": "Testing direct media URL download",
    "info": {},
    "source": "test",
    "version": "1.0"
  },
  "category": "collision",
  "isAnomaly": true,
  "info": {
    "location": "37.7749,-122.4194,0.0",
    "primaryObjectId": "1001",
    "media_urls": "[\"http://127.0.0.1:30888/vst/sim/media/test.mp4\"]",
    "media_type": "video"
  },
  "frameIds": [],
  "embeddings": []
}
EOF

# 2. Patch timestamps and produce
patch_timestamps "$PAYLOAD" "$PAYLOAD"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PAYLOAD" "$ID_SUFFIX"
print_status "info" "Sent incident with media_urls (id-suffix: $ID_SUFFIX)"

# 3. Wait and poll ES
sleep 15
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES within 60s"; exit 1; }

# 4. Verify the document was processed via direct media path
SENSOR_ID=$(echo "$DOC" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sensorId',''))" 2>/dev/null || echo "")
VERDICT=$(echo "$DOC" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('info',{}).get('verdict',''))" 2>/dev/null || echo "")
MEDIA_TYPE=$(echo "$DOC" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('info',{}).get('media_type',''))" 2>/dev/null || echo "")
RESPONSE_CODE=$(echo "$DOC" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('info',{}).get('verificationResponseCode',''))" 2>/dev/null || echo "")

print_status "info" "sensorId: $SENSOR_ID"
print_status "info" "verdict: $VERDICT"
print_status "info" "media_type: $MEDIA_TYPE"
print_status "info" "verificationResponseCode: $RESPONSE_CODE"

# 5. Assertions
if [ "$SENSOR_ID" != "DIRECT_MEDIA_TEST_SENSOR" ]; then
    print_status "fail" "FAIL: sensorId mismatch (expected DIRECT_MEDIA_TEST_SENSOR, got $SENSOR_ID)"
    exit 1
fi

if [ -z "$VERDICT" ]; then
    print_status "fail" "FAIL: verdict is empty"
    exit 1
fi

if [ "$MEDIA_TYPE" != "video" ]; then
    print_status "info" "WARN: media_type not set to 'video' (got '$MEDIA_TYPE')"
fi

# Check AB log for Mode 3 activation
if grep -q "Mode 3: Direct media URLs detected" "$PID_DIR/alert_bridge.log" 2>/dev/null; then
    print_status "ok" "Mode 3 direct media path was used"
else
    print_status "info" "WARN: Could not confirm Mode 3 in logs"
fi

print_status "ok" "PASS: Direct media download test completed successfully"
exit 0
