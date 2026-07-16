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
# run_benchmark.sh — End-to-end benchmark runner for VIA Engine.
#
# Deploys exactly one compose file (the one passed with -f). That file may include
# others via Compose "include:" (e.g. media-server.yaml with nginx:alpine and
# alpine:3.20 for the perf media server), so the stack can have multiple services.
#
# Automates the full workflow:
#   1. Launches the Docker Compose stack (main file + media server)
#   2. Polls the lvs container until it's healthy
#   3. Runs the perf benchmark against lvs
#   4. Optionally tears down the stack on completion
#
# Prerequisites:
#   - Run ./setup.sh first to create .env and the shared media volume
#   - Set ARTIFACTORY_USER and ARTIFACTORY_TOKEN (in environment or .env)
#   - A benchmark config in ../perf/benchmark/ (default: config.yaml)
#
# Run with -h for full usage and examples.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCHMARK_DIR="$SCRIPT_DIR/../perf/benchmark"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; }
step()  { echo -e "${CYAN}[➜]${NC} $*"; }

usage() {
    cat <<EOF
Usage: $(basename "$0") -f <compose-file> [options]

Launch a compose stack, wait for lvs to be healthy, and run the perf benchmark.

Required:
  -f FILE        Compose file (name under compose/ or absolute path, e.g. x86-rtvi-cr-nemo3-nano_4gpu.yaml)

Options:
  -c CONFIG      Benchmark config file (default: config.yaml)
  -s SCENARIO    Benchmark scenario(s) to run (default: all scenarios in config)
  -p PORT        LVS backend port (default: 38111)
  -t TIMEOUT     Max seconds to wait for lvs health (default: 600)
  -v GPUS        VLM GPU indices, comma-separated (e.g. 0,1)
  -l GPUS        LLM GPU indices, comma-separated (e.g. 2,3)
  -G OVERLAY     GPU overlay for MODEL_PATH (default: auto-detect by GPU name):
                   auto                       — detect by GPU name (default)
                   none                       — skip overlay, use base default
                   h100 | h200 | b200 | gh200 | gb200 | l40s | thor | spark   — force a specific device
                   rtx-pro-6000 | rtx-pro-4500                                 — force a specific RTX Pro device
                   /path/to/custom.yaml       — absolute path to a custom overlay
                 GPUs without a device-specific overlay (A100, A40, V100, ...) use the base compose default.
  -r RELEASE     Release identifier for result metadata (overrides config.yaml global.release)
  -I IMAGE       Override LVS image in compose file before launch (optional)
  -O NAME        Output JSON base name for result (enables CI-style output dir)
  -d             Tear down the compose stack after benchmark completes
  -h             Show this help

CI / dashboard (env or pass -O for output): CONFIG_ID, TRIGGERED_BY, PIPELINE_URL,
  UPLOAD_TO_MINIO (non-empty = --upload), VLM_MODEL, LLM_MODEL, VISION_INPUT_TOKENS, GPU_MODEL. Use -r for release (overrides config.yaml).
  On bare metal CI nodes set DOCKER_USE_SUDO=true so docker/compose run with sudo.

Examples:
  $(basename "$0") -f x86-rtvi-cr-nemo3-nano_4gpu.yaml                              # auto-detects GPU overlay
  $(basename "$0") -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -s single_file_test
  $(basename "$0") -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -s single_file_test -d
  $(basename "$0") -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -v 0,1 -l 2,3                # split VLM/LLM across GPUs
  $(basename "$0") -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -G h100                      # force device overlay
  $(basename "$0") -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -G rtx-pro-6000              # force RTX Pro 6000 Blackwell overlay
  $(basename "$0") -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -G none                      # use base compose default
  $(basename "$0") -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -c vss_sample_config.yaml -s single_file_test file_burst_test
  $(basename "$0") -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -O vss-perf-results -d
EOF
    exit 0
}

COMPOSE_FILE=""
BENCHMARK_CONFIG="config.yaml"
SCENARIO_ARGS=""
LVS_PORT=38111
HEALTH_TIMEOUT=600
VLM_GPUS=""
LLM_GPUS=""
IMAGE_TAG_OVERRIDE=""
RELEASE=""
OUTPUT_JSON_NAME=""
TEARDOWN=false
GPU_OVERLAY="auto"

