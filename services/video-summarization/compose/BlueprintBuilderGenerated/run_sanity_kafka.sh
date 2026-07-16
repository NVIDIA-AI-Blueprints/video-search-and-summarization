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
# Live-stream Kafka sanity test (LVS-driven trigger flow).
#
# Brings up the BlueprintBuilderGenerated compose stack with the `rtvi` and
# `kafka` profiles plus the streaming env knobs (KAFKA_ENABLED=true,
# LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=true; USE_RTVI_VLM and
# RTVI_VLM_URL_PASSTHROUGH are now deprecated and hard-coded to true on
# the LVS side) and exercises:
#
#   1. POST {RTVI}/v1/stream/add  with camera_id="" and NO metadata.prompt
#      -> asset registered, NO captioning yet. Verify response.inference is
#      not true.
#   2. POST {LVS}/v1/generate_captions with scenario, events,
#      chunk_duration:10. LVS fires start_captions on RTVI
#      (fire-and-forget; the caption prompt is derived from
#      scenario+events). Returns 200 immediately.
#   3. Wait + poll ES until raw_events docs land in default_<asset_id>.
#   4. POST {LVS}/v1/stream_summarize with multiple (start_time, end_time)
#      windows using ISO 8601 timestamps computed from the captioning
#      start time (sync CompletionResponse with events / video_summary
#      JSON-stringified inside choices[0].message.content):
#          (+0s, +15s)    - early-window events
#          (+5s, +10s)    - narrow-window events
#          (+30s, +50s)   - mid-window events
#          (+60s, +90s)   - past-end-of-clip (expect 0 events / 200)
#          (+300s, +450s) - well past end (expect 0 events / 200)
#          ("", "")       - all events (expect >=1 event)
#      Asserts that >= 1 of these 6 follow-up windows returns a non-empty
#      events list (validates end-to-end captioning + Kafka + Logstash +
#      ES + ctx-rag aggregator).
#   5. Verify structured_events + aggregated_summary docs in ES.
#   9. POST {LVS}/v1/summarize stream=true against the
#      2-minute media-server mp4 (URL passthrough). stream=true is the
#      design intent for the file path under Kafka mode -- LVS replies
#      with SSE EventSourceResponse end-to-end (matching the SSE-only
#      RTVI -> LVS captioning leg). The script does NOT iterate every
#      progress event; it captures only the FINAL data: line before
#      [DONE] (which carries the full aggregated events + video_summary
#      payload) and asserts events + video_summary are non-empty AND
#      that raw_events + structured_events + aggregated_summary docs
#      land in default_<file_id> via Kafka -> Logstash. The 2-minute
#      clip + chunk_duration=15 yields ~8 chunks (matches run_sanity.sh).
#
# Usage:
#   bash run_sanity_kafka.sh                  # uses .env from this directory
#   bash run_sanity_kafka.sh /path/to/.env    # use a custom .env
#   bash run_sanity_kafka.sh -h | --help      # show this help
######################################################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$SCRIPT_DIR"

