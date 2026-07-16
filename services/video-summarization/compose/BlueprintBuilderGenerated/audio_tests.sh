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

#
# Audio summarization end-to-end test.
#
# Launches the in-stack RTVI VLM (COMPOSE_PROFILES=rtvi) with a vllm-compatible
# Nemotron-Nano-V3-Omni audio model and runs two sequential summarization
# requests against Artifactory-hosted video files — no media-server required.
#
# Usage:
#   bash audio_tests.sh              # uses .env from the same directory
#   bash audio_tests.sh /path/.env   # use a custom .env file
#   KEEP_STACK=1 bash audio_tests.sh # leave stack running after tests
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ENV_FILE="${SCRIPT_DIR}/.env"
ENV_FILE="${1:-${DEFAULT_ENV_FILE}}"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env file not found at $ENV_FILE"
    exit 1
fi

# ── Parse individual values from .env ─────────────────────────────────────────
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
RTVI_VLM_PORT=$(parse_env_var "RTVI_VLM_PORT" "8420")

LVS_URL="http://localhost:${LVS_BACKEND_PORT}"
RTVI_URL="http://localhost:${RTVI_VLM_PORT}"

# ── Apply env overrides (shell env takes precedence over --env-file) ──────────
export VLM_MODEL_TO_USE=vllm-compatible
export MODEL_PATH="git:https://huggingface.co/nvidia/Nemotron-Nano-V3-Omni-GA0420-FP8"
export VLM_MODEL_SUPPORTS_AUDIO=true
export VLM_TRUST_REMOTE_CODE=true
export ENABLE_AUDIO=true
export VLLM_GPU_MEMORY_UTILIZATION=0.8
export KAFKA_ENABLED=false
export COMPOSE_PROFILES=rtvi
export INSTALL_PROPRIETARY_CODECS=true
unset VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME  || true
unset RTVI_VLM_URL                          || true  # compose defaults to http://rtvi-vlm:8000

# HF_TOKEN is required to download the gated Nemotron model.
# It may be commented out in .env (grep handles both commented and uncommented).
# Shell env takes precedence if already set.
if [ -z "${HF_TOKEN:-}" ]; then
    HF_TOKEN=$(grep -E "^#?HF_TOKEN=" "$ENV_FILE" 2>/dev/null \
        | tail -1 | cut -d'=' -f2- | tr -d '"' | tr -d "'" | xargs) || true
fi
if [ -z "${HF_TOKEN:-}" ]; then
    echo "ERROR: HF_TOKEN is not set. Export it in the shell or add/uncomment HF_TOKEN in ${ENV_FILE}."
    exit 1
fi
export HF_TOKEN

# ── Teardown handler ──────────────────────────────────────────────────────────
STACK_BROUGHT_UP=0
teardown() {
    if [ "${STACK_BROUGHT_UP}" != "1" ]; then return 0; fi
    if [ "${KEEP_STACK:-0}" = "1" ]; then
        echo
        echo "  KEEP_STACK=1 — stack left running. Tear down manually:"
        echo "    cd ${SCRIPT_DIR} && docker compose -f docker-compose.yml --env-file ${ENV_FILE} --profile rtvi down -v --remove-orphans"
        return 0
    fi
    echo
    echo "  Tearing stack down..."
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
        --profile rtvi down -v --remove-orphans 2>&1 | grep -v "^time=" || true
}
trap 'teardown' EXIT

# ── Pre-flight: check for host port conflicts ─────────────────────────────────
# Elasticsearch (9200/9300) and the LVS/RTVI host ports must be free before
# docker compose can bind them. A conflict causes the container to silently
# fail to start, which then causes the health-wait loop to spin and time out.
check_port_free() {
    local port="$1"
    local label="$2"
    if ss -tlnp 2>/dev/null | grep -qE ":${port}[ \t]"; then
        local holder
        holder=$(docker ps --format "{{.Names}}  {{.Ports}}" 2>/dev/null \
            | grep ":${port}->" | head -3 || true)
        echo "  ERROR: port ${port} (${label}) is already in use."
        if [ -n "$holder" ]; then
            echo "         Held by: ${holder}"
        fi
        return 1
    fi
    return 0
}