while getopts "f:c:s:p:t:v:l:G:r:I:O:dh" opt; do
    case $opt in
        f) COMPOSE_FILE="$OPTARG" ;;
        c) BENCHMARK_CONFIG="$OPTARG" ;;
        s) SCENARIO_ARGS="$OPTARG" ;;
        p) LVS_PORT="$OPTARG" ;;
        t) HEALTH_TIMEOUT="$OPTARG" ;;
        v) VLM_GPUS="$OPTARG" ;;
        l) LLM_GPUS="$OPTARG" ;;
        G) GPU_OVERLAY="$OPTARG" ;;
        r) RELEASE="$OPTARG" ;;
        I) IMAGE_TAG_OVERRIDE="$OPTARG" ;;
        O) OUTPUT_JSON_NAME="$OPTARG" ;;
        d) TEARDOWN=true ;;
        h) usage ;;
        *) usage ;;
    esac
done

# Handle multiple -s args by collecting remaining positional args as scenarios
shift $((OPTIND - 1))
if [ -n "$SCENARIO_ARGS" ]; then
    SCENARIO_ARGS="$SCENARIO_ARGS $*"
else
    SCENARIO_ARGS="$*"
fi

if [ -z "$COMPOSE_FILE" ]; then
    error "Compose file is required (-f)"
    usage
fi

