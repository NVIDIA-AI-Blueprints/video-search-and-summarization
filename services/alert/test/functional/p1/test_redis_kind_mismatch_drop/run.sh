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

# Test: a payload whose declared kind contradicts the stream it arrived on
# Description: The stream is the authority on kind, and the pipeline stamps that
#              answer over the payload's notification_type. So an incident-shaped
#              payload published to the alert stream is not rejected by anything
#              downstream — it is relabelled an alert, verified with the alert
#              prompt, and published to the alert destination, silently. Only the
#              source can see the contradiction, because only the source knows
#              which stream the entry came from.
#
#              This asserts three things a unit test cannot, because all three
#              are about a real producer, a real consumer group, and a real
#              XACK:
#                1. the contradicting entry produces no output on either
#                   destination — not the kind it claimed, not the kind the
#                   stream says;
#                2. it is acked rather than left pending, so it is dropped once
#                   instead of being replayed on every restart forever;
#                3. a well-formed alert published alongside it still lands. A
#                   source that rejected the whole read batch would also pass
#                   1 and 2.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
export REPO_ROOT
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ALERT_INPUT_STREAM="mdx-alerts-km"
ALERT_OUTPUT_STREAM="mdx-vlm-alerts-km"
INCIDENT_OUTPUT_STREAM="mdx-vlm-incidents-km"
CONSUMER_GROUP="alert-bridge-vlm-group-p1-kind-mismatch"
LIAR_SENSOR_ID="REDIS_KIND_MISMATCH_SENSOR"
CONTROL_SENSOR_ID="REDIS_KIND_AGREEING_SENSOR"
TEST_NAME="redis_kind_mismatch_drop"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: Redis Streams payload contradicting its stream's kind ==="

mkdir -p "$PID_DIR"

require_redis "$TEST_NAME" || exit $?

# 1. Confirm the redisStream source was actually selected. Without this a
#    silent fallback to Kafka would make everything below pass for a reason
#    that has nothing to do with what is being tested.
if grep -qE "Creating source: .*resolved to 'redisStream'" "$PID_DIR/alert_bridge.log" 2>/dev/null; then
    print_status "ok" "AB log confirms the redisStream source was selected"
else
    print_status "fail" "FAIL: AB did not select the redisStream source"
    tail -20 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
    exit 1
fi

# 2. Local mock video so VLM verification has media for the control alert.
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

# 3. Two payloads that differ in exactly one field. Everything else is held
#    equal so the outcomes below can only be attributed to that field.
#
#    That includes analyticsModule.id, which is not free text: for an alert it
#    becomes the event's category, a prompt is looked up by it, and an
#    unconfigured category is dropped before the VLM with "No prompt found".
#    The control alert has to survive for this test to conclude anything, so it
#    names a type from alert_type_config.json — "Stop Anomaly Module", which
#    matches a vehicle stopped in a travel lane. A descriptive name here made
#    both payloads disappear, which looks like the feature working.
write_alert_payload() {
    local path="$1" sensor_id="$2" declared_kind="$3"
    local declaration=""
    if [ -n "$declared_kind" ]; then
        declaration="\"notification_type\": \"$declared_kind\","
    fi
    cat > "$path" << EOF
{
  "id": "test-redis-kind-$sensor_id-$ID_SUFFIX",
  $declaration
  "timestamp": "2025-01-01T00:00:00.000Z",
  "end": "2025-01-01T00:01:00.000Z",
  "sensor": {
    "id": "$sensor_id",
    "type": "traffic_camera",
    "description": "Redis kind-authority test camera"
  },
  "analyticsModule": {
    "id": "Stop Anomaly Module",
    "description": "Testing the kind-compatibility gate on the alert route",
    "source": "test",
    "version": "1.0",
    "info": {}
  },
  "event": {
    "id": "test-redis-kind-$sensor_id-$ID_SUFFIX",
    "type": "vehicle_waiting",
    "description": "Vehicle stopped in a travel lane"
  },
  "place": { "name": "Redis Kind Mismatch Test Location" },
  "videoPath": "$MOCK_VIDEO_PATH",
  "info": {
    "video_path": "$MOCK_VIDEO_PATH",
    "scene_summary": "Vehicle stopped in travel lane"
  }
}
EOF
}