PORTS_OK=1
check_port_free 9200  "Elasticsearch HTTP"      || PORTS_OK=0
check_port_free 9300  "Elasticsearch transport" || PORTS_OK=0
check_port_free "${LVS_BACKEND_PORT}" "LVS API" || PORTS_OK=0
check_port_free "${RTVI_VLM_PORT}"   "RTVI VLM" || PORTS_OK=0
if [ "$PORTS_OK" = "0" ]; then
    echo ""
    echo "  Stop the conflicting containers and re-run."
    echo "  Tip: the elasticsearch conflict usually comes from a Kafka or"
    echo "  run_sanity_kafka.sh stack left running. Stop it with:"
    echo "    docker stop elasticsearch && docker rm elasticsearch"
    exit 1
fi

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════════════════╗"
echo "║              LVS Audio Summarization — End-to-End Tests                    ║"
echo "╚══════════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "  .env file:    ${ENV_FILE}"
echo "  Compose file: ${COMPOSE_FILE}"
echo "  LVS URL:      ${LVS_URL}"
echo "  RTVI VLM URL: ${RTVI_URL}  (in-stack, profile=rtvi)"
echo "  Model:        ${MODEL_PATH}"
echo "  KEEP_STACK:   ${KEEP_STACK:-0}"
echo ""

# ── Launch the stack ──────────────────────────────────────────────────────────
# LVS depends on wait-for-llm (which checks the external LLM, not rtvi-vlm) so
# it starts right away. LVS then spends up to 2.5 min internally retrying its
# rtvi-vlm connection -- this blows the 5-min LVS health window if rtvi-vlm is
# still loading. Fix: start infra + rtvi-vlm first, wait for rtvi-vlm, then
# start LVS so it connects on the first attempt.
echo "==> Tearing down any existing stack..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    --profile rtvi down 2>&1 | grep -v "^time=" || true
echo ""
echo "==> Starting infrastructure + RTVI VLM (LVS starts after model is ready)..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    --profile rtvi up -d \
    elasticsearch milvus-standalone graph-db arango-db rtvi-vlm \
    2>&1 | grep -v "^time=" || true
STACK_BROUGHT_UP=1
echo ""

# ── Wait for RTVI VLM ─────────────────────────────────────────────────────────
# Model download can take 10-20 min; allow up to 30 min.
echo "==> Waiting for RTVI VLM to become healthy (up to 30 min)..."
RTVI_RETRIES=0
while [ $RTVI_RETRIES -lt 180 ]; do
    if curl -s -o /dev/null -w "%{http_code}" \
            "${RTVI_URL}/v1/health/ready" 2>/dev/null | grep -q "200"; then
        echo "  [ok] RTVI VLM is ready."
        break
    fi
    RTVI_RETRIES=$((RTVI_RETRIES + 1))
    echo "  (attempt ${RTVI_RETRIES}/180) ..."
    sleep 10
done
if [ $RTVI_RETRIES -ge 180 ]; then
    echo "  ERROR: RTVI VLM did not become healthy within 30 minutes."
    echo "  Check logs: docker compose -f $COMPOSE_FILE logs rtvi-vlm"
    exit 1
fi
echo ""

# ── Start LVS now that RTVI VLM is healthy ────────────────────────────────────
echo "==> Starting LVS..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    --profile rtvi up -d lvs \
    2>&1 | grep -v "^time=" || true
echo ""

# ── Wait for LVS ──────────────────────────────────────────────────────────────
echo "==> Waiting for LVS to become healthy (up to 10 min)..."
LVS_RETRIES=0
while [ $LVS_RETRIES -lt 120 ]; do
    if curl -s -o /dev/null -w "%{http_code}" \
            "${LVS_URL}/v1/ready" 2>/dev/null | grep -q "200"; then
        echo "  [ok] LVS is ready."
        break
    fi
    LVS_RETRIES=$((LVS_RETRIES + 1))
    echo "  (attempt ${LVS_RETRIES}/120) ..."
    sleep 5
done
if [ $LVS_RETRIES -ge 120 ]; then
    echo "  ERROR: LVS did not become healthy within 10 minutes."
    echo "  Check logs: docker compose -f $COMPOSE_FILE logs lvs"
    exit 1
