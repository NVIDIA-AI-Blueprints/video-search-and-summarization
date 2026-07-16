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
# Sanity test script for LVS + RTVI VLM integration.
#
# Reads the .env file from the same directory to derive service ports and
# configuration, then runs health, model, and end-to-end summarization tests
# against the deployed docker-compose stack.
######################################################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$SCRIPT_DIR"

# ── Help menu ─────────────────────────────────────────────────────────────────
show_help() {
    cat <<'HELPEOF'

  LVS + RTVI VLM Integration — Sanity Test Script
  ════════════════════════════════════════════════

  PURPOSE
    Validates that the LVS (Long Video Summarization) service is running
    correctly with the RTVI VLM backend by exercising key API endpoints:
      1. Health checks for LVS and RTVI VLM
      2. Model discovery (LVS proxies models from RTVI VLM)
      3. End-to-end video summarization (download → upload → VLM inference → structured output)
      4. Negative input validation tests

  USAGE
    bash run_sanity.sh              # uses .env from the same directory
    bash run_sanity.sh /path/.env   # use a custom .env file
    bash run_sanity.sh -h | --help  # show this help

  KEY FILES
    docker-compose.yml              Compose stack definition (LVS + RTVI VLM + Kafka + Redis + ES)
    .env                            Environment variables (ports, API keys, RTVI config)
    configmaps/config.yaml          CA-RAG config mounted into LVS (summarization functions)
    ../../config/config.yaml        Reference config from the LVS source tree

  ENV VARIABLES READ FROM .env
    LVS_BACKEND_PORT     Host port for LVS API (default: 38111)
    RTVI_VLM_PORT        Host port for RTVI VLM API (default: 8000)

  COMPOSE PROFILES
    The kafka / logstash profile is intentionally NOT activated here --
    this sanity script only exercises the file path
    (KAFKA_ENABLED unused). See run_sanity_kafka.sh
    for the live-stream + Kafka end-to-end flow.

    `--profile rtvi` activation is dynamic, driven by RTVI_VLM_URL:
      * RTVI_VLM_URL unset -> `--profile rtvi` is added to every
        `docker compose` call so the in-stack rtvi-vlm container starts.
      * RTVI_VLM_URL set (in shell env OR .env) -> the `--profile rtvi`
        flag is dropped; LVS will reach the external RTVI VLM at the
        configured URL via the shared Docker network.

  ENV VARIABLES READ FROM SHELL
    KEEP_STACK           "1" leaves the compose stack running on exit
                         (success OR failure) so the operator can inspect
                         logs / re-run targeted tests. Default tears the
                         stack down with `down -v --remove-orphans`.

  NOTE: USE_RTVI_VLM and RTVI_VLM_URL_PASSTHROUGH are deprecated. The
  LVS server now always uses RTVI VLM and always passes the source URL
  through to RTVI -- both env knobs are ignored.

HELPEOF
}

show_setup_notes() {
    cat <<SETUPEOF

  ┌──────────────────────────────────────────────────────────────────────────┐
  │                     SETUP & TROUBLESHOOTING                            │
  └──────────────────────────────────────────────────────────────────────────┘

  PREREQUISITES
    1. Docker and docker-compose v2 installed
    2. NVIDIA GPU with nvidia-container-toolkit
    3. Access to nvcr.io (for LVS image) or a locally built LVS image
    4. RTVI VLM image built from rtvi-microservices repo:
         cd <rtvi-microservices-repo> && make build-rtvi_vlm

  SETUP STEPS

    Step 1: Configure the .env file
      ${COMPOSE_DIR}/.env

      Required variables:
        NGC_API_KEY=<your-ngc-key>
        NVIDIA_API_KEY=<your-nvidia-api-key>
        OPENAI_API_KEY=<your-openai-key>     (if using openai-compat VLM)
        ARTIFACTORY_USER=<username>
        ARTIFACTORY_TOKEN=<token>
        RTVI_VLM_IMAGE=<your-rtvi-vlm-image>
        RTVI_VLM_GPU=0                       (GPU device for RTVI VLM)
        LVS_VLM_HOST=rtvi-vlm               (Docker service name)
        LVS_VLM_PORT=8000                    (RTVI VLM internal port)

    Step 2: Create the shared media volume
      docker volume create via-media-data

    Step 3: Start the compose stack
      cd ${COMPOSE_DIR}
      docker compose -f docker-compose.yml up -d

    Step 4: Wait for all services to be healthy
      docker compose -f docker-compose.yml ps

    Step 5: Run this sanity script
      bash ${COMPOSE_DIR}/run_sanity.sh

  TO POINT AT AN EXTERNAL RTVI VLM (no in-stack rtvi-vlm container):
      Set RTVI_VLM_URL=http://<external-host>:<port> in .env (or export
      it in the shell before invoking this script). The script detects
      RTVI_VLM_URL automatically: when it's non-empty, `--profile rtvi`
      is omitted from every docker compose call (so the bundled
      rtvi-vlm container does NOT start) and the in-stack RTVI probes
      are skipped. No script edits required.

SETUPEOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    show_help
    show_setup_notes
    exit 0
fi

# ── Resolve .env ──────────────────────────────────────────────────────────────
# Default to the BlueprintBuilderGenerated/.env that ships next to this
# script. SCRIPT_DIR is resolved from BASH_SOURCE so the default works
# regardless of where the operator invokes the script from (cwd, symlink,
# or a `bash /abs/path/run_sanity.sh` call). A positional arg overrides.
DEFAULT_ENV_FILE="${SCRIPT_DIR}/.env"
ENV_FILE="${1:-${DEFAULT_ENV_FILE}}"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env file not found at $ENV_FILE"
    echo "       (default location: ${DEFAULT_ENV_FILE})"
    echo ""
    echo "Run with --help for setup instructions."
    exit 1
fi

# ── Parse .env ────────────────────────────────────────────────────────────────
# Source only the variables we need (skip comments, empty lines, and quoted values)
parse_env_var() {
    local var_name="$1"
    local default="$2"
    local val
    val=$(grep -E "^${var_name}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d'=' -f2-)
    # Strip surrounding quotes if present
    val="${val%\"}"
    val="${val#\"}"
    val="${val%\'}"
    val="${val#\'}"
    echo "${val:-$default}"
}

LVS_BACKEND_PORT=$(parse_env_var "LVS_BACKEND_PORT" "38111")
RTVI_VLM_PORT=$(parse_env_var "RTVI_VLM_PORT" "8000")

# RTVI_VLM_URL signals "point LVS at an EXTERNAL RTVI VLM" -- when it's
# set (in shell env OR uncommented in .env), the in-stack rtvi-vlm
# container is unnecessary, so we drop `--profile rtvi` from the compose
# calls and skip the in-stack RTVI probes (Tests 1 / 2 below). Shell env
# takes precedence over .env so an operator can override on the fly:
#   RTVI_VLM_URL=http://<rtvi-host>:<port> bash run_sanity.sh
RTVI_VLM_URL="${RTVI_VLM_URL:-$(parse_env_var "RTVI_VLM_URL" "")}"

LVS_URL="http://localhost:${LVS_BACKEND_PORT}"
RTVI_URL="http://localhost:${RTVI_VLM_PORT}"
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.yml"

# RTVI_PROFILE_FLAG is spliced into every `docker compose` call. Empty
# when RTVI_VLM_URL points at an external RTVI VLM (skip starting the
# in-stack container), otherwise `--profile rtvi` to spin up the
# bundled rtvi-vlm. kafka / logstash are NEVER activated here -- this
# script only exercises the legacy file path; run_sanity_kafka.sh
# covers the streaming Kafka pipeline.
if [ -n "$RTVI_VLM_URL" ]; then
    RTVI_PROFILE_FLAG=""
    RTVI_PROFILE_ACTIVE=false
else
    RTVI_PROFILE_FLAG="--profile rtvi --profile media"
    RTVI_PROFILE_ACTIVE=true
fi

# Activate `media` profile (media-server.yaml).
if [ -z "${COMPOSE_PROFILES:-}" ]; then
    export COMPOSE_PROFILES="media"
elif [[ ",${COMPOSE_PROFILES}," != *",media,"* ]]; then
    export COMPOSE_PROFILES="${COMPOSE_PROFILES},media"
fi

# ── Formatting ────────────────────────────────────────────────────────────────
PASS=0
FAIL=0
TOTAL=0

header() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  $1"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

run_test() {
    local name="$1"
    local expected_code="$2"
    shift 2
    local curl_args=("$@")

    TOTAL=$((TOTAL + 1))
    echo ""
    echo "── Test ${TOTAL}: ${name} ──"
    echo "   Command: curl ${curl_args[*]}"
    echo ""

    local http_code body
    body=$(curl -s -w "\n__HTTP_CODE__%{http_code}" "${curl_args[@]}" 2>&1) || true
    http_code=$(echo "$body" | grep "__HTTP_CODE__" | sed 's/__HTTP_CODE__//')
    body=$(echo "$body" | grep -v "__HTTP_CODE__")

    echo "   HTTP Status: ${http_code}"
    echo "   Response:"
    if echo "$body" | python3 -m json.tool 2>/dev/null; then
        :
    else
        echo "   $body"
    fi

    if echo "$expected_code" | grep -q "$http_code"; then
        echo ""
        echo "   ✅ PASS (expected ${expected_code}, got ${http_code})"
        PASS=$((PASS + 1))
    else
        echo ""
        echo "   ❌ FAIL (expected ${expected_code}, got ${http_code})"
        FAIL=$((FAIL + 1))
    fi
}

# ── Teardown handler ──────────────────────────────────────────────────────────
# KEEP_STACK=1 leaves the compose stack running on exit (success OR failure)
# so the operator can poke at logs / re-run targeted tests against the same
# LVS instance. Default behaviour tears the stack down so consecutive runs
# start clean. STACK_BROUGHT_UP guards the teardown so we don't tear down a
# stack we never brought up (e.g. early `exit 1` on .env validation).
STACK_BROUGHT_UP=0
teardown() {
    if [ "${STACK_BROUGHT_UP}" != "1" ]; then return 0; fi
    if [ "${KEEP_STACK:-0}" = "1" ]; then
        echo
        echo "  KEEP_STACK=1 — leaving stack up. Tear down manually with:"
        echo "    cd ${COMPOSE_DIR} && docker compose -f docker-compose.yml --env-file ${ENV_FILE} down -v --remove-orphans"
        return 0
    fi
    echo
    echo "  Tearing stack down..."
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
        ${RTVI_PROFILE_FLAG} down -v --remove-orphans 2>&1 \
        | grep -v "^time=" || true
}
trap 'teardown' EXIT

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════════════════╗"
echo "║              LVS + RTVI VLM Integration — Sanity Tests                     ║"
echo "╚══════════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "  .env file:       ${ENV_FILE}"
echo "  Compose file:    ${COMPOSE_FILE}"
echo "  LVS URL:         ${LVS_URL}"
echo "  RTVI VLM URL:    ${RTVI_URL}"
echo "  RTVI in-stack:   ${RTVI_PROFILE_ACTIVE} (RTVI_VLM_URL=${RTVI_VLM_URL:-<unset>})"
echo "  KEEP_STACK:      ${KEEP_STACK:-0}"
echo ""

# ── Pre-flight: ensure shared media volume exists ─────────────────────────────
if docker volume inspect via-media-data &>/dev/null; then
    echo "  [ok] Docker volume 'via-media-data' exists"
else
    echo "  [..] Creating Docker volume 'via-media-data'..."
    docker volume create via-media-data
    echo "  [ok] Created 'via-media-data'"
fi
echo ""

# ── Bring up a clean stack ────────────────────────────────────────────────────
# `${RTVI_PROFILE_FLAG}` resolves to `--profile rtvi` when LVS is to use
# the in-stack rtvi-vlm container, and to the empty string when
# RTVI_VLM_URL points at an external RTVI VLM (in which case we skip
# spinning up the bundled container entirely). `kafka` / `logstash` are
# never activated here -- this sanity script only exercises the legacy
# file path, which doesn't need the streaming Kafka pipeline (see
# run_sanity_kafka.sh for that). Profile activation is driven entirely
# by the explicit --profile CLI flags below so the script's compose
# target stays self-contained.
echo "  Tearing down any existing stack..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    ${RTVI_PROFILE_FLAG} down 2>&1 | grep -v "^time=" || true
echo ""
echo "  Starting compose stack..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    ${RTVI_PROFILE_FLAG} up -d 2>&1 | grep -v "^time=" || true
# Mark that this script owns the running stack -- the EXIT trap will tear
# it down (unless KEEP_STACK=1).
STACK_BROUGHT_UP=1
echo ""
echo "  Waiting for LVS to become healthy..."
LVS_RETRIES=0
while [ $LVS_RETRIES -lt 60 ]; do
    if curl -s -o /dev/null -w "%{http_code}" "${LVS_URL}/v1/ready" 2>/dev/null | grep -q "200"; then
        echo "  [ok] LVS is ready."
        break
    fi
    LVS_RETRIES=$((LVS_RETRIES + 1))
    sleep 5
done
if [ $LVS_RETRIES -ge 60 ]; then
    echo "  ERROR: LVS did not become healthy within 5 minutes."
    echo "  Check logs: docker compose -f $COMPOSE_FILE logs lvs"
    exit 1
fi
echo ""

# ══════════════════════════════════════════════════════════════════════════════
header "1. Health & Connectivity"
# ══════════════════════════════════════════════════════════════════════════════

run_test "LVS /v1/ready" \
    "200" \
    "${LVS_URL}/v1/ready"

if [ "$RTVI_PROFILE_ACTIVE" = "true" ]; then
    run_test "RTVI VLM /v1/health/ready (in-stack)" \
        "200" \
        "${RTVI_URL}/v1/health/ready"
fi

# ══════════════════════════════════════════════════════════════════════════════
header "2. Model Discovery"
# ══════════════════════════════════════════════════════════════════════════════

run_test "LVS /models — list available VLM models" \
    "200" \
    "${LVS_URL}/models"

# Extract model ID for subsequent tests
MODEL_ID=$(curl -s "${LVS_URL}/models" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'])" 2>/dev/null \
    || echo "unknown")
echo ""
echo "   Detected model: ${MODEL_ID}"

if [ "$RTVI_PROFILE_ACTIVE" = "true" ]; then
    run_test "RTVI VLM /v1/models — direct model list (in-stack)" \
        "200" \
        "${RTVI_URL}/v1/models"
fi

# ══════════════════════════════════════════════════════════════════════════════
header "3. End-to-End Summarization"
# ══════════════════════════════════════════════════════════════════════════════

run_e2e_summarize() {
    local stream_value="$1"   # "true" or "false"
    local label="$2"

    TOTAL=$((TOTAL + 1))
    echo ""
    echo "── Test ${TOTAL}: E2E Summarize — ${label} (stream=${stream_value}) ──"
    echo "   (this may take 1-3 minutes depending on VLM inference speed; 2min video / 15s chunks = 8 chunks)"

    SUMMARIZE_PAYLOAD=$(cat <<EOFPAYLOAD
{
    "url": "http://media-server/2min.mp4",
    "model": "${MODEL_ID}",
    "scenario": "general surveillance",
    "events": ["activity", "movement", "object"],
    "chunk_duration": 15,
    "stream": ${stream_value}
}
EOFPAYLOAD
    )

    echo ""
    echo "   Payload:"
    echo "$SUMMARIZE_PAYLOAD" | python3 -m json.tool 2>/dev/null || echo "$SUMMARIZE_PAYLOAD"
    echo ""

    if [ "$stream_value" = "true" ]; then
        # SSE response: LVS yields per-chunk progress events followed by a
        # final aggregated event and a terminating `data: [DONE]`. We don't
        # iterate every event -- we keep only the LAST `data:` payload that
        # JSON-decodes (the aggregator's CompletionResponse) and assert on
        # that, mirroring the run_sanity_kafka.sh Step 9 SSE consumer.
        echo "   Command: curl -sS -N -X POST ${LVS_URL}/v1/summarize -H 'Accept: text/event-stream' -d '...'"
        echo ""
        local sse_file="${HOME}/sanity_file_sum_sse_${stream_value}.txt"
        curl -sS -N -X POST "${LVS_URL}/v1/summarize" \
            -H "Content-Type: application/json" \
            -H "Accept: text/event-stream" \
            -w "\n__HTTP_CODE__%{http_code}" \
            -d "$SUMMARIZE_PAYLOAD" \
            > "$sse_file" 2>&1 || true

        HTTP_CODE=$(grep -oE '__HTTP_CODE__[0-9]+' "$sse_file" 2>/dev/null \
            | tail -1 | sed 's/__HTTP_CODE__//' || echo "")

        # Pick the final JSON-decoded `data:` event before [DONE].
        local final_file="${HOME}/sanity_file_sum_final_${stream_value}.json"
        python3 - <<PYEOF > "$final_file"
import json, sys
last = None
try:
    with open("$sse_file") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]" or payload.startswith("__HTTP_CODE__"):
                continue
            try:
                last = json.loads(payload)
            except Exception:
                continue
except FileNotFoundError:
    pass
json.dump(last or {}, sys.stdout)
PYEOF

        echo "   HTTP Status: ${HTTP_CODE} (SSE)"
        echo "   Final SSE event (parsed):"
        python3 -m json.tool < "$final_file" 2>/dev/null || cat "$final_file"
        BODY=$(cat "$final_file")
    else
        # stream=false (default): single sync CompletionResponse.
        echo "   Command: curl -s -X POST ${LVS_URL}/v1/summarize -H 'Content-Type: application/json' -d '...'"
        echo ""
        RESPONSE=$(curl -s -w "\n__HTTP_CODE__%{http_code}" \
            -X POST "${LVS_URL}/v1/summarize" \
            -H "Content-Type: application/json" \
            -d "$SUMMARIZE_PAYLOAD" 2>&1) || true

        HTTP_CODE=$(echo "$RESPONSE" | grep "__HTTP_CODE__" | sed 's/__HTTP_CODE__//')
        BODY=$(echo "$RESPONSE" | grep -v "__HTTP_CODE__")

        echo "   HTTP Status: ${HTTP_CODE}"
        echo "   Response:"
        echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "   $BODY"
    fi

    echo ""
    echo "   Content (parsed):"
    echo "$BODY" | python3 -m json.tool 2>/dev/null | jq '.choices[0].message.content | fromjson' 2>/dev/null || echo "   (could not parse content)"

    if [ "$HTTP_CODE" = "200" ] && [ -n "$BODY" ]; then
        OBJ_TYPE=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('object',''))" 2>/dev/null || echo "")
        NUM_CHOICES=$(echo "$BODY" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('choices',[])))" 2>/dev/null || echo "0")
        RESP_MODEL=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('model',''))" 2>/dev/null || echo "")

        echo ""
        echo "   Validation:"
        echo "     object type:  ${OBJ_TYPE}"
        echo "     model:        ${RESP_MODEL}"
        echo "     choices:      ${NUM_CHOICES}"

        # Validation:
        #   stream=false: response is a single sync CompletionResponse with
        #                 object="summarization.completion".
        #   stream=true:  the LAST `data:` event before `[DONE]` is one of:
        #                   * "summarization.progressing" — per-chunk event
        #                     (always emitted; carries the chunk's caption
        #                     in choices[0].message.content). This is the
        #                     LAST event when stream_options.include_usage
        #                     is NOT set (the default).
        #                   * "summarization.completion" — only emitted
        #                     after the last progress event when
        #                     stream_options.include_usage=true; carries
        #                     a `usage` field but `choices: []`.
        #                 We don't pass include_usage in the payload, so
        #                 the LAST event is always "summarization.progressing"
        #                 with choices > 0.
        #
        # PASS condition:
        #   - object starts with "summarization." (stream=false: completion;
        #     stream=true: progressing or completion)
        #   - AND (choices > 0 OR object == "summarization.completion")
        #     (the include_usage completion event has empty choices but is
        #      still a valid stream terminator)
        #   - AND choices[0].message.content parses as JSON with a
        #     non-empty `events` list AND a non-empty `video_summary`.
        #     This guards against the failure mode where the aggregator
        #     returns a structurally-valid CompletionResponse but the
        #     LLM produced nothing (e.g. all reasoning, no answer), which
        #     used to silently pass: see terminals/1.txt:3221-3291.
        case "$OBJ_TYPE" in
            summarization.completion|summarization.progressing) OBJ_OK=1 ;;
            *) OBJ_OK=0 ;;
        esac

        # Inspect the aggregator payload nested inside
        # choices[0].message.content. We accept a missing/empty content
        # ONLY for the bare `summarization.completion` terminator that
        # carries usage metrics with `choices: []` (include_usage=true);
        # in every other case empty events / empty video_summary is a
        # hard FAIL.
        CONTENT_REPORT=$(BODY="$BODY" python3 - <<'PYEOF' 2>/dev/null || echo "OK=0 EVENTS=0 SUMMARY_LEN=0 REASON=parse_error"
import json, os
body = os.environ.get("BODY", "")
try:
    b = json.loads(body)
except Exception as ex:
    print(f"OK=0 EVENTS=0 SUMMARY_LEN=0 REASON=top_level_not_json:{ex}")
    raise SystemExit
choices = b.get("choices") or []
if not choices:
    print(f"OK=0 EVENTS=0 SUMMARY_LEN=0 REASON=no_choices object={b.get('object','')}")
    raise SystemExit
content = (choices[0].get("message") or {}).get("content", "") or ""
if not content.strip():
    print("OK=0 EVENTS=0 SUMMARY_LEN=0 REASON=empty_content")
    raise SystemExit
try:
    payload = json.loads(content)
except Exception as ex:
    print(f"OK=0 EVENTS=0 SUMMARY_LEN=0 REASON=content_not_json:{ex}")
    raise SystemExit
events = payload.get("events") or []
summary = (payload.get("video_summary") or "").strip()
ok = 1 if (len(events) > 0 and summary) else 0
print(f"OK={ok} EVENTS={len(events)} SUMMARY_LEN={len(summary)} REASON=ok")
PYEOF
)
        CONTENT_OK=$(echo "$CONTENT_REPORT" | sed -n 's/.*OK=\([0-9]*\).*/\1/p')
        EVENTS_RET=$(echo "$CONTENT_REPORT" | sed -n 's/.*EVENTS=\([0-9]*\).*/\1/p')
        SUMMARY_LEN=$(echo "$CONTENT_REPORT" | sed -n 's/.*SUMMARY_LEN=\([0-9]*\).*/\1/p')
        CONTENT_REASON=$(echo "$CONTENT_REPORT" | sed -n 's/.*REASON=\(.*\)/\1/p')

        echo "     events:       ${EVENTS_RET:-0}"
        echo "     summary_len:  ${SUMMARY_LEN:-0}"

        if [ "${OBJ_OK}" != "1" ] || { [ "$NUM_CHOICES" -le 0 ] && [ "$OBJ_TYPE" != "summarization.completion" ]; }; then
            echo ""
            echo "   ❌ FAIL — unexpected response structure (object=${OBJ_TYPE}, choices=${NUM_CHOICES})"
            FAIL=$((FAIL + 1))
        elif [ "$OBJ_TYPE" = "summarization.completion" ] && [ "$NUM_CHOICES" -le 0 ]; then
            # Bare include_usage completion terminator (no choices, no
            # content) — pass on structure alone, content check N/A.
            echo ""
            echo "   ✅ PASS — ${OBJ_TYPE} (usage-only terminator, no choices)"
            PASS=$((PASS + 1))
        elif [ "${CONTENT_OK:-0}" = "1" ]; then
            echo ""
            echo "   ✅ PASS — ${OBJ_TYPE} with ${NUM_CHOICES} choice(s); ${EVENTS_RET} event(s), summary ${SUMMARY_LEN} chars"
            PASS=$((PASS + 1))
        else
            echo ""
            echo "   ❌ FAIL — empty aggregator payload (events=${EVENTS_RET:-0}, summary_len=${SUMMARY_LEN:-0}, reason=${CONTENT_REASON:-unknown})"
            echo "            structurally HTTP 200 / object=${OBJ_TYPE} but choices[0].message.content has empty events list and/or empty video_summary."
            FAIL=$((FAIL + 1))
        fi
    else
        echo ""
        echo "   ❌ FAIL (expected 200 with non-empty body, got HTTP=${HTTP_CODE} body_len=${#BODY})"
        FAIL=$((FAIL + 1))
    fi
}