show_help() {
    cat <<'HELPEOF'

  Live-stream Kafka sanity test
  =============================

  Drives the streaming LVS -> RTVI -> Kafka -> Logstash -> ES path end-to-end:
    1. POST RTVI /v1/stream/add with camera_id="" and NO metadata.prompt.
       asset.sensor_name="" so info[streamId] carries the asset UUID
       (Logstash routes raw_events to default_<asset_id>). RTVI does NOT
       start captioning yet.
    2. POST LVS /v1/generate_captions to trigger VLM captioning on RTVI
       (fire-and-forget). Returns 200 immediately.
    3. wait for Logstash to index raw_events in ES default_<asset_id>
    4. POST LVS /v1/stream_summarize with multiple ISO 8601
       (start_time, end_time) windows. Assert >= 1 of the 6 follow-up
       windows returns non-empty events.
    5. verify structured_events + aggregated_summary docs in ES
    9. file-summarize via /v1/summarize stream=true
       (URL passthrough against the in-stack media-server 2-min mp4).
       LVS replies with SSE; the script captures only the final
       data: event before [DONE] and verifies the SAME three doc_types
       appear in default_<file_id> -- proves the file path under
       summarization.kafka_enabled=true mirrors the live-stream Kafka
       path end-to-end with an event-driven user response.

  USAGE
    bash run_sanity_kafka.sh                  # uses .env in same directory
    bash run_sanity_kafka.sh /path/to/.env    # custom .env
    bash run_sanity_kafka.sh -h | --help      # this help

  REQUIRED .env VARIABLES
    NGC_API_KEY, NVIDIA_API_KEY, OPENAI_API_KEY (when VLM uses openai-compat)
    ARTIFACTORY_USER, ARTIFACTORY_TOKEN, HF_TOKEN
    RTVI_VLM_IMAGE, LVS_VLM_HOST=rtvi-vlm, LVS_VLM_PORT=8000

  FORCED OVERRIDES (this script sets them every run)
    KAFKA_ENABLED=true
    KAFKA_BOOTSTRAP_SERVERS=kafka:9092
    KAFKA_TOPIC=mdx-vlm-captions
    KAFKA_STRUCTURED_SUMMARY_TOPIC=mdx-structured-events-summary
    LVS_DATABASE_BACKEND=elasticsearch_db
    LVS_EMB_ENABLE=false
    LVS_EMB_DIMENSIONS=1024
    LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=true   # so sequential aggregates
                                                # see consistent ES state

  PROFILE ACTIVATION
    Every `docker compose` call in this script passes
    `${RTVI_PROFILE_FLAG} --profile kafka` explicitly. RTVI_PROFILE_FLAG
    resolves to:
      * `--profile rtvi`  when RTVI_VLM_URL is unset -> the in-stack
                          rtvi-vlm container starts.
      * `` (empty)        when RTVI_VLM_URL is set in the shell env or
                          .env -> the in-stack container is skipped and
                          LVS reaches the external RTVI VLM directly.
    The kafka profile is always on (this is the Kafka sanity script).

  ENV KNOBS THIS SCRIPT READS
    LVS_BACKEND_PORT       host port for via-engine API   (default: 38111)
    RTVI_VLM_PORT          host port for rtvi-vlm API     (default: 8000)
    SANITY_RAW_EVENTS_TIMEOUT  seconds to wait for raw_events (default: 600)
    SANITY_MIN_RAW_EVENTS  minimum raw_events docs before step 7 (default: 3)
    SANITY_RTSP_URL        RTSP source RTVI will pull frames from
                           (default: rtsp://<your-rtsp-server>:8554/<stream>)
    SANITY_FILE_URL        HTTP(S) MP4 URL used by step 9 file-summarize
                           (default: http://media-server/2min.mp4 — the
                           same in-stack media-server URL run_sanity.sh
                           uses; resolved by docker DNS so no auth /
                           artifactory hit at request time)
    SANITY_FILE_TIMEOUT    seconds to wait for file-summarize HTTP response
                           (default: 900)
    SANITY_FILE_KAFKA_TIMEOUT  seconds to wait for raw/structured/aggregated
                           docs in default_<file_id> after file summarize
                           (default: 60)
    KEEP_STACK             "1" to leave stack up after tests (default: 0)

HELPEOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    show_help
    exit 0
fi

# -- .env --------------------------------------------------------------------
# Default to the BlueprintBuilderGenerated/.env that ships next to this
# script. SCRIPT_DIR is resolved from BASH_SOURCE so the default works
# regardless of where the operator invokes the script from. A positional
# arg overrides.
DEFAULT_ENV_FILE="${SCRIPT_DIR}/.env"
ENV_FILE="${1:-${DEFAULT_ENV_FILE}}"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env not found at $ENV_FILE (run with --help)"
    echo "       (default location: ${DEFAULT_ENV_FILE})"
    exit 1
fi

parse_env_var() {
    local var_name="$1" default="$2" val
    val=$(grep -E "^${var_name}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d'=' -f2-)
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    echo "${val:-$default}"
}

LVS_BACKEND_PORT=$(parse_env_var "LVS_BACKEND_PORT" "38111")
RTVI_VLM_PORT=$(parse_env_var "RTVI_VLM_PORT" "8000")

# RTVI_VLM_URL signals "point LVS at an EXTERNAL RTVI VLM" -- when it's
# set (in shell env OR uncommented in .env), the in-stack rtvi-vlm
# container is unnecessary, so we drop `--profile rtvi` from every
# compose call below and skip the in-stack rtvi-vlm /v1/health/ready
# probe. Shell env wins so an operator can override on the fly:
#   RTVI_VLM_URL=http://<rtvi-host>:<port> bash run_sanity_kafka.sh
RTVI_VLM_URL="${RTVI_VLM_URL:-$(parse_env_var "RTVI_VLM_URL" "")}"
if [ -n "$RTVI_VLM_URL" ]; then
    RTVI_PROFILE_FLAG=""
else
    RTVI_PROFILE_FLAG="--profile rtvi --profile media"
fi

# Activate `media` profile (media-server.yaml).
if [ -z "${COMPOSE_PROFILES:-}" ]; then
    export COMPOSE_PROFILES="media"
elif [[ ",${COMPOSE_PROFILES}," != *",media,"* ]]; then
    export COMPOSE_PROFILES="${COMPOSE_PROFILES},media"
fi

LVS_URL="http://localhost:${LVS_BACKEND_PORT}"
RTVI_URL="http://localhost:${RTVI_VLM_PORT}"
ES_URL="http://localhost:9200"
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.yml"
SANITY_RAW_EVENTS_TIMEOUT="${SANITY_RAW_EVENTS_TIMEOUT:-600}"
SANITY_MIN_RAW_EVENTS="${SANITY_MIN_RAW_EVENTS:-3}"
SANITY_FILE_URL="${SANITY_FILE_URL:-http://media-server/2min.mp4}"
SANITY_FILE_TIMEOUT="${SANITY_FILE_TIMEOUT:-900}"
SANITY_FILE_KAFKA_TIMEOUT="${SANITY_FILE_KAFKA_TIMEOUT:-60}"

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

compose() {
    # USE_RTVI_VLM and RTVI_VLM_URL_PASSTHROUGH are deprecated: the LVS
    # server now hard-codes both to true, so they no longer need to be
    # exported here. KAFKA_ENABLED and the Kafka topic / DB knobs stay
    # because the compose stack reads them at container start.
    #
    # Profile activation is driven entirely by the explicit `--profile`
    # flags every call site below passes (`${RTVI_PROFILE_FLAG}
    # --profile kafka`). RTVI_PROFILE_FLAG is empty when RTVI_VLM_URL
    # points at an external RTVI VLM, so the in-stack rtvi-vlm
    # container is skipped. Keeping profile activation on the CLI side
    # makes the script self-contained and immune to operator overrides
    # in .env.
    KAFKA_ENABLED=true \
    KAFKA_BOOTSTRAP_SERVERS=kafka:9092 \
    KAFKA_TOPIC=mdx-vlm-captions \
    KAFKA_STRUCTURED_SUMMARY_TOPIC=mdx-structured-events-summary \
    LVS_DATABASE_BACKEND=elasticsearch_db \
    LVS_EMB_ENABLE=false \
    LVS_EMB_DIMENSIONS=1024 \
    LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=true \
    VIA_DEV_API=true \
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
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
    # Note: doc_type is mapped as `keyword` directly by the visionllm index
    # template, so do NOT append `.keyword` (no auto-generated sub-field
    # exists for an explicit keyword mapping; the term query would silently
    # match zero docs).
    local index="$1"
    curl -fsS -X POST "${ES_URL}/${index}/_search" \
        -H 'Content-Type: application/json' \
        -d "{\"query\":{\"term\":{\"metadata.content_metadata.doc_type\":\"raw_events\"}},\"size\":0}" \
        2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['hits']['total']['value'])" 2>/dev/null \
        || echo 0
}

# -- teardown ---------------------------------------------------------------
STACK_BROUGHT_UP=0
teardown() {
    if [ "${STACK_BROUGHT_UP}" != "1" ]; then return 0; fi
    if [ "${KEEP_STACK:-0}" = "1" ]; then
        echo; echo "  KEEP_STACK=1 - leaving stack up. Tear down manually with:"
        echo "    cd ${COMPOSE_DIR} && docker compose -f docker-compose.yml ${RTVI_PROFILE_FLAG} --profile kafka down -v --remove-orphans"
        return 0
    fi
    echo; echo "  Tearing stack down..."
    compose ${RTVI_PROFILE_FLAG} --profile kafka down -v --remove-orphans 2>&1 | grep -v "^time=" || true
}
trap 'teardown' EXIT

# -- banner -----------------------------------------------------------------
echo
echo "Live-stream + file Kafka sanity test"
echo "  .env file:     ${ENV_FILE}"
echo "  compose file:  ${COMPOSE_FILE}"
echo "  via-engine:    ${LVS_URL}"
echo "  rtvi-vlm:      ${RTVI_URL} (RTVI_VLM_URL=${RTVI_VLM_URL:-<unset, in-stack via --profile rtvi>})"
echo "  elasticsearch: ${ES_URL}"

command -v docker >/dev/null || { echo "  docker not found on PATH"; exit 1; }

# Pre-flight: ensure the shared media volume exists -------------------------
# media-server (and other services in the compose stack) mount the external
# `via-media-data` Docker volume. `docker compose up` aborts immediately
# with `external volume "via-media-data" not found` if it is missing, so
# create it on demand here -- mirrors run_sanity.sh's pre-flight check.
if docker volume inspect via-media-data &>/dev/null; then
    echo "  [ok] Docker volume 'via-media-data' exists"
else
    echo "  [..] Creating Docker volume 'via-media-data'..."
    docker volume create via-media-data
    echo "  [ok] Created 'via-media-data'"
fi
echo ""

# 1. Bring up the stack -----------------------------------------------------
# Single-phase up: docker-compose now wires `rtvi-vlm depends_on kafka:
# service_healthy` (with required: false), which makes Kafka start and
# pass its healthcheck BEFORE rtvi-vlm boots. That kills the cold-start
# race where rtvi-vlm's Kafka producer init ran before the broker was
# ready -- see Pitfall #6 in docs/streaming_rtvi_kafka_logstash.md.
header "1. docker compose ${RTVI_PROFILE_FLAG:-(no rtvi profile, external RTVI)} --profile kafka up -d"
STACK_BROUGHT_UP=1
if ! compose ${RTVI_PROFILE_FLAG} --profile kafka up -d 2>&1 | grep -v "^time="; then
    fail "docker compose up -d failed"; exit 1
fi

# 2. Wait for healthy services ---------------------------------------------
# Skip the in-stack rtvi-vlm /v1/health/ready probe when RTVI_VLM_URL is
# set -- the in-stack container was never started in that case (LVS will
# reach the external RTVI VLM directly via the configured URL).
header "2. Wait for healthy services"
wait_http "elasticsearch" "${ES_URL}/_cluster/health?wait_for_status=yellow&timeout=5s" 180 || exit 1
wait_http "via-engine"    "${LVS_URL}/v1/ready"           600 || exit 1
if [ -z "$RTVI_VLM_URL" ]; then
    wait_http "rtvi-vlm"      "${RTVI_URL}/v1/health/ready"   600 || exit 1
else
    echo "  SKIP  in-stack rtvi-vlm probe (RTVI_VLM_URL=${RTVI_VLM_URL} -> external RTVI)"
fi

# 3. Resolve model ----------------------------------------------------------
header "3. Resolve VLM model"
MODEL_ID=$(curl -fsS "${LVS_URL}/models" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null || echo "")
if [ -n "$MODEL_ID" ]; then
    pass "via-engine /models -> ${MODEL_ID}"
else
    fail "via-engine /models returned no model"; exit 1
fi

# 4. Add a live stream on RTVI-VLM (NO inference) --------------------------
# Provide an RTSP source so RTVI can actually pull frames; override with
# SANITY_RTSP_URL to point at your own stream.
SANITY_RTSP_URL="${SANITY_RTSP_URL:-rtsp://<your-rtsp-server>:8554/<stream>}"
header "4. POST ${RTVI_URL}/v1/stream/add (camera_url=${SANITY_RTSP_URL}, NO inference)"

# Scenario + events list are sent to LVS in the /v1/generate_captions body
# (for auto-prompt generation). stream_summarize only needs start_time/
# end_time (ISO 8601) — scenario/events are set at caption time, not summarize time.
SCENARIO="warehouse safety monitoring"
EVENTS_LIST="box dropping, not wearing PPE, unsafe forklift operations, walking into restricted area, unauthorized personnel, forklift stuck, poor handling of hazardous materials, arson, theft, fire, normal activity"

# camera_id="" so RTVI's asset.sensor_name is empty and info[streamId]
# carries chunk.streamId (the asset UUID) -- Logstash needs this to derive
# the ES index name (default_<asset_uuid>) without an explicit
# collection_name field on the wire.
#
# NO metadata.prompt -> RTVI does NOT auto-start inference. LVS will
# trigger captioning via POST /v1/generate_captions below (step 5).
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
    fail "stream/add unexpectedly started inference (response: ${ADD_STREAM_RESP}). Did metadata.prompt sneak into the payload?"; exit 1
fi
pass "stream/add inference=${INFERENCE_FLAG} (asset_id=${ASSET_ID})"
INDEX_NAME="default_$(echo "$ASSET_ID" | tr '-' '_')"

# Build the GenerateCaptionsRequest payload (step 5).
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

# Build the StreamSummarizeRequest payload (step 7).
# Only start_time/end_time and model are needed — scenario/events/
# objects_of_interest are set at caption time via generate_captions.
# start_time/end_time are ISO 8601 strings; empty string means "no filter".
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

# Convert a second-offset relative to CAPTIONS_START_EPOCH into an ISO 8601 string.
offset_to_iso() {
    local offset="$1"
    python3 -c "
from datetime import datetime, timezone, timedelta
import os
epoch = float(os.environ['CAPTIONS_START_EPOCH'])
dt = datetime.fromtimestamp(epoch + $offset, tz=timezone.utc)
print(dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond // 1000:03d}Z')
"
}

# 5. POST /v1/generate_captions -- triggers RTVI captioning ----------------
# LVS fires start_captions on RTVI (fire-and-forget). RTVI starts
# captioning in the background and publishes raw_events to Kafka.
# Logstash consumes them and writes to ES. LVS returns 200 immediately
# with a GenerateCaptionsResponse (status=accepted).
header "5. POST ${LVS_URL}/v1/generate_captions -- triggers RTVI captioning"
TRIGGER_PAYLOAD=$(build_generate_captions_payload)
TRIGGER_HTTP=$(curl -sS -o ${HOME}/sanity_trigger.json -w "%{http_code}" \
    -X POST "${LVS_URL}/v1/generate_captions" \
    -H 'Content-Type: application/json' \
    --max-time 120 \
    -d "$TRIGGER_PAYLOAD") || true
if [ "$TRIGGER_HTTP" = "200" ]; then
    CAPTIONS_START_EPOCH=$(python3 -c "import time; print(f'{time.time():.3f}')")
    export CAPTIONS_START_EPOCH
    TRIGGER_STATUS=$(python3 -c "
import json
b = json.load(open('${HOME}/sanity_trigger.json'))
print(b.get('status', ''))
" 2>/dev/null || echo "")
    pass "/v1/generate_captions -> 200 (status=${TRIGGER_STATUS}), captions_start=${CAPTIONS_START_EPOCH}"
else
    fail "/v1/generate_captions -> HTTP ${TRIGGER_HTTP}"
    cat ${HOME}/sanity_trigger.json
    exit 1
fi

# 6. Wait for raw_events to land in ES -------------------------------------
# RTVI is now generating captions in the background (kicked off by step
# 5's generate_captions trigger) and publishing them to Kafka; Logstash
# will consume + index them. Wait for >= SANITY_MIN_RAW_EVENTS docs so
# the follow-up summarize loop in step 7 has enough material to
# demonstrate non-empty windows.
header "6. Wait for >= ${SANITY_MIN_RAW_EVENTS} raw_events docs in ${INDEX_NAME}"
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
    echo "  hint: docker logs logstash | tail -100"
    echo "  hint: docker logs rtvi-vlm | grep -iE 'kafka|generate_captions' | tail -30"
    exit 1
fi

# 7. POST /v1/stream_summarize (multiple windows) -------------------------
# Captioning is already running from step 5. Each call queries the DB
# for existing captions and aggregates them for the given time window.
#
# Happy-path assertion: at least 1 of these 6 follow-up windows must
# return a non-empty `events` list. This validates that captioning +
# Kafka + Logstash + ES + ctx-rag aggregator end-to-end produced real
# structured_events from real VLM captions on the warehouse RTSP feed.
# Out-of-range windows like (60,90) and (300,450) are EXPECTED to be
# empty (they exercise the time filter against the 60-second clip), so
# the assertion is intentionally loose: NON_EMPTY_COUNT >= 1.
header "7. POST ${LVS_URL}/v1/stream_summarize (6 follow-up windows)"
sleep 20
# Offset pairs (seconds from CAPTIONS_START_EPOCH). "ALL" means "all events".
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
    SUM_HTTP=$(curl -sS -o ${HOME}/sanity_sum.json -w "%{http_code}" \
        -X POST "${LVS_URL}/v1/stream_summarize" \
        -H 'Content-Type: application/json' \
        --max-time 600 \
        -d "$SUMMARIZE_PAYLOAD") || true
    if [ "$SUM_HTTP" != "200" ]; then
        fail "stream_summarize (${ST},${ET}) -> HTTP ${SUM_HTTP}"
        cat ${HOME}/sanity_sum.json
        continue
    fi
    pass "stream_summarize (${ST},${ET}) -> 200"
    sleep 20
    # Aggregator output is JSON-stringified inside choices[0].message.content
    # (preserves the existing CompletionResponse contract on /v1/summarize).
    python3 <<PYEOF
import json
try:
    b = json.load(open('${HOME}/sanity_sum.json'))
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
b = json.load(open('${HOME}/sanity_sum.json'))
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

# Happy-path union assertion: at least one follow-up window returned events.
echo
if [ "${NON_EMPTY_COUNT:-0}" -gt 0 ] 2>/dev/null; then
    pass "${NON_EMPTY_COUNT}/6 stream_summarize call(s) returned non-empty events list (>= 1 required)"
else
    fail "All 6 stream_summarize calls returned empty events lists. Either captioning failed to start, or the time filter is dropping every chunk."
fi

# 8. Verify stream_summarize published to Kafka -> ES ---------------------
# After each /v1/stream_summarize call above, LVS publishes one
# `structured_events` Kafka message per batch (default batch_size=50, so
# usually 1 per call) and one `aggregated_summary` per call to
# `mdx-structured-events-summary`. Logstash consumes them and writes to the
# same `default_<asset_id>` ES index. We poll ES until both doc types
# are present, then assert the counts.
header "8. Verify structured_events + aggregated_summary in ES"

es_count_doc_type() {
    # Note: doc_type is mapped as `keyword` directly by the visionllm index
    # template, so do NOT append `.keyword`.
    local index="$1" doc_type="$2"
    curl -fsS -X POST "${ES_URL}/${index}/_search" \
        -H 'Content-Type: application/json' \
        -d "{\"query\":{\"term\":{\"metadata.content_metadata.doc_type\":\"${doc_type}\"}},\"size\":0}" \
        2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['hits']['total']['value'])" 2>/dev/null \
        || echo 0
}

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
    echo "  hint: docker logs lvs | grep -i 'kafka message delivered'"
fi

if [ "${AGG:-0}" -gt 0 ] 2>/dev/null; then
    pass "found ${AGG} aggregated_summary doc(s) in ${INDEX_NAME}"
else
    fail "no aggregated_summary docs in ${INDEX_NAME} after 60s"
    echo "  hint: docker logs lvs | grep -i 'kafka message delivered'"
fi

# Also print a sample aggregated_summary doc so the operator can verify
# the wire shape end-to-end (text + metadata.content_metadata.{uuid,
# camera_id, doc_type=aggregated_summary, total_events}).
#
# `_source` excludes the heavy embedding vector. Note: `_source_excludes`
# is a URL query parameter in ES, NOT a request-body field -- using it in
# the body returns 400 and aborts the script under `set -euo pipefail`.
# The leading `set +e` / trailing `set -e` block keeps a pretty-printer
# failure from masking the real test summary below.
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

# 9. File summarize via Kafka mode -----------------------------------------
# Validates that the file path under summarization.kafka_enabled=true
# now mirrors the live-stream path -- end-to-end SSE, RTVI -> LVS -> user.
# Flow:
#   - LVS registers the URL as an asset (file_id == asset_id; URL
#     passthrough is now hard-coded on the LVS side).
#   - LVS POSTs /v1/generate_captions stream=true on RTVI; RTVI pulls the
#     mp4 chunk-by-chunk and publishes per-chunk nv.VisionLLM events to
#     Kafka. Logstash indexes them as raw_events into default_<file_id>.
#   - LVS consumes RTVI's SSE chunk responses and (when stream=true on
#     the user request) re-emits them as SSE events to the user, giving
#     a per-chunk progress feed instead of a single sync blob.
#   - LVS waits the full SSE consumer + sleeps tools.elasticsearch_db
#     .params.kafka_consumer_settle_secs (default 5s) so Logstash has
#     time to flush, then dispatches ctx_mgr.call({"summarization":
#     {"uuids": [file_id]}}). The aggregator (kafka_enabled=true) reads
#     raw_events back from ES and returns events + video_summary.
#   - LVS publishes structured_events + aggregated_summary back to
#     Kafka; Logstash writes them into the same default_<file_id> index.
#   - LVS yields one final SSE event with the aggregated payload before
#     terminating with `data: [DONE]`. The script captures only that
#     final event for assertions / printing.
header "9. POST ${LVS_URL}/v1/summarize stream=true (URL passthrough: ${SANITY_FILE_URL})"

FILE_SUMMARIZE_PAYLOAD=$(SANITY_FILE_URL="$SANITY_FILE_URL" MODEL_ID="$MODEL_ID" \
    SCENARIO="$SCENARIO" EVENTS_LIST="$EVENTS_LIST" python3 -c "
import json, os
# stream=True is REQUIRED for the file path under Kafka mode: it switches
# the response to SSE (EventSourceResponse), which is the design intent
# documented in docs/streaming_rtvi_kafka_logstash.md.
# RTVI -> LVS captioning is already SSE-only end-to-end; extending it
# to LVS -> user keeps the whole pipeline event-driven and gives the
# client per-chunk progress instead of a single-blob sync response.
#
# chunk_duration=15 mirrors run_sanity.sh -- 2min video / 15s chunks = 8
# chunks, a well-tested known-good fixture for the aggregator and Kafka
# publish-back path.
print(json.dumps({
    'url': os.environ['SANITY_FILE_URL'],
    'model': os.environ['MODEL_ID'],
    'stream': True,
    'summarize': True,
    'scenario': os.environ['SCENARIO'],
    'events': [e.strip() for e in os.environ['EVENTS_LIST'].split(',') if e.strip()],
    'chunk_duration': 15,
}))")

# SSE response: each event is `data: <json>` followed by a blank line,
# terminated by `data: [DONE]`. We do NOT parse intermediate progress
# events here -- the sanity assertion only needs the final aggregated
# event (the last `data:` line before `[DONE]` whose content carries
# the structured `events` + `video_summary` JSON). curl -N keeps the
# pipe unbuffered so we capture the stream as it arrives; --max-time
# bounds the whole transfer.
curl -sS -N -X POST "${LVS_URL}/v1/summarize" \
    -H 'Content-Type: application/json' \
    -H 'Accept: text/event-stream' \
    --max-time "$SANITY_FILE_TIMEOUT" \
    -w "\n__HTTP_CODE__%{http_code}" \
    -d "$FILE_SUMMARIZE_PAYLOAD" \
    > ${HOME}/sanity_file_sum_sse.txt 2>&1 || true

FILE_SUM_HTTP=$(grep -oE '__HTTP_CODE__[0-9]+' ${HOME}/sanity_file_sum_sse.txt 2>/dev/null \
    | tail -1 | sed 's/__HTTP_CODE__//' || echo "")

if [ "$FILE_SUM_HTTP" != "200" ]; then
    fail "file /v1/summarize stream=true -> HTTP ${FILE_SUM_HTTP}"
    head -50 ${HOME}/sanity_file_sum_sse.txt || true
    # don't exit -- still print the summary so the live-stream pass shows
else
    pass "file /v1/summarize stream=true -> 200 (SSE response)"
fi

# Pick the LAST `data:` line whose payload is JSON (i.e. not `[DONE]`).
# That is the final aggregator event LVS yields right before terminating
# the stream -- it carries the full `choices[0].message.content` with
# the events list + video_summary JSON-stringified inside.
python3 - <<PYEOF > ${HOME}/sanity_file_sum_final.json
import json, sys
last = None
try:
    with open('${HOME}/sanity_file_sum_sse.txt') as fh:
        for raw in fh:
            line = raw.rstrip('\r\n')
            if not line.startswith('data:'):
                continue
            payload = line[len('data:'):].strip()
            if not payload or payload == '[DONE]':
                continue
            # Skip the curl trailer marker if it accidentally landed on a data line
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

# Extract file_id, events list, and video_summary from the final SSE event.
FILE_ID=$(python3 -c "
import json, sys
try:
    b = json.load(open('${HOME}/sanity_file_sum_final.json'))
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
    cat ${HOME}/sanity_file_sum_final.json || true
fi

# Print the structured events + aggregated summary the operator cares
# about. We deliberately show ONLY the final aggregator output, not
# every per-chunk progress event LVS streamed during the run.
echo
echo "  -- final aggregator event (events + video_summary) --"
python3 <<PYEOF
import json
try:
    b = json.load(open('${HOME}/sanity_file_sum_final.json'))
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
b = json.load(open('${HOME}/sanity_file_sum_final.json'))
c = (b.get('choices') or [{}])[0].get('message', {}).get('content', '')
try:
    print(len(json.loads(c).get('events', []) or []))
except Exception:
    print(0)
" 2>/dev/null || echo 0)

FILE_HAS_SUMMARY=$(python3 -c "
import json
b = json.load(open('${HOME}/sanity_file_sum_final.json'))
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

# Verify ES indexed raw_events + structured_events + aggregated_summary
# in default_<file_id> via Kafka -> Logstash. raw_events came from RTVI's
# per-chunk publish; structured_events + aggregated_summary came from
# LVS's _publish_aggregate_to_kafka after the aggregator returned.
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
        pass "file path: found ${F_RAW} raw_events doc(s) in ${FILE_INDEX} (came via RTVI -> Kafka -> Logstash)"
    else
        fail "file path: no raw_events docs in ${FILE_INDEX} after ${SANITY_FILE_KAFKA_TIMEOUT}s"
        echo "  hint: docker logs logstash | tail -50"
        echo "  hint: docker logs rtvi-vlm | grep -iE 'kafka|generate_captions' | tail -30"
    fi

    if [ "${F_SE:-0}" -gt 0 ] 2>/dev/null; then
        pass "file path: found ${F_SE} structured_events doc(s) in ${FILE_INDEX} (came via LVS _publish_aggregate_to_kafka)"
    else
        fail "file path: no structured_events docs in ${FILE_INDEX} after ${SANITY_FILE_KAFKA_TIMEOUT}s"
        echo "  hint: docker logs lvs | grep -i 'kafka message delivered' | tail -30"
    fi

    if [ "${F_AGG:-0}" -gt 0 ] 2>/dev/null; then
        pass "file path: found ${F_AGG} aggregated_summary doc(s) in ${FILE_INDEX}"
    else
        fail "file path: no aggregated_summary docs in ${FILE_INDEX} after ${SANITY_FILE_KAFKA_TIMEOUT}s"
    fi

    # Sample one raw_events doc so the operator can eyeball that
    # info.streamId == file_id (= asset_id). This is the smoking gun
    # that the chunks really came through Kafka and not via the legacy
    # in-process add_doc path (which is now gated off in vss_ctx_rag
    # when summarization.kafka_enabled=true).
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

# 10. Sticky-routing header (x-stream-id) on LVS->RTVI calls — METLVSMS-500
# ---------------------------------------------------------------------------
# Steps 5 + 9 above exercised:
#   - step 5: LVS start_captions  -> RTVI POST /v1/generate_captions  (live)
#   - step 9: LVS generate_captions_stream -> RTVI POST /v1/generate_captions  (file)
# RtviVlmClient logs an INFO line on every sticky outbound call that
# includes the x-stream-id value. Assert at least one log line each for
# start_captions and generate_captions_stream is present in the lvs
# container output.
header "10. Verify x-stream-id header on LVS -> RTVI calls"
# `set -euo pipefail` is active script-wide: grep returns non-zero when
# there are no matches, and the subsequent `XSID_*=$(...)` assignments
# would trigger errexit and abort the script before the if/else can
# print pass/fail. Disable errexit + pipefail locally for the counts,
# then restore.
#
# `--tail` must be large enough to span the entire session: ctx-rag emits a
# DEBUG queue-poll line per process every ~5s while idle, so a 1000-line
# window can be filled in <2 minutes and push earlier RTVI INFO lines out.
# Use a generous default; override via SANITY_LVS_LOG_TAIL if needed.
SANITY_LVS_LOG_TAIL="${SANITY_LVS_LOG_TAIL:-20000}"
set +e
set +o pipefail
LVS_LOG_DUMP=$(compose ${RTVI_PROFILE_FLAG} --profile kafka logs --no-color --tail="$SANITY_LVS_LOG_TAIL" lvs 2>/dev/null)

XSID_START=$(printf '%s\n' "$LVS_LOG_DUMP" \
    | grep -cE 'RTVI start_captions: x-stream-id=')
XSID_GEN=$(printf '%s\n' "$LVS_LOG_DUMP" \
    | grep -cE 'RTVI generate_captions_stream: x-stream-id=')
set -e
set -o pipefail

if [ "${XSID_START:-0}" -ge 1 ]; then
    pass "lvs logs contain ${XSID_START} 'start_captions: x-stream-id=' line(s)"
else
    fail "lvs logs do NOT contain 'start_captions: x-stream-id=' line"
    echo "  hint: docker compose -f $COMPOSE_FILE logs lvs | grep x-stream-id"
fi

if [ "${XSID_GEN:-0}" -ge 1 ]; then
    pass "lvs logs contain ${XSID_GEN} 'generate_captions_stream: x-stream-id=' line(s)"
else
    fail "lvs logs do NOT contain 'generate_captions_stream: x-stream-id=' line"
    echo "  hint: docker compose -f $COMPOSE_FILE logs lvs | grep x-stream-id"
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
