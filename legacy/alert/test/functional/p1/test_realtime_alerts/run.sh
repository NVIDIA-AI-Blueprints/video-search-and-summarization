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

# Test: Realtime Alerts API
# Description: Test POST/GET/DELETE /api/v1/realtime endpoints
#              Validates create, list, delete realtime VLM alert rules
#              Verifies RTVI simulator received the expected calls
#              Tests caption-start failure path with rollback
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

AB_HOST="${AB_HOST:-http://localhost:9080}"
RTSP_URL="${RTSP_URL:-rtsp://localhost:31554/nvstream/home/vst/vst_release/streamer_videos/sample-warehouse-ladder.mp4}"
RTVI_SIM_PORT="${RTVI_SIM_PORT:-8018}"
RTVI_SIM_HOST="http://localhost:${RTVI_SIM_PORT}"
TEST_NAME="realtime_alerts"

echo "=== P1: Realtime Alerts API ==="
echo "Target: $AB_HOST"
echo ""

# Start RTVI VLM mock simulator
RTVI_SIM_PID=""
if ! curl -sf "${RTVI_SIM_HOST}/v1/ready" >/dev/null 2>&1; then
    print_status "wait" "Starting RTVI VLM mock on port $RTVI_SIM_PORT..."
    RTVI_SIM_PORT=$RTVI_SIM_PORT python3 "$REPO_ROOT/test/sim_scripts/rtvi/rtvi_vlm_sim.py" \
        > "${PID_DIR:-/tmp}/rtvi_sim.log" 2>&1 &
    RTVI_SIM_PID=$!
    for i in $(seq 1 10); do
        if curl -sf "${RTVI_SIM_HOST}/v1/ready" >/dev/null 2>&1; then
            print_status "ok" "RTVI VLM mock ready (PID=$RTVI_SIM_PID)"
            break
        fi
        sleep 1
    done
fi

cleanup_rtvi() {
    if [ -n "$RTVI_SIM_PID" ]; then
        kill "$RTVI_SIM_PID" 2>/dev/null || true
    fi
}
trap cleanup_rtvi EXIT

ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
ES_RULES_INDEX="ab-alert-realtime-rules"

PASSED=0
FAILED=0
RULE_ID=""

# Flush the rules index so previous test runs don't interfere
curl -s -X DELETE "${ES_HOST}/${ES_RULES_INDEX}" >/dev/null 2>&1 || true

# Helper: query rule from ES rules index by document id
es_get_rule() {
    local doc_id="$1"
    curl -s "${ES_HOST}/${ES_RULES_INDEX}/_doc/${doc_id}" 2>/dev/null || echo '{"found":false}'
}

# Helper: count docs in ES rules index
es_rules_count() {
    local resp
    resp=$(curl -s "${ES_HOST}/${ES_RULES_INDEX}/_count" 2>/dev/null || echo '{"count":-1}')
    echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',-1))" 2>/dev/null || echo "-1"
}

# Helper to make requests
do_request() {
    local method="$1" path="$2" data="${3:-}"
    local url="$AB_HOST$path"
    if [ -n "$data" ]; then
        curl -s -X "$method" "$url" -H "Content-Type: application/json" -d "$data" 2>/dev/null || echo '{"status":"curl_error"}'
    else
        curl -s -X "$method" "$url" 2>/dev/null || echo '{"status":"curl_error"}'
    fi
}

# Helper: query RTVI sim call log
rtvi_calls() {
    local method="${1:-}" path="${2:-}"
    local url="${RTVI_SIM_HOST}/v1/calls?"
    [ -n "$method" ] && url="${url}method=${method}&"
    [ -n "$path" ] && url="${url}path=${path}&"
    curl -s "$url" 2>/dev/null || echo '{"calls":[],"count":0}'
}

# Clear RTVI call log before each major test group
clear_rtvi_calls() {
    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/calls" >/dev/null 2>&1
}

# Clear RTVI faults
clear_rtvi_faults() {
    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/fault" >/dev/null 2>&1
}

# Set RTVI fault injection
set_rtvi_fault() {
    local endpoint="$1" status_code="$2" body="$3"
    curl -s -X PUT "${RTVI_SIM_HOST}/v1/fault" \
        -H "Content-Type: application/json" \
        -d "{\"endpoint\": \"$endpoint\", \"status_code\": $status_code, \"body\": $body}" \
        >/dev/null 2>&1
}

# ===========================================================================
# Test 1: POST /api/v1/realtime - Create alert rule + verify RTVI calls
# ===========================================================================
clear_rtvi_calls
print_status "wait" "Test 1: POST /api/v1/realtime (create alert rule)..."
RESPONSE=$(do_request "POST" "/api/v1/realtime" "{
    \"liveStreamUrl\": \"$RTSP_URL\",
    \"sensor_id\": \"test-sensor-realtime-001\",
    \"sensor_name\": \"Warehouse-Ladder-Cam\",
    \"description\": \"Warehouse ladder monitoring stream\",
    \"prompt\": \"Detect safety violations with ladder\",
    \"system_prompt\": \"Answer yes or no\",
    \"alert_type\": \"collision\",
    \"chunk_duration\": 60,
    \"chunk_overlap_duration\": 10,
    \"num_frames_per_second_or_fixed_frames_chunk\": 5,
    \"use_fps_for_chunking\": false,
    \"vlm_input_width\": 512,
    \"vlm_input_height\": 512,
    \"enable_reasoning\": false
}" || echo '{"status":"error"}')

STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "error")
RULE_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

if [ "$STATUS" = "success" ] && [ -n "$RULE_ID" ]; then
    print_status "ok" "PASS: Alert rule created (id=$RULE_ID)"
    ((PASSED++))
else
    print_status "fail" "FAIL: Could not create alert rule (status=$STATUS)"
    echo "  Response: $RESPONSE"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 1b: Verify RTVI received POST /v1/streams/add
# ---------------------------------------------------------------------------
print_status "wait" "Test 1b: Verify RTVI received POST /v1/streams/add..."
RTVI_STREAMS_ADD=$(rtvi_calls "POST" "streams/add")
STREAMS_ADD_COUNT=$(echo "$RTVI_STREAMS_ADD" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")
STREAMS_ADD_CHECK=$(echo "$RTVI_STREAMS_ADD" | python3 -c "
import sys,json
data = json.load(sys.stdin)
calls = data.get('calls',[])
if not calls:
    print('no_calls')
else:
    stream = calls[-1].get('body',{}).get('streams',[{}])[0]
    errors = []
    if stream.get('liveStreamUrl','') != '$RTSP_URL':
        errors.append('liveStreamUrl')
    if stream.get('id','') != 'test-sensor-realtime-001':
        errors.append('id')
    print('ok' if not errors else 'fail:' + ','.join(errors))
" 2>/dev/null || echo "error")

if [ "$STREAMS_ADD_CHECK" = "ok" ]; then
    print_status "ok" "PASS: RTVI received streams/add with correct URL and id"
    ((PASSED++))
else
    print_status "fail" "FAIL: RTVI streams/add check=$STREAMS_ADD_CHECK"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 1c: Verify RTVI received POST /v1/generate_captions
# ---------------------------------------------------------------------------
print_status "wait" "Test 1c: Verify RTVI received POST /v1/generate_captions..."
RTVI_CAPTIONS=$(rtvi_calls "POST" "generate_captions")
CAPTIONS_OK=$(echo "$RTVI_CAPTIONS" | python3 -c "
import sys,json
data = json.load(sys.stdin)
calls = data.get('calls',[])
if not calls:
    print('no_calls')
else:
    body = calls[-1].get('body',{})
    errors = []
    if body.get('prompt','') != 'Detect safety violations with ladder':
        errors.append('prompt')
    if body.get('system_prompt','') != 'Answer yes or no':
        errors.append('system_prompt')
    if body.get('alert_category','') != 'collision':
        errors.append('alert_category')
    if body.get('chunk_duration') != 60:
        errors.append('chunk_duration')
    if body.get('chunk_overlap_duration') != 10:
        errors.append('chunk_overlap_duration')
    if body.get('num_frames_per_second_or_fixed_frames_chunk') != 5:
        errors.append('num_frames')
    if body.get('use_fps_for_chunking') != False:
        errors.append('use_fps_for_chunking')
    if body.get('vlm_input_width') != 512:
        errors.append('vlm_input_width')
    if body.get('vlm_input_height') != 512:
        errors.append('vlm_input_height')
    if body.get('enable_reasoning') != False:
        errors.append('enable_reasoning')
    if not body.get('id',''):
        errors.append('id_empty')
    print('ok' if not errors else 'fail:' + ','.join(errors))
" 2>/dev/null || echo "error")

if [ "$CAPTIONS_OK" = "ok" ]; then
    print_status "ok" "PASS: RTVI received generate_captions with all fields"
    ((PASSED++))
else
    print_status "fail" "FAIL: RTVI generate_captions check=$CAPTIONS_OK"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 1d: Verify sensor-params forwarded in streams/add body
# ---------------------------------------------------------------------------
print_status "wait" "Test 1d: Verify sensor-params forwarded in streams/add..."
SENSOR_PARAMS_CHECK=$(echo "$RTVI_STREAMS_ADD" | python3 -c "
import sys,json
data = json.load(sys.stdin)
calls = data.get('calls',[])
if not calls:
    print('no_calls')
else:
    stream = calls[-1].get('body',{}).get('streams',[{}])[0]
    errors = []
    if 'description' not in stream:
        errors.append('description_missing')
    if stream.get('sensor_name','') != 'Warehouse-Ladder-Cam':
        errors.append('sensor_name')
    print('ok' if not errors else 'fail:' + ','.join(errors))
" 2>/dev/null || echo "error")

if [ "$SENSOR_PARAMS_CHECK" = "ok" ]; then
    print_status "ok" "PASS: Sensor-params (description, sensor_name) forwarded to RTVI"
    ((PASSED++))
else
    print_status "fail" "FAIL: sensor-params check=$SENSOR_PARAMS_CHECK"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 1d: Verify rule persisted in Elasticsearch
# ---------------------------------------------------------------------------
if [ -n "$RULE_ID" ]; then
    print_status "wait" "Test 1d: Verify rule persisted in ES index $ES_RULES_INDEX..."
    sleep 1
    ES_RULE=$(es_get_rule "$RULE_ID")
    ES_FOUND=$(echo "$ES_RULE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('found',False))" 2>/dev/null || echo "False")
    ES_STATUS=$(echo "$ES_RULE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('_source',{}).get('status',''))" 2>/dev/null || echo "")
    ES_STREAM_ID=$(echo "$ES_RULE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('_source',{}).get('rtvi_stream_id',''))" 2>/dev/null || echo "")

    if [ "$ES_FOUND" = "True" ] && [ "$ES_STATUS" = "active" ] && [ -n "$ES_STREAM_ID" ]; then
        print_status "ok" "PASS: Rule persisted in ES (status=active, rtvi_stream_id=$ES_STREAM_ID)"
        ((PASSED++))
    else
        print_status "fail" "FAIL: Rule not persisted correctly (found=$ES_FOUND status=$ES_STATUS stream_id=$ES_STREAM_ID)"
        ((FAILED++))
    fi
else
    print_status "fail" "SKIP: No rule_id to check ES persistence"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 1e: GET /api/v1/realtime/{alert_rule_id} - Get single rule by id
# ---------------------------------------------------------------------------
if [ -n "$RULE_ID" ]; then
    print_status "wait" "Test 1e: GET /api/v1/realtime/$RULE_ID (get by id)..."
    RESPONSE=$(do_request "GET" "/api/v1/realtime/${RULE_ID}" || echo '{"status":"error"}')
    GET_STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "error")
    GET_RULE_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rule',{}).get('id',''))" 2>/dev/null || echo "")
    GET_ALERT_TYPE=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rule',{}).get('alert_type',''))" 2>/dev/null || echo "")

    if [ "$GET_STATUS" = "success" ] && [ "$GET_RULE_ID" = "$RULE_ID" ] && [ "$GET_ALERT_TYPE" = "collision" ]; then
        print_status "ok" "PASS: GET by id returned correct rule"
        ((PASSED++))
    else
        print_status "fail" "FAIL: GET by id failed (status=$GET_STATUS rule_id=$GET_RULE_ID alert_type=$GET_ALERT_TYPE)"
        echo "  Response: $RESPONSE"
        ((FAILED++))
    fi
else
    print_status "fail" "SKIP: No rule_id to test GET by id"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 2: GET /api/v1/realtime - List active alert rules
# ---------------------------------------------------------------------------
print_status "wait" "Test 2: GET /api/v1/realtime (list rules)..."
RESPONSE=$(do_request "GET" "/api/v1/realtime" || echo '{"status":"error"}')

STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "error")
COUNT=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")

if [ "$STATUS" = "success" ] && [ "$COUNT" -ge 1 ]; then
    print_status "ok" "PASS: Listed rules (count=$COUNT)"
    ((PASSED++))
else
    print_status "fail" "FAIL: List rules failed (status=$STATUS, count=$COUNT)"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 3: Verify rule fields echoed back + rtvi_stream_id not leaked
# ---------------------------------------------------------------------------
if [ -n "$RULE_ID" ]; then
    print_status "wait" "Test 3: Verify rule fields echoed back and rtvi_stream_id not leaked..."
    RESPONSE=$(do_request "GET" "/api/v1/realtime" || echo '{"status":"error"}')
    FIELD_CHECK=$(echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
rules = data.get('rules', [])
rule = None
for r in rules:
    if r.get('id') == '$RULE_ID':
        rule = r
        break

if rule is None:
    print('not_found')
else:
    errors = []
    if rule.get('alert_type') != 'collision':
        errors.append('alert_type')
    if rule.get('prompt') != 'Detect safety violations with ladder':
        errors.append('prompt')
    if rule.get('system_prompt') != 'Answer yes or no':
        errors.append('system_prompt')
    if rule.get('chunk_duration') != 60:
        errors.append('chunk_duration')
    if rule.get('chunk_overlap_duration') != 10:
        errors.append('chunk_overlap_duration')
    if rule.get('num_frames_per_second_or_fixed_frames_chunk') != 5:
        errors.append('num_frames')
    if rule.get('use_fps_for_chunking') != False:
        errors.append('use_fps_for_chunking')
    if rule.get('vlm_input_width') != 512:
        errors.append('vlm_input_width')
    if rule.get('vlm_input_height') != 512:
        errors.append('vlm_input_height')
    if rule.get('enable_reasoning') != False:
        errors.append('enable_reasoning')
    if rule.get('live_stream_url') != '$RTSP_URL':
        errors.append('live_stream_url')
    if 'rtvi_stream_id' in rule:
        errors.append('rtvi_stream_id_LEAKED')
    if errors:
        print('fail:' + ','.join(errors))
    else:
        print('ok')
" 2>/dev/null || echo "error")

    if [ "$FIELD_CHECK" = "ok" ]; then
        print_status "ok" "PASS: All fields echoed correctly, rtvi_stream_id not leaked"
        ((PASSED++))
    else
        print_status "fail" "FAIL: Field check=$FIELD_CHECK"
        ((FAILED++))
    fi
else
    print_status "fail" "SKIP: No rule_id to verify"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 4: DELETE /api/v1/realtime/{alert_rule_id} - Delete rule
# ---------------------------------------------------------------------------
if [ -n "$RULE_ID" ]; then
    clear_rtvi_calls
    print_status "wait" "Test 4: DELETE /api/v1/realtime/{alert_rule_id} (delete rule)..."
    RESPONSE=$(do_request "DELETE" "/api/v1/realtime/${RULE_ID}" || echo '{"status":"error"}')
    
    STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "error")
    
    if [ "$STATUS" = "success" ]; then
        print_status "ok" "PASS: Rule deleted"
        ((PASSED++))
    else
        print_status "fail" "FAIL: Delete rule failed"
        ((FAILED++))
    fi
else
    print_status "fail" "SKIP: No rule_id to test DELETE"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 4b: Verify RTVI received both DELETE calls
# ---------------------------------------------------------------------------
print_status "wait" "Test 4b: Verify RTVI received DELETE captions + DELETE stream..."
sleep 1
RTVI_DELETES=$(rtvi_calls "DELETE" "")
DELETE_CHECK=$(echo "$RTVI_DELETES" | python3 -c "
import sys, json
data = json.load(sys.stdin)
calls = data.get('calls', [])
paths = [c['path'] for c in calls]
has_captions_delete = any('generate_captions/' in p for p in paths)
has_stream_delete = any('streams/delete/' in p for p in paths)
if has_captions_delete and has_stream_delete:
    print('ok')
else:
    missing = []
    if not has_captions_delete:
        missing.append('stop_captions_alerts')
    if not has_stream_delete:
        missing.append('stop_stream')
    print('missing:' + ','.join(missing))
" 2>/dev/null || echo "error")

if [ "$DELETE_CHECK" = "ok" ]; then
    print_status "ok" "PASS: RTVI received both DELETE calls"
    ((PASSED++))
else
    print_status "fail" "FAIL: RTVI DELETE check=$DELETE_CHECK"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 4c: Verify rule removed from ES after DELETE
# ---------------------------------------------------------------------------
if [ -n "$RULE_ID" ]; then
    print_status "wait" "Test 4c: Verify rule removed from ES index..."
    ES_RULE=$(es_get_rule "$RULE_ID")
    ES_FOUND=$(echo "$ES_RULE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('found',False))" 2>/dev/null || echo "True")

    if [ "$ES_FOUND" = "False" ]; then
        print_status "ok" "PASS: Rule removed from ES index"
        ((PASSED++))
    else
        print_status "fail" "FAIL: Rule still exists in ES after DELETE"
        ((FAILED++))
    fi
fi

# ---------------------------------------------------------------------------
# Test 4d: GET by id after DELETE returns 404
# ---------------------------------------------------------------------------
if [ -n "$RULE_ID" ]; then
    print_status "wait" "Test 4d: GET by id after DELETE (expect 404)..."
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$AB_HOST/api/v1/realtime/${RULE_ID}" 2>/dev/null || echo "0")

    if [ "$HTTP_CODE" = "404" ]; then
        print_status "ok" "PASS: 404 for deleted rule"
        ((PASSED++))
    else
        print_status "fail" "FAIL: Expected 404, got $HTTP_CODE"
        ((FAILED++))
    fi
fi

# ---------------------------------------------------------------------------
# Test 5: Verify rule removed from list
# ---------------------------------------------------------------------------
print_status "wait" "Test 5: Verify rule $RULE_ID removed..."
RESPONSE=$(do_request "GET" "/api/v1/realtime" || echo '{"status":"error"}')
FOUND=$(echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
rules = data.get('rules', [])
found = any(r.get('id') == '$RULE_ID' for r in rules)
print('yes' if found else 'no')
" 2>/dev/null || echo "error")

if [ "$FOUND" = "no" ]; then
    print_status "ok" "PASS: Rule $RULE_ID removed"
    ((PASSED++))
else
    print_status "fail" "FAIL: Rule $RULE_ID still exists"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 6: DELETE non-existent rule returns 422 (malformed UUID)
# ---------------------------------------------------------------------------
print_status "wait" "Test 6: DELETE malformed UUID (expect 422)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$AB_HOST/api/v1/realtime/not-a-uuid" 2>/dev/null || echo "0")

if [ "$HTTP_CODE" = "422" ]; then
    print_status "ok" "PASS: 422 for malformed UUID"
    ((PASSED++))
else
    print_status "fail" "FAIL: Expected 422, got $HTTP_CODE"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 6b: DELETE valid UUID that doesn't exist returns 404
# ---------------------------------------------------------------------------
print_status "wait" "Test 6b: DELETE non-existent valid UUID (expect 404)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$AB_HOST/api/v1/realtime/00000000-0000-0000-0000-000000000000" 2>/dev/null || echo "0")

if [ "$HTTP_CODE" = "404" ]; then
    print_status "ok" "PASS: 404 for non-existent rule"
    ((PASSED++))
else
    print_status "fail" "FAIL: Expected 404, got $HTTP_CODE"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 7: POST missing required fields returns 422
# ---------------------------------------------------------------------------
print_status "wait" "Test 7: POST missing required fields (expect 422)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$AB_HOST/api/v1/realtime" \
    -H "Content-Type: application/json" \
    -d '{"prompt": "test"}' 2>/dev/null || echo "0")

if [ "$HTTP_CODE" = "422" ]; then
    print_status "ok" "PASS: 422 for missing live_stream_url and alert_type"
    ((PASSED++))
else
    print_status "fail" "FAIL: Expected 422, got $HTTP_CODE"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 7b: POST invalid RTSP URL returns 422
# ---------------------------------------------------------------------------
print_status "wait" "Test 7b: POST invalid URL (http:// instead of rtsp://) (expect 422)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$AB_HOST/api/v1/realtime" \
    -H "Content-Type: application/json" \
    -d '{"live_stream_url": "http://not-rtsp.com/stream", "alert_type": "test", "prompt": "test"}' 2>/dev/null || echo "0")

