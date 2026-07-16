#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

######################################################################################################
# LVS Kafka sanity test for Helm / Kubernetes deployments.
#
# Adapted from compose/BlueprintBuilderGenerated/run_sanity_kafka.sh for a
# Helm-deployed LVS stack (dev-profile-lvs). Instead of bringing up Docker
# Compose, this script:
#   - Sets up kubectl port-forwards to internal K8s services (vss-summarization,
#     vss-rtvi-vlm, elasticsearch).
#   - Exercises the same end-to-end path: RTVI stream/add -> generate_captions
#     -> Kafka -> Logstash -> ES -> stream_summarize, plus file summarize via
#     /v1/summarize stream=true.
#
# Prerequisites:
#   - The LVS Helm release is installed and all pods are Running/Ready.
#   - kubectl (or microk8s kubectl) is configured and can reach the cluster.
#   - python3 and curl are on PATH.
#
# Usage:
#   bash eval/run_sanity_kafka_helm.sh                    # defaults
#   bash eval/run_sanity_kafka_helm.sh -h | --help
#   SANITY_FILE_URL=http://host/video.mp4 bash eval/run_sanity_kafka_helm.sh
#
# Environment knobs (all optional):
#   KUBE_NS                 Kubernetes namespace          (default: lvs)
#   KUBECTL                 kubectl binary                (default: auto-detect microk8s kubectl / kubectl)
#   LVS_LOCAL_PORT          local port for vss-summarization  (default: 38111)
#   RTVI_LOCAL_PORT         local port for vss-rtvi-vlm       (default: 18000)
#   ES_LOCAL_PORT           local port for elasticsearch      (default: 19200)
#   SANITY_FILE_URL         HTTP(S) MP4 for file summarize    (default: http://<your-media-host>:<port>/<sample>.mp4)
#   SANITY_RTSP_URL         RTSP source for live-stream test  (default: rtsp://<your-rtsp-server>:8554/<stream>)
#   SANITY_RAW_EVENTS_TIMEOUT  seconds to wait for raw_events (default: 600)
#   SANITY_MIN_RAW_EVENTS   minimum raw_events before step 7  (default: 3)
#   SANITY_FILE_TIMEOUT     seconds for file-summarize curl   (default: 900)
#   SANITY_FILE_KAFKA_TIMEOUT  seconds for file ES docs       (default: 120)
######################################################################################################
set -euo pipefail

show_help() {
    sed -n '2,/^######/{ /^#/s/^# \?//p }' "$0"
    exit 0
}
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && show_help

# -- config ------------------------------------------------------------------
KUBE_NS="${KUBE_NS:-lvs}"

if [ -z "${KUBECTL:-}" ]; then
    if command -v microk8s &>/dev/null && sudo microk8s kubectl version --client &>/dev/null 2>&1; then
        KUBECTL="sudo microk8s kubectl"
    else
        KUBECTL="kubectl"
    fi
fi

LVS_LOCAL_PORT="${LVS_LOCAL_PORT:-32000}"
RTVI_LOCAL_PORT="${RTVI_LOCAL_PORT:-18000}"
ES_LOCAL_PORT="${ES_LOCAL_PORT:-19200}"

LVS_URL="http://localhost:${LVS_LOCAL_PORT}"
RTVI_URL="http://localhost:${RTVI_LOCAL_PORT}"
ES_URL="http://localhost:${ES_LOCAL_PORT}"

SANITY_FILE_URL="${SANITY_FILE_URL:-http://<your-media-host>:<port>/<sample>.mp4}"
SANITY_RTSP_URL="${SANITY_RTSP_URL:-rtsp://<your-rtsp-server>:8554/<stream>}"
SANITY_RAW_EVENTS_TIMEOUT="${SANITY_RAW_EVENTS_TIMEOUT:-600}"
SANITY_MIN_RAW_EVENTS="${SANITY_MIN_RAW_EVENTS:-3}"
SANITY_FILE_TIMEOUT="${SANITY_FILE_TIMEOUT:-900}"
SANITY_FILE_KAFKA_TIMEOUT="${SANITY_FILE_KAFKA_TIMEOUT:-120}"

