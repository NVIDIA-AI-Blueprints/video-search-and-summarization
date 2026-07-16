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
# Single-file sanity test: 1min.mp4, 10s chunks.
#
# Lightweight counterpart to run_sanity.sh — exercises only a single E2E
# summarization round-trip (stream=true + stream=false) against a 1-minute
# video with 10-second chunks (≈ 6 chunks). Useful for quick smoke-testing
# a deployment without waiting for the full 2min/15s run.
#
# Reads the same .env as run_sanity.sh. Does NOT bring up or tear down the
# compose stack — assumes an already-running stack (start with
# `docker compose up -d` or via run_sanity.sh with KEEP_STACK=1 first).
######################################################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Help ──────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'HELPEOF'

  Single-file Sanity Test — 1min.mp4 / 10s chunks
  ════════════════════════════════════════════════

  USAGE
    bash run_one_sanity.sh              # uses .env from the same directory
    bash run_one_sanity.sh /path/.env   # use a custom .env file
    bash run_one_sanity.sh -h | --help  # show this help

  PREREQUISITES
    The compose stack must already be running and LVS must be healthy.
    Start it with:
      docker compose -f docker-compose.yml --env-file .env up -d
    or run run_sanity.sh with KEEP_STACK=1 first.

  WHAT IT TESTS
    1. LVS health check
    2. Model discovery
    3. E2E summarize stream=false (1min.mp4, chunk_duration=10)
    4. E2E summarize stream=true  (1min.mp4, chunk_duration=10)

HELPEOF
    exit 0
fi

# ── Resolve .env ──────────────────────────────────────────────────────────────
DEFAULT_ENV_FILE="${SCRIPT_DIR}/.env"
ENV_FILE="${1:-${DEFAULT_ENV_FILE}}"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env file not found at $ENV_FILE"
    exit 1
fi

parse_env_var() {
    local var_name="$1"
    local default="$2"
    local val
    val=$(grep -E "^${var_name}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d'=' -f2-)
    val="${val%\"}"
    val="${val#\"}"
    val="${val%\'}"
    val="${val#\'}"
    echo "${val:-$default}"
}

LVS_BACKEND_PORT=$(parse_env_var "LVS_BACKEND_PORT" "38111")
LVS_URL="http://localhost:${LVS_BACKEND_PORT}"

VIDEO_FILE="1min.mp4"
CHUNK_DURATION=10

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

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════════════════╗"
echo "║         Single-file Sanity — ${VIDEO_FILE} / ${CHUNK_DURATION}s chunks                        ║"
echo "╚══════════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "  .env file:       ${ENV_FILE}"
echo "  LVS URL:         ${LVS_URL}"
echo "  Video:           ${VIDEO_FILE}"
echo "  Chunk duration:  ${CHUNK_DURATION}s"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
header "1. Health & Model"
# ══════════════════════════════════════════════════════════════════════════════

run_test "LVS /v1/ready" "200" "${LVS_URL}/v1/ready"

run_test "LVS /models" "200" "${LVS_URL}/models"

MODEL_ID=$(curl -s "${LVS_URL}/models" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'])" 2>/dev/null \
    || echo "unknown")
echo ""
echo "   Detected model: ${MODEL_ID}"

# ══════════════════════════════════════════════════════════════════════════════
header "2. E2E Summarize — ${VIDEO_FILE} / ${CHUNK_DURATION}s chunks"
# ══════════════════════════════════════════════════════════════════════════════

run_e2e_summarize() {
    local stream_value="$1"   # "true" or "false"
    local label="$2"

    TOTAL=$((TOTAL + 1))
    echo ""
    echo "── Test ${TOTAL}: E2E Summarize — ${label} (stream=${stream_value}) ──"
    echo "   (${VIDEO_FILE} / ${CHUNK_DURATION}s chunks ≈ 6 chunks)"

    SUMMARIZE_PAYLOAD=$(cat <<EOFPAYLOAD
{
    "url": "http://media-server/${VIDEO_FILE}",
    "model": "${MODEL_ID}",
    "scenario": "general surveillance",
    "events": ["activity", "movement", "object"],
    "chunk_duration": ${CHUNK_DURATION},
    "stream": ${stream_value},
    "stream_options": {"include_usage": true}
}
EOFPAYLOAD
    )

    echo ""
    echo "   Payload:"
    echo "$SUMMARIZE_PAYLOAD" | python3 -m json.tool 2>/dev/null || echo "$SUMMARIZE_PAYLOAD"
    echo ""

    if [ "$stream_value" = "true" ]; then
        echo "   Command: curl -sS -N -X POST ${LVS_URL}/v1/summarize -H 'Accept: text/event-stream' -d '...'"
        echo ""
        local sse_file="/tmp/sanity_one_sse_${stream_value}.txt"
        curl -sS -N -X POST "${LVS_URL}/v1/summarize" \
            -H "Content-Type: application/json" \
            -H "Accept: text/event-stream" \
            -w "\n__HTTP_CODE__%{http_code}" \
            -d "$SUMMARIZE_PAYLOAD" \
            > "$sse_file" 2>&1 || true

        HTTP_CODE=$(grep -oE '__HTTP_CODE__[0-9]+' "$sse_file" 2>/dev/null \
            | tail -1 | sed 's/__HTTP_CODE__//' || echo "")

        local final_file="/tmp/sanity_one_final_${stream_value}.json"
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
    echo "   ── Full CompletionResponse ──"
    echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
    echo ""

    if [ "$HTTP_CODE" = "200" ] && [ -n "$BODY" ]; then
        OBJ_TYPE=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('object',''))" 2>/dev/null || echo "")
        NUM_CHOICES=$(echo "$BODY" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('choices',[])))" 2>/dev/null || echo "0")
        RESP_MODEL=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('model',''))" 2>/dev/null || echo "")

        echo ""
        echo "   Validation:"
        echo "     object type:  ${OBJ_TYPE}"
        echo "     model:        ${RESP_MODEL}"
        echo "     choices:      ${NUM_CHOICES}"

        case "$OBJ_TYPE" in
            summarization.completion|summarization.progressing) OBJ_OK=1 ;;
            *) OBJ_OK=0 ;;
        esac

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
            FAIL=$((FAIL + 1))
        fi
    else
        echo ""
        echo "   ❌ FAIL (expected 200 with non-empty body, got HTTP=${HTTP_CODE} body_len=${#BODY})"
        FAIL=$((FAIL + 1))
    fi
}

run_e2e_summarize "false" "sync CompletionResponse"
run_e2e_summarize "true"  "SSE EventSourceResponse"

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
    exit 1
else
    echo "  ✅ ALL TESTS PASSED"
    exit 0
fi