# Run two E2E summarize cases against the already-running stack:
#   1. stream=false -> sync CompletionResponse (legacy default)
#   2. stream=true  -> SSE EventSourceResponse (chunk progress + final
#                      aggregated event before `data: [DONE]`)
# Both exercise the same RTVI -> ctx-rag pipeline; only the LVS->user
# response shape differs. Together they confirm the file path works
# end-to-end under both response modes.
run_e2e_summarize "false" "sync CompletionResponse"
run_e2e_summarize "true"  "SSE EventSourceResponse"


# ══════════════════════════════════════════════════════════════════════════════
header "3a. Sticky-routing header (x-stream-id) on LVS->RTVI calls — METLVSMS-500"
# ══════════════════════════════════════════════════════════════════════════════
# The two summarize runs above exercise POST /v1/generate_captions on RTVI.
# RtviVlmClient logs an INFO line on every sticky outbound call that
# includes the x-stream-id value. Assert at least one such log line is
# present in the lvs container output.

TOTAL=$((TOTAL + 1))
echo ""
echo "── Test ${TOTAL}: lvs container logs x-stream-id on sticky RTVI calls ──"
# `set -euo pipefail` is active for the whole script: grep returns non-zero
# when there are no matches and the resulting pipeline exit code would abort
# the script before we can format a FAIL message. Disable pipefail / errexit
# locally for the count, then restore.
#
# `--tail` must be large enough to span the entire session: ctx-rag emits a
# DEBUG queue-poll line per process every ~5s while idle, so a 500-line
# window can be filled in <1 minute and push earlier RTVI INFO lines out.
# Use a generous default; override via SANITY_LVS_LOG_TAIL if needed.
SANITY_LVS_LOG_TAIL="${SANITY_LVS_LOG_TAIL:-20000}"
set +e
set +o pipefail
LVS_LOG_DUMP=$(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    ${RTVI_PROFILE_FLAG} logs --no-color --tail="$SANITY_LVS_LOG_TAIL" lvs 2>/dev/null)
XSID_HITS=$(printf '%s\n' "$LVS_LOG_DUMP" \
    | grep -cE 'RTVI generate_captions_stream: x-stream-id=')