LIAR_PAYLOAD="$PID_DIR/alert_redis_kind_liar.json"
CONTROL_PAYLOAD="$PID_DIR/alert_redis_kind_control.json"
write_alert_payload "$LIAR_PAYLOAD" "$LIAR_SENSOR_ID" "incident"
write_alert_payload "$CONTROL_PAYLOAD" "$CONTROL_SENSOR_ID" ""

# 4. Publish both to the alert stream. The contradiction is the pairing, not
#    the payload: this same body on the incident stream is perfectly valid.
produce_alert_redis "$REPO_ROOT" "$ALERT_INPUT_STREAM" "$LIAR_PAYLOAD" "${ID_SUFFIX}_liar"
print_status "info" "Published an alert declaring notification_type=incident to '$ALERT_INPUT_STREAM'"
produce_alert_redis "$REPO_ROOT" "$ALERT_INPUT_STREAM" "$CONTROL_PAYLOAD" "${ID_SUFFIX}_control"
print_status "info" "Published a well-formed alert to the same stream"

# 5. The control has to land first. Until it does there is nothing to conclude
#    from the other one's absence — an idle pipeline looks the same.
print_status "wait" "Polling '$ALERT_OUTPUT_STREAM' for the well-formed alert (up to 60s)..."
CONTROL_DOC=$(poll_redis_stream_for_sensor "$ALERT_OUTPUT_STREAM" "$CONTROL_SENSOR_ID" 60 3 || echo "")

if [ -z "$CONTROL_DOC" ]; then
    print_status "fail" "FAIL: the well-formed alert never reached '$ALERT_OUTPUT_STREAM'"
    print_status "info" "Nothing below can be concluded: an idle pipeline drops everything."
    print_status "info" "Input stream length: $(redis_stream_len "$ALERT_INPUT_STREAM")"
    print_status "info" "Last 30 lines of the AB log:"
    tail -30 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
    exit 1
fi
print_status "ok" "The well-formed alert was processed — the rejection below is selective"

# 6. The contradicting entry reached neither destination. Both are checked
#    because there are two distinct failure modes and they are worth telling
#    apart: on the alert stream means the declaration was ignored and the entry
#    relabelled; on the incident stream means terminal routing believed the
#    payload over the stream.
for stream in "$ALERT_OUTPUT_STREAM" "$INCIDENT_OUTPUT_STREAM"; do
    HIT=$(poll_redis_stream_for_sensor "$stream" "$LIAR_SENSOR_ID" 5 2 || echo "")
    if [ -n "$HIT" ]; then
        print_status "fail" "FAIL: the contradicting payload was published to '$stream'"
        print_status "info" "  document: $HIT"
        exit 1
    fi
done
print_status "ok" "The contradicting payload reached neither output stream"

# 7. The drop is named in the log and attributed to the kind conflict rather
#    than to a malformed payload — the body is valid, and a run that dropped it
#    as schema_invalid would satisfy step 6 while telling operators the wrong
#    thing about their producer.
if grep -q "declares notification_type='incident'" "$PID_DIR/alert_bridge.log" 2>/dev/null; then
    print_status "ok" "The AB log attributes the drop to the declared kind"
else
    print_status "fail" "FAIL: no kind-conflict drop in the AB log"
    print_status "info" "Dropped entries the log does mention:"
    grep "Dropping Redis entry" "$PID_DIR/alert_bridge.log" 2>/dev/null | tail -5 || \
        print_status "info" "  (none)"
    exit 1
fi

# 8. A rejected entry must still be acked. Left pending it is redelivered on
#    every restart and on every reclaim sweep, so a config that produces one
#    bad entry produces an unbounded stream of retries of it.
PENDING=$(redis_pending_count "$ALERT_INPUT_STREAM" "$CONSUMER_GROUP")
if [ "${PENDING:-0}" = "0" ]; then
    print_status "ok" "The rejected entry was acked (no pending entries)"
else
    print_status "fail" "FAIL: $PENDING entries still pending on '$ALERT_INPUT_STREAM'"
    print_status "info" "A dropped entry that is not acked is replayed forever."
    exit 1
fi

echo ""
print_status "ok" "PASS: the stream stayed the authority on kind, and the contradiction was dropped once"
exit 0