SCENARIO="warehouse safety monitoring"
EVENTS_LIST="box dropping, not wearing PPE, unsafe forklift operations, walking into restricted area, unauthorized personnel, forklift stuck, poor handling of hazardous materials, arson, theft, fire, normal activity"

# -- helpers -----------------------------------------------------------------
PASS=0; FAIL=0; TOTAL=0
pass() { PASS=$((PASS + 1)); TOTAL=$((TOTAL + 1)); echo "  PASS  $1"; }
fail() { FAIL=$((FAIL + 1)); TOTAL=$((TOTAL + 1)); echo "  FAIL  $1"; }
header() {
    echo
    echo "================================================================================"
    echo "  $1"
    echo "================================================================================"
}

wait_http() {
    local label="$1" url="$2" timeout="$3"
    local deadline=$(( $(date +%s) + timeout ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if curl -fsS -o /dev/null "$url" 2>/dev/null; then
            pass "${label} ready (${url})"
            return 0
        fi
        sleep 3
    done
    fail "${label} not ready within ${timeout}s (${url})"
    return 1
}

es_count_raw_events() {
    local index="$1"
    curl -fsS -X POST "${ES_URL}/${index}/_search" \
        -H 'Content-Type: application/json' \
        -d "{\"query\":{\"term\":{\"metadata.content_metadata.doc_type\":\"raw_events\"}},\"size\":0}" \
        2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['hits']['total']['value'])" 2>/dev/null \
        || echo 0
}

es_count_doc_type() {
    local index="$1" doc_type="$2"
    curl -fsS -X POST "${ES_URL}/${index}/_search" \
        -H 'Content-Type: application/json' \
        -d "{\"query\":{\"term\":{\"metadata.content_metadata.doc_type\":\"${doc_type}\"}},\"size\":0}" \
        2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['hits']['total']['value'])" 2>/dev/null \
        || echo 0
}

build_generate_captions_payload() {
    ASSET_ID="$ASSET_ID" MODEL_ID="$MODEL_ID" \
    SCENARIO="$SCENARIO" EVENTS_LIST="$EVENTS_LIST" python3 -c "
import json, os
print(json.dumps({
    'id': os.environ['ASSET_ID'],
    'model': os.environ['MODEL_ID'],
    'scenario': os.environ['SCENARIO'],
    'events': [e.strip() for e in os.environ['EVENTS_LIST'].split(',') if e.strip()],
    'chunk_duration': 10,
}))"
}

build_stream_summarize_payload() {
    local st="$1" et="$2"
    ASSET_ID="$ASSET_ID" MODEL_ID="$MODEL_ID" \
    ST="$st" ET="$et" python3 -c "
import json, os
payload = {
    'id': os.environ['ASSET_ID'],
    'model': os.environ['MODEL_ID'],
}
st = os.environ['ST']
et = os.environ['ET']
if st:
    payload['start_time'] = st
if et:
    payload['end_time'] = et
print(json.dumps(payload))"
}