if [ "$HTTP_CODE" = "422" ]; then
    print_status "ok" "PASS: 422 for non-RTSP URL"
    ((PASSED++))
else
    print_status "fail" "FAIL: Expected 422, got $HTTP_CODE"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 8: GET /api/v1/realtime/incidents
# ---------------------------------------------------------------------------
print_status "wait" "Test 8: GET /api/v1/realtime/incidents..."
RESPONSE=$(do_request "GET" "/api/v1/realtime/incidents" || echo '{"status":"error"}')
STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "error")

if [ "$STATUS" = "success" ]; then
    print_status "ok" "PASS: Incidents endpoint works"
    ((PASSED++))
else
    print_status "fail" "FAIL: Incidents endpoint failed"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 8b: GET /api/v1/realtime/incidents with invalid timestamp returns 422
# ---------------------------------------------------------------------------
print_status "wait" "Test 8b: GET incidents with invalid start_time (expect 422)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$AB_HOST/api/v1/realtime/incidents?start_time=yesterday" 2>/dev/null || echo "0")

if [ "$HTTP_CODE" = "422" ]; then
    print_status "ok" "PASS: 422 for invalid timestamp"
    ((PASSED++))
else
    print_status "fail" "FAIL: Expected 422, got $HTTP_CODE"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 9: Caption-start failure — RTVI rejects generate_captions_alerts