fi
echo ""

# ── Discover model ID ─────────────────────────────────────────────────────────
MODEL=$(curl -fsS "${LVS_URL}/models" | python3 -c \
    "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])")
echo "  Detected model: ${MODEL}"
echo ""

# ── Test runner ───────────────────────────────────────────────────────────────
PASS=0
FAIL=0

run_summarize_test() {
    local label="$1"
    local payload="$2"

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ${label}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    local response http_code body
    response=$(curl -s -w "\n__HTTP_CODE__%{http_code}" \
        -X POST "${LVS_URL}/v1/summarize" \
        -H "accept: application/json" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>&1) || true

    http_code=$(echo "$response" | grep "__HTTP_CODE__" | sed 's/__HTTP_CODE__//')
    body=$(echo "$response" | grep -v "__HTTP_CODE__")

    echo "  HTTP Status: ${http_code}"
    echo ""

    if [ "$http_code" = "200" ] && [ -n "$body" ]; then
        local content
        content=$(echo "$body" | python3 -c \
            "import sys,json; b=json.load(sys.stdin); print(b['choices'][0]['message']['content'])" \
            2>/dev/null || echo "")

        if [ -n "$content" ]; then
            echo "  ✅ PASS — non-empty response content"
            echo ""
            echo "  Parsed response:"
            echo "$content" | python3 -m json.tool 2>/dev/null || echo "$content"
            PASS=$((PASS + 1))
        else
            echo "  ❌ FAIL — HTTP 200 but choices[0].message.content is empty"
            echo ""
            echo "  Raw body:"
            echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
            FAIL=$((FAIL + 1))
        fi
    else
        echo "  ❌ FAIL — expected HTTP 200 with body, got HTTP=${http_code}"
        echo ""
        echo "  Raw body:"
        echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
        FAIL=$((FAIL + 1))
    fi
    echo ""
}

# ── Test 1: Jensen AI Summit India 2024 Clip ──────────────────────────────────
VIDEO1="${AUDIO_TEST_VIDEO1:-http://media-server/video_with_audio/clip1.mp4}"

PAYLOAD1=$(cat <<EOF
{
  "url": "${VIDEO1}",
  "model": "${MODEL}",
  "scenario": "presentation",
  "events": ["person speaking", "person walking"],
  "objects_of_interest": ["leather jacket", "person"],
  "chunk_duration": 15,
  "chunk_overlap_duration": 5,
  "enable_vlm_structured_output": true,
  "auto_generate_prompt": true,
  "enable_audio": true,
  "num_frames_per_second_or_fixed_frames_chunk": 15,
  "use_fps_for_chunking": false,
  "max_tokens": 512,
  "temperature": 0.2,
  "top_p": 1.0,
  "seed": 10
}
EOF
)

run_summarize_test "Test 1: Jensen AI Summit India 2024 Clip" "$PAYLOAD1"

# ── Test 2: GPU Comparison Tech News Clip ─────────────────────────────────────
VIDEO2="${AUDIO_TEST_VIDEO2:-http://media-server/video_with_audio/clip2.mp4}"

PAYLOAD2=$(cat <<EOF
{
  "url": "${VIDEO2}",
  "model": "${MODEL}",
  "scenario": "Tech news report",
  "events": ["Reporter presenting", "semiconductor ICs displayed", "Infographics presented"],
  "objects_of_interest": ["charts", "networking cables", "electronic hardware", "reporters", "GPU"],
  "chunk_duration": 15,
  "chunk_overlap_duration": 5,
  "enable_vlm_structured_output": true,
  "auto_generate_prompt": true,
  "enable_audio": true,
  "num_frames_per_second_or_fixed_frames_chunk": 15,
  "use_fps_for_chunking": false,
  "max_tokens": 512,
  "temperature": 0.2,
  "top_p": 1.0,
  "seed": 10
}
EOF
)

run_summarize_test "Test 2: Tech News Report — GPU Comparison" "$PAYLOAD2"

# ── Summary ───────────────────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Summary: ${PASS} passed, ${FAIL} failed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "  ❌ SOME TESTS FAILED"
    exit 1
else
    echo "  ✅ ALL TESTS PASSED"
    exit 0
fi