# Resolve compose file path: absolute or relative to SCRIPT_DIR (compose/)
if [[ "$COMPOSE_FILE" == /* ]]; then
    COMPOSE_FILE_PATH="$COMPOSE_FILE"
else
    COMPOSE_FILE_PATH="$SCRIPT_DIR/$COMPOSE_FILE"
fi
if [ ! -f "$COMPOSE_FILE_PATH" ]; then
    error "Compose file not found: $COMPOSE_FILE_PATH"
    exit 1
fi

# --- GPU overlay selection ---
# Picks compose/overlays/gpu-<name>.yaml by matching the device name of GPU 0
# from nvidia-smi (e.g. "H100", "B200", "RTX PRO 6000 Blackwell") against known
# device-specific overlays. Assumes all GPUs on the node are the same model.
# No family fallback: GPUs without a device-specific overlay (A100, H200, A40,
# V100, ...) use the base compose file's MODEL_PATH default. Override with
# -G <auto|none|<device>|/abs/path>.

# Map a device name string (e.g. "NVIDIA H100 80GB HBM3") to a device-specific
# overlay basename. Echo "" and return 1 if no match.
match_device_overlay() {
    local name_lc
    name_lc=$(echo "$1" | tr '[:upper:]' '[:lower:]')

    case "$name_lc" in
        *"rtx pro 6000"*"blackwell"*)  echo "gpu-rtx-pro-6000.yaml" ;;
        *"rtx pro 4500"*)              echo "gpu-rtx-pro-4500.yaml" ;;
        # gb200/gh200 must come before b200/h100: "gb200" contains "b200" as a
        # substring, so *"b200"* would false-match a GB200. (gh200 doesn't
        # actually collide with h100, but order it here for symmetry.)
        *"gh200"*)                     echo "gpu-gh200.yaml" ;;
        *"h200"*)                      echo "gpu-h200.yaml" ;;
        *"h100"*)                      echo "gpu-h100.yaml" ;;
        *"gb200"*|*"grace blackwell"*) echo "gpu-gb200.yaml" ;;
        *"b200"*)                      echo "gpu-b200.yaml" ;;
        *"l40s"*)                      echo "gpu-l40s.yaml" ;;
        *"t5000"*|*"thor"*)            echo "gpu-thor.yaml" ;;
        *"gb10"*|*"spark"*)            echo "gpu-spark.yaml" ;;
        *)                             return 1 ;;
    esac
}

detect_gpu_overlay() {
    if ! command -v nvidia-smi &>/dev/null; then
        warn "nvidia-smi not found; skipping GPU overlay auto-detect"
        return 1
    fi

    local name
    name=$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits -i 0 2>/dev/null \
              | head -1 | awk '{gsub(/^ +| +$/,""); print}')
    if [ -z "$name" ]; then
        warn "nvidia-smi returned no devices; skipping GPU overlay auto-detect"
        return 1
    fi

    local overlay
    if ! overlay=$(match_device_overlay "$name"); then
        warn "No device-specific overlay for '$name'; using base compose default"
        return 1
    fi

    local overlay_path="$SCRIPT_DIR/overlays/$overlay"
    if [ ! -f "$overlay_path" ]; then
        warn "Matched device overlay $overlay but $overlay_path does not exist; using base compose default"
        return 1
    fi

    info "GPU '$name' → overlay: overlays/$overlay"
    OVERLAY_FILE_PATH="$overlay_path"
    return 0
}

OVERLAY_FILE_PATH=""
case "$GPU_OVERLAY" in
    auto)
        detect_gpu_overlay || true   # missing overlay falls back to base default
        ;;
    none|"")
        step "GPU overlay disabled (-G none); using base compose default for MODEL_PATH"
        ;;
    h100|h200|b200|l40s|thor|spark|rtx-pro-6000|rtx-pro-4500|gh200|gb200)
        OVERLAY_FILE_PATH="$SCRIPT_DIR/overlays/gpu-${GPU_OVERLAY}.yaml"
        if [ ! -f "$OVERLAY_FILE_PATH" ]; then
            error "Overlay not found: $OVERLAY_FILE_PATH"
            exit 1
        fi
        info "GPU overlay forced via -G: overlays/gpu-${GPU_OVERLAY}.yaml"
        ;;
    /*)
        OVERLAY_FILE_PATH="$GPU_OVERLAY"
        if [ ! -f "$OVERLAY_FILE_PATH" ]; then
            error "Custom overlay file not found: $OVERLAY_FILE_PATH"
            exit 1
        fi
        info "GPU overlay forced via -G (absolute path): $OVERLAY_FILE_PATH"
        ;;
    *)
        error "Invalid -G OVERLAY: '$GPU_OVERLAY'. Use auto|none|<device>|/abs/path"
        error "  devices: h100|h200|b200|gh200|gb200|l40s|thor|spark|rtx-pro-6000|rtx-pro-4500"
        exit 1
        ;;
esac

# Build the compose -f argument list used by every docker compose invocation below.
COMPOSE_FILE_ARGS=(-f "$COMPOSE_FILE_PATH")
if [ -n "$OVERLAY_FILE_PATH" ]; then
    COMPOSE_FILE_ARGS+=(-f "$OVERLAY_FILE_PATH")
fi

if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

if [ -z "${ARTIFACTORY_USER:-}" ] || [ -z "${ARTIFACTORY_TOKEN:-}" ]; then
    error "ARTIFACTORY_USER and ARTIFACTORY_TOKEN must be set"
    echo "  Example:"
    echo "  export ARTIFACTORY_USER='<username>'"
    echo "  export ARTIFACTORY_TOKEN='<token>'"
    echo "  # or set them in compose/.env"
    exit 1
fi

# --- Optional: override LVS image in compose file (e.g. for CI or local -I) ---
if [ -n "$IMAGE_TAG_OVERRIDE" ]; then
    step "Overriding LVS image in compose file with: $IMAGE_TAG_OVERRIDE"
    sed -i "s|image: nvcr.io/.*/vss-video-summarization:.*|image: ${IMAGE_TAG_OVERRIDE}|g" "$COMPOSE_FILE_PATH"
fi

# --- Export env for compose (CI may set NGC_API_KEY, LOCAL_NIM_CACHE, etc.) ---
[ -n "${NGC_API_KEY:-}" ] && export NGC_API_KEY
[ -n "${LOCAL_NIM_CACHE:-}" ] && export LOCAL_NIM_CACHE
[ -n "${NVIDIA_API_KEY:-}" ] && export NVIDIA_API_KEY
[ -n "${HF_TOKEN:-}" ] && export HF_TOKEN

# Activate `media` profile (media-server.yaml).
if [ -z "${COMPOSE_PROFILES:-}" ]; then
    export COMPOSE_PROFILES="media"
elif [[ ",${COMPOSE_PROFILES}," != *",media,"* ]]; then
    export COMPOSE_PROFILES="${COMPOSE_PROFILES},media"
fi

# --- Docker command prefix: use sudo when DOCKER_USE_SUDO is set (e.g. CI bare metal) ---
# Use sudo -E so NGC_API_KEY, LOCAL_NIM_CACHE, etc. are passed to docker compose (sudo normally resets env).
if [ -n "${DOCKER_USE_SUDO:-}" ] && [ "${DOCKER_USE_SUDO}" != "0" ] && [ "${DOCKER_USE_SUDO}" != "false" ]; then
    DOCKER_PREFIX="sudo -E "
else
    DOCKER_PREFIX=""
fi

# --- Step 1: Launch compose stack ---
step "Starting compose stack: $COMPOSE_FILE${OVERLAY_FILE_PATH:+ + overlays/$(basename "$OVERLAY_FILE_PATH")}"
LVS_BACKEND_PORT="$LVS_PORT" ${DOCKER_PREFIX}docker compose "${COMPOSE_FILE_ARGS[@]}" up -d

# Stream all service logs in the background immediately after containers start,
# so startup output (including crashes) is visible. Must start before --wait so
# logs are captured even if a container exits non-zero and the wait fails.
# setsid puts the process in its own session (and thus its own process group),
# so that kill -- -$LVS_LOG_PID can kill the entire process tree (parent + all
# child log-streamer processes spawned by docker compose). Without setsid,
# killing only the parent PID leaves orphaned children holding stdout open,
# which causes the Jenkins sh step to hang indefinitely.
setsid ${DOCKER_PREFIX}docker compose "${COMPOSE_FILE_ARGS[@]}" logs -f &
LVS_LOG_PID=$!
step "Streaming all service logs in background (PID: $LVS_LOG_PID)"

# Wait for all health checks to pass (or fail)
LVS_BACKEND_PORT="$LVS_PORT" ${DOCKER_PREFIX}docker compose "${COMPOSE_FILE_ARGS[@]}" \
    up --wait --wait-timeout "$HEALTH_TIMEOUT" || {
    # kill -- -PID sends the signal to the entire process group (the leading
    # dash before PID means "process group ID", not a single PID). This ensures
    # all child processes spawned by docker compose logs -f are also terminated.
    kill -- -$LVS_LOG_PID 2>/dev/null || true
    exit 1
}

# --- Step 2: Wait for lvs to be ready ---
step "Waiting for lvs to be ready on port $LVS_PORT (timeout: ${HEALTH_TIMEOUT}s)..."
elapsed=0
interval=5
while [ $elapsed -lt $HEALTH_TIMEOUT ]; do
    if curl -sf "http://localhost:$LVS_PORT/v1/ready" > /dev/null 2>&1; then
        info "lvs is ready (${elapsed}s)"
        break
    fi
    sleep $interval
    elapsed=$((elapsed + interval))
    printf "\r  Waiting... %ds / %ds" "$elapsed" "$HEALTH_TIMEOUT"
done

# Stop streaming logs when wait phase ends (align with Groovy path: its sh block exits here so the background job dies implicitly; we must kill explicitly)
kill -- -$LVS_LOG_PID 2>/dev/null || true

if [ $elapsed -ge $HEALTH_TIMEOUT ]; then
    echo ""
    error "lvs did not become ready within ${HEALTH_TIMEOUT}s"
    echo "  Check logs: ${DOCKER_PREFIX}docker compose ${COMPOSE_FILE_ARGS[*]} logs -f lvs"
    exit 1
fi

# --- Deployment info: surface the actual MODEL_PATH + compose layers applied ---
# Useful post-deploy verification: did the GPU overlay land, what checkpoint is
# loaded, which compose files were stitched together. Reads container labels and
# env directly so it reflects ground truth (not just the pre-deploy plan).
step "Deployment info (rtvi-vlm)"
RTVI_CONTAINER=$(${DOCKER_PREFIX}docker compose "${COMPOSE_FILE_ARGS[@]}" ps -q rtvi-vlm 2>/dev/null | head -1)
if [ -n "$RTVI_CONTAINER" ]; then
    rtvi_env=$(${DOCKER_PREFIX}docker inspect "$RTVI_CONTAINER" \
                 --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null)
    rtvi_compose_files=$(${DOCKER_PREFIX}docker inspect "$RTVI_CONTAINER" \
                 --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null)
    rtvi_model_path=$(echo "$rtvi_env" | awk -F= '/^MODEL_PATH=/{print substr($0, index($0,"=")+1); exit}')
    rtvi_model_type=$(echo "$rtvi_env" | awk -F= '/^VLM_MODEL_TO_USE=/{print $2; exit}')
    rtvi_batch_size=$(echo "$rtvi_env" | awk -F= '/^VLM_BATCH_SIZE=/{print $2; exit}')
    rtvi_input_w=$(echo "$rtvi_env" | awk -F= '/^VLM_INPUT_WIDTH=/{print $2; exit}')
    rtvi_input_h=$(echo "$rtvi_env" | awk -F= '/^VLM_INPUT_HEIGHT=/{print $2; exit}')
    rtvi_num_frames=$(echo "$rtvi_env" | awk -F= '/^VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK=/{print $2; exit}')
    info "  container         : $RTVI_CONTAINER"
    info "  VLM_MODEL_TO_USE  : ${rtvi_model_type:-(unset)}"
    info "  MODEL_PATH        : ${rtvi_model_path:-(unset)}"
    info "  input resolution  : ${rtvi_input_w:-?}x${rtvi_input_h:-?}  (frames: ${rtvi_num_frames:-default})"
    info "  VLM_BATCH_SIZE    : ${rtvi_batch_size:-(auto)}"
    info "  compose files applied:"
    echo "$rtvi_compose_files" | tr ',' '\n' | sed 's|^|      |'
else
    warn "Could not locate rtvi-vlm container; skipping deployment info"
fi

step "Waiting 30s for services to stabilize..."
sleep 30

# Quick probe: LVS must reach media-server inside the compose network (distinct from host→Artifactory).
step "Probing http://media-server/10min.mp4 from lvs container (Range: 0-0)"
if ! ${DOCKER_PREFIX}docker compose "${COMPOSE_FILE_ARGS[@]}" exec -T lvs python3 -c \
    'import urllib.request as u; r=u.Request("http://media-server/10min.mp4", headers={"Range":"bytes=0-0"}); o=u.urlopen(r, timeout=20); print("media-server probe:", o.status, "Content-Length=", o.headers.get("Content-Length"), "Content-Range=", o.headers.get("Content-Range"))'; then
    error "Media probe failed (e.g. HTTP 404). Perf videos are missing on media-server — see downloader logs (Artifactory) or pre-populate via-media-data."
    echo "  ${DOCKER_PREFIX}docker compose ${COMPOSE_FILE_ARGS[*]} logs downloader"
    exit 1
fi

# --- Step 3: Run benchmark ---
step "Running benchmark: config=$BENCHMARK_CONFIG"

# CI mode: fixed output dir for pipeline artifact copy; optional metadata and MinIO upload
if [ -n "$OUTPUT_JSON_NAME" ]; then
    CI_OUTPUT_DIR="vss-perf-report"
    export VIA_OUTPUT_DIR="$CI_OUTPUT_DIR"
else
    COMPOSE_BASENAME="${COMPOSE_FILE##*/}"
    COMPOSE_NAME="${COMPOSE_BASENAME%.yaml}"
    export VIA_OUTPUT_DIR="vss-perf-report-${COMPOSE_NAME}-$(date +%Y%m%d-%H%M%S)"
fi
step "Output directory: $VIA_OUTPUT_DIR"

BENCHMARK_CMD="python3 vss_perf_benchmark.py --config $BENCHMARK_CONFIG"
if [ -n "$SCENARIO_ARGS" ]; then
    BENCHMARK_CMD="$BENCHMARK_CMD --scenario $SCENARIO_ARGS"
    step "Scenarios: $SCENARIO_ARGS"
fi
if [ -n "$OUTPUT_JSON_NAME" ]; then
    BENCHMARK_CMD="$BENCHMARK_CMD --output-json $OUTPUT_JSON_NAME --output-dir $VIA_OUTPUT_DIR"
    [ -n "${CONFIG_ID:-}" ] && BENCHMARK_CMD="$BENCHMARK_CMD --config-id $CONFIG_ID"
    [ -n "${TRIGGERED_BY:-}" ] && BENCHMARK_CMD="$BENCHMARK_CMD --triggered-by $TRIGGERED_BY"
    [ -n "${PIPELINE_URL:-}" ] && BENCHMARK_CMD="$BENCHMARK_CMD --pipeline-url $PIPELINE_URL"
    if [ -n "${UPLOAD_TO_MINIO:-}" ] && [ "$UPLOAD_TO_MINIO" != "false" ] && [ "$UPLOAD_TO_MINIO" != "0" ]; then
        BENCHMARK_CMD="$BENCHMARK_CMD --upload"
    fi
    [ -n "${VLM_MODEL:-}" ] && BENCHMARK_CMD="$BENCHMARK_CMD --vlm-model \"${VLM_MODEL}\""
    [ -n "${LLM_MODEL:-}" ] && BENCHMARK_CMD="$BENCHMARK_CMD --llm-model \"${LLM_MODEL}\""
    [ -n "${VISION_INPUT_TOKENS:-}" ] && BENCHMARK_CMD="$BENCHMARK_CMD --vision-input-tokens \"${VISION_INPUT_TOKENS}\""
    [ -n "${GPU_MODEL:-}" ] && BENCHMARK_CMD="$BENCHMARK_CMD --gpu-model \"${GPU_MODEL}\""
fi
[ -n "$VLM_GPUS" ] && BENCHMARK_CMD="$BENCHMARK_CMD --vlm-gpus $VLM_GPUS"
[ -n "$LLM_GPUS" ] && BENCHMARK_CMD="$BENCHMARK_CMD --llm-gpus $LLM_GPUS"
[ -n "$RELEASE" ] && BENCHMARK_CMD="$BENCHMARK_CMD --release \"$RELEASE\""

cd "$BENCHMARK_DIR"
if [ ! -f vss-perf-env/bin/activate ]; then
    step "Creating virtualenv: vss-perf-env"
    # Minimal BM images may lack ensurepip; CI also installs via pipeline-helpers before this script.
    if ! python3 -m ensurepip --version >/dev/null 2>&1; then
        step "Installing python3-venv (python3 -m venv requires ensurepip)"
        if ! command -v apt-get &>/dev/null; then
            echo "Error: apt-get not found; cannot auto-install python3-venv. Please install it manually." >&2
            exit 1
        fi
        PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        SUDO_CMD=""
        if [ "$(id -u)" -ne 0 ]; then
            SUDO_CMD="sudo "
        fi
        ${SUDO_CMD}apt-get update -qq
        ${SUDO_CMD}apt-get install -y "python${PYTHON_VER}-venv"
    fi
    python3 -m venv vss-perf-env
    set +u
    source vss-perf-env/bin/activate
    set -u
    pip install -q -r requirements.txt
    info "Installed dependencies"
else
    set +u
    source vss-perf-env/bin/activate
    set -u
fi
info "Activated virtualenv: vss-perf-env"
export VIA_BACKEND="http://localhost:$LVS_PORT"
[ -n "$VLM_GPUS" ] && export VIA_VLM_GPUS="$VLM_GPUS"
[ -n "$LLM_GPUS" ] && export VIA_LLM_GPUS="$LLM_GPUS"
eval "$BENCHMARK_CMD"
benchmark_exit=$?

if [ $benchmark_exit -eq 0 ]; then
    info "Benchmark completed successfully"
else
    error "Benchmark failed (exit code: $benchmark_exit)"
    step "Recent lvs logs (debug HTTP 500 on /summarize, model, video pipeline)"
    ${DOCKER_PREFIX}docker compose "${COMPOSE_FILE_ARGS[@]}" logs --no-color --tail 400 lvs 2>/dev/null || true
fi

# --- Step 4: Teardown (optional) ---
if [ "$TEARDOWN" = true ]; then
    step "Tearing down compose stack..."
    ${DOCKER_PREFIX}docker compose "${COMPOSE_FILE_ARGS[@]}" down
    info "Stack torn down"
fi

exit $benchmark_exit