set -e
set -o pipefail

if [ "${XSID_HITS:-0}" -ge 1 ]; then
    echo "   ✅ PASS — found ${XSID_HITS} 'x-stream-id=' log line(s) on generate_captions"
    PASS=$((PASS + 1))
else
    echo "   ❌ FAIL — no 'x-stream-id=' log line found on generate_captions"
    echo "   hint: docker compose -f $COMPOSE_FILE logs lvs | grep x-stream-id"
    FAIL=$((FAIL + 1))
fi


# ══════════════════════════════════════════════════════════════════════════════
header "4. Negative Tests — Input Validation"
# ══════════════════════════════════════════════════════════════════════════════

run_test "Missing required fields (model, scenario, events)" \
    "422" \
    -X POST "${LVS_URL}/v1/summarize" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://media-server/0.5min.mp4"}'

run_test "Wrong model name → 400 or 500" \
    "400\|500" \
    -X POST "${LVS_URL}/v1/summarize" \
    -H "Content-Type: application/json" \
    -d "{\"url\":\"http://127.0.0.1/test.mp4\",\"model\":\"nonexistent-model\",\"scenario\":\"general\",\"events\":[\"activity\"]}"
    
# ══════════════════════════════════════════════════════════════════════════════
header "Summary"
# ══════════════════════════════════════════════════════════════════════════════

echo ""
echo "  Total:  ${TOTAL}"
echo "  Passed: ${PASS}"
echo "  Failed: ${FAIL}"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "  ❌ SOME TESTS FAILED"
    echo ""
    echo "  Run 'bash $(basename "$0") --help' for setup instructions."
    exit 1
else
    echo "  ✅ ALL TESTS PASSED"
    exit 0
fi