offset_to_iso() {
    local offset="$1"
    python3 -c "
from datetime import datetime, timezone
import os
epoch = float(os.environ['CAPTIONS_START_EPOCH'])
dt = datetime.fromtimestamp(epoch + $offset, tz=timezone.utc)
print(dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond // 1000:03d}Z')
"
}

# -- port-forward management -------------------------------------------------
PF_PIDS=()
cleanup() {
    echo
    echo "  Cleaning up port-forwards..."
    for pid in "${PF_PIDS[@]}"; do
        kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done
}
trap 'cleanup; exit 130' INT TERM
trap cleanup EXIT

kill_port_holder() {
    local port="$1"
    local pids
    pids=$(ss -tlnp "sport = :${port}" 2>/dev/null \
        | grep -oP 'pid=\K[0-9]+' || true)
    if [ -n "$pids" ]; then
        echo "  [pf] killing stale process(es) on port ${port}: ${pids}"
        for p in $pids; do kill "$p" 2>/dev/null || true; done
        sleep 1
    fi
}

start_port_forward() {
    local svc="$1" local_port="$2" remote_port="$3" label="$4"
    kill_port_holder "$local_port"
    local logf="/tmp/pf_${label}.log"
    echo "  [pf] ${label}: localhost:${local_port} -> svc/${svc}:${remote_port} (-n ${KUBE_NS})"
    setsid ${KUBECTL} port-forward "svc/${svc}" "${local_port}:${remote_port}" \
        -n "${KUBE_NS}" </dev/null >"$logf" 2>&1 &
    PF_PIDS+=($!)
    sleep 2
    if ! kill -0 "${PF_PIDS[-1]}" 2>/dev/null; then
        echo "  [pf] ERROR: port-forward for ${label} died immediately:"
        cat "$logf" 2>/dev/null || true
        return 1
    fi
}

# -- banner ------------------------------------------------------------------
echo
echo "LVS Helm Kafka sanity test"
echo "  namespace:     ${KUBE_NS}"
echo "  kubectl:       ${KUBECTL}"
echo "  vss-summarization: ${LVS_URL} (port-forward :${LVS_LOCAL_PORT})"
echo "  vss-rtvi-vlm:      ${RTVI_URL} (port-forward :${RTVI_LOCAL_PORT})"
echo "  elasticsearch:     ${ES_URL} (port-forward :${ES_LOCAL_PORT})"
echo "  file URL:      ${SANITY_FILE_URL}"
echo "  RTSP URL:      ${SANITY_RTSP_URL}"

# -- 1. Verify pods are running ---------------------------------------------
header "1. Verify pods are Running in namespace ${KUBE_NS}"
NOT_READY=$(${KUBECTL} get pods -n "${KUBE_NS}" --no-headers 2>/dev/null \
    | grep -v -E 'Running|Completed' || true)
if [ -n "$NOT_READY" ]; then
    echo "  WARNING: some pods are not Running:"
    echo "$NOT_READY" | sed 's/^/    /'
    echo "  Continuing anyway (services may still respond)..."
else
    pass "all pods Running/Completed"
fi

# -- 2. Start port-forwards ------------------------------------------------
header "2. Start port-forwards"
start_port_forward "vss-summarization" "$LVS_LOCAL_PORT"  "38111" "vss-summarization"
start_port_forward "vss-rtvi-vlm"      "$RTVI_LOCAL_PORT" "8000"  "vss-rtvi-vlm"
start_port_forward "elasticsearch"     "$ES_LOCAL_PORT"   "9200"  "elasticsearch"
sleep 3

for label in vss-summarization vss-rtvi-vlm elasticsearch; do
    if [ -s "/tmp/pf_${label}.log" ]; then
        echo "  [pf] stderr from ${label}:"
        sed 's/^/    /' "/tmp/pf_${label}.log"
    fi
done

# -- 3. Wait for healthy services ------------------------------------------
header "3. Wait for healthy services"
wait_http "elasticsearch" "${ES_URL}/_cluster/health?wait_for_status=yellow&timeout=5s" 120 || exit 1
wait_http "vss-summarization" "${LVS_URL}/v1/ready" 300 || exit 1
wait_http "vss-rtvi-vlm"      "${RTVI_URL}/v1/health/ready" 300 || exit 1

# -- 4. Resolve VLM model --------------------------------------------------
header "4. Resolve VLM model"
MODEL_ID=$(curl -fsS "${LVS_URL}/models" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null || echo "")
if [ -n "$MODEL_ID" ]; then
    pass "vss-summarization /models -> ${MODEL_ID}"
else
    fail "vss-summarization /models returned no model"; exit 1
fi

# -- 5. Add a live stream on RTVI-VLM (NO inference) -----------------------
header "5. POST ${RTVI_URL}/v1/stream/add (camera_url=${SANITY_RTSP_URL}, NO inference)"

ADD_STREAM_PAYLOAD=$(SANITY_RTSP_URL="$SANITY_RTSP_URL" python3 -c "
import json, os
print(json.dumps({
    'key': 'sensor',
    'value': {
        'camera_id': '',
        'camera_url': os.environ['SANITY_RTSP_URL'],
        'change': 'camera_add',
    },
}))")

ADD_STREAM_RESP=$(curl -fsS -X POST "${RTVI_URL}/v1/stream/add" \
    -H 'Content-Type: application/json' \
    -d "$ADD_STREAM_PAYLOAD" 2>&1) || { fail "stream/add failed: ${ADD_STREAM_RESP}"; exit 1; }

ASSET_ID=$(printf '%s' "$ADD_STREAM_RESP" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('asset_id',''))" 2>/dev/null || echo "")
INFERENCE_FLAG=$(printf '%s' "$ADD_STREAM_RESP" \
    | python3 -c "import sys,json; print(str(json.load(sys.stdin).get('inference', False)).lower())" 2>/dev/null || echo "false")

if [ -z "$ASSET_ID" ]; then
    fail "stream/add returned no asset_id (response: ${ADD_STREAM_RESP})"; exit 1
fi
if [ "$INFERENCE_FLAG" = "true" ]; then
    fail "stream/add unexpectedly started inference (response: ${ADD_STREAM_RESP})"; exit 1
fi
pass "stream/add inference=${INFERENCE_FLAG} (asset_id=${ASSET_ID})"
INDEX_NAME="default_$(echo "$ASSET_ID" | tr '-' '_')"

# -- 6. POST /v1/generate_captions -- triggers RTVI captioning -------------
header "6. POST ${LVS_URL}/v1/generate_captions -- triggers RTVI captioning"
TRIGGER_PAYLOAD=$(build_generate_captions_payload)
TRIGGER_HTTP=$(curl -sS -o /tmp/sanity_trigger.json -w "%{http_code}" \
    -X POST "${LVS_URL}/v1/generate_captions" \
    -H 'Content-Type: application/json' \
    --max-time 120 \
    -d "$TRIGGER_PAYLOAD") || true
if [ "$TRIGGER_HTTP" = "200" ]; then
    CAPTIONS_START_EPOCH=$(python3 -c "import time; print(f'{time.time():.3f}')")
    export CAPTIONS_START_EPOCH
    TRIGGER_STATUS=$(python3 -c "
import json
b = json.load(open('/tmp/sanity_trigger.json'))
print(b.get('status', ''))
" 2>/dev/null || echo "")
    pass "/v1/generate_captions -> 200 (status=${TRIGGER_STATUS}), captions_start=${CAPTIONS_START_EPOCH}"
else
    fail "/v1/generate_captions -> HTTP ${TRIGGER_HTTP}"
    cat /tmp/sanity_trigger.json 2>/dev/null || true
    exit 1
fi

# -- 7. Wait for raw_events to land in ES ----------------------------------
header "7. Wait for >= ${SANITY_MIN_RAW_EVENTS} raw_events docs in ${INDEX_NAME}"
deadline=$(( $(date +%s) + SANITY_RAW_EVENTS_TIMEOUT ))
RAW=0
while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -fsS -o /dev/null "${ES_URL}/${INDEX_NAME}" 2>/dev/null; then
        curl -s -o /dev/null -X POST "${ES_URL}/${INDEX_NAME}/_refresh" 2>/dev/null
        RAW=$(es_count_raw_events "$INDEX_NAME")
        if [ "${RAW:-0}" -ge "$SANITY_MIN_RAW_EVENTS" ] 2>/dev/null; then break; fi
    fi
    sleep 3
done
if [ "${RAW:-0}" -ge "$SANITY_MIN_RAW_EVENTS" ] 2>/dev/null; then
    pass "found ${RAW} raw_events doc(s) (>= ${SANITY_MIN_RAW_EVENTS})"
else
    fail "only ${RAW} raw_events docs in ${INDEX_NAME} after ${SANITY_RAW_EVENTS_TIMEOUT}s (need >= ${SANITY_MIN_RAW_EVENTS})"
    echo "  hint: ${KUBECTL} logs -l app.kubernetes.io/name=logstash -n ${KUBE_NS} --tail=50"
    echo "  hint: ${KUBECTL} logs -l app.kubernetes.io/name=vss-rtvi-vlm -n ${KUBE_NS} --tail=30"
    exit 1
fi

# -- 8. POST /v1/stream_summarize (multiple windows) -----------------------
header "8. POST ${LVS_URL}/v1/stream_summarize (6 follow-up windows)"
sleep 20
OFFSET_WINDOWS=("0 15" "5 10" "30 50" "60 90" "300 450" "ALL")
NON_EMPTY_COUNT=0
for w in "${OFFSET_WINDOWS[@]}"; do
    if [ "$w" = "ALL" ]; then
        ST=""; ET=""
    else
        set -- $w
        ST=$(offset_to_iso "$1")
        ET=$(offset_to_iso "$2")
    fi
    SUMMARIZE_PAYLOAD=$(build_stream_summarize_payload "$ST" "$ET")
    echo
    echo "  -- window start_time=${ST:-all} end_time=${ET:-all}"
    SUM_HTTP=$(curl -sS -o /tmp/sanity_sum.json -w "%{http_code}" \
        -X POST "${LVS_URL}/v1/stream_summarize" \
        -H 'Content-Type: application/json' \
        --max-time 600 \
        -d "$SUMMARIZE_PAYLOAD") || true
    if [ "$SUM_HTTP" != "200" ]; then
        fail "stream_summarize (${ST},${ET}) -> HTTP ${SUM_HTTP}"
        cat /tmp/sanity_sum.json 2>/dev/null || true
        continue
    fi
    pass "stream_summarize (${ST},${ET}) -> 200"
    sleep 20
    python3 <<'PYEOF'
import json
try:
    b = json.load(open("/tmp/sanity_sum.json"))
except Exception as ex:
    print(f"  (could not parse response: {ex})")
    raise SystemExit
content = (b.get("choices") or [{}])[0].get("message", {}).get("content", "")
try:
    payload = json.loads(content)
except Exception as ex:
    print(f"  (could not parse content as JSON: {ex} | content[:200]={content[:200]!r})")
    raise SystemExit
total = payload.get("total_events", 0)
events = payload.get("events", []) or []
summary = payload.get("video_summary", "") or ""
print(f"  total_events: {total}   events_returned: {len(events)}")
print("  structured events:")
if not events:
    print("    (none)")
else:
    for line in json.dumps(events, indent=2, ensure_ascii=False).splitlines():
        print("    " + line)
print("  aggregated_summary:")
if not summary:
    print("    (empty)")
else:
    for line in summary.splitlines() or [summary]:
        print("    " + line)
PYEOF
    EVENTS_COUNT=$(python3 -c "
import json
b = json.load(open('/tmp/sanity_sum.json'))
c = (b.get('choices') or [{}])[0].get('message', {}).get('content', '')
try:
    print(len(json.loads(c).get('events', [])))
except Exception:
    print(0)
" 2>/dev/null || echo 0)
    if [ "${EVENTS_COUNT:-0}" -gt 0 ] 2>/dev/null; then
        NON_EMPTY_COUNT=$((NON_EMPTY_COUNT + 1))
    fi
    if [ -z "${ST}" ] && [ -z "${ET}" ]; then
        if [ "${EVENTS_COUNT:-0}" -gt 0 ] 2>/dev/null; then
            pass "(all) returned ${EVENTS_COUNT} event(s)"
        else
            fail "(all) expected >=1 event, got ${EVENTS_COUNT}"
        fi
    fi
done

echo
if [ "${NON_EMPTY_COUNT:-0}" -gt 0 ] 2>/dev/null; then
    pass "${NON_EMPTY_COUNT}/6 stream_summarize call(s) returned non-empty events list (>= 1 required)"
else
    fail "All 6 stream_summarize calls returned empty events lists."
fi

# -- 9. Verify structured_events + aggregated_summary in ES ----------------
header "9. Verify structured_events + aggregated_summary in ES"

deadline=$(( $(date +%s) + 60 ))
SE=0; AGG=0
while [ "$(date +%s)" -lt "$deadline" ]; do
    curl -s -o /dev/null -X POST "${ES_URL}/${INDEX_NAME}/_refresh" 2>/dev/null
    SE=$(es_count_doc_type "$INDEX_NAME" structured_events)
    AGG=$(es_count_doc_type "$INDEX_NAME" aggregated_summary)
    if [ "${SE:-0}" -gt 0 ] && [ "${AGG:-0}" -gt 0 ] 2>/dev/null; then break; fi
    sleep 2
done

if [ "${SE:-0}" -gt 0 ] 2>/dev/null; then
    pass "found ${SE} structured_events doc(s) in ${INDEX_NAME}"
else
    fail "no structured_events docs in ${INDEX_NAME} after 60s"
    echo "  hint: ${KUBECTL} logs -l app.kubernetes.io/name=vss-summarization -n ${KUBE_NS} --tail=50"
fi

if [ "${AGG:-0}" -gt 0 ] 2>/dev/null; then
    pass "found ${AGG} aggregated_summary doc(s) in ${INDEX_NAME}"
else
    fail "no aggregated_summary docs in ${INDEX_NAME} after 60s"
    echo "  hint: ${KUBECTL} logs -l app.kubernetes.io/name=vss-summarization -n ${KUBE_NS} --tail=50"
fi

if [ "${AGG:-0}" -gt 0 ] 2>/dev/null; then
    echo
    echo "  -- sample aggregated_summary doc (excluding vector field) --"
    set +e
    AGG_SAMPLE=$(curl -fsS -X POST "${ES_URL}/${INDEX_NAME}/_search" \
        -H 'Content-Type: application/json' \
        -d '{"query":{"term":{"metadata.content_metadata.doc_type":"aggregated_summary"}},"size":1,"_source":{"excludes":["vector"]}}' \
        2>/dev/null)
    if [ -n "$AGG_SAMPLE" ]; then
        printf '%s' "$AGG_SAMPLE" | python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
except Exception as ex:
    print(f'    (could not parse ES response: {ex})')
    raise SystemExit(0)
hits = r.get('hits', {}).get('hits', [])
if not hits:
    print('    (no hits)')
else:
    for line in json.dumps(hits[0]['_source'], indent=2, ensure_ascii=False).splitlines():
        print('    ' + line)
" || echo "    (pretty-printer failed)"
    else
        echo "    (no response from ES)"
    fi
    set -e
fi

# -- 10. File summarize via /v1/summarize stream=true ----------------------
header "10. POST ${LVS_URL}/v1/summarize stream=true (URL: ${SANITY_FILE_URL})"

FILE_SUMMARIZE_PAYLOAD=$(SANITY_FILE_URL="$SANITY_FILE_URL" MODEL_ID="$MODEL_ID" \
    SCENARIO="$SCENARIO" EVENTS_LIST="$EVENTS_LIST" python3 -c "
import json, os
print(json.dumps({
    'url': os.environ['SANITY_FILE_URL'],
    'model': os.environ['MODEL_ID'],
    'stream': True,
    'summarize': True,
    'scenario': os.environ['SCENARIO'],
    'events': [e.strip() for e in os.environ['EVENTS_LIST'].split(',') if e.strip()],
    'chunk_duration': 15,
}))")

curl -sS -N -X POST "${LVS_URL}/v1/summarize" \
    -H 'Content-Type: application/json' \
    -H 'Accept: text/event-stream' \
    --max-time "$SANITY_FILE_TIMEOUT" \
    -w "\n__HTTP_CODE__%{http_code}" \
    -d "$FILE_SUMMARIZE_PAYLOAD" \
    > /tmp/sanity_file_sum_sse.txt 2>&1 || true

FILE_SUM_HTTP=$(grep -oE '__HTTP_CODE__[0-9]+' /tmp/sanity_file_sum_sse.txt 2>/dev/null \
    | tail -1 | sed 's/__HTTP_CODE__//' || echo "")

if [ "$FILE_SUM_HTTP" != "200" ]; then
    fail "file /v1/summarize stream=true -> HTTP ${FILE_SUM_HTTP}"
    head -50 /tmp/sanity_file_sum_sse.txt || true
else
    pass "file /v1/summarize stream=true -> 200 (SSE response)"
fi

python3 - <<'PYEOF' > /tmp/sanity_file_sum_final.json
import json, sys
last = None
try:
    with open('/tmp/sanity_file_sum_sse.txt') as fh:
        for raw in fh:
            line = raw.rstrip('\r\n')
            if not line.startswith('data:'):
                continue
            payload = line[len('data:'):].strip()
            if not payload or payload == '[DONE]':
                continue
            if payload.startswith('__HTTP_CODE__'):
                continue
            try:
                last = json.loads(payload)
            except Exception:
                continue
except FileNotFoundError:
    pass
if last is None:
    json.dump({}, sys.stdout)
else:
    json.dump(last, sys.stdout)
PYEOF

FILE_ID=$(python3 -c "
import json, sys
try:
    b = json.load(open('/tmp/sanity_file_sum_final.json'))
except Exception:
    sys.exit(0)
c = (b.get('choices') or [{}])[0].get('message', {}).get('content', '')
try:
    p = json.loads(c)
except Exception:
    p = {}
print(p.get('uuid', '') or b.get('video_id', '') or b.get('id', ''))
" 2>/dev/null || echo "")

if [ -n "$FILE_ID" ]; then
    pass "file_id resolved -> ${FILE_ID}"
else
    fail "could not resolve file_id from final SSE event"
    cat /tmp/sanity_file_sum_final.json 2>/dev/null || true
fi

echo
echo "  -- final aggregator event (events + video_summary) --"
python3 <<'PYEOF'
import json
try:
    b = json.load(open('/tmp/sanity_file_sum_final.json'))
except Exception as ex:
    print(f"    (could not parse final SSE event: {ex})")
    raise SystemExit
content = (b.get('choices') or [{}])[0].get('message', {}).get('content', '')
try:
    payload = json.loads(content)
except Exception as ex:
    print(f"    (final event content is not JSON: {ex} | content[:200]={content[:200]!r})")
    raise SystemExit
events = payload.get('events') or []
summary = payload.get('video_summary') or ''
total = payload.get('total_events', len(events))
print(f"    total_events:    {total}")
print(f"    events_returned: {len(events)}")
print()
print("    structured_events:")
if not events:
    print("      (none)")
else:
    for line in json.dumps(events, indent=2, ensure_ascii=False).splitlines():
        print("      " + line)
print()
print("    aggregated_summary:")
if not summary:
    print("      (empty)")
else:
    for line in summary.splitlines() or [summary]:
        print("      " + line)
PYEOF

FILE_EVENTS_COUNT=$(python3 -c "
import json
b = json.load(open('/tmp/sanity_file_sum_final.json'))
c = (b.get('choices') or [{}])[0].get('message', {}).get('content', '')
try:
    print(len(json.loads(c).get('events', []) or []))
except Exception:
    print(0)
" 2>/dev/null || echo 0)

FILE_HAS_SUMMARY=$(python3 -c "
import json
b = json.load(open('/tmp/sanity_file_sum_final.json'))
c = (b.get('choices') or [{}])[0].get('message', {}).get('content', '')
try:
    s = (json.loads(c).get('video_summary', '') or '').strip()
except Exception:
    s = ''
print('1' if s else '0')
" 2>/dev/null || echo 0)

if [ "${FILE_EVENTS_COUNT:-0}" -gt 0 ] 2>/dev/null; then
    pass "file summarize returned ${FILE_EVENTS_COUNT} structured event(s)"
else
    fail "file summarize returned 0 events (expected >= 1)"
fi

if [ "${FILE_HAS_SUMMARY}" = "1" ]; then
    pass "file summarize returned non-empty video_summary"
else
    fail "file summarize returned empty video_summary"
fi

if [ -n "$FILE_ID" ]; then
    FILE_INDEX="default_$(echo "$FILE_ID" | tr '-' '_')"
    deadline=$(( $(date +%s) + SANITY_FILE_KAFKA_TIMEOUT ))
    F_RAW=0; F_SE=0; F_AGG=0
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if curl -fsS -o /dev/null "${ES_URL}/${FILE_INDEX}" 2>/dev/null; then
            curl -s -o /dev/null -X POST "${ES_URL}/${FILE_INDEX}/_refresh" 2>/dev/null
            F_RAW=$(es_count_raw_events "$FILE_INDEX")
            F_SE=$(es_count_doc_type "$FILE_INDEX" structured_events)
            F_AGG=$(es_count_doc_type "$FILE_INDEX" aggregated_summary)
            if [ "${F_RAW:-0}" -gt 0 ] && [ "${F_SE:-0}" -gt 0 ] && [ "${F_AGG:-0}" -gt 0 ] 2>/dev/null; then
                break
            fi
        fi
        sleep 2
    done

    if [ "${F_RAW:-0}" -gt 0 ] 2>/dev/null; then
        pass "file path: found ${F_RAW} raw_events doc(s) in ${FILE_INDEX} (RTVI -> Kafka -> Logstash)"
    else
        fail "file path: no raw_events docs in ${FILE_INDEX} after ${SANITY_FILE_KAFKA_TIMEOUT}s"
        echo "  hint: ${KUBECTL} logs -l app.kubernetes.io/name=logstash -n ${KUBE_NS} --tail=50"
        echo "  hint: ${KUBECTL} logs -l app.kubernetes.io/name=vss-rtvi-vlm -n ${KUBE_NS} --tail=30"
    fi

    if [ "${F_SE:-0}" -gt 0 ] 2>/dev/null; then
        pass "file path: found ${F_SE} structured_events doc(s) in ${FILE_INDEX} (LVS -> Kafka)"
    else
        fail "file path: no structured_events docs in ${FILE_INDEX} after ${SANITY_FILE_KAFKA_TIMEOUT}s"
        echo "  hint: ${KUBECTL} logs -l app.kubernetes.io/name=vss-summarization -n ${KUBE_NS} --tail=30"
    fi

    if [ "${F_AGG:-0}" -gt 0 ] 2>/dev/null; then
        pass "file path: found ${F_AGG} aggregated_summary doc(s) in ${FILE_INDEX}"
    else
        fail "file path: no aggregated_summary docs in ${FILE_INDEX} after ${SANITY_FILE_KAFKA_TIMEOUT}s"
    fi

    if [ "${F_RAW:-0}" -gt 0 ] 2>/dev/null; then
        echo
        echo "  -- sample raw_events doc (excluding vector field) --"
        set +e
        RAW_SAMPLE=$(curl -fsS -X POST "${ES_URL}/${FILE_INDEX}/_search" \
            -H 'Content-Type: application/json' \
            -d '{"query":{"term":{"metadata.content_metadata.doc_type":"raw_events"}},"size":1,"_source":{"excludes":["vector"]}}' \
            2>/dev/null)
        if [ -n "$RAW_SAMPLE" ]; then
            printf '%s' "$RAW_SAMPLE" | python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
except Exception as ex:
    print(f'    (could not parse ES response: {ex})')
    raise SystemExit(0)
hits = r.get('hits', {}).get('hits', [])
if not hits:
    print('    (no hits)')
else:
    for line in json.dumps(hits[0]['_source'], indent=2, ensure_ascii=False).splitlines():
        print('    ' + line)
" || echo "    (pretty-printer failed)"
        else
            echo "    (no response from ES)"
        fi
        set -e
    fi
fi

# -- summary ---------------------------------------------------------------
header "Summary"
echo
echo "  Total:  ${TOTAL}"
echo "  Passed: ${PASS}"
echo "  Failed: ${FAIL}"
echo
if [ "$FAIL" -gt 0 ]; then
    echo "  SOME TESTS FAILED"
    exit 1
fi
echo "  ALL TESTS PASSED"