#         Expects: stream rolled back on RTVI, rule absent in AB
# ---------------------------------------------------------------------------
print_status "wait" "Test 9: Caption-start failure path (RTVI rejects generate_captions_alerts)..."
clear_rtvi_calls
clear_rtvi_faults
set_rtvi_fault "generate_captions" 500 '{"error":"injected: caption generation unavailable"}'

RESPONSE=$(do_request "POST" "/api/v1/realtime" "{
    \"liveStreamUrl\": \"$RTSP_URL\",
    \"sensor_id\": \"test-sensor-fault-001\",
    \"prompt\": \"Fault injection test\",
    \"alert_type\": \"fault_test\",
    \"model\": \"test-model\"
}" || echo '{"status":"error"}')

FAIL_STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")

if [ "$FAIL_STATUS" = "error" ]; then
    print_status "ok" "PASS: AB returned error when captions failed"
    ((PASSED++))
else
    print_status "fail" "FAIL: Expected error status, got=$FAIL_STATUS"
    echo "  Response: $RESPONSE"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 9b: Verify RTVI stream was rolled back (DELETE /v1/streams/delete called)
# ---------------------------------------------------------------------------
print_status "wait" "Test 9b: Verify RTVI stream rolled back after caption failure..."
sleep 1
RTVI_ROLLBACK=$(rtvi_calls "DELETE" "streams/delete")
ROLLBACK_COUNT=$(echo "$RTVI_ROLLBACK" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")

if [ "$ROLLBACK_COUNT" -ge 1 ]; then
    print_status "ok" "PASS: RTVI stream rolled back (delete called)"
    ((PASSED++))
else
    print_status "fail" "FAIL: RTVI stream NOT rolled back (delete count=$ROLLBACK_COUNT)"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 9c: Verify no rule left in AB after caption failure
# ---------------------------------------------------------------------------
print_status "wait" "Test 9c: Verify no fault_test rule in AB after failure..."
RESPONSE=$(do_request "GET" "/api/v1/realtime" || echo '{"status":"error"}')
FAULT_RULE_FOUND=$(echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
rules = data.get('rules', [])
found = any(r.get('alert_type') == 'fault_test' for r in rules)
print('yes' if found else 'no')
" 2>/dev/null || echo "error")

if [ "$FAULT_RULE_FOUND" = "no" ]; then
    print_status "ok" "PASS: No orphan rule in AB after caption failure"
    ((PASSED++))
else
    print_status "fail" "FAIL: Orphan fault_test rule found in AB"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 9d: Verify no orphan rule in ES after caption failure (rollback)
# ---------------------------------------------------------------------------
print_status "wait" "Test 9d: Verify no orphan fault_test rule in ES index..."
ES_FAULT_FOUND=$(curl -s "${ES_HOST}/${ES_RULES_INDEX}/_search" \
    -H "Content-Type: application/json" \
    -d '{"query":{"term":{"alert_type.keyword":"fault_test"}}}' 2>/dev/null \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
total = data.get('hits',{}).get('total',{})
count = total.get('value',0) if isinstance(total, dict) else total
print('yes' if count > 0 else 'no')
" 2>/dev/null || echo "error")

if [ "$ES_FAULT_FOUND" = "no" ]; then
    print_status "ok" "PASS: No orphan fault_test rule in ES (rollback successful)"
    ((PASSED++))
else
    print_status "fail" "FAIL: Orphan fault_test rule found in ES index (rollback failed)"
    ((FAILED++))
fi

clear_rtvi_faults

# ===========================================================================
# Test 10: Extended VLM params — REST path
#          POST with all 12 new params, verify GET echoes them, then DELETE
# ===========================================================================
EXT_RULE_ID=""
clear_rtvi_calls
print_status "wait" "Test 10: POST with extended VLM params (max_tokens, temperature, top_p, top_k, etc.)..."
RESPONSE=$(do_request "POST" "/api/v1/realtime" "{
    \"liveStreamUrl\": \"$RTSP_URL\",
    \"prompt\": \"Detect PPE violations\",
    \"system_prompt\": \"You are a safety monitor\",
    \"alert_type\": \"ppe\",
    \"model\": \"cosmos-reason1\",
    \"api_type\": \"internal\",
    \"response_format\": {\"type\": \"text\"},
    \"stream_options\": {\"include_usage\": true},
    \"max_tokens\": 512,
    \"temperature\": 0.2,
    \"top_p\": 0.95,
    \"top_k\": 50,
    \"ignore_eos\": true,
    \"seed\": 42,
    \"enable_audio\": false,
    \"mm_processor_kwargs\": {\"resize_mode\": \"fit\"}
}" || echo '{"status":"error"}')

EXT_STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "error")
EXT_RULE_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

if [ "$EXT_STATUS" = "success" ] && [ -n "$EXT_RULE_ID" ]; then
    print_status "ok" "PASS: Extended-param rule created (id=$EXT_RULE_ID)"
    ((PASSED++))
else
    print_status "fail" "FAIL: Could not create extended-param rule (status=$EXT_STATUS)"
    echo "  Response: $RESPONSE"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 10b: Verify RTVI received the extended params in generate_captions
# ---------------------------------------------------------------------------
print_status "wait" "Test 10b: Verify RTVI generate_captions carried extended params..."
RTVI_CAPTIONS_EXT=$(rtvi_calls "POST" "generate_captions")
EXT_PARAMS_OK=$(echo "$RTVI_CAPTIONS_EXT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
calls = data.get('calls', [])
if not calls:
    print('no_calls')
else:
    body = calls[-1].get('body', {})
    errors = []
    if body.get('max_tokens') != 512:
        errors.append('max_tokens')
    if body.get('temperature') != 0.2:
        errors.append('temperature')
    if body.get('top_p') != 0.95:
        errors.append('top_p')
    if body.get('top_k') != 50:
        errors.append('top_k')
    if body.get('ignore_eos') is not True:
        errors.append('ignore_eos')
    if body.get('seed') != 42:
        errors.append('seed')
    if body.get('enable_audio') is not False:
        errors.append('enable_audio')
    if body.get('mm_processor_kwargs', {}).get('resize_mode') != 'fit':
        errors.append('mm_processor_kwargs')
    if body.get('api_type') != 'internal':
        errors.append('api_type')
    print('fail:' + ','.join(errors) if errors else 'ok')
" 2>/dev/null || echo "error")

if [ "$EXT_PARAMS_OK" = "ok" ]; then
    print_status "ok" "PASS: RTVI received all extended params correctly"
    ((PASSED++))
else
    print_status "fail" "FAIL: Extended params check=$EXT_PARAMS_OK"
    echo "  RTVI response: $RTVI_CAPTIONS_EXT"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Test 10c: GET /api/v1/realtime echoes extended fields in rule response
# ---------------------------------------------------------------------------
if [ -n "$EXT_RULE_ID" ]; then
    print_status "wait" "Test 10c: GET rule echoes extended fields..."
    RESPONSE=$(do_request "GET" "/api/v1/realtime/${EXT_RULE_ID}" || echo '{"status":"error"}')
    GET_EXT_OK=$(echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
rule = data.get('rule', {})
errors = []
if rule.get('max_tokens') != 512:
    errors.append('max_tokens')
if rule.get('temperature') != 0.2:
    errors.append('temperature')
if rule.get('top_p') != 0.95:
    errors.append('top_p')
if rule.get('top_k') != 50:
    errors.append('top_k')
if rule.get('ignore_eos') is not True:
    errors.append('ignore_eos')
if rule.get('seed') != 42:
    errors.append('seed')
if rule.get('enable_audio') is not False:
    errors.append('enable_audio')
if rule.get('api_type') != 'internal':
    errors.append('api_type')
print('fail:' + ','.join(errors) if errors else 'ok')
" 2>/dev/null || echo "error")

    if [ "$GET_EXT_OK" = "ok" ]; then
        print_status "ok" "PASS: GET rule echoes all extended fields"
        ((PASSED++))
    else
        print_status "fail" "FAIL: GET rule extended-field check=$GET_EXT_OK"
        echo "  Response: $RESPONSE"
        ((FAILED++))
    fi
fi

# ---------------------------------------------------------------------------
# Test 10d: Verify extended fields absent from a rule that doesn't use them
# ---------------------------------------------------------------------------
print_status "wait" "Test 10d: Verify extended fields absent when not set..."
PLAIN_RULE_RESP=$(do_request "POST" "/api/v1/realtime" "{
    \"liveStreamUrl\": \"$RTSP_URL\",
    \"prompt\": \"Detect fire\",
    \"alert_type\": \"fire\",
    \"model\": \"cosmos-reason1\"
}" || echo '{"status":"error"}')
PLAIN_RULE_ID=$(echo "$PLAIN_RULE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

if [ -n "$PLAIN_RULE_ID" ]; then
    PLAIN_GET=$(do_request "GET" "/api/v1/realtime/${PLAIN_RULE_ID}" || echo '{"status":"error"}')
    ABSENT_OK=$(echo "$PLAIN_GET" | python3 -c "
import sys, json
rule = json.load(sys.stdin).get('rule', {})
leaked = [f for f in ['api_type','response_format','stream_options','max_tokens',
    'temperature','top_p','top_k','ignore_eos','seed','media_info',
    'enable_audio','mm_processor_kwargs'] if f in rule]
print('leaked:' + ','.join(leaked) if leaked else 'ok')
" 2>/dev/null || echo "error")

    if [ "$ABSENT_OK" = "ok" ]; then
        print_status "ok" "PASS: Extended fields correctly absent when not set"
        ((PASSED++))
    else
        print_status "fail" "FAIL: Extended fields leaked into plain rule: $ABSENT_OK"
        ((FAILED++))
    fi
    do_request "DELETE" "/api/v1/realtime/${PLAIN_RULE_ID}" >/dev/null
fi

# ---------------------------------------------------------------------------
# Test 10e: Validate out-of-range values rejected with 422
# ---------------------------------------------------------------------------
print_status "wait" "Test 10e: Out-of-range extended field values return 422..."
OOR_ALL_PASS=true
for field_val in "max_tokens:0" "temperature:-0.1" "top_p:1.5" "top_k:-1"; do
    field="${field_val%%:*}"
    val="${field_val##*:}"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$AB_HOST/api/v1/realtime" \
        -H "Content-Type: application/json" \
        -d "{\"live_stream_url\":\"$RTSP_URL\",\"alert_type\":\"fire\",\"prompt\":\"test\",\"${field}\":${val}}" \
        2>/dev/null || echo "0")
    if [ "$HTTP_CODE" != "422" ]; then
        print_status "fail" "  FAIL: $field=$val expected 422, got $HTTP_CODE"
        OOR_ALL_PASS=false
    fi
done
if [ "$OOR_ALL_PASS" = "true" ]; then
    print_status "ok" "PASS: Out-of-range values all rejected with 422"
    ((PASSED++))
else
    ((FAILED++))
fi

# Cleanup: delete the extended-param rule
if [ -n "$EXT_RULE_ID" ]; then
    do_request "DELETE" "/api/v1/realtime/${EXT_RULE_ID}" >/dev/null
fi

# ===========================================================================
# Test 11: Stream reuse via GET /v1/streams/get-stream-info
#          Two rules with the same sensor_id should share one RTVI stream:
#          rule A calls /streams/add, rule B sees the existing entry and
#          reuses it. Verifies the get-stream-info probe + dedupe logic.
# ===========================================================================
REUSE_SENSOR_ID="test-sensor-reuse-001"
REUSE_RULE_A=""
REUSE_RULE_B=""

clear_rtvi_calls
print_status "wait" "Test 11: Create rule A with sensor_id=$REUSE_SENSOR_ID..."
RESPONSE=$(do_request "POST" "/api/v1/realtime" "{
    \"liveStreamUrl\": \"$RTSP_URL\",
    \"sensor_id\": \"$REUSE_SENSOR_ID\",
    \"sensor_name\": \"Reuse-Test-Cam\",
    \"prompt\": \"Detect intrusion\",
    \"alert_type\": \"intrusion\",
    \"model\": \"test-model\"
}" || echo '{"status":"error"}')
REUSE_RULE_A=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

if [ -n "$REUSE_RULE_A" ]; then
    print_status "ok" "PASS: Rule A created (id=$REUSE_RULE_A)"
    ((PASSED++))
else
    print_status "fail" "FAIL: Rule A creation failed"
    echo "  Response: $RESPONSE"
    ((FAILED++))
fi

print_status "wait" "Test 11b: Create rule B with same sensor_id (should reuse)..."
RESPONSE=$(do_request "POST" "/api/v1/realtime" "{
    \"liveStreamUrl\": \"$RTSP_URL\",
    \"sensor_id\": \"$REUSE_SENSOR_ID\",
    \"sensor_name\": \"Reuse-Test-Cam\",
    \"prompt\": \"Detect fire\",
    \"alert_type\": \"fire\",
    \"model\": \"test-model\"
}" || echo '{"status":"error"}')
REUSE_RULE_B=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

if [ -n "$REUSE_RULE_B" ]; then
    print_status "ok" "PASS: Rule B created (id=$REUSE_RULE_B)"
    ((PASSED++))
else
    print_status "fail" "FAIL: Rule B creation failed"
    echo "  Response: $RESPONSE"
    ((FAILED++))
fi

# Verify only ONE /streams/add call hit RTVI even though we created TWO rules.
# The second rule's _resolve_or_add_stream sees the existing stream via
# get-stream-info and reuses its id without re-adding.
print_status "wait" "Test 11c: Verify only one /streams/add for two rules..."
ADD_COUNT=$(rtvi_calls "POST" "streams/add" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")

if [ "$ADD_COUNT" = "1" ]; then
    print_status "ok" "PASS: Only 1 /streams/add call (reuse worked)"
    ((PASSED++))
else
    print_status "fail" "FAIL: Expected 1 /streams/add, got $ADD_COUNT"
    ((FAILED++))
fi

# Verify get-stream-info was called (at least twice — once per rule).
print_status "wait" "Test 11d: Verify GET /v1/streams/get-stream-info was called..."
GET_INFO_COUNT=$(rtvi_calls "GET" "streams/get-stream-info" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")

if [ "$GET_INFO_COUNT" -ge 2 ]; then
    print_status "ok" "PASS: get-stream-info called $GET_INFO_COUNT time(s)"
    ((PASSED++))
else
    print_status "fail" "FAIL: Expected get-stream-info ≥ 2, got $GET_INFO_COUNT"
    ((FAILED++))
fi

# Verify rule B's ES doc is flagged owns_rtvi_stream=False (it reused, didn't add).
print_status "wait" "Test 11e: Verify rule B has owns_rtvi_stream=False in ES..."
sleep 1
RULE_B_ES=$(es_get_rule "$REUSE_RULE_B")
RULE_B_OWNS=$(echo "$RULE_B_ES" | python3 -c "
import sys, json
src = json.load(sys.stdin).get('_source', {})
print(src.get('owns_rtvi_stream', 'MISSING'))
" 2>/dev/null || echo "error")

if [ "$RULE_B_OWNS" = "False" ]; then
    print_status "ok" "PASS: Rule B owns_rtvi_stream=False (reuse path)"
    ((PASSED++))
else
    print_status "fail" "FAIL: Rule B owns_rtvi_stream=$RULE_B_OWNS (expected False)"
    ((FAILED++))
fi

# Verify rule A's ES doc is flagged owns_rtvi_stream=True (it added the stream).
print_status "wait" "Test 11f: Verify rule A has owns_rtvi_stream=True in ES..."
RULE_A_ES=$(es_get_rule "$REUSE_RULE_A")
RULE_A_OWNS=$(echo "$RULE_A_ES" | python3 -c "
import sys, json
src = json.load(sys.stdin).get('_source', {})
print(src.get('owns_rtvi_stream', 'MISSING'))
" 2>/dev/null || echo "error")

if [ "$RULE_A_OWNS" = "True" ]; then
    print_status "ok" "PASS: Rule A owns_rtvi_stream=True (owner)"
    ((PASSED++))
else
    print_status "fail" "FAIL: Rule A owns_rtvi_stream=$RULE_A_OWNS (expected True)"
    ((FAILED++))
fi

# ===========================================================================
# Test 12: Ref-count stop semantics
#          Deleting one of two siblings must NOT call /streams/delete; the
#          stream is only torn down when the *last* reader is removed.
# ===========================================================================
clear_rtvi_calls
print_status "wait" "Test 12: DELETE rule A while rule B still references the stream..."
RESPONSE=$(do_request "DELETE" "/api/v1/realtime/${REUSE_RULE_A}" || echo '{"status":"error"}')
DEL_STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "error")

if [ "$DEL_STATUS" = "success" ]; then
    print_status "ok" "PASS: Rule A deleted"
    ((PASSED++))
else
    print_status "fail" "FAIL: Delete rule A failed (status=$DEL_STATUS)"
    ((FAILED++))
fi

print_status "wait" "Test 12b: Verify /streams/delete NOT called (rule B still uses stream)..."
sleep 1
DELETE_STREAM_COUNT=$(rtvi_calls "DELETE" "streams/delete" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")

if [ "$DELETE_STREAM_COUNT" = "0" ]; then
    print_status "ok" "PASS: streams/delete NOT called — sibling protected"
    ((PASSED++))
else
    print_status "fail" "FAIL: Expected 0 streams/delete calls, got $DELETE_STREAM_COUNT"
    ((FAILED++))
fi

# Captions for rule A *should* have been stopped, even though the stream stays.
print_status "wait" "Test 12c: Verify generate_captions DELETE called (rule A's captions stopped)..."
DELETE_CAPTIONS_COUNT=$(rtvi_calls "DELETE" "generate_captions" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")

if [ "$DELETE_CAPTIONS_COUNT" -ge 1 ]; then
    print_status "ok" "PASS: stop_captions called for rule A"
    ((PASSED++))
else
    print_status "fail" "FAIL: Expected stop_captions ≥ 1, got $DELETE_CAPTIONS_COUNT"
    ((FAILED++))
fi

# Now delete rule B — last reader, so /streams/delete should fire.
clear_rtvi_calls
print_status "wait" "Test 12d: DELETE rule B (last reader) — expect streams/delete..."
RESPONSE=$(do_request "DELETE" "/api/v1/realtime/${REUSE_RULE_B}" || echo '{"status":"error"}')
DEL_STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "error")
sleep 1
DELETE_STREAM_COUNT=$(rtvi_calls "DELETE" "streams/delete" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")

if [ "$DEL_STATUS" = "success" ] && [ "$DELETE_STREAM_COUNT" -ge 1 ]; then
    print_status "ok" "PASS: Rule B deleted, streams/delete fired (last reader cleanup)"
    ((PASSED++))
else
    print_status "fail" "FAIL: del_status=$DEL_STATUS streams/delete count=$DELETE_STREAM_COUNT"
    ((FAILED++))
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
TOTAL=$((PASSED + FAILED))
if [ "$FAILED" -eq 0 ]; then
    print_status "ok" "RESULTS: $PASSED/$TOTAL passed — ALL TESTS PASSED"
    exit 0
else
    print_status "fail" "RESULTS: $PASSED/$TOTAL passed, $FAILED failed"
    exit 1
fi
