#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
# =============================================================================
# setup_perf_env.sh — End-to-end performance environment setup for RTVI VLM
#
# What this script does:
#   1. Validates required environment variables and tools
#   2. Checks NTP clock synchronization via chrony (warns if offset > 100ms)
#   3. Detects platform and starts sys_cache_cleaner (DGX Spark / Jetson Thor only)
#   4. Downloads and extracts the VST package from Artifactory
#   5. Patches VST image tags and makes Redis port configurable via $REDIS_PORT
#   6. Fetches the LVS warehouse video and derives benchmark test videos
#   7. Detects the host IP for RTSP stream URLs
#   8. Starts nvstreamer, waits for health at http://localhost:${NVSTREAMER_HTTP_PORT}, then starts VST
#   9. Polls VST sensor streams API until live streams with /live/ paths appear
#  10. Creates a Python virtual environment with benchmark dependencies
#  11. Injects the discovered VST RTSP URL + live ports into rtvi_vlm_config_test.yaml
#      and the four platform-specific configs (h100, rtx_pro, jetson, spark)
#  12. Generates .env.perf from env vars and starts RTVI VLM via compose.perf.yaml
#
# Required environment variables:
#   ARTIFACTORY_USER    — Artifactory username (required when VST must be downloaded)
#   ARTIFACTORY_TOKEN   — Artifactory API token / password (required when VST must be downloaded)
#   NGC_API_KEY         — NGC API key for model download (nvapi-...)
#   NVIDIA_VISIBLE_DEVICES — GPU index(es) to expose to the RTVI container and DCGM exporter
#                            e.g. export NVIDIA_VISIBLE_DEVICES=0
#                            Can also be set inline: NVIDIA_VISIBLE_DEVICES=3 bash perf/setup_perf_env.sh
#
# Optional environment variables (all have sensible defaults):
#   RTVI_IMAGE          — RTVI VLM image (default: GHCR develop-latest;
#                         develop-latest-sbsa on DGX Spark)
#   VLM_MODEL_PRESET    — Optional model preset; supported values:
#                         cr2-fp8-static-kv8, cr2-fp8-dynamic-kv8,
#                         cr2-nvfp4-dynamic-kv8, cr3-nano-reasoner-fp8,
#                         cr3-nano-reasoner-nvfp4
#   VLM_MODEL_TO_USE    — VLM model key (default: cosmos-reason2)
#   MODEL_PATH          — Model source path (default: NGC Cosmos Reason2 path)
#   BACKEND_PORT        — Host port for the RTVI VLM service (default: 8010)
#   HF_TOKEN            — HuggingFace token for private model repos (optional)
#   ARTIFACTORY_BASE    — Base Artifactory URL
#   VST_PKG_URL         — Full URL to vst_package.tar.gz
#   VST_LOCAL_PACKAGE   — Local vst_package.tar.gz to use before Artifactory download
#   VST_IMAGE_REGISTRY  — Registry/repository prefix for VST images
#   VST_IMAGE_TAG       — Tag for VST images
#   VST_STREAMPROCESSING_IMAGE — Full image override for stream-processing
#   VST_SENSOR_IMAGE    — Full image override for sensor-ms
#   VST_INGRESS_IMAGE   — Full image override for ingress/nginx
#   VST_NVSTREAMER_IMAGE — Full image override for nvstreamer
#   BCD_10S_VIDEO_SOURCE_PATH — Optional local override for the canonical 10 s clip
#   BCD_10S_VIDEO_FILENAME — Canonical 10 s / 10 FPS clip filename
#   BCD_10M_VIDEO_FILENAME — Canonical 10 min / 10 FPS warehouse clip filename
#   BCD_60M_VIDEO_FILENAME — Canonical 60 min / 10 FPS warehouse clip filename
#   LVS_VIDEO_VERSION  — NGC VSS warehouse asset version used as the source
#   LVS_VIDEO_DATA_DIR — Local cache for the LVS warehouse source videos
#   REFRESH_BCD_VIDEOS — Force re-fetch and regeneration of BCD 10 FPS clips
#   VST_DIR             — Local directory to extract VST package into
#   VENV_DIR            — Python virtual environment directory
#   REDIS_PORT          — Redis port (default: 6379); change if 6379 is in use
#   CENTRALIZE_DB_PORT  — PostgreSQL port (default: 5432); change if 5432 is in use
#   VST_INGRESS_PORT    — VST nginx/HTTP API port (default: 30888)
#   VST_SENSOR_PORT     — VST sensor service HTTP port (default: 30000)
#   VST_STREAM_PROC_PORT — VST stream processor HTTP port (default: 30001)
#   VST_RTSP_PORT       — VST RTSP server port (default: 30554)
#   VST_ENVOY_BASE_ID   — Envoy shared-memory base ID (default: 1)
#   DISABLE_IPV6        — Set to 'true' to add IPv6-disable sysctl entries (default: false)
#                          WARNING: disables IPv6 system-wide; may break other services
#   VST_API_BASE        — VST HTTP API base URL (default: http://localhost:${VST_INGRESS_PORT})
#   PERF_VIDEOS_DIR     — Host path for benchmark videos (default: ${VST_DIR}/videos)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
usage() {
    cat <<'EOF'
Usage: bash perf/setup_perf_env.sh [-h|--help]

End-to-end setup script for the RTVI VLM performance benchmark environment.
Runs 12 steps: VST download → nvstreamer → VST → test videos → .env.perf
generation → RTVI VLM startup → Python venv → RTSP URL injection.

Required environment variables (must be exported before running):
  ARTIFACTORY_USER      Artifactory username (required when VST must be downloaded)
  ARTIFACTORY_TOKEN     Artifactory API token / password (required when VST must be downloaded)
  NGC_API_KEY           NGC API key for model download (nvapi-...)
  NVIDIA_VISIBLE_DEVICES  GPU index(es) for RTVI container + DCGM exporter
                          Can be set inline: NVIDIA_VISIBLE_DEVICES=3 bash perf/setup_perf_env.sh

Optional environment variables (sensible defaults shown):
  RTVI_IMAGE            RTVI VLM Docker image
                        (default: GHCR develop-latest; develop-latest-sbsa on DGX Spark)
  BACKEND_PORT          RTVI VLM host port                 (default: 8010)
  REDIS_PORT            VST Redis port                     (default: 6379)
  CENTRALIZE_DB_PORT    VST PostgreSQL port                (default: 5432)
  VST_INGRESS_PORT      VST nginx/HTTP API port            (default: 30888)
  VST_SENSOR_PORT       VST sensor service HTTP port       (default: 30000)
  VST_STREAM_PROC_PORT  VST stream processor HTTP port     (default: 30001)
  VST_RTSP_PORT         VST RTSP server port               (default: 30554)
  VST_ENVOY_BASE_ID     Envoy shared-memory base ID        (default: 1)
  DISABLE_IPV6          Set to 'true' to add IPv6-disable  (default: false)
                        sysctl entries (net.ipv6.conf.*).
                        WARNING: disables IPv6 system-wide and persists across
                        reboots via /etc/sysctl.d/99-vss.conf. May break other
                        services or container networks that rely on IPv6.
  NODE_EXPORTER_PORT    Node Exporter host port            (default: 9100)
  DCGM_EXPORTER_PORT    DCGM Exporter host port            (default: 9400)
  PROMETHEUS_PORT       Prometheus host port               (default: 9090)
  VLM_MODEL_PRESET      Optional model preset. Supported values:
                        cr2-fp8-static-kv8, cr2-fp8-dynamic-kv8,
                        cr2-nvfp4-dynamic-kv8, cr3-nano-reasoner-fp8,
                        cr3-nano-reasoner-nvfp4
                        When set, fills VLM_MODEL_TO_USE and MODEL_PATH unless
                        those variables are explicitly exported.
  VLM_MODEL_TO_USE      VLM model key                      (default: cosmos-reason2)
  MODEL_PATH            Model source path                  (default: NGC Cosmos Reason2)
  HF_TOKEN              HuggingFace token for private repos (default: empty)
  ARTIFACTORY_BASE      Base Artifactory URL
  VST_PKG_URL           Full URL to vst_package.tar.gz
  VST_LOCAL_PACKAGE     Local vst_package.tar.gz override  (default: perf/vst_package.tar.gz)
  VST_IMAGE_REGISTRY    VST image registry/repo prefix     (default: nvcr.io/rxczgrvsg8nx/vst-dev)
  VST_IMAGE_TAG         VST image tag                      (default: 2.1.0-26.04.1)
  VST_STREAMPROCESSING_IMAGE  Full stream-processing image override
  VST_SENSOR_IMAGE      Full sensor-ms image override
  VST_INGRESS_IMAGE     Full ingress image override
  VST_NVSTREAMER_IMAGE  Full nvstreamer image override
  BCD_10S_VIDEO_SOURCE_PATH
                        Optional local path to a 10 s, 10 FPS BCD clip.
                        When set, setup copies it to PERF_VIDEOS_DIR under
                        BCD_10S_VIDEO_FILENAME.
  BCD_10S_VIDEO_FILENAME
                        Canonical BCD 10 s clip filename
                        (default: FPS10_Res1080p_Dur10sec_1.mp4)
  BCD_10M_VIDEO_FILENAME
                        Canonical 10 min / 10 FPS warehouse clip
                        (default: warehouse_gopro_10m_10fps.mp4)
  BCD_60M_VIDEO_FILENAME
                        Canonical 60 min / 10 FPS warehouse clip
                        (default: warehouse_gopro_60m_10fps.mp4)
  LVS_VIDEO_VERSION     NGC VSS warehouse asset version
                        (default: v3.3.0-09152026)
  LVS_VIDEO_DATA_DIR    Local cache for LVS warehouse source videos
                        (default: \$VST_DIR/lvs-benchmark-data)
  REFRESH_BCD_VIDEOS    Force re-fetch and regeneration of BCD 10 FPS clips
                        even when same-named local files exist (default: false)
  VST_DIR               Local directory for VST package    (default: ~/rtvi-perf/vst_package)
  VST_COMPOSE_PROJECT         Docker Compose project name for VST and nvstreamer containers
                              Prefixes container names to avoid conflicts  (default: rtvi-perf-vst)

  VENV_DIR              Python virtual environment path    (default: ~/rtvi-vlm-perf-env)
  PERF_VIDEOS_DIR       Host path for benchmark videos     (default: \$VST_DIR/videos)
  VST_API_BASE          VST HTTP API base URL              (default: http://localhost:30888)
  RTVI_HEALTH_TIMEOUT   Seconds to wait for RTVI VLM ready (default: 600)
  NVSTREAMER_HTTP_PORT  nvstreamer HTTP API host port      (default: 31000)
  NVSTREAMER_RTSP_PORT  nvstreamer RTSP host port          (default: 31554)
  NVSTREAMER_POLL_TIMEOUT  Seconds to wait for nvstreamer  (default: 600)
  STREAM_POLL_TIMEOUT   Seconds to wait for VST streams    (default: 600)
  ASSET_TMPFS_SIZE      RAM size for /tmp/assets tmpfs     (default: 8g)
  VLLM_MAX_NUM_BATCHED_TOKENS  vLLM token budget cap       (default: vLLM default)
  VLLM_GPU_MEMORY_UTILIZATION  GPU memory fraction         (default: vLLM default, e.g. 0.7)
  VLLM_ENABLE_PREFIX_CACHING   Enable vLLM prefix cache    (default: false)
  VLLM_DISABLE_MM_PREPROCESSOR_CACHE
                        Disable vLLM MM preprocessor cache (default: true)
  VLLM_MM_TENSOR_IPC   Optional vLLM MM tensor IPC mode    (default: empty)
  VLLM_MULTIMODAL_TENSOR_IPC
                        Optional legacy vLLM tensor IPC toggle, true/false
  VLLM_MM_ENCODER_ATTN_BACKEND
                        Optional MM encoder attention override, e.g. XFORMERS
  VLLM_ATTENTION_BACKEND
                        Optional decoder attention override, e.g. TRITON_ATTN
  VLLM_NVFP4_GEMM_BACKEND
                        Optional NVFP4 GEMM backend override, e.g. cutlass
  VLLM_ENFORCE_EAGER    Disable CUDA graph capture in vLLM (default: false)
  VIA_EVS_SESSION       Enable session-level EVS (EVS++)    (default: false)
  VLM_VIDEO_PRUNING_RATE
                        EVS pruning rate; required to activate pruning in
                        vLLM (default: empty = no pruning; use 0.5 for EVS++)
  VLLM_EVS_SIMILARITY_THRESHOLD
                        EVS frame similarity threshold      (default: 0.4)
  VIA_EVS_TOKEN_BUDGET  EVS token budget                    (default: 1)
  RTVI_DISABLE_LIVESTREAM_PREVIEW
                        Disable live preview branch for perf (default: true)
  RTVI_RTSP_LATENCY     RTSP jitter buffer in milliseconds (default: 300)
  RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY
                        rtpjitterbuffer drop-on-latency      (default: false)
  RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS
                        rtpjitterbuffer fast start packets (default: 2)
  RTVI_ENABLE_LIVE_TIMESTAMP_FILTER
                        Opt into live timestampfilter path (default: false)
  RTVI_ENABLE_FILE_TIMESTAMP_FILTER
                        Enable file timestampfilter path (default: true)
  RTVI_EMPTY_CUDA_CACHE_ON_RESULT
                        Empty CUDA cache after every result (default: false)
  VLM_MAX_MODEL_LEN     Model context window length        (default: compose 32768)
  NUM_VLM_PROCS         Parallel VLM inference processes   (default: auto)
  VLM_BATCH_SIZE        VLM request batch size             (default: auto)
  VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK
                        Fixed frames per chunk or FPS rate (default: 80)
  RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT
                        Inject frame timestamps into prompt (default: false)

Teardown:
  bash perf/teardown_perf_env.sh   # stops all services started by this script

Example:
  export ARTIFACTORY_USER=jdoe
  export ARTIFACTORY_TOKEN=mytoken
  # Optional: override the platform-specific GHCR develop image.
  # export RTVI_IMAGE=registry/rtvi_vlm:custom
  export NGC_API_KEY=nvapi-abc123
  export BACKEND_PORT=8010
  export REDIS_PORT=6379
  export CENTRALIZE_DB_PORT=5432
  # Optional: run CR3 Nano Reasoner FP8 instead of the default CR2 FP8 static KV model.
  # export VLM_MODEL_PRESET=cr3-nano-reasoner-fp8
  # Optional: run CR3 Nano Reasoner NVFP4 on Blackwell platforms.
  # export VLM_MODEL_PRESET=cr3-nano-reasoner-nvfp4
  bash perf/setup_perf_env.sh
EOF
}

for _arg in "$@"; do
    case "${_arg}" in
        -h|--help) usage; exit 0 ;;
        *) echo "[setup_perf_env] ERROR: Unknown argument '${_arg}'. Use -h for help." >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DOTENV_PATH="${REPO_ROOT}/.env"
ENV_PERF_FILE="${ENV_PERF_FILE:-${REPO_ROOT}/docker/.env.perf}"
_CALLER_VLM_MODEL_TO_USE_SET="${VLM_MODEL_TO_USE+x}"
_CALLER_MODEL_PATH_SET="${MODEL_PATH+x}"
_CALLER_VLLM_ENABLE_PREFIX_CACHING_SET="${VLLM_ENABLE_PREFIX_CACHING+x}"
_CALLER_VLLM_DISABLE_MM_PREPROCESSOR_CACHE_SET="${VLLM_DISABLE_MM_PREPROCESSOR_CACHE+x}"
declare -A _LOADED_ENV_DEFAULTS=()

is_env_perf_bootstrap_key() {
    case "$1" in
        BACKEND_PORT | RTVI_IMAGE | NVIDIA_VISIBLE_DEVICES | NGC_API_KEY | NVIDIA_API_KEY | \
            HF_TOKEN | VLM_MODEL_PRESET | VLM_MODEL_TO_USE | MODEL_PATH | PERF_VIDEOS_DIR | \
            NODE_EXPORTER_PORT | DCGM_EXPORTER_PORT | PROMETHEUS_PORT | ASSET_TMPFS_SIZE)
            return 0
            ;;
    esac
    return 1
}

load_env_defaults() {
    local env_file="$1"
    local bootstrap_only="${2:-false}"
    [[ -f "${env_file}" ]] || return 0

    local _line _key _value
    while IFS= read -r _line || [[ -n "${_line}" ]]; do
        # Trim leading/trailing whitespace.
        _line="${_line#"${_line%%[![:space:]]*}"}"
        _line="${_line%"${_line##*[![:space:]]}"}"
        [[ -n "${_line}" && "${_line}" != \#* ]] || continue

        if [[ "${_line}" == export[[:space:]]* ]]; then
            _line="${_line#export}"
            _line="${_line#"${_line%%[![:space:]]*}"}"
        fi
        [[ "${_line}" == *=* ]] || continue

        _key="${_line%%=*}"
        _value="${_line#*=}"
        _key="${_key%"${_key##*[![:space:]]}"}"
        _value="${_value#"${_value%%[![:space:]]*}"}"
        _value="${_value%"${_value##*[![:space:]]}"}"
        [[ "${_key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        if [[ "${bootstrap_only}" == "true" ]] && ! is_env_perf_bootstrap_key "${_key}"; then
            continue
        fi

        if [[ "${_value}" == \"*\" && "${_value}" == *\" ]]; then
            _value="${_value:1:${#_value}-2}"
        elif [[ "${_value}" == \'*\' && "${_value}" == *\' ]]; then
            _value="${_value:1:${#_value}-2}"
        fi

        if [[ -z "${!_key+x}" || -n "${_LOADED_ENV_DEFAULTS[${_key}]+x}" ]]; then
            printf -v "${_key}" "%s" "${_value}"
            export "${_key}"
            _LOADED_ENV_DEFAULTS["${_key}"]=1
        fi
    done < "${env_file}"
}

load_env_defaults "${ROOT_DOTENV_PATH}"
load_env_defaults "${ENV_PERF_FILE}" true

# Published benchmark runs must disable vLLM prefix caching and the multimodal
# preprocessor cache. Do not let stale generated .env.perf files from older
# setup runs silently carry the old cache-on defaults forward; only explicit
# shell exports can override these values for non-standard experiments.
if [[ -z "${_CALLER_VLLM_ENABLE_PREFIX_CACHING_SET}" ]]; then
    unset VLLM_ENABLE_PREFIX_CACHING || true
fi
if [[ -z "${_CALLER_VLLM_DISABLE_MM_PREPROCESSOR_CACHE_SET}" ]]; then
    unset VLLM_DISABLE_MM_PREPROCESSOR_CACHE || true
fi

# ---------------------------------------------------------------------------
# Configuration — all values can be overridden by environment variables
# ---------------------------------------------------------------------------
ARTIFACTORY_BASE="${ARTIFACTORY_BASE:-https://artifactory.nvidia.com/artifactory}"
ARTIFACTORY_USER="${ARTIFACTORY_USER:-}"
ARTIFACTORY_TOKEN="${ARTIFACTORY_TOKEN:-}"
VST_PKG_URL="${VST_PKG_URL:-${ARTIFACTORY_BASE}/sw-ds-generic-bld-local/lmm/build/vst_package.tar.gz}"
VST_IMAGE_REGISTRY="${VST_IMAGE_REGISTRY:-nvcr.io/rxczgrvsg8nx/vst-dev}"
VST_IMAGE_TAG="${VST_IMAGE_TAG:-2.1.0-26.04.1}"
VST_STREAMPROCESSING_IMAGE="${VST_STREAMPROCESSING_IMAGE:-${VST_IMAGE_REGISTRY}/vst-streamprocessing:${VST_IMAGE_TAG}}"
VST_SENSOR_IMAGE="${VST_SENSOR_IMAGE:-${VST_IMAGE_REGISTRY}/vst-sensor:${VST_IMAGE_TAG}}"
VST_INGRESS_IMAGE="${VST_INGRESS_IMAGE:-${VST_IMAGE_REGISTRY}/vst-ingress:${VST_IMAGE_TAG}}"
VST_NVSTREAMER_IMAGE="${VST_NVSTREAMER_IMAGE:-${VST_IMAGE_REGISTRY}/nvstreamer:${VST_IMAGE_TAG}}"
VIDEOS_URL="${VIDEOS_URL:-${ARTIFACTORY_BASE}/sw-ds-generic-bld-local/via-engine/media/perf}"
BCD_10S_VIDEO_SOURCE_PATH="${BCD_10S_VIDEO_SOURCE_PATH:-}"
BCD_10S_VIDEO_FILENAME="${BCD_10S_VIDEO_FILENAME:-FPS10_Res1080p_Dur10sec_1.mp4}"
BCD_10M_VIDEO_FILENAME="${BCD_10M_VIDEO_FILENAME:-warehouse_gopro_10m_10fps.mp4}"
BCD_60M_VIDEO_FILENAME="${BCD_60M_VIDEO_FILENAME:-warehouse_gopro_60m_10fps.mp4}"
LVS_VIDEO_VERSION="${LVS_VIDEO_VERSION:-v3.3.0-09152026}"
REFRESH_BCD_VIDEOS="${REFRESH_BCD_VIDEOS:-false}"
VST_DIR="${VST_DIR:-${HOME}/rtvi-perf/vst_package}"
LVS_VIDEO_DATA_DIR="${LVS_VIDEO_DATA_DIR:-${VST_DIR}/lvs-benchmark-data}"
# Project name passed to docker compose for both VST and nvstreamer containers.
# Prefixes all container names (e.g. rtvi-perf-vst-redis-server-1) so they don't
# conflict with other VST deployments on the same host.
VST_COMPOSE_PROJECT="${VST_COMPOSE_PROJECT:-rtvi-perf-vst}"

VENV_DIR="${VENV_DIR:-${HOME}/rtvi-vlm-perf-env}"
REDIS_PORT="${REDIS_PORT:-6379}"
CENTRALIZE_DB_PORT="${CENTRALIZE_DB_PORT:-5432}"
VST_INGRESS_PORT="${VST_INGRESS_PORT:-30888}"
VST_SENSOR_PORT="${VST_SENSOR_PORT:-30000}"
VST_STREAM_PROC_PORT="${VST_STREAM_PROC_PORT:-30001}"
VST_RTSP_PORT="${VST_RTSP_PORT:-30554}"
VST_ENVOY_BASE_ID="${VST_ENVOY_BASE_ID:-1}"
NVSTREAMER_HTTP_PORT="${NVSTREAMER_HTTP_PORT:-31000}"
NVSTREAMER_RTSP_PORT="${NVSTREAMER_RTSP_PORT:-31554}"
DISABLE_IPV6="${DISABLE_IPV6:-false}"
VST_API_BASE="${VST_API_BASE:-http://localhost:${VST_INGRESS_PORT}}"
# Directory where benchmark videos are stored on the host.
# nvstreamer reads from this directory for RTSP streaming.
# compose.perf.yaml mounts it into the container at /opt/nvidia/rtvi/streams/perf/
# so file-based benchmarks can reference videos at that path.
# Defaults to ${VST_DIR}/videos so both uses share a single copy of each file.
PERF_VIDEOS_DIR="${PERF_VIDEOS_DIR:-${VST_DIR}/videos}"
VST_STREAMS_API="${VST_API_BASE}/vst/api/v1/sensor/streams"
BENCHMARK_VIDEOS=(
    "warehouse_gopro_10s.mp4"
    "warehouse_gopro_1m.mp4"
    "warehouse_gopro_10m.mp4"
    "warehouse_gopro_60m.mp4"
)
LVS_VIDEO_FETCH_SCRIPT="${SCRIPT_DIR}/../../../../skills/benchmarking/benchmark-video-summarization/scripts/fetch-videos.sh"
LVS_VIDEO_SOURCE_PATH="${LVS_VIDEO_DATA_DIR}/videos/warehouse_10min.mp4"

BENCHMARK_DIR="${SCRIPT_DIR}/benchmark"
BENCHMARK_CONFIG="${BENCHMARK_DIR}/rtvi_vlm_config_test.yaml"
REQUIREMENTS_FILE="${BENCHMARK_DIR}/requirements.txt"
VST_LOCAL_PACKAGE="${VST_LOCAL_PACKAGE:-${SCRIPT_DIR}/vst_package.tar.gz}"

# RTVI VLM service (compose.perf.yaml)
COMPOSE_PERF_YAML="${COMPOSE_PERF_YAML:-${REPO_ROOT}/docker/compose.perf.yaml}"
RTVI_HEALTH_TIMEOUT="${RTVI_HEALTH_TIMEOUT:-600}"  # seconds; model download can take several minutes

# RTVI VLM container configuration — used to auto-generate .env.perf at Step 12.
# NGC_API_KEY is required; the image default is selected after platform detection.
RTVI_IMAGE="${RTVI_IMAGE:-}"
NGC_API_KEY="${NGC_API_KEY:-}"
NVIDIA_API_KEY="${NVIDIA_API_KEY:-}"
HF_TOKEN="${HF_TOKEN:-}"
VLM_MODEL_PRESET="${VLM_MODEL_PRESET:-}"
VLM_MODEL_TO_USE="${VLM_MODEL_TO_USE:-}"
MODEL_PATH="${MODEL_PATH:-}"
BACKEND_PORT="${BACKEND_PORT:-8010}"
NVIDIA_VISIBLE_DEVICES="${NVIDIA_VISIBLE_DEVICES:-}"  # required — no default; user must specify GPU
# Monitoring service ports — override if the defaults conflict with existing services
NODE_EXPORTER_PORT="${NODE_EXPORTER_PORT:-9100}"
DCGM_EXPORTER_PORT="${DCGM_EXPORTER_PORT:-9400}"
PROMETHEUS_PORT="${PROMETHEUS_PORT:-9090}"
# tmpfs size for /tmp/assets inside the RTVI container (backed by RAM for zero-I/O asset serving)
ASSET_TMPFS_SIZE="${ASSET_TMPFS_SIZE:-8g}"
MAX_ASSET_STORAGE_SIZE_GB="${MAX_ASSET_STORAGE_SIZE_GB:-}"
ASSET_MAX_AGE_HOURS="${ASSET_MAX_AGE_HOURS:-0}"
ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS="${ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS:-}"
ASSET_DOWNLOAD_MAX_REDIRECTS="${ASSET_DOWNLOAD_MAX_REDIRECTS:-0}"
ASSET_DOWNLOAD_AUTH_TOKENS="${ASSET_DOWNLOAD_AUTH_TOKENS:-}"
# vLLM / VLM tuning knobs — all optional, passed through to compose.perf.yaml
VLLM_MAX_NUM_BATCHED_TOKENS="${VLLM_MAX_NUM_BATCHED_TOKENS:-8192}"  # token budget cap; leave empty to use vLLM default
VLLM_GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-0.7}"  # fraction of GPU memory reserved (e.g. 0.7)
VLLM_ENABLE_PREFIX_CACHING="${VLLM_ENABLE_PREFIX_CACHING:-false}"
VLLM_DISABLE_MM_PREPROCESSOR_CACHE="${VLLM_DISABLE_MM_PREPROCESSOR_CACHE:-true}"
RTVI_VLLM_MM_PROCESSOR_CACHE_GB="${RTVI_VLLM_MM_PROCESSOR_CACHE_GB:-0}"
RTVI_VLLM_MOE_BACKEND="${RTVI_VLLM_MOE_BACKEND:-}"
RTVI_VLM_MAX_GENERATION_TOKENS="${RTVI_VLM_MAX_GENERATION_TOKENS:-16384}"
RTVI_VLM_KAFKA_ASYNC_SEND_QUEUE_MAXSIZE="${RTVI_VLM_KAFKA_ASYNC_SEND_QUEUE_MAXSIZE:-1024}"
VLLM_NUM_PREPROCESS_WORKERS="${VLLM_NUM_PREPROCESS_WORKERS:-16}"
VLLM_MM_TENSOR_IPC="${VLLM_MM_TENSOR_IPC:-}"
VLLM_MULTIMODAL_TENSOR_IPC="${VLLM_MULTIMODAL_TENSOR_IPC:-}"
VLLM_MM_ENCODER_ATTN_BACKEND="${VLLM_MM_ENCODER_ATTN_BACKEND:-}"  # optional: XFORMERS, FLASH_ATTN, TORCH_SDPA
VLLM_ATTENTION_BACKEND="${VLLM_ATTENTION_BACKEND:-}"              # optional: TRITON_ATTN, FLASHINFER
VLLM_NVFP4_GEMM_BACKEND="${VLLM_NVFP4_GEMM_BACKEND:-}"            # optional: cutlass
VLLM_ENFORCE_EAGER="${VLLM_ENFORCE_EAGER:-false}"
TORCH_CUDNN_V8_API_DISABLED="${TORCH_CUDNN_V8_API_DISABLED:-false}"
RTVI_ENABLE_GOP_DECODE_OPT="${RTVI_ENABLE_GOP_DECODE_OPT:-true}"
VLM_USE_FPS_FOR_CHUNKING="${VLM_USE_FPS_FOR_CHUNKING:-}"
VSS_INPUT_MEDIA_VERIFICATION_TIMEOUT_SEC="${VSS_INPUT_MEDIA_VERIFICATION_TIMEOUT_SEC:-300}"
# EVS (Efficient Video Sampling) — token pruning of redundant video tokens.
# VLM_VIDEO_PRUNING_RATE is what actually activates pruning in vLLM; the
# VIA_EVS_* knobs configure the session-level EVS path in RTVI.
VIA_EVS_SESSION="${VIA_EVS_SESSION:-false}"
# No default: an unset or empty rate means no pruning, matching VIA_EVS_SESSION
# being off by default. This is the value vLLM keys pruning off, so a default
# here would silently make every perf run an EVS run. Set it (0.5 is the usual
# EVS++ value) to turn pruning on.
VLM_VIDEO_PRUNING_RATE="${VLM_VIDEO_PRUNING_RATE:-}"
VLLM_EVS_SIMILARITY_THRESHOLD="${VLLM_EVS_SIMILARITY_THRESHOLD:-0.4}"
VIA_EVS_TOKEN_BUDGET="${VIA_EVS_TOKEN_BUDGET:-1}"
PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
VLM_MAX_MODEL_LEN="${VLM_MAX_MODEL_LEN:-}"                      # model context window; leave empty for compose default
NUM_VLM_PROCS="${NUM_VLM_PROCS:-}"                              # number of parallel VLM inference processes
VLM_BATCH_SIZE="${VLM_BATCH_SIZE:-}"                            # VLM request batch size
RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT="${RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT:-false}"  # disable to save ~180 prompt tokens
RTVI_DISABLE_LIVESTREAM_PREVIEW="${RTVI_DISABLE_LIVESTREAM_PREVIEW:-true}"
RTVI_RTSP_LATENCY="${RTVI_RTSP_LATENCY:-300}"
RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY="${RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY:-false}"
RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS="${RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS:-2}"
RTVI_ENABLE_LIVE_TIMESTAMP_FILTER="${RTVI_ENABLE_LIVE_TIMESTAMP_FILTER:-false}"
RTVI_ENABLE_FILE_TIMESTAMP_FILTER="${RTVI_ENABLE_FILE_TIMESTAMP_FILTER:-true}"
RTVI_EMPTY_CUDA_CACHE_ON_RESULT="${RTVI_EMPTY_CUDA_CACHE_ON_RESULT:-false}"
# Frame sampling: fixed frame count per chunk (e.g. 80) or FPS-based (e.g. 2.0 with use_fps_for_chunking=true in YAML)
VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK="${VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK:-20}"

# ---------------------------------------------------------------------------
# Colors — only when stdout is a terminal (disabled when piped/redirected)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    _C_RESET='\033[0m'
    _C_CYAN='\033[0;36m'      # info prefix
    _C_BOLD_CYAN='\033[1;36m' # step headers
    _C_YELLOW='\033[0;33m'    # warnings
    _C_RED='\033[0;31m'       # errors
    _C_GREEN='\033[0;32m'     # success / ready
    _C_BOLD='\033[1m'         # banners
    _C_SECTION='\033[1;35m'   # bold magenta — section headings
else
    _C_RESET='' _C_CYAN='' _C_BOLD_CYAN='' _C_YELLOW='' _C_RED='' _C_GREEN='' _C_BOLD='' _C_SECTION=''
fi

# Helpers
# ---------------------------------------------------------------------------
log()  {
    # Bold cyan for "Step X/12:" lines, regular cyan prefix otherwise
    if [[ "$*" == Step* ]]; then
        echo -e "${_C_BOLD_CYAN}[setup_perf_env]${_C_RESET} ${_C_BOLD}$*${_C_RESET}"
    else
        echo -e "${_C_CYAN}[setup_perf_env]${_C_RESET} $*"
    fi
}
warn() { echo -e "${_C_YELLOW}[setup_perf_env] WARNING:${_C_RESET} $*" >&2; }
die() {
    echo -e "${_C_RED}[setup_perf_env] ERROR:${_C_RESET} $*" >&2
    # When compose containers were started this run, bring them down automatically
    # so their ports are released and a re-run doesn't hit conflicts.
    if [[ "${RTVI_STARTED:-false}" == "true" ]] && [[ -f "${COMPOSE_PERF_YAML:-}" ]]; then
        echo -e "${_C_YELLOW}[setup_perf_env] WARNING:${_C_RESET} Stopping monitoring + RTVI containers to release ports..." >&2
        local _die_env_arg=()
        [[ -f "${ENV_PERF_FILE:-}" ]] && _die_env_arg=(--env-file "${ENV_PERF_FILE}")
        docker compose -f "${COMPOSE_PERF_YAML}" "${_die_env_arg[@]}" down 2>&1 | \
            sed "s/^/  [compose down] /" >&2 || true
    fi
    # Always stop VST and nvstreamer on error so their ports are released for a re-run.
    if [[ -f "${VST_DIR:-}/deploy.sh" ]]; then
        echo -e "${_C_YELLOW}[setup_perf_env] WARNING:${_C_RESET} Stopping VST and nvstreamer to release ports..." >&2
        (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" bash deploy.sh down vst 2>/dev/null) || true
        (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" bash deploy.sh down nvstreamer 2>/dev/null) || true
    fi
    # Stop cache cleaner if we started one
    local _pid_file="/tmp/sys_cache_cleaner.pid"
    if [[ -f "${_pid_file}" ]]; then
        local _pid
        _pid=$(cat "${_pid_file}" 2>/dev/null || true)
        if [[ -n "${_pid}" ]]; then
            sudo kill "${_pid}" 2>/dev/null || true
            rm -f "${_pid_file}"
        fi
    fi
    exit 1
}

apply_model_preset() {
    local _preset="$1"
    local _preset_model=""
    local _preset_path=""

    case "${_preset}" in
        "")
            return 0
            ;;
        cr2-fp8-static-kv8)
            _preset_model="cosmos-reason2"
            _preset_path="ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-static-kv8"
            ;;
        cr2-fp8-dynamic-kv8)
            _preset_model="cosmos-reason2"
            _preset_path="ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-dynamic-kv8"
            ;;
        cr2-nvfp4-dynamic-kv8)
            _preset_model="cosmos-reason2"
            _preset_path="ngc:nim/nvidia/cosmos-reason2-8b:0303-fp4-dynamic-kv8"
            ;;
        cr3-nano-reasoner-fp8)
            _preset_model="cosmos-reason3"
            _preset_path="ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix"
            ;;
        cr3-nano-reasoner-nvfp4)
            _preset_model="cosmos-reason3"
            _preset_path="ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-nvfp4-full-quantize-final_format_fix"
            ;;
        *)
            die "Unknown VLM_MODEL_PRESET='${_preset}'. Supported values: cr2-fp8-static-kv8, cr2-fp8-dynamic-kv8, cr2-nvfp4-dynamic-kv8, cr3-nano-reasoner-fp8, cr3-nano-reasoner-nvfp4."
            ;;
    esac

    if [[ -z "${_CALLER_VLM_MODEL_TO_USE_SET}" ]]; then
        VLM_MODEL_TO_USE="${_preset_model}"
    fi
    if [[ -z "${_CALLER_MODEL_PATH_SET}" ]]; then
        MODEL_PATH="${_preset_path}"
    fi
}

infer_vlm_model_from_model_path() {
    if [[ -n "${_CALLER_VLM_MODEL_TO_USE_SET}" ]]; then
        return 0
    fi

    case "${MODEL_PATH}" in
        *cosmos3-nano-reasoner*)
            VLM_MODEL_TO_USE="cosmos-reason3"
            ;;
        *cosmos-reason2-8b*)
            VLM_MODEL_TO_USE="cosmos-reason2"
            ;;
    esac
}

apply_model_preset "${VLM_MODEL_PRESET}"
infer_vlm_model_from_model_path
VLM_MODEL_TO_USE="${VLM_MODEL_TO_USE:-cosmos-reason2}"
MODEL_PATH="${MODEL_PATH:-ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-static-kv8}"

if [[ "${VLLM_ENABLE_PREFIX_CACHING,,}" != "false" || "${VLLM_DISABLE_MM_PREPROCESSOR_CACHE,,}" != "true" ]]; then
    warn "Non-standard vLLM cache settings: VLLM_ENABLE_PREFIX_CACHING=${VLLM_ENABLE_PREFIX_CACHING}, \
VLLM_DISABLE_MM_PREPROCESSOR_CACHE=${VLLM_DISABLE_MM_PREPROCESSOR_CACHE}. \
Published benchmarks should use false/true."
fi

section() {
    echo ""
    echo -e "${_C_SECTION}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${_C_RESET}"
    echo -e "${_C_SECTION}  $*${_C_RESET}"
    echo -e "${_C_SECTION}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${_C_RESET}"
    echo ""
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not found. Please install it."
}

validate_bcd_video() {
    local video="$1"
    local expected_duration="$2"
    local resolution fps actual_duration

    [[ -s "${video}" ]] || return 1
    resolution="$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "${video}")" \
        || return 1
    fps="$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of default=nw=1:nk=1 "${video}")" \
        || return 1
    actual_duration="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "${video}")" \
        || return 1
    [[ "${resolution}" == "1920x1080" && "${fps}" == "10/1" ]] \
        && awk -v actual="${actual_duration}" -v expected="${expected_duration}" \
            'BEGIN { exit !(actual >= expected - 0.1 && actual <= expected + 0.1) }'
}

# Returns 0 (true) if the given TCP port is already bound on the host.
port_in_use() {
    ss -tlnH 2>/dev/null | awk '{print $4}' | grep -qE ":${1}$"
}

# ---------------------------------------------------------------------------
# Cleanup — invoked on Ctrl-C (SIGINT) or SIGTERM
# Brings down any containers started by this script so nothing is left dangling.
# NOT registered on EXIT so that a successful run leaves VST running for benchmarks.
# ---------------------------------------------------------------------------
VST_STARTED=false
RTVI_STARTED=false

cleanup() {
    echo ""
    warn "Interrupted — tearing down containers..."

    # Stop cache cleaner if we started one
    local pid_file="/tmp/sys_cache_cleaner.pid"
    if [[ -f "${pid_file}" ]]; then
        local pid
        pid=$(cat "${pid_file}" 2>/dev/null || true)
        if [[ -n "${pid}" ]]; then
            sudo kill "${pid}" 2>/dev/null || true
            rm -f "${pid_file}"
            log "  Cache cleaner stopped."
        fi
    fi

    # Stop RTVI VLM service and monitoring containers (dcgm-exporter, node-exporter, prometheus).
    # Always pass --env-file when available so compose can resolve required vars (e.g. BACKEND_PORT?).
    # Fall back to running without it — BACKEND_PORT etc. are already in the shell environment.
    if [[ -f "${COMPOSE_PERF_YAML}" ]]; then
        log "  Stopping RTVI VLM + monitoring containers (dcgm-exporter, node-exporter, prometheus)..."
        if [[ -f "${ENV_PERF_FILE}" ]]; then
            docker compose -f "${COMPOSE_PERF_YAML}" --env-file "${ENV_PERF_FILE}" down 2>&1 | \
                sed "s/^/  [compose down] /" || true
        else
            docker compose -f "${COMPOSE_PERF_YAML}" down 2>&1 | \
                sed "s/^/  [compose down] /" || true
        fi
        log "  compose.perf.yaml containers stopped."
    fi

    # Stop VST and nvstreamer — always attempt if deploy.sh exists.
    # deploy.sh down is idempotent so safe even if they were already running
    # before the script started or were never started this run.
    if [[ -f "${VST_DIR}/deploy.sh" ]]; then
        log "  Stopping VST..."
        (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" bash deploy.sh down vst 2>/dev/null) || true
        log "  Stopping nvstreamer..."
        (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" bash deploy.sh down nvstreamer 2>/dev/null) || true
        log "  VST and nvstreamer stopped."
    fi

    exit 130   # standard exit code for SIGINT
}

trap cleanup INT TERM

section "Prerequisites"

# ---------------------------------------------------------------------------
# Step 1: Validate prerequisites
# ---------------------------------------------------------------------------
log "Step 1/12: Validating prerequisites..."

# Collect missing required variables so we can show them all at once
_missing=()
_needs_artifactory=false
if [[ ! -f "${VST_LOCAL_PACKAGE}" && ! -f "${VST_DIR}/vst_package.tar.gz" ]]; then
    _needs_artifactory=true
fi
if [[ "${_needs_artifactory}" == "true" ]]; then
    [[ -n "${ARTIFACTORY_USER}" ]]      || _missing+=("ARTIFACTORY_USER")
    [[ -n "${ARTIFACTORY_TOKEN}" ]]     || _missing+=("ARTIFACTORY_TOKEN")
fi
if [[ -n "${BCD_10S_VIDEO_SOURCE_PATH}" && ! -f "${BCD_10S_VIDEO_SOURCE_PATH}" ]]; then
    _missing+=("BCD_10S_VIDEO_SOURCE_PATH")
fi
[[ -n "${NGC_API_KEY:-}" ]]             || _missing+=("NGC_API_KEY")
[[ -n "${NVIDIA_VISIBLE_DEVICES:-}" ]]  || _missing+=("NVIDIA_VISIBLE_DEVICES")

if [[ "${#_missing[@]}" -gt 0 ]]; then
    echo "" >&2
    echo -e "${_C_RED}[setup_perf_env] ERROR: The following required variables are not set.${_C_RESET}" >&2
    echo "" >&2
    echo "  Export them before running this script:" >&2
    echo "" >&2
    for _var in "${_missing[@]}"; do
        case "${_var}" in
            ARTIFACTORY_USER)      echo -e "    ${_C_YELLOW}export ARTIFACTORY_USER=<your_artifactory_username>${_C_RESET}" >&2 ;;
            ARTIFACTORY_TOKEN)     echo -e "    ${_C_YELLOW}export ARTIFACTORY_TOKEN=<your_artifactory_token>${_C_RESET}" >&2 ;;
            BCD_10S_VIDEO_SOURCE_PATH)
                echo -e "    ${_C_YELLOW}export BCD_10S_VIDEO_SOURCE_PATH=/path/to/10s_10fps_clip.mp4${_C_RESET}" >&2
                ;;
            NGC_API_KEY)           echo -e "    ${_C_YELLOW}export NGC_API_KEY=<nvapi-...>${_C_RESET}" >&2 ;;
            NVIDIA_VISIBLE_DEVICES)
                echo -e "    ${_C_YELLOW}export NVIDIA_VISIBLE_DEVICES=<gpu_index>${_C_RESET}  # or inline: NVIDIA_VISIBLE_DEVICES=0 bash perf/setup_perf_env.sh" >&2
                ;;
        esac
    done
    echo "" >&2
    echo "  Optional (override defaults if needed):" >&2
    echo "    export BACKEND_PORT=${BACKEND_PORT}            # default: 8010" >&2
    echo "    export REDIS_PORT=${REDIS_PORT}              # default: 6379" >&2
    echo "    export CENTRALIZE_DB_PORT=${CENTRALIZE_DB_PORT}  # default: 5432" >&2
    echo "    export NODE_EXPORTER_PORT=9100  # default: 9100" >&2
    echo "    export DCGM_EXPORTER_PORT=9400  # default: 9400" >&2
    echo "    export PROMETHEUS_PORT=9090     # default: 9090" >&2
    echo "" >&2
    echo -e "  Then re-run: ${_C_CYAN}bash perf/setup_perf_env.sh${_C_RESET}" >&2
    echo "" >&2
    exit 1
fi

log "  ${_C_GREEN}All required variables are set.${_C_RESET}"
log "  BACKEND_PORT         = ${BACKEND_PORT}"
log "  REDIS_PORT           = ${REDIS_PORT}"
log "  CENTRALIZE_DB_PORT   = ${CENTRALIZE_DB_PORT}"
log "  VST_INGRESS_PORT     = ${VST_INGRESS_PORT}"
log "  VST_SENSOR_PORT      = ${VST_SENSOR_PORT}"
log "  VST_STREAM_PROC_PORT = ${VST_STREAM_PROC_PORT}"
log "  VST_RTSP_PORT        = ${VST_RTSP_PORT}"
[[ "${VST_ENVOY_BASE_ID}" =~ ^[0-9]+$ ]] || die "VST_ENVOY_BASE_ID must be a non-negative integer"
log "  VST_ENVOY_BASE_ID    = ${VST_ENVOY_BASE_ID}"

require_cmd curl
require_cmd jq
require_cmd tar
require_cmd python3
require_cmd sed
require_cmd ffprobe
_needs_lvs_generation="${REFRESH_BCD_VIDEOS}"
if [[ -z "${BCD_10S_VIDEO_SOURCE_PATH}" ]] \
    && ! validate_bcd_video "${PERF_VIDEOS_DIR}/${BCD_10S_VIDEO_FILENAME}" 10; then
    _needs_lvs_generation=true
fi
validate_bcd_video "${PERF_VIDEOS_DIR}/${BCD_10M_VIDEO_FILENAME}" 600 \
    || _needs_lvs_generation=true
validate_bcd_video "${PERF_VIDEOS_DIR}/${BCD_60M_VIDEO_FILENAME}" 3600 \
    || _needs_lvs_generation=true
if [[ "${_needs_lvs_generation}" == "true" ]]; then
    require_cmd ffmpeg
    if [[ ! -f "${LVS_VIDEO_SOURCE_PATH}" || "${REFRESH_BCD_VIDEOS}" == "true" ]]; then
        require_cmd ngc
    fi
    [[ -f "${LVS_VIDEO_FETCH_SCRIPT}" ]] || die "LVS video fetch script not found: ${LVS_VIDEO_FETCH_SCRIPT}"
fi

# Check that the current user can run docker without sudo.
# Running docker commands as root (via sudo) causes containers and volumes to be
# owned by root, which can cause permission errors in subsequent steps.
if ! id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    echo "" >&2
    echo -e "${_C_RED}[setup_perf_env] ERROR: User '${USER}' is not in the 'docker' group.${_C_RESET}" >&2
    echo "" >&2
    echo "  Add your user to the docker group and re-login:" >&2
    echo -e "    ${_C_YELLOW}sudo usermod -aG docker \${USER}${_C_RESET}" >&2
    echo -e "    ${_C_YELLOW}newgrp docker${_C_RESET}   # apply without full re-login (current shell only)" >&2
    echo "  Or log out and log back in for the group change to take effect." >&2
    echo "" >&2
    die "Docker group membership required."
fi
log "  ${_C_GREEN}User '${USER}' is in the 'docker' group.${_C_RESET}"

# Check that the 'nvidia' runtime is registered with the Docker daemon.
# VST containers (stream-processing, nvstreamer) specify runtime: nvidia in their
# compose files. If daemon.json does not register the runtime, Docker fails with:
#   "Error response from daemon: unknown or invalid runtime name: nvidia"
# Fix: run 'sudo nvidia-ctk runtime configure --runtime=docker' then restart Docker.
if ! docker info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}' 2>/dev/null \
        | tr ' ' '\n' | grep -qx nvidia; then
    echo "" >&2
    echo -e "${_C_RED}[setup_perf_env] ERROR: nvidia Docker runtime not configured.${_C_RESET}" >&2
    echo "" >&2
    echo "  Register the nvidia runtime and restart Docker:" >&2
    echo -e "    ${_C_YELLOW}sudo nvidia-ctk runtime configure --runtime=docker${_C_RESET}" >&2
    echo -e "    ${_C_YELLOW}sudo systemctl restart docker${_C_RESET}" >&2
    echo "" >&2
    echo "  Or manually add to /etc/docker/daemon.json:" >&2
    echo -e "    ${_C_YELLOW}{ \"runtimes\": { \"nvidia\": { \"path\": \"nvidia-container-runtime\", \"runtimeArgs\": [] } } }${_C_RESET}" >&2
    echo "" >&2
    die "nvidia Docker runtime required."
fi
log "  ${_C_GREEN}nvidia Docker runtime is configured.${_C_RESET}"

# Confirm which GPU selection will be used by the RTVI container and DCGM exporter.
# Avoid plain nvidia-smi here; GPU metrics collection should use nvidia-smi dmon/DCGM.
log "  NVIDIA_VISIBLE_DEVICES = ${NVIDIA_VISIBLE_DEVICES}  (RTVI container + DCGM exporter)"

# ---------------------------------------------------------------------------
# Step 2: NTP clock synchronization check
# ---------------------------------------------------------------------------
log "Step 2/12: Checking NTP clock synchronization..."
# Accurate latency measurements require system clocks to be synchronized.
# Skewed clocks cause timestamp-based chunk latency calculations to appear
# artificially high or low. This step warns but does not block setup.

NTP_OK=true

# Check systemd timedatectl (available on most modern Linux systems)
if command -v timedatectl >/dev/null 2>&1; then
    # timedatectl show is more reliable than parsing human output
    NTP_SYNC=$(timedatectl show --property=NTPSynchronized --value 2>/dev/null \
               || timedatectl 2>/dev/null | awk '/NTP synchronized/{print $NF}')
    if [[ "${NTP_SYNC}" == "yes" ]]; then
        log "  timedatectl: NTP synchronized = yes"
    else
        NTP_OK=false
        warn "  timedatectl: NTP synchronized = ${NTP_SYNC:-unknown}"
    fi
else
    warn "  timedatectl not found — skipping systemd NTP check"
fi

# Check chrony tracking offset (authoritative for latency benchmarking)
if command -v chronyc >/dev/null 2>&1; then
    TRACKING=$(chronyc tracking 2>/dev/null || true)
    if [[ -n "${TRACKING}" ]]; then
        OFFSET_VAL=$(echo "${TRACKING}" | awk '/System time/{print $4}')
        OFFSET_DIR=$(echo "${TRACKING}" | awk '/System time/{print $5}')
        RMS_OFFSET=$(echo "${TRACKING}" | awk '/RMS offset/{print $4, $5}')
        STRATUM=$(echo "${TRACKING}" | awk '/Stratum/{print $3}')
        log "  chronyc tracking:"
        log "    Stratum      : ${STRATUM:-unknown}"
        log "    System offset: ${OFFSET_VAL:-unknown} seconds ${OFFSET_DIR:-}"
        log "    RMS offset   : ${RMS_OFFSET:-unknown}"

        # Warn if offset > 100ms — the benchmark measures latencies at chunk
        # boundaries using wall-clock timestamps; > 100ms introduces noise.
        if [[ -n "${OFFSET_VAL}" ]]; then
            TOO_LARGE=$(awk -v v="${OFFSET_VAL}" 'BEGIN{print (v+0 > 0.1) ? "yes" : "no"}')
            if [[ "${TOO_LARGE}" == "yes" ]]; then
                NTP_OK=false
                warn "  System clock offset ${OFFSET_VAL}s exceeds 100ms threshold"
                warn "  Chunk latency results may be inaccurate"
            fi
        fi
    else
        warn "  chronyc tracking returned no output — is chronyd running?"
        NTP_OK=false
    fi
else
    warn "  chrony not installed — cannot verify clock accuracy"
    warn "  Install with: sudo apt install chrony -y"
    NTP_OK=false
fi

if [[ "${NTP_OK}" == "false" ]]; then
    warn "  -------------------------------------------------------"
    warn "  Clock sync issue detected. To fix:"
    warn "    sudo apt install chrony -y"
    warn "    sudo systemctl enable --now chronyd"
    warn "    chronyc sources -v        # verify reachable time sources"
    warn "    chronyc tracking          # confirm offset < 100ms"
    warn "  -------------------------------------------------------"
    warn "  Continuing setup — benchmark latency results may be skewed."
else
    log "  Clock synchronization: OK"
fi

# ---------------------------------------------------------------------------
# Step 3: Platform detection and system cache cleaner
# ---------------------------------------------------------------------------
log "Step 3/12: Detecting platform for cache cleaner..."
# perf/tools/sys_cache_cleaner.sh drops Linux page/dentry/inode caches every
# 3 seconds and disables transparent huge pages. Required on DGX Spark (GB10)
# and Jetson Thor to prevent memory pressure accumulation during long-running
# VLM benchmarks. Must run as root; sudo is invoked below.

CACHE_CLEANER_SCRIPT="${SCRIPT_DIR}/tools/sys_cache_cleaner.sh"
PLATFORM="unknown"

# Jetson Thor: ARM64 + device-tree model string contains "Jetson"
if [[ "$(uname -m)" == "aarch64" ]]; then
    DT_MODEL=""
    if [[ -r /proc/device-tree/model ]]; then
        DT_MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)
    fi
    if echo "${DT_MODEL}" | grep -qi "jetson"; then
        PLATFORM="jetson"
        log "  Jetson platform detected: ${DT_MODEL}"
    fi
fi

# DGX Spark (GB10 ARM): check DMI product name only.
# DGX Spark is ARM-based with a GB10 chip and unified memory.
# Do NOT match generic "dgx" (catches DGX B200 servers) or "B200" (B200 SXM on x86 HGX/DGX).
if [[ "${PLATFORM}" == "unknown" ]]; then
    DMI_PRODUCT=""
    if [[ -r /sys/class/dmi/id/product_name ]]; then
        DMI_PRODUCT=$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)
    fi
    if echo "${DMI_PRODUCT}" | grep -qiE "DGX Spark|GB10"; then
        PLATFORM="dgx_spark"
        log "  DGX Spark platform detected (DMI: ${DMI_PRODUCT})"
    fi
fi

if [[ -z "${RTVI_IMAGE}" ]]; then
    if [[ "${PLATFORM}" == "dgx_spark" ]]; then
        RTVI_IMAGE="ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-latest-sbsa"
    else
        RTVI_IMAGE="ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-latest"
    fi
    log "  RTVI VLM image default: ${RTVI_IMAGE}"
fi

if [[ "${PLATFORM}" == "jetson" || "${PLATFORM}" == "dgx_spark" ]]; then
    if [[ ! -f "${CACHE_CLEANER_SCRIPT}" ]]; then
        warn "  Cache cleaner script not found at ${CACHE_CLEANER_SCRIPT} — skipping."
    else
        log "  Starting sys_cache_cleaner.sh in background (requires sudo)..."
        sudo bash "${CACHE_CLEANER_SCRIPT}" &
        CACHE_CLEANER_PID=$!
        echo "${CACHE_CLEANER_PID}" > /tmp/sys_cache_cleaner.pid
        log "  Cache cleaner started (PID ${CACHE_CLEANER_PID}, saved to /tmp/sys_cache_cleaner.pid)"
        log "  To stop after benchmarks: sudo kill \$(cat /tmp/sys_cache_cleaner.pid)"
    fi
else
    log "  Platform '${PLATFORM}' does not require cache cleaner — skipping."
fi

section "Stream Download"

# ---------------------------------------------------------------------------
# Step 4: Download and extract VST package
# ---------------------------------------------------------------------------
log "Step 4/12: Downloading VST package..."

mkdir -p "${VST_DIR}"

VST_TARBALL="${VST_DIR}/vst_package.tar.gz"

if [[ -f "${VST_LOCAL_PACKAGE}" ]]; then
    log "  Using local VST package: ${VST_LOCAL_PACKAGE}"
    if [[ -f "${VST_TARBALL}" ]] && cmp -s "${VST_LOCAL_PACKAGE}" "${VST_TARBALL}"; then
        log "  Local VST package already staged at ${VST_TARBALL}."
    else
        cp -f "${VST_LOCAL_PACKAGE}" "${VST_TARBALL}" \
            || die "Failed to stage local VST package from ${VST_LOCAL_PACKAGE}"
    fi
elif [[ -f "${VST_TARBALL}" ]]; then
    log "  vst_package.tar.gz already present, skipping download."
else
    log "  Downloading from ${VST_PKG_URL} ..."
    curl -fsSL \
        -u "${ARTIFACTORY_USER}:${ARTIFACTORY_TOKEN}" \
        -o "${VST_TARBALL}" \
        "${VST_PKG_URL}" \
        || die "Failed to download VST package from ${VST_PKG_URL}"
fi

# Stop any previously running VST containers BEFORE overwriting deploy.sh.
# The tarball re-extraction below replaces deploy.sh with a fresh copy and
# then patches it with the new COMPOSE_PROJECT_NAME prefix, so by Step 8 the
# patched deploy.sh can only stop containers from the *current* project.
# Running down here (against the *previous* deploy.sh, which may have an older
# or unprefixed project name) ensures no stale containers hold ports 6379 etc.
if [[ -f "${VST_DIR}/deploy.sh" ]]; then
    log "  Stopping pre-existing VST containers before re-extraction..."
    (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" bash deploy.sh down vst       2>/dev/null) || true
    (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" bash deploy.sh down nvstreamer 2>/dev/null) || true
fi
# Belt-and-suspenders: force-remove any container whose name contains the project
# prefix, regardless of which project name deploy.sh used internally.  This catches
# auto-named containers (e.g. the base 'redis' service) from prior runs where the
# project suffix differed.  Safe here because nvstreamer/VST are not yet running
# for the current invocation — they are started in Step 8 after re-extraction.
_stale_vst=$(docker ps -aq --filter "name=${VST_COMPOSE_PROJECT}" 2>/dev/null || true)
if [[ -n "${_stale_vst}" ]]; then
    log "  Force-removing remaining ${VST_COMPOSE_PROJECT} containers (project-name drift)..."
    echo "${_stale_vst}" | xargs docker rm -f 2>/dev/null || true
fi

log "  Extracting VST package to ${VST_DIR} ..."
tar -xzf "${VST_TARBALL}" -C "${VST_DIR}" --strip-components=1

set_compose_env_value() {
    local env_file="$1"
    local key="$2"
    local value="$3"
    local escaped_value="${value//&/\\&}"

    if [[ ! -f "${env_file}" ]]; then
        warn "  ${env_file} not found — ${key} image override skipped."
        return 0
    fi

    if grep -q "^${key}=" "${env_file}"; then
        sed -i "s|^${key}=.*|${key}=${escaped_value}|" "${env_file}" \
            || die "Failed to patch ${key} in ${env_file}"
    else
        printf '%s=%s\n' "${key}" "${value}" >> "${env_file}" \
            || die "Failed to append ${key} to ${env_file}"
    fi
    log "  Set ${key}=${value} in ${env_file#"${VST_DIR}/"}"
}

patch_direct_vst_image_refs() {
    local image_name="$1"
    local image_value="$2"
    local escaped_value="${image_value//&/\\&}"
    local patched_count=0
    local compose_file

    for compose_file in "${_vst_yaml_files[@]}"; do
        if grep -Eq "^[[:space:]]*image:[[:space:]]*['\"]?[^[:space:]#'\"]*${image_name}:" \
            "${compose_file}"; then
            sed -i -E \
                "s|^([[:space:]]*image:[[:space:]]*)['\"]?[^[:space:]#'\"]*${image_name}:[^[:space:]#'\"]*['\"]?|\\1${escaped_value}|g" \
                "${compose_file}" || die "Failed to patch ${image_name} image in ${compose_file}"
            patched_count=$((patched_count + 1))
        fi
    done

    if [[ "${patched_count}" -gt 0 ]]; then
        log "  Patched direct ${image_name} image reference(s) in ${patched_count} compose file(s)."
    fi
}

# Pin the VST package to the requested image set. The current VST package reads
# these images from compose.env; the direct compose rewrite keeps this robust if
# a future package version inlines image names in YAML.
log "  Patching VST images:"
log "    vst-streamprocessing: ${VST_STREAMPROCESSING_IMAGE}"
log "    vst-sensor:           ${VST_SENSOR_IMAGE}"
log "    vst-ingress:          ${VST_INGRESS_IMAGE}"
log "    nvstreamer:           ${VST_NVSTREAMER_IMAGE}"
set_compose_env_value "${VST_DIR}/stream-processing/compose.env" \
    "VST_STREAM_PROCESSOR_IMAGE" "${VST_STREAMPROCESSING_IMAGE}"
set_compose_env_value "${VST_DIR}/stream-processing/compose.env" \
    "VST_SENSOR_IMAGE" "${VST_SENSOR_IMAGE}"
set_compose_env_value "${VST_DIR}/stream-processing/compose.env" \
    "NGINX_IMAGE" "${VST_INGRESS_IMAGE}"
set_compose_env_value "${VST_DIR}/nvstreamer/compose.env" \
    "NVSTREAMER_IMAGE" "${VST_NVSTREAMER_IMAGE}"
_vst_yaml_files=()
while IFS= read -r -d '' _f; do _vst_yaml_files+=("${_f}"); done < <(
    find "${VST_DIR}" \( -name "*.yaml" -o -name "*.yml" \) -print0 2>/dev/null
)
patch_direct_vst_image_refs "vst-streamprocessing" "${VST_STREAMPROCESSING_IMAGE}"
patch_direct_vst_image_refs "vst-sensor" "${VST_SENSOR_IMAGE}"
patch_direct_vst_image_refs "vst-ingress" "${VST_INGRESS_IMAGE}"
patch_direct_vst_image_refs "nvstreamer" "${VST_NVSTREAMER_IMAGE}"

# Thor exposes libcuda under /opt/nvidia/l4t-gpu-libs, while these VST
# binaries dlopen the absolute path /usr/lib/aarch64-linux-gnu/libcuda.so.
if [[ "${PLATFORM}" == "jetson" ]]; then
    _host_libcuda=$(readlink -f /usr/lib/aarch64-linux-gnu/libcuda.so 2>/dev/null || true)
    if [[ "${_host_libcuda}" == /opt/nvidia/l4t-gpu-libs/*/libcuda.so* ]]; then
        for _vst_yaml in "${_vst_yaml_files[@]}"; do
            if grep -q 'exec /home/vst/vst_release/launch_vst' "${_vst_yaml}"; then
                sed -i \
                    "s|exec /home/vst/vst_release/launch_vst|ln -sf ${_host_libcuda} /usr/lib/aarch64-linux-gnu/libcuda.so \&\& exec /home/vst/vst_release/launch_vst|g" \
                    "${_vst_yaml}" || die "Failed to patch Thor libcuda path in ${_vst_yaml}"
            fi
        done
        log "  Patched Thor VST launchers to expose ${_host_libcuda} at the libcuda path they expect."
    else
        die "Thor libcuda target is unavailable: ${_host_libcuda:-not found}"
    fi
fi

# ---------------------------------------------------------------------------
# Step 5: Patch VST for configurable Redis port, PostgreSQL port, and VST ports
# ---------------------------------------------------------------------------
log "Step 5/12: Patching VST for configurable Redis port (REDIS_PORT=${REDIS_PORT}) and PostgreSQL port (CENTRALIZE_DB_PORT=${CENTRALIZE_DB_PORT})..."

STREAM_PROC_COMPOSE="${VST_DIR}/stream-processing/docker-compose.yaml"
VST_DEPLOY_SH="${VST_DIR}/deploy.sh"

# Patch stream-processing/docker-compose.yaml:
#   redis-server command → add --port ${REDIS_PORT:-6379}
if [[ -f "${STREAM_PROC_COMPOSE}" ]]; then
    if grep -q -- '--port \${REDIS_PORT' "${STREAM_PROC_COMPOSE}"; then
        log "  stream-processing/docker-compose.yaml already patched, skipping."
    else
        sed -i 's|command: redis-server --save|command: redis-server --port ${REDIS_PORT:-6379} --save|' \
            "${STREAM_PROC_COMPOSE}" \
            || die "Failed to patch ${STREAM_PROC_COMPOSE}"
        sed -i 's|\["CMD", "redis-cli", "ping"\]|["CMD", "redis-cli", "-p", "${REDIS_PORT:-6379}", "ping"]|' \
            "${STREAM_PROC_COMPOSE}" \
            || die "Failed to patch redis-cli ping command in ${STREAM_PROC_COMPOSE}"
        log "  Patched redis-server command and redis-cli ping command in ${STREAM_PROC_COMPOSE}"
    fi
else
    warn "  ${STREAM_PROC_COMPOSE} not found — skipping redis-server patch."
fi

# Patch stream-processing/configs/postgresql.conf:
#   Set 'port = <CENTRALIZE_DB_PORT>' so postgres listens on the configured port.
#   postgresql.conf is already mounted into the centralizedb container and postgres
#   is started with -c config_file pointing to it — this is the canonical way to set
#   the postgres port, rather than patching YAML command args.
POSTGRESQL_CONF="${VST_DIR}/stream-processing/configs/postgresql.conf"
if [[ -f "${POSTGRESQL_CONF}" ]]; then
    if grep -q "^port = ${CENTRALIZE_DB_PORT}" "${POSTGRESQL_CONF}"; then
        log "  postgresql.conf already has port = ${CENTRALIZE_DB_PORT}, skipping."
    elif grep -q '^port = ' "${POSTGRESQL_CONF}"; then
        sed -i "s|^port = .*|port = ${CENTRALIZE_DB_PORT}|" "${POSTGRESQL_CONF}" \
            || die "Failed to update port in ${POSTGRESQL_CONF}"
        log "  Updated port = ${CENTRALIZE_DB_PORT} in ${POSTGRESQL_CONF}"
    else
        # No port line yet — insert after listen_addresses
        sed -i "s|^\(listen_addresses = .*\)|\1\nport = ${CENTRALIZE_DB_PORT}|" "${POSTGRESQL_CONF}" \
            || die "Failed to insert port in ${POSTGRESQL_CONF}"
        log "  Added port = ${CENTRALIZE_DB_PORT} to ${POSTGRESQL_CONF}"
    fi
else
    warn "  ${POSTGRESQL_CONF} not found — skipping centralizedb port patch."
fi

# Patch stream-processing/docker-compose.yaml:
#   centralizedb healthcheck → add -p ${CENTRALIZE_DB_PORT:-5432} to pg_isready.
#   Docker Compose expands ${CENTRALIZE_DB_PORT:-5432} from compose.env at 'up' time.
if [[ -f "${STREAM_PROC_COMPOSE}" ]]; then
    if grep -q 'pg_isready.*CENTRALIZE_DB_PORT' "${STREAM_PROC_COMPOSE}"; then
        log "  pg_isready healthcheck already patched in ${STREAM_PROC_COMPOSE}, skipping."
    else
        sed -i 's|pg_isready -h 127.0.0.1|pg_isready -h 127.0.0.1 -p ${CENTRALIZE_DB_PORT:-5432}|' \
            "${STREAM_PROC_COMPOSE}" \
            || die "Failed to patch pg_isready healthcheck in ${STREAM_PROC_COMPOSE}"
        log "  Patched pg_isready healthcheck port in ${STREAM_PROC_COMPOSE}"
    fi
else
    warn "  ${STREAM_PROC_COMPOSE} not found — skipping pg_isready healthcheck patch."
fi

# Patch nginx-vst.conf and nginx-mms.conf: replace hardcoded listen port 30888.
# nginx doesn't support env var substitution in conf files, so we bake the value in.
for _nginx_conf in \
    "${VST_DIR}/stream-processing/configs/nginx-vst.conf" \
    "${VST_DIR}/stream-processing/configs/nginx-mms.conf"; do
    if [[ -f "${_nginx_conf}" ]]; then
        if grep -q "listen ${VST_INGRESS_PORT};" "${_nginx_conf}"; then
            log "  $(basename "${_nginx_conf}") listen port already ${VST_INGRESS_PORT}, skipping."
        else
            sed -i "s|listen [0-9]*;|listen ${VST_INGRESS_PORT};|g" "${_nginx_conf}" \
                || warn "  Could not patch listen port in ${_nginx_conf}"
            log "  Patched listen port to ${VST_INGRESS_PORT} in $(basename "${_nginx_conf}")"
        fi
    else
        warn "  ${_nginx_conf} not found — skipping nginx listen port patch."
    fi
done

# Patch docker-compose.yaml vst-ingress healthcheck: hardcoded port 30888.
if [[ -f "${STREAM_PROC_COMPOSE}" ]]; then
    if grep -q "127\.0\.0\.1/${VST_INGRESS_PORT}" "${STREAM_PROC_COMPOSE}"; then
        log "  vst-ingress healthcheck already patched for port ${VST_INGRESS_PORT}, skipping."
    else
        sed -i "s|127\.0\.0\.1/30888|127.0.0.1/${VST_INGRESS_PORT}|g" "${STREAM_PROC_COMPOSE}" \
            || warn "  Could not patch vst-ingress healthcheck port in ${STREAM_PROC_COMPOSE}"
        log "  Patched vst-ingress healthcheck port to ${VST_INGRESS_PORT} in ${STREAM_PROC_COMPOSE##*/}"
    fi
fi

# Patch vst_config.json .network.http_port.
# stream-processing uses VST_SENSOR_PORT; nvstreamer uses NVSTREAMER_HTTP_PORT.
# Handle both formats: "http_port": "31000" (stream-processing) and "http_port":"31000" (nvstreamer).
_cfg="${VST_DIR}/stream-processing/configs/vst_config.json"
if [[ -f "${_cfg}" ]]; then
    sed -i "s|\"http_port\":[[:space:]]*\"[0-9]*\"|\"http_port\": \"${VST_SENSOR_PORT}\"|g" \
        "${_cfg}" || true
    log "  Updated .network.http_port to ${VST_SENSOR_PORT} in stream-processing/vst_config.json"
    # Also patch rtsp_server_port — stream-processing binds its RTSP proxy on this port.
    # If left at the default (30554) it conflicts with another user's VST on the same host.
    sed -i "s|\"rtsp_server_port\":[[:space:]]*[0-9]*|\"rtsp_server_port\": ${VST_RTSP_PORT}|g" \
        "${_cfg}" || true
    log "  Updated .network.rtsp_server_port to ${VST_RTSP_PORT} in stream-processing/vst_config.json"
fi
_cfg="${VST_DIR}/nvstreamer/configs/vst_config.json"
if [[ -f "${_cfg}" ]]; then
    sed -i "s|\"http_port\":[[:space:]]*\"[0-9]*\"|\"http_port\":\"${NVSTREAMER_HTTP_PORT}\"|g" \
        "${_cfg}" || true
    log "  Updated .network.http_port to ${NVSTREAMER_HTTP_PORT} in nvstreamer/vst_config.json"
fi

# Patch nginx-mms.conf: update hardcoded proxy_pass targets from default sensor port (30000)
# to VST_SENSOR_PORT.  The nginx ingress container routes all /vst/api/v1/sensor/* requests
# to the sensor-ms HTTP backend.  If this is left at 30000 the VST API is broken whenever
# VST_SENSOR_PORT != 30000.
_nginx_cfg="${VST_DIR}/stream-processing/configs/nginx-mms.conf"
if [[ -f "${_nginx_cfg}" ]]; then
    sed -i "s|proxy_pass http://localhost:30000|proxy_pass http://localhost:${VST_SENSOR_PORT}|g" \
        "${_nginx_cfg}" || true
    log "  Patched nginx-mms.conf: sensor proxy_pass → localhost:${VST_SENSOR_PORT}"
else
    warn "  ${_nginx_cfg} not found — nginx sensor port patch skipped."
fi

# Patch sdr-compose.yaml: update VST_STREAMS_ENDPOINT and VST_STATUS_ENDPOINT.
# The stream-processing-ms-1 service uses these env vars to call sensor-ms's API.
# If they point to 30000 (default) while sensor-ms is on VST_SENSOR_PORT, stream-processing
# cannot communicate with sensor-ms, and sensor-ms /v1/ready never returns 200.
_sdr_compose="${VST_DIR}/stream-processing/sdr-streamprocessing/sdr-compose.yaml"
if [[ -f "${_sdr_compose}" ]]; then
    sed -i "s|http://localhost:30000/api/v1/sensor|http://localhost:${VST_SENSOR_PORT}/api/v1/sensor|g" \
        "${_sdr_compose}" || true
    sed -i -E "s|--base-id[[:space:]]+[0-9]+|--base-id ${VST_ENVOY_BASE_ID}|g" \
        "${_sdr_compose}" || true
    log "  Patched sdr-compose.yaml: VST_STREAMS/STATUS_ENDPOINT → localhost:${VST_SENSOR_PORT}"
    log "  Patched sdr-compose.yaml: Envoy base ID → ${VST_ENVOY_BASE_ID}"
else
    warn "  ${_sdr_compose} not found — sdr-compose.yaml sensor port patch skipped."
fi

# Patch envoy.yaml: update the stream-processing backend port (cluster endpoint).
# Envoy listens on 10000 and forwards to the stream-processing-ms-1 HTTP backend.
# The port_value for the headerless_service cluster must match STREAM_PROCESSOR_HTTP_PORT_1
# (VST_STREAM_PROC_PORT).  If left at 30001 (default) Envoy routes to the wrong service.
_envoy_yaml="${VST_DIR}/stream-processing/sdr-streamprocessing/envoy.yaml"
if [[ -f "${_envoy_yaml}" ]]; then
    sed -i "s|port_value: \"30001\"|port_value: \"${VST_STREAM_PROC_PORT}\"|g" \
        "${_envoy_yaml}" || true
    log "  Patched envoy.yaml: headerless_service port_value → ${VST_STREAM_PROC_PORT}"
else
    warn "  ${_envoy_yaml} not found — envoy.yaml stream-processing port patch skipped."
fi

# Patch deploy.sh: update_stream_processing_env() to write all configurable ports
# (REDIS_PORT, CENTRALIZE_DB_PORT, and all VST_* ports) into compose.env at VST
# startup time. All three sets of changes are applied in a single awk pass to
# avoid a chain-dependency that breaks silently when the function signature changes.
DEPLOY_SH_PORTS_MARKER='# End of update_stream_processing_env ports patch (safe)'
if [[ -f "${VST_DEPLOY_SH}" ]]; then
    # Fix old patch that lacked || true on the vst_config.json sed.
    if grep -q 'vst_config.json"$' "${VST_DEPLOY_SH}" 2>/dev/null; then
        sed -i 's|\(.*vst_config.json"\)$|\1 || true|' "${VST_DEPLOY_SH}" \
            && log "  Fixed existing deploy.sh patch (added || true guard on vst_config.json sed)" \
            || warn "  Could not fix existing deploy.sh patch — manual fix may be needed"
    fi

    if grep -q "${DEPLOY_SH_PORTS_MARKER}" "${VST_DEPLOY_SH}"; then
        log "  deploy.sh ports patch already applied, skipping."
    elif ! grep -q 'update_stream_processing_env' "${VST_DEPLOY_SH}"; then
        warn "  update_stream_processing_env() not found in ${VST_DEPLOY_SH} — skipping ports patch."
    else
        # Single awk pass: insert all port variables before the closing } of
        # update_stream_processing_env(). Avoids the brittle multi-pass chain.
        awk -v vst_dir="${VST_DIR}" -v marker="${DEPLOY_SH_PORTS_MARKER}" '
            /^update_stream_processing_env\(\)/ { in_func=1 }
            in_func && /^\}/ {
                print "    local redis_port=\"${REDIS_PORT:-6379}\""
                print "    sed -i \"s|^REDIS_PORT=.*|REDIS_PORT=${redis_port}|\" \"${env_file}\""
                print "    for _cfg in \"" vst_dir "/stream-processing/configs/vst_config.json\" \"" vst_dir "/nvstreamer/configs/vst_config.json\"; do"
                print "        [[ -f \"${_cfg}\" ]] || continue"
                print "        sed -i \"s|\\\"redis_server_env_var\\\": \\\"localhost:[0-9]*\\\"|\\\"redis_server_env_var\\\": \\\"localhost:${redis_port}\\\"|g\" \"${_cfg}\" || true"
                print "        sed -i \"s|\\\"redis_server_env_var\\\": \\\"REDIS_SVC_SERVICE_HOST:[0-9]*\\\"|\\\"redis_server_env_var\\\": \\\"REDIS_SVC_SERVICE_HOST:${redis_port}\\\"|g\" \"${_cfg}\" || true"
                print "    done"
                print "    local centralize_db_port=\"${CENTRALIZE_DB_PORT:-5432}\""
                print "    sed -i \"s|^CENTRALIZE_DB_PORT=.*|CENTRALIZE_DB_PORT=${centralize_db_port}|\" \"${env_file}\""
                print "    local vst_ingress_port=\"${VST_INGRESS_PORT:-30888}\""
                print "    local vst_sensor_port=\"${VST_SENSOR_PORT:-30000}\""
                print "    local vst_stream_proc_port=\"${VST_STREAM_PROC_PORT:-30001}\""
                print "    local vst_rtsp_port=\"${VST_RTSP_PORT:-30554}\""
                print "    sed -i \"s|^SENSOR_HTTP_PORT=.*|SENSOR_HTTP_PORT=${vst_sensor_port}|\" \"${env_file}\""
                print "    sed -i \"s|^STREAM_PROCESSOR_HTTP_PORT_1=.*|STREAM_PROCESSOR_HTTP_PORT_1=${vst_stream_proc_port}|\" \"${env_file}\""
                print "    sed -i \"s|^RTSP_SERVER_PORT_1=.*|RTSP_SERVER_PORT_1=${vst_rtsp_port}|\" \"${env_file}\""
                print "    sed -i \"s|^VST_INGRESS_ENDPOINT=.*|VST_INGRESS_ENDPOINT=\\${HOST_IP}:${vst_ingress_port}/vst|\" \"${env_file}\""
                print "    " marker
                in_func=0
            }
            { print }
        ' "${VST_DEPLOY_SH}" > "${VST_DEPLOY_SH}.patched" \
            && mv "${VST_DEPLOY_SH}.patched" "${VST_DEPLOY_SH}" \
            && chmod +x "${VST_DEPLOY_SH}" \
            || die "Failed to patch ${VST_DEPLOY_SH}"
        log "  Patched update_stream_processing_env() in ${VST_DEPLOY_SH} (all ports)"
    fi
else
    warn "  ${VST_DEPLOY_SH} not found — deploy.sh patch skipped."
fi

# Patch VST compose files: prefix all container_name values with VST_COMPOSE_PROJECT.
# Services with a static container_name ignore COMPOSE_PROJECT_NAME entirely, so we
# bake the prefix directly into the compose file at setup time.
# The tarball is re-extracted on every run so this patch is always re-applied fresh.
log "  Looking for compose files under ${VST_DIR}..."
_compose_files=()
while IFS= read -r -d '' _f; do _compose_files+=("${_f}"); done < <(
    find "${VST_DIR}" \( -name "*.yaml" -o -name "*.yml" \) -print0 2>/dev/null \
        | xargs -0 grep -l 'container_name:' 2>/dev/null \
        | tr '\n' '\0'
)
log "  Found ${#_compose_files[@]} compose file(s)."
for _compose_file in "${_compose_files[@]}"; do
    # Handle unquoted:      container_name: foo
    sed -i "/container_name:.*${VST_COMPOSE_PROJECT}/! s|container_name: \([^\"'][^ #]*\)|container_name: ${VST_COMPOSE_PROJECT}-\1|g" \
        "${_compose_file}" || true
    # Handle double-quoted: container_name: "foo"
    sed -i "/container_name:.*${VST_COMPOSE_PROJECT}/! s|container_name: \"\([^\"]*\)\"|container_name: \"${VST_COMPOSE_PROJECT}-\1\"|g" \
        "${_compose_file}" || true
    # Handle single-quoted: container_name: 'foo'
    sed -i "/container_name:.*${VST_COMPOSE_PROJECT}/! s|container_name: '\([^']*\)'|container_name: '${VST_COMPOSE_PROJECT}-\1'|g" \
        "${_compose_file}" || true
    log "  Prefixed container_name entries in ${_compose_file#"${VST_DIR}/"}"
done

# Patch deploy.sh: prefix all docker compose project names with VST_COMPOSE_PROJECT.
# deploy.sh hardcodes project names via COMPOSE_PROJECT_NAME="x" or -p x; we rewrite
# them to "${VST_COMPOSE_PROJECT}-x" so all containers get the project prefix.
# Handles double-quoted, single-quoted, and unquoted forms; skips already-patched lines.
if [[ -f "${VST_DEPLOY_SH}" ]]; then
    # COMPOSE_PROJECT_NAME="value" or COMPOSE_PROJECT_NAME='value'
    sed -i "/COMPOSE_PROJECT_NAME.*${VST_COMPOSE_PROJECT}/! s/COMPOSE_PROJECT_NAME=\"\([^\"$][^\"]*\)\"/COMPOSE_PROJECT_NAME=\"${VST_COMPOSE_PROJECT}-\1\"/g" \
        "${VST_DEPLOY_SH}" || true
    sed -i "/COMPOSE_PROJECT_NAME.*${VST_COMPOSE_PROJECT}/! s/COMPOSE_PROJECT_NAME='\([^'$][^']*\)'/COMPOSE_PROJECT_NAME=\"${VST_COMPOSE_PROJECT}-\1\"/g" \
        "${VST_DEPLOY_SH}" || true
    # -p "value" or -p 'value' or -p value
    sed -i "/-p.*${VST_COMPOSE_PROJECT}/! s/-p \"\([^\"$][^\"]*\)\"/-p \"${VST_COMPOSE_PROJECT}-\1\"/g" \
        "${VST_DEPLOY_SH}" || true
    sed -i "/-p.*${VST_COMPOSE_PROJECT}/! s/-p '\([^'$][^']*\)'/-p \"${VST_COMPOSE_PROJECT}-\1\"/g" \
        "${VST_DEPLOY_SH}" || true
    sed -i "/-p.*${VST_COMPOSE_PROJECT}/! s/-p \([a-zA-Z][a-zA-Z0-9_-]*\)\b/-p \"${VST_COMPOSE_PROJECT}-\1\"/g" \
        "${VST_DEPLOY_SH}" || true
    # Show result (grep returns 1 when no matches — guard with || true so set -eo pipefail doesn't exit)
    grep -n 'COMPOSE_PROJECT_NAME\| -p ' "${VST_DEPLOY_SH}" 2>/dev/null | head -20 \
        | while IFS= read -r _l; do log "    after:  ${_l}"; done || true
    # Add --remove-orphans to all "docker compose up" calls so stale containers from
    # previous runs with different project names are cleaned up automatically.
    sed -i 's/docker compose \(.*\) up \(-d\)/docker compose \1 up \2 --remove-orphans/g' \
        "${VST_DEPLOY_SH}" || true
    sed -i 's/docker compose \(.*\) up$/docker compose \1 up --remove-orphans/' \
        "${VST_DEPLOY_SH}" || true
    log "  Patched deploy.sh: all compose projects prefixed with ${VST_COMPOSE_PROJECT}"
fi

# Directly update all vst_config.json instances that hardcode the Redis port.
# Two known locations in the VST package:
#   stream-processing/configs/vst_config.json — "redis_server_env_var": "localhost:6379"
#   nvstreamer/configs/vst_config.json        — "redis_server_env_var": "REDIS_SVC_SERVICE_HOST:6379"
# Both patterns need their port replaced; use two targeted sed passes per file.
for _vst_cfg in \
    "${VST_DIR}/stream-processing/configs/vst_config.json" \
    "${VST_DIR}/nvstreamer/configs/vst_config.json"; do
    if [[ -f "${_vst_cfg}" ]]; then
        sed -i "s|\"redis_server_env_var\": \"localhost:[0-9]*\"|\"redis_server_env_var\": \"localhost:${REDIS_PORT}\"|g" \
            "${_vst_cfg}" || warn "  Could not patch localhost redis port in ${_vst_cfg}"
        sed -i "s|\"redis_server_env_var\": \"REDIS_SVC_SERVICE_HOST:[0-9]*\"|\"redis_server_env_var\": \"REDIS_SVC_SERVICE_HOST:${REDIS_PORT}\"|g" \
            "${_vst_cfg}" || warn "  Could not patch REDIS_SVC_SERVICE_HOST redis port in ${_vst_cfg}"
        log "  Updated redis port to ${REDIS_PORT} in $(basename "$(dirname "${_vst_cfg}")")/$(basename "${_vst_cfg}")"
    else
        warn "  ${_vst_cfg} not found — skipping redis port patch."
    fi
done

# Patch nvstreamer/compose.env: set NVSTREAMER_HTTP_PORT_1 and NVSTREAMER_RTSP_PORT_1.
# nvstreamer uses network_mode: host — there are no docker port mappings to patch.
# Ports are controlled entirely by HTTP_PORT and RTSP_SERVER_PORT env vars which
# docker-compose.yaml reads from compose.env as NVSTREAMER_HTTP_PORT_1 / NVSTREAMER_RTSP_PORT_1.
_ns_compose_env="${VST_DIR}/nvstreamer/compose.env"
if [[ -f "${_ns_compose_env}" ]]; then
    sed -i "s/^NVSTREAMER_HTTP_PORT_1=.*/NVSTREAMER_HTTP_PORT_1=${NVSTREAMER_HTTP_PORT}/" \
        "${_ns_compose_env}" || true
    sed -i "s/^NVSTREAMER_RTSP_PORT_1=.*/NVSTREAMER_RTSP_PORT_1=${NVSTREAMER_RTSP_PORT}/" \
        "${_ns_compose_env}" || true
    log "  Patched nvstreamer/compose.env: HTTP_PORT_1=${NVSTREAMER_HTTP_PORT}, RTSP_PORT_1=${NVSTREAMER_RTSP_PORT}"
else
    warn "  ${_ns_compose_env} not found — nvstreamer port patch skipped."
fi

# Patch stream-processing/configs/rtsp_streams.json: update nvstreamer endpoint 1 to
# NVSTREAMER_HTTP_PORT.  VST's stream-processing service polls this file to discover which
# nvstreamer HTTP API to query for live streams.  If entry 1 still points to the default
# port (31000) another user's nvstreamer on that port will be used instead of ours.
# Only the first endpoint is patched (entry 1 = nvstreamer-1); entries 2-5 are left as-is
# (they will simply fail to connect since nvstreamer-2..5 are not started, which is harmless).
_rtsp_streams="${VST_DIR}/stream-processing/configs/rtsp_streams.json"
if [[ -f "${_rtsp_streams}" ]]; then
    # GNU sed: 0,/pattern/ replaces only the FIRST occurrence — avoids duplicating streams
    # by pointing all 5 entries to the same nvstreamer.
    sed -i "0,/\"endpoint\": \"localhost:[0-9]*\"/{s|\"endpoint\": \"localhost:[0-9]*\"|\"endpoint\": \"localhost:${NVSTREAMER_HTTP_PORT}\"|}" \
        "${_rtsp_streams}" || true
    log "  Patched rtsp_streams.json: nvstreamer endpoint 1 → localhost:${NVSTREAMER_HTTP_PORT}"
else
    warn "  ${_rtsp_streams} not found — rtsp_streams.json nvstreamer endpoint patch skipped."
fi

# ---------------------------------------------------------------------------
# Step 6: Prepare benchmark test videos
# ---------------------------------------------------------------------------
log "Step 6/12: Preparing benchmark test videos..."
# Videos are stored in PERF_VIDEOS_DIR (default: ${VST_DIR}/videos).
# nvstreamer (VST) reads from this directory for RTSP streaming.
# compose.perf.yaml mounts it into the RTVI container at
# /opt/nvidia/rtvi/streams/perf/ so file-based benchmarks can reach them.

mkdir -p "${PERF_VIDEOS_DIR}"

# Four legacy warehouse GoPro videos covering older video-duration test points:
#   warehouse_gopro_10s.mp4  —   10 s  (legacy 29.97 FPS source)
#   warehouse_gopro_1m.mp4   —   60 s  (legacy; kept for backward compatibility)
#   warehouse_gopro_10m.mp4  —  600 s  (legacy 29.97 FPS source)
#   warehouse_gopro_60m.mp4  — 3600 s  (legacy 29.97 FPS source)
download_video() {
    local video="$1"
    local required="$2"
    local force="${3:-false}"
    local dest="${PERF_VIDEOS_DIR}/${video}"

    if [[ -f "${dest}" && "${force}" != "true" ]]; then
        log "  ${video} already present, skipping."
        return
    fi

    if [[ "${force}" == "true" && -f "${dest}" ]]; then
        log "  Refreshing ${video} ..."
    else
        log "  Downloading ${video} ..."
    fi
    if curl -fsSL \
        -u "${ARTIFACTORY_USER}:${ARTIFACTORY_TOKEN}" \
        -o "${dest}" \
        "${VIDEOS_URL}/${video}"; then
        return
    fi

    rm -f "${dest}"
    if [[ "${required}" == "true" ]]; then
        die "Failed to download required benchmark video ${video} from ${VIDEOS_URL}/${video}"
    fi
    warn "  Failed to download ${video} — continuing without it."
}

for video in "${BENCHMARK_VIDEOS[@]}"; do
    if [[ -n "${ARTIFACTORY_USER}" && -n "${ARTIFACTORY_TOKEN}" ]]; then
        download_video "${video}" false
    elif [[ ! -f "${PERF_VIDEOS_DIR}/${video}" ]]; then
        log "  Skipping optional legacy video ${video} (Artifactory credentials not set)."
    fi
done

if [[ "${_needs_lvs_generation}" == "true" \
      && ( ! -f "${LVS_VIDEO_SOURCE_PATH}" || "${REFRESH_BCD_VIDEOS}" == "true" ) ]]; then
    log "  Fetching LVS warehouse videos from NGC..."
    FORCE="$([[ "${REFRESH_BCD_VIDEOS}" == "true" ]] && echo 1 || echo 0)" \
        VSS_BENCHMARK_DATA_DIR="${LVS_VIDEO_DATA_DIR}" \
        bash "${LVS_VIDEO_FETCH_SCRIPT}" "${LVS_VIDEO_VERSION}"
fi

generate_bcd_video() {
    local duration="$1"
    local filename="$2"
    local dest="${PERF_VIDEOS_DIR}/${filename}"
    local tmp="${dest}.tmp.mp4"

    if [[ "${REFRESH_BCD_VIDEOS}" != "true" ]] \
        && validate_bcd_video "${dest}" "${duration}"; then
        log "  ${filename} already present, skipping."
        return
    fi
    [[ ! -e "${dest}" ]] || warn "  ${filename} is invalid; regenerating it."

    log "  Generating ${filename} (${duration} s, 1080p, 10 FPS)..."
    rm -f "${tmp}"
    if ! ffmpeg -hide_banner -loglevel error -y -stream_loop -1 \
        -i "${LVS_VIDEO_SOURCE_PATH}" -map 0:v:0 -an -sn -dn -t "${duration}" \
        -vf fps=10 -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p \
        -movflags +faststart -map_metadata -1 "${tmp}"; then
        rm -f "${tmp}"
        die "Failed to generate ${filename} from ${LVS_VIDEO_SOURCE_PATH}"
    fi

    if ! validate_bcd_video "${tmp}" "${duration}"; then
        rm -f "${tmp}"
        die "Generated ${filename} failed 1080p/10 FPS/${duration} s validation"
    fi
    mv -f "${tmp}" "${dest}"
}

_bcd_10s_dest="${PERF_VIDEOS_DIR}/${BCD_10S_VIDEO_FILENAME}"
if [[ -n "${BCD_10S_VIDEO_SOURCE_PATH}" ]]; then
    if [[ "${REFRESH_BCD_VIDEOS}" != "true" ]] \
        && validate_bcd_video "${_bcd_10s_dest}" 10; then
        log "  ${BCD_10S_VIDEO_FILENAME} already present, skipping."
    else
        _bcd_10s_tmp="${_bcd_10s_dest}.tmp.mp4"
        rm -f "${_bcd_10s_tmp}"
        cp "${BCD_10S_VIDEO_SOURCE_PATH}" "${_bcd_10s_tmp}" \
            || die "Failed to stage BCD 10 FPS clip from ${BCD_10S_VIDEO_SOURCE_PATH}"
        if ! validate_bcd_video "${_bcd_10s_tmp}" 10; then
            rm -f "${_bcd_10s_tmp}"
            die "BCD_10S_VIDEO_SOURCE_PATH must be 1920x1080, 10 FPS, and 10 seconds"
        fi
        mv -f "${_bcd_10s_tmp}" "${_bcd_10s_dest}"
        log "  Staged BCD 10 s / 10 FPS clip → ${_bcd_10s_dest}"
    fi
else
    generate_bcd_video 10 "${BCD_10S_VIDEO_FILENAME}"
fi
generate_bcd_video 600 "${BCD_10M_VIDEO_FILENAME}"
generate_bcd_video 3600 "${BCD_60M_VIDEO_FILENAME}"

# ---------------------------------------------------------------------------
# Step 7: Detect host IP
# ---------------------------------------------------------------------------
log "Step 7/12: Detecting host IP..."

HOST_IP=""
if command -v ip >/dev/null 2>&1; then
    HOST_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
fi

if [[ -z "${HOST_IP}" ]] && command -v hostname >/dev/null 2>&1; then
    HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi

[[ -n "${HOST_IP}" ]] || die "Could not detect host IP. Set HOST_IP manually and re-run."
log "  Host IP: ${HOST_IP}"

section "nvstreamer"

# ---------------------------------------------------------------------------
# Step 8: Start nvstreamer, wait for it to be healthy, then start VST
# ---------------------------------------------------------------------------
log "Step 8/12: Starting nvstreamer then VST..."

VST_DEPLOY_SH="${VST_DIR}/deploy.sh"
NVSTREAMER_HEALTH="${NVSTREAMER_HEALTH:-http://localhost:${NVSTREAMER_HTTP_PORT}}"
NVSTREAMER_POLL_TIMEOUT="${NVSTREAMER_POLL_TIMEOUT:-600}"  # seconds

if [[ ! -f "${VST_DEPLOY_SH}" ]]; then
    warn "  deploy.sh not found at ${VST_DEPLOY_SH} — skipping nvstreamer/VST start."
else
    log "  Stopping any existing nvstreamer containers..."
    (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" bash deploy.sh down nvstreamer 2>/dev/null) || true
    (cd "${VST_DIR}" && bash deploy.sh down nvstreamer 2>/dev/null) || true
    log "  Starting nvstreamer..."
    # Ports are already baked into nvstreamer/compose.env by the patching above.
    (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" \
        REDIS_PORT="${REDIS_PORT}" \
        bash deploy.sh up nvstreamer) \
        || die "Failed to start nvstreamer"

    # Resolve the actual container name for log/error messages
    NVSTREAMER_CONTAINER=$(docker ps --format "{{.Names}}" 2>/dev/null \
        | grep -E "nvstreamer" | head -1 || true)
    NVSTREAMER_CONTAINER="${NVSTREAMER_CONTAINER:-nvstreamer}"

    # nvstreamer uses network_mode: host so docker port shows nothing.
    # Confirm ports via the container's own environment variables.
    _ns_http=$(docker inspect "${NVSTREAMER_CONTAINER}" \
        --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | grep "^HTTP_PORT=" | cut -d= -f2 || true)
    _ns_rtsp=$(docker inspect "${NVSTREAMER_CONTAINER}" \
        --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | grep "^RTSP_SERVER_PORT=" | cut -d= -f2 || true)
    if [[ -n "${_ns_http}" ]]; then
        log "  nvstreamer env: HTTP_PORT=${_ns_http}, RTSP_SERVER_PORT=${_ns_rtsp} (host networking)"
    else
        warn "  Could not read nvstreamer env — container may not be running"
    fi
    log "  Waiting for nvstreamer at ${NVSTREAMER_HEALTH}..."
    NVEL=0
    until curl -sf "${NVSTREAMER_HEALTH}" >/dev/null 2>&1; do
        NVEL=$((NVEL + 5))
        if [[ "${NVEL}" -ge "${NVSTREAMER_POLL_TIMEOUT}" ]]; then
            warn "  nvstreamer did not become healthy after ${NVSTREAMER_POLL_TIMEOUT}s."
            warn "  Container env: HTTP_PORT=${_ns_http}, RTSP_SERVER_PORT=${_ns_rtsp}"
            warn "  Last 20 log lines:"
            docker logs --tail 20 "${NVSTREAMER_CONTAINER}" 2>&1 \
                | while IFS= read -r _l; do warn "    ${_l}"; done || true
            die "nvstreamer health check timed out at ${NVSTREAMER_HEALTH}"
        fi
        log "  [${NVEL}s] nvstreamer not ready yet, retrying..."
        sleep 5
    done
    log "  ${_C_GREEN}nvstreamer is up.${_C_RESET}"

    section "VST"
    log "  Stopping any existing VST containers..."
    (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" bash deploy.sh down vst 2>/dev/null) || true
    (cd "${VST_DIR}" && bash deploy.sh down vst 2>/dev/null) || true
    # Belt-and-suspenders: force-remove any remaining VST containers that
    # deploy.sh missed (e.g. stale containers from a prior interrupted run
    # holding port ${REDIS_PORT}/redis or ${CENTRALIZE_DB_PORT}/centralizedb).
    # nvstreamer containers are explicitly excluded — they are already running.
    _stale_vst_non_ns=$(docker ps -aq --filter "name=${VST_COMPOSE_PROJECT}" 2>/dev/null \
        | xargs -r docker inspect --format '{{.Name}}' 2>/dev/null \
        | grep -v "nvstreamer" || true)
    if [[ -n "${_stale_vst_non_ns}" ]]; then
        log "  Force-removing remaining VST containers to free ports ${REDIS_PORT}/${CENTRALIZE_DB_PORT}..."
        echo "${_stale_vst_non_ns}" | xargs -r docker rm -f 2>/dev/null || true
    fi
    log "  Starting VST..."
    (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" \
        REDIS_PORT="${REDIS_PORT}" CENTRALIZE_DB_PORT="${CENTRALIZE_DB_PORT}" \
        VST_INGRESS_PORT="${VST_INGRESS_PORT}" \
        VST_SENSOR_PORT="${VST_SENSOR_PORT}" \
        VST_STREAM_PROC_PORT="${VST_STREAM_PROC_PORT}" \
        VST_RTSP_PORT="${VST_RTSP_PORT}" \
        bash deploy.sh up vst) \
        || die "Failed to start VST"
    VST_STARTED=true
    log "  VST started. UI: http://localhost:30888/vst/"
fi

# ---------------------------------------------------------------------------
# Step 9: Wait for VST live streams to become available
# ---------------------------------------------------------------------------
log "Step 9/12: Waiting for VST live streams (${VST_STREAMS_API})..."

RTSP_URLS=""
ELAPSED=0
STREAM_POLL_TIMEOUT="${STREAM_POLL_TIMEOUT:-600}"   # seconds; override via env if needed

while [[ -z "${RTSP_URLS}" ]]; do
    RESP=$(curl -sf "${VST_STREAMS_API}" 2>/dev/null || true)
    if [[ -n "${RESP}" ]]; then
        # First try /live/ paths (standard VST stream format)
        RTSP_URLS=$(echo "${RESP}" \
            | jq -r '.. | strings | select(startswith("rtsp://")) | select(contains("/live/"))' \
            2>/dev/null \
            | paste -sd ';' \
            | sed 's/;$//')
        # If no /live/ streams, accept any RTSP URL from the response
        if [[ -z "${RTSP_URLS}" ]]; then
            RTSP_URLS=$(echo "${RESP}" \
                | jq -r '.. | strings | select(startswith("rtsp://"))' \
                2>/dev/null \
                | paste -sd ';' \
                | sed 's/;$//')
            [[ -n "${RTSP_URLS}" ]] && warn "  No /live/ streams found — using all RTSP URLs from VST response."
        fi
    fi
    if [[ -z "${RTSP_URLS}" ]]; then
        ELAPSED=$((ELAPSED + 10))
        if [[ "${ELAPSED}" -ge "${STREAM_POLL_TIMEOUT}" ]]; then
            warn "  Timed out after ${STREAM_POLL_TIMEOUT}s waiting for streams."
            warn "  Raw VST response:"
            curl -s "${VST_STREAMS_API}" 2>/dev/null | jq . >&2 || \
                warn "  (API unreachable at ${VST_STREAMS_API})"
            die "No RTSP streams found. Check VST logs: docker logs nvstreamer"
        fi
        log "  [${ELAPSED}s] Streams not ready yet, retrying..."
        sleep 10
    fi
done

STREAM_COUNT=$(echo "${RTSP_URLS}" | tr ';' '\n' | wc -l)
FIRST_RTSP_URL=$(echo "${RTSP_URLS}" | tr ';' '\n' | head -1)

log "  Detected ${STREAM_COUNT} live stream(s):"
echo "${RTSP_URLS}" | tr ';' '\n' | while read -r url; do log "    ${url}"; done

# Prefer the BCD 10 FPS 60-minute stream used for long-run BCD tests.
# Look for it by name in the detected stream list; fall back to constructing the URL from HOST_IP.
INJECT_RTSP_URL=$(echo "${RTSP_URLS}" | tr ';' '\n' | grep -i "warehouse_gopro_60m_10fps" | head -1 || true)
if [[ -z "${INJECT_RTSP_URL}" ]]; then
    INJECT_RTSP_URL="rtsp://${HOST_IP}:${NVSTREAMER_RTSP_PORT}/warehouse_gopro_60m_10fps"
    log "  warehouse_gopro_60m_10fps not found in VST stream list; using constructed URL."
fi
log "  Benchmark RTSP URL (warehouse_gopro_60m_10fps): ${_C_CYAN}${INJECT_RTSP_URL}${_C_RESET}"

section "Python Environment"

# ---------------------------------------------------------------------------
# Step 10: Create Python virtual environment
# ---------------------------------------------------------------------------
log "Step 10/12: Setting up Python virtual environment at ${VENV_DIR} ..."

if [[ ! -d "${VENV_DIR}" ]]; then
    python3 -m venv "${VENV_DIR}" || die "Failed to create Python venv at ${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

pip install --quiet --upgrade pip

if [[ -f "${REQUIREMENTS_FILE}" ]]; then
    log "  Installing requirements from ${REQUIREMENTS_FILE} ..."
    pip install --quiet -r "${REQUIREMENTS_FILE}"
fi

log "  Installing extra benchmark dependencies ..."
pip install --quiet sseclient-py requests tabulate tqdm pyyaml protobuf

log "  Virtual environment ready."

# ---------------------------------------------------------------------------
# Step 11: Inject live ports and stream URL into benchmark config
# ---------------------------------------------------------------------------
log "Step 11/12: Updating ${BENCHMARK_CONFIG} with live ports and stream URL..."

if [[ -f "${BENCHMARK_CONFIG}" ]]; then
    # RTSP stream URL (warehouse_gopro_60m_10fps selected in Step 9).
    # Pattern matches both the RTSP_STREAM_URL placeholder (first run) and any
    # existing rtsp:// value (re-runs), making the substitution idempotent.
    sed -i "s|rtsp_url: \"[^\"]*\"|rtsp_url: \"${INJECT_RTSP_URL}\"|g" "${BENCHMARK_CONFIG}" \
        || die "Failed to update rtsp_url in ${BENCHMARK_CONFIG}"
    log "  Updated rtsp_url             → ${INJECT_RTSP_URL}"

    # Backend URL — always overwrite so a changed BACKEND_PORT is picked up on re-runs
    sed -i "s|rtvi_backend:.*|rtvi_backend: \"http://localhost:${BACKEND_PORT}/v1\"|" "${BENCHMARK_CONFIG}" \
        || die "Failed to update rtvi_backend in ${BENCHMARK_CONFIG}"
    log "  Updated rtvi_backend         → http://localhost:${BACKEND_PORT}/v1"

    # DCGM exporter URL
    sed -i "s|dcgm_exporter_url:.*|dcgm_exporter_url: \"http://localhost:${DCGM_EXPORTER_PORT}/metrics\"|" "${BENCHMARK_CONFIG}" \
        || die "Failed to update dcgm_exporter_url in ${BENCHMARK_CONFIG}"
    log "  Updated dcgm_exporter_url    → http://localhost:${DCGM_EXPORTER_PORT}/metrics"

    # Node exporter URL
    sed -i "s|node_exporter_url:.*|node_exporter_url: \"http://localhost:${NODE_EXPORTER_PORT}/metrics\"|" "${BENCHMARK_CONFIG}" \
        || die "Failed to update node_exporter_url in ${BENCHMARK_CONFIG}"
    log "  Updated node_exporter_url    → http://localhost:${NODE_EXPORTER_PORT}/metrics"

    # GPU list — convert NVIDIA_VISIBLE_DEVICES (e.g. "0" or "0,1" or "3") to a YAML array.
    # Falls back to [0] if the value is non-numeric (e.g. "all").
    if echo "${NVIDIA_VISIBLE_DEVICES}" | grep -qE '^[0-9]+(,[0-9]+)*$'; then
        _vlm_gpus="[$(echo "${NVIDIA_VISIBLE_DEVICES}" | sed 's/,/, /g')]"
    else
        _vlm_gpus="[0]"
        warn "  NVIDIA_VISIBLE_DEVICES='${NVIDIA_VISIBLE_DEVICES}' is not a numeric list; defaulting vlm_gpus to [0]"
    fi
    sed -i "s|vlm_gpus:.*|vlm_gpus: ${_vlm_gpus}  # from NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES}|" "${BENCHMARK_CONFIG}" \
        || die "Failed to update vlm_gpus in ${BENCHMARK_CONFIG}"
    log "  Updated vlm_gpus             → ${_vlm_gpus}"

    # Apply the same substitutions to all platform-specific configs
    for _platform_cfg in \
        "${BENCHMARK_DIR}/rtvi_vlm_config_h100.yaml" \
        "${BENCHMARK_DIR}/rtvi_vlm_config_rtx_pro.yaml" \
        "${BENCHMARK_DIR}/rtvi_vlm_config_l40s.yaml" \
        "${BENCHMARK_DIR}/rtvi_vlm_config_jetson.yaml" \
        "${BENCHMARK_DIR}/rtvi_vlm_config_spark.yaml" \
        "${BENCHMARK_DIR}/rtvi_vlm_bcd_3_2_config.yaml" \
        "${BENCHMARK_DIR}/rtvi_vlm_bcd_3_2_spark_config.yaml" \
        "${BENCHMARK_DIR}/rtvi_vlm_bcd_3_2_thor_config.yaml"; do
        if [[ -f "${_platform_cfg}" ]]; then
            sed -i "s|rtsp_url: \"[^\"]*\"|rtsp_url: \"${INJECT_RTSP_URL}\"|g" "${_platform_cfg}"
            sed -i "s|rtvi_backend:.*|rtvi_backend: \"http://localhost:${BACKEND_PORT}/v1\"|" "${_platform_cfg}"
            sed -i "s|dcgm_exporter_url:.*|dcgm_exporter_url: \"http://localhost:${DCGM_EXPORTER_PORT}/metrics\"|" "${_platform_cfg}"
            sed -i "s|node_exporter_url:.*|node_exporter_url: \"http://localhost:${NODE_EXPORTER_PORT}/metrics\"|" "${_platform_cfg}"
            sed -i "s|vlm_gpus:.*|vlm_gpus: ${_vlm_gpus}  # from NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES}|" "${_platform_cfg}"
            log "  Patched $(basename "${_platform_cfg}")"
        fi
    done
else
    warn "  ${BENCHMARK_CONFIG} not found — skipping RTSP URL substitution."
fi

section "RTVI VLM"

# ---------------------------------------------------------------------------
# Step 12: Generate .env.perf and start RTVI VLM service (compose.perf.yaml)
# ---------------------------------------------------------------------------
log "Step 12/12: Generating .env.perf and starting RTVI VLM service..."

if [[ ! -f "${COMPOSE_PERF_YAML}" ]]; then
    warn "  compose.perf.yaml not found at ${COMPOSE_PERF_YAML} — skipping RTVI VLM start."
else
    # Stop any containers from a previous run before checking ports or starting new ones.
    # This is idempotent — a no-op if nothing is running.  Uses the existing .env.perf
    # (if present) so non-default port values from the previous run are honoured.
    log "  Stopping any previous compose.perf.yaml containers..."
    _prev_env_arg=()
    [[ -f "${ENV_PERF_FILE}" ]] && _prev_env_arg=(--env-file "${ENV_PERF_FILE}")
    docker compose -f "${COMPOSE_PERF_YAML}" "${_prev_env_arg[@]}" down 2>&1 | \
        sed "s/^/  [compose down] /" || true

    # Check for port conflicts before attempting to start
    _port_conflicts=()
    port_in_use "${BACKEND_PORT}"       && _port_conflicts+=("BACKEND_PORT=${BACKEND_PORT}             → export BACKEND_PORT=<free_port>")
    port_in_use "${NODE_EXPORTER_PORT}" && _port_conflicts+=("NODE_EXPORTER_PORT=${NODE_EXPORTER_PORT}  → export NODE_EXPORTER_PORT=<free_port>")
    port_in_use "${DCGM_EXPORTER_PORT}" && _port_conflicts+=("DCGM_EXPORTER_PORT=${DCGM_EXPORTER_PORT}  → export DCGM_EXPORTER_PORT=<free_port>")
    port_in_use "${PROMETHEUS_PORT}"    && _port_conflicts+=("PROMETHEUS_PORT=${PROMETHEUS_PORT}       → export PROMETHEUS_PORT=<free_port>")

    if [[ "${#_port_conflicts[@]}" -gt 0 ]]; then
        echo "" >&2
        echo -e "${_C_RED}[setup_perf_env] ERROR: The following ports are already in use on this host:${_C_RESET}" >&2
        echo "" >&2
        for _conflict in "${_port_conflicts[@]}"; do
            echo -e "    ${_C_YELLOW}${_conflict}${_C_RESET}" >&2
        done
        echo "" >&2
        echo "  Export the override variable(s) listed above and re-run:" >&2
        echo -e "    ${_C_CYAN}bash perf/setup_perf_env.sh${_C_RESET}" >&2
        echo "" >&2
        die "Port conflict(s) detected — cannot start compose.perf.yaml."
    fi

    # Auto-generate .env.perf from environment variables collected at the top
    # of this script (or overridden by the caller's environment).
    log "  Generating ${ENV_PERF_FILE} ..."
    mkdir -p "$(dirname "${ENV_PERF_FILE}")"
    cat > "${ENV_PERF_FILE}" <<EOF
# Auto-generated by perf/setup_perf_env.sh on $(date)
# Re-run setup_perf_env.sh to regenerate with updated values.

BACKEND_PORT=${BACKEND_PORT}
RTVI_IMAGE=${RTVI_IMAGE}
NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES}
NGC_API_KEY=${NGC_API_KEY}
NVIDIA_API_KEY=${NVIDIA_API_KEY}
HF_TOKEN=${HF_TOKEN}
VLM_MODEL_PRESET=${VLM_MODEL_PRESET}
VLM_MODEL_TO_USE=${VLM_MODEL_TO_USE}
MODEL_PATH=${MODEL_PATH}
PERF_VIDEOS_DIR=${PERF_VIDEOS_DIR}
NODE_EXPORTER_PORT=${NODE_EXPORTER_PORT}
DCGM_EXPORTER_PORT=${DCGM_EXPORTER_PORT}
PROMETHEUS_PORT=${PROMETHEUS_PORT}
ASSET_TMPFS_SIZE=${ASSET_TMPFS_SIZE}
MAX_ASSET_STORAGE_SIZE_GB=${MAX_ASSET_STORAGE_SIZE_GB}
ASSET_MAX_AGE_HOURS=${ASSET_MAX_AGE_HOURS}
ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS=${ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS}
ASSET_DOWNLOAD_MAX_REDIRECTS=${ASSET_DOWNLOAD_MAX_REDIRECTS}
ASSET_DOWNLOAD_AUTH_TOKENS=${ASSET_DOWNLOAD_AUTH_TOKENS}
# BCD benchmark required settings
VLLM_IGNORE_EOS=true
VSS_SKIP_INPUT_MEDIA_VERIFICATION=1
VSS_INPUT_MEDIA_VERIFICATION_TIMEOUT_SEC=${VSS_INPUT_MEDIA_VERIFICATION_TIMEOUT_SEC}
# EVS (Efficient Video Sampling) — VLM_VIDEO_PRUNING_RATE activates pruning in vLLM
VIA_EVS_SESSION=${VIA_EVS_SESSION}
VLM_VIDEO_PRUNING_RATE=${VLM_VIDEO_PRUNING_RATE}
VLLM_EVS_SIMILARITY_THRESHOLD=${VLLM_EVS_SIMILARITY_THRESHOLD}
VIA_EVS_TOKEN_BUDGET=${VIA_EVS_TOKEN_BUDGET}
# Disable vLLM prefix and MM preprocessor caches for true perf measurements unless overridden.
VLLM_ENABLE_PREFIX_CACHING=${VLLM_ENABLE_PREFIX_CACHING}
VLLM_DISABLE_MM_PREPROCESSOR_CACHE=${VLLM_DISABLE_MM_PREPROCESSOR_CACHE}
RTVI_VLLM_MM_PROCESSOR_CACHE_GB=${RTVI_VLLM_MM_PROCESSOR_CACHE_GB}
RTVI_VLLM_MOE_BACKEND=${RTVI_VLLM_MOE_BACKEND}
RTVI_VLM_MAX_GENERATION_TOKENS=${RTVI_VLM_MAX_GENERATION_TOKENS}
RTVI_VLM_KAFKA_ASYNC_SEND_QUEUE_MAXSIZE=${RTVI_VLM_KAFKA_ASYNC_SEND_QUEUE_MAXSIZE}
VLLM_NUM_PREPROCESS_WORKERS=${VLLM_NUM_PREPROCESS_WORKERS}
VLLM_MM_TENSOR_IPC=${VLLM_MM_TENSOR_IPC}
VLLM_MULTIMODAL_TENSOR_IPC=${VLLM_MULTIMODAL_TENSOR_IPC}
VLLM_MM_ENCODER_ATTN_BACKEND=${VLLM_MM_ENCODER_ATTN_BACKEND}
VLLM_ATTENTION_BACKEND=${VLLM_ATTENTION_BACKEND}
VLLM_NVFP4_GEMM_BACKEND=${VLLM_NVFP4_GEMM_BACKEND}
VLLM_ENFORCE_EAGER=${VLLM_ENFORCE_EAGER}
TORCH_CUDNN_V8_API_DISABLED=${TORCH_CUDNN_V8_API_DISABLED}
RTVI_ENABLE_GOP_DECODE_OPT=${RTVI_ENABLE_GOP_DECODE_OPT}
VLM_USE_FPS_FOR_CHUNKING=${VLM_USE_FPS_FOR_CHUNKING}
PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF}
# vLLM tuning knobs (empty = use compose.perf.yaml / vLLM defaults)
VLLM_MAX_NUM_BATCHED_TOKENS=${VLLM_MAX_NUM_BATCHED_TOKENS}
VLLM_GPU_MEMORY_UTILIZATION=${VLLM_GPU_MEMORY_UTILIZATION}
VLM_MAX_MODEL_LEN=${VLM_MAX_MODEL_LEN}
NUM_VLM_PROCS=${NUM_VLM_PROCS}
VLM_BATCH_SIZE=${VLM_BATCH_SIZE}
RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT=${RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT}
RTVI_DISABLE_LIVESTREAM_PREVIEW=${RTVI_DISABLE_LIVESTREAM_PREVIEW}
RTVI_RTSP_LATENCY=${RTVI_RTSP_LATENCY}
RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY=${RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY}
RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS=${RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS}
RTVI_ENABLE_LIVE_TIMESTAMP_FILTER=${RTVI_ENABLE_LIVE_TIMESTAMP_FILTER}
RTVI_ENABLE_FILE_TIMESTAMP_FILTER=${RTVI_ENABLE_FILE_TIMESTAMP_FILTER}
RTVI_EMPTY_CUDA_CACHE_ON_RESULT=${RTVI_EMPTY_CUDA_CACHE_ON_RESULT}
VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK=${VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK}
EOF
    log "  .env.perf written to ${ENV_PERF_FILE}"

    RTVI_HEALTH_URL="http://localhost:${BACKEND_PORT}/v1/health/ready"

    # Derive compose project name from the compose file's directory (docker compose default).
    # Used for docker label filters — avoids re-parsing the compose file (which hits BACKEND_PORT?).
    _compose_project=$(basename "$(dirname "${COMPOSE_PERF_YAML}")" | tr '[:upper:]' '[:lower:]')

    log "  Launching RTVI VLM (this may take several minutes for model download/load)..."
    docker compose -f "${COMPOSE_PERF_YAML}" --env-file "${ENV_PERF_FILE}" up -d \
        || die "Failed to start RTVI VLM service"
    RTVI_STARTED=true

    # Start a background docker logs -f so container output is visible in real time
    # while we poll for health, without blocking the poll loop.
    _log_pid=""
    _log_cid=""
    for _init_attempt in 1 2 3 4 5; do
        _log_cid=$(docker ps -q \
            --filter "label=com.docker.compose.project=${_compose_project}" \
            --filter "label=com.docker.compose.service=rtvi-server" 2>/dev/null | head -1 || true)
        [[ -n "${_log_cid}" ]] && break
        sleep 1
    done
    if [[ -n "${_log_cid}" ]]; then
        log "  Container ${_log_cid}: streaming logs in real time..."
        docker logs -f "${_log_cid}" 2>&1 | sed --unbuffered "s/^/  [rtvi-server] /" &
        _log_pid=$!
    else
        warn "  Could not resolve container ID for log streaming — logs unavailable until container starts."
    fi

    log "  Waiting for RTVI VLM at ${RTVI_HEALTH_URL} (timeout: ${RTVI_HEALTH_TIMEOUT}s)..."
    RTEL=0
    _rtvi_cid_logged=false

    while true; do
        # Find the running rtvi-server container via docker labels — bypasses compose file parsing.
        _rtvi_cid=$(docker ps -q \
            --filter "label=com.docker.compose.project=${_compose_project}" \
            --filter "label=com.docker.compose.service=rtvi-server" 2>/dev/null | head -1 || true)

        # If not running, also check stopped containers so we can detect exit/OOM.
        if [[ -z "${_rtvi_cid}" ]]; then
            _rtvi_cid=$(docker ps -aq \
                --filter "label=com.docker.compose.project=${_compose_project}" \
                --filter "label=com.docker.compose.service=rtvi-server" 2>/dev/null | head -1 || true)
        fi

        if [[ -n "${_rtvi_cid}" && "${_rtvi_cid_logged}" == "false" ]]; then
            log "  Monitoring container: ${_rtvi_cid}"
            _rtvi_cid_logged=true
        fi

        _health=""
        if [[ -n "${_rtvi_cid}" ]]; then
            _state=$(docker inspect --format='{{.State.Status}}' "${_rtvi_cid}" 2>/dev/null || true)

            # Fail immediately if the container has exited (OOM, crash, bad config)
            if [[ "${_state}" == "exited" ]]; then
                _oom=$(docker inspect --format='{{.State.OOMKilled}}' "${_rtvi_cid}" 2>/dev/null || true)
                _code=$(docker inspect --format='{{.State.ExitCode}}' "${_rtvi_cid}" 2>/dev/null || true)
                [[ -n "${_log_pid}" ]] && { kill "${_log_pid}" 2>/dev/null || true; wait "${_log_pid}" 2>/dev/null || true; }
                if [[ "${_oom}" == "true" ]]; then
                    die "RTVI VLM container was OOM-killed. Reduce GPU memory pressure (e.g. VLLM_GPU_MEMORY_UTILIZATION, VLM_BATCH_SIZE) and re-run. Logs: docker compose -f ${COMPOSE_PERF_YAML} logs rtvi-server"
                else
                    die "RTVI VLM container exited unexpectedly (exit code: ${_code:-?}). Check logs: docker compose -f ${COMPOSE_PERF_YAML} logs rtvi-server"
                fi
            fi

            # Check Docker healthcheck status before polling the HTTP endpoint.
            # healthy   → model loaded and API is up; done.
            # unhealthy → healthcheck failed; fail fast rather than waiting for timeout.
            # starting  → still in start_period; keep waiting.
            _health=$(docker inspect --format='{{.State.Health.Status}}' "${_rtvi_cid}" 2>/dev/null || true)
            if [[ "${_health}" == "healthy" ]]; then
                [[ -n "${_log_pid}" ]] && { kill "${_log_pid}" 2>/dev/null || true; wait "${_log_pid}" 2>/dev/null || true; }
                log "  ${_C_GREEN}RTVI VLM container is healthy.${_C_RESET}"
                break
            elif [[ "${_health}" == "unhealthy" ]]; then
                [[ -n "${_log_pid}" ]] && { kill "${_log_pid}" 2>/dev/null || true; wait "${_log_pid}" 2>/dev/null || true; }
                die "RTVI VLM container is unhealthy (docker healthcheck failed). Check logs: docker compose -f ${COMPOSE_PERF_YAML} logs rtvi-server"
            fi
            # "starting" or empty (no healthcheck) — fall through to direct API check below
        fi

        # Fallback: poll the health endpoint directly (handles containers without a healthcheck)
        if curl -sf "${RTVI_HEALTH_URL}" >/dev/null 2>&1; then
            [[ -n "${_log_pid}" ]] && { kill "${_log_pid}" 2>/dev/null || true; wait "${_log_pid}" 2>/dev/null || true; }
            log "  ${_C_GREEN}RTVI VLM is ready at ${RTVI_HEALTH_URL}${_C_RESET}"
            break
        fi

        RTEL=$((RTEL + 15))
        if [[ "${RTEL}" -ge "${RTVI_HEALTH_TIMEOUT}" ]]; then
            [[ -n "${_log_pid}" ]] && { kill "${_log_pid}" 2>/dev/null || true; wait "${_log_pid}" 2>/dev/null || true; }
            die "RTVI VLM not healthy after ${RTVI_HEALTH_TIMEOUT}s. Check logs: docker compose -f ${COMPOSE_PERF_YAML} logs rtvi-server"
        fi
        log "  [${RTEL}s] RTVI VLM not ready yet (container: ${_health:-starting}, model loading)..."

        sleep 15
    done
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo -e "${_C_BOLD}============================================================${_C_RESET}"
echo -e "${_C_BOLD}  RTVI VLM Performance Environment — Setup Summary${_C_RESET}"
echo -e "${_C_BOLD}============================================================${_C_RESET}"
echo ""
echo -e "  VST live streams detected: ${_C_GREEN}${STREAM_COUNT}${_C_RESET}"
echo -e "  Benchmark RTSP URL       : ${_C_CYAN}${INJECT_RTSP_URL}${_C_RESET}"
echo ""
if [[ "${STREAM_COUNT}" -gt 1 ]]; then
    echo "  All detected streams:"
    echo "${RTSP_URLS}" | tr ';' '\n' | while read -r url; do echo -e "    ${_C_CYAN}${url}${_C_RESET}"; done
    echo ""
fi
if [[ "${RTVI_STARTED}" == "true" ]]; then
echo -e "  RTVI VLM service   : ${_C_GREEN}http://localhost:${BACKEND_PORT}/v1/health/ready  [ready]${_C_RESET}"
echo -e "  .env.perf          : ${ENV_PERF_FILE}"
else
echo -e "  RTVI VLM service   : ${_C_YELLOW}NOT started (compose.perf.yaml not found at ${COMPOSE_PERF_YAML})${_C_RESET}"
fi
echo ""
echo -e "${_C_BOLD}  Next steps:${_C_RESET}"
echo ""
echo "  1. Activate the Python environment:"
echo -e "       ${_C_CYAN}source ${VENV_DIR}/bin/activate${_C_RESET}"
echo ""
echo "  2. Run benchmarks (from repo root):"
echo -e "       ${_C_CYAN}# Select the config for your GPU platform:"
echo -e "       CONFIG=perf/benchmark/rtvi_vlm_bcd_3_2_config.yaml   # BCD 3.2 profile: 640x640, 10/20/40 frames"
echo -e "       # Legacy platform configs:"
echo -e "       # CONFIG=perf/benchmark/rtvi_vlm_config_h100.yaml     # H100 / H100-NVL / H100-PCIe"
echo -e "       # CONFIG=perf/benchmark/rtvi_vlm_config_rtx_pro.yaml  # RTX 6000 Ada / RTX PRO"
echo -e "       # CONFIG=perf/benchmark/rtvi_vlm_config_l40s.yaml     # L40S (set VLLM_GPU_MEMORY_UTILIZATION=0.45 in .env.perf)"
echo -e "       # CONFIG=perf/benchmark/rtvi_vlm_config_jetson.yaml   # Jetson Thor"
echo -e "       # CONFIG=perf/benchmark/rtvi_vlm_config_spark.yaml    # DGX Spark (GB10)"
echo -e "       BENCH=\"python3 perf/benchmark/rtvi_perf_benchmark.py --config \$CONFIG\""
echo ""
echo "       # ── BCD 1: Maximum streams per GPU [Stream Processing] ──────────────────────"
echo "       \$BENCH --scenario max_live_streams_test_1_token_2k     # BCD 3.2,  OSL=1,   640x640/10 frames"
echo "       \$BENCH --scenario max_live_streams_test_100_token_2k   # BCD 3.2,  OSL=100, 640x640/10 frames"
echo "       \$BENCH --scenario max_live_streams_test_1_token_4k     # BCD 3.2,  OSL=1,   640x640/20 frames"
echo "       \$BENCH --scenario max_live_streams_test_100_token_4k   # BCD 3.2,  OSL=100, 640x640/20 frames"
echo "       \$BENCH --scenario max_live_streams_test_1_token_8k     # BCD 3.2,  OSL=1,   640x640/40 frames"
echo "       \$BENCH --scenario max_live_streams_test_100_token_8k   # BCD 3.2,  OSL=100, 640x640/40 frames"
echo "       # Legacy configs also still include max_live_streams_test_*_token and *_448 scenarios."
echo ""
echo "       # Optional BCD 1 CLI overrides (override YAML config values at runtime):"
echo "       #   --initial-stream-count N        starting stream count (default: from YAML)"
echo "       #   --add-stream-count N            streams added per ramp step (default: from YAML)"
echo "       #   --binary-search-refinement      enable Phase 2 binary search (default: on)"
echo "       #   --no-binary-search-refinement   disable Phase 2 binary search"
echo "       # Example: fast ramp with Phase 2 refinement:"
echo "       #   \$BENCH --scenario max_live_streams_test_1_token_8k \\"
echo "       #          --initial-stream-count 5 --add-stream-count 5"
echo ""
echo "       # ── BCD 2: VLM E2E Latency [Stream Processing] ──────────────────────────────"
echo "       \$BENCH --scenario concurrency_test_1_token_2k          # BCD 3.2, OSL=1,   640x640/10 frames"
echo "       \$BENCH --scenario concurrency_test_100_token_2k        # BCD 3.2, OSL=100, 640x640/10 frames"
echo "       \$BENCH --scenario concurrency_test_1_token_4k          # BCD 3.2, OSL=1,   640x640/20 frames"
echo "       \$BENCH --scenario concurrency_test_100_token_4k        # BCD 3.2, OSL=100, 640x640/20 frames"
echo "       \$BENCH --scenario concurrency_test_1_token_8k          # BCD 3.2, OSL=1,   640x640/40 frames"
echo "       \$BENCH --scenario concurrency_test_100_token_8k        # BCD 3.2, OSL=100, 640x640/40 frames"
echo "       # dGPU stream counts are [1,16,32,64,128]."
echo "       # Edge override: add --concurrency-levels 1 8 16 for AGX Thor / DGX Spark."
echo ""
echo "       # ── BCD 3: Request Throughput [Non-Streaming] ───────────────────────────────"
echo "       \$BENCH --scenario file_burst_1_token_2k     # BCD 3.2, OSL=1,   5-min steady state"
echo "       \$BENCH --scenario file_burst_100_token_2k   # BCD 3.2, OSL=100, 5-min steady state"
echo "       \$BENCH --scenario file_burst_1_token_4k     # BCD 3.2, OSL=1,   5-min steady state"
echo "       \$BENCH --scenario file_burst_100_token_4k   # BCD 3.2, OSL=100, 5-min steady state"
echo "       \$BENCH --scenario file_burst_1_token_8k     # BCD 3.2, OSL=1,   5-min steady state"
echo "       \$BENCH --scenario file_burst_100_token_8k   # BCD 3.2, OSL=100, 5-min steady state"
echo ""
echo "       # ── BCD 4: VLM E2E Request Latency [Non-Streaming] ──────────────────────────"
echo "       \$BENCH --scenario e2e_latency_1_token_2k     # BCD 3.2, OSL=1,   10s/10m/60m videos"
echo "       \$BENCH --scenario e2e_latency_100_token_2k   # BCD 3.2, OSL=100, 10s/10m/60m videos"
echo "       \$BENCH --scenario e2e_latency_1_token_4k     # BCD 3.2, OSL=1,   10s/10m/60m videos"
echo "       \$BENCH --scenario e2e_latency_100_token_4k   # BCD 3.2, OSL=100, 10s/10m/60m videos"
echo "       \$BENCH --scenario e2e_latency_1_token_8k     # BCD 3.2, OSL=1,   10s/10m/60m videos"
echo -e "       \$BENCH --scenario e2e_latency_100_token_8k   # BCD 3.2, OSL=100, 10s/10m/60m videos${_C_RESET}"
echo ""
echo "  3. (Optional) Generate dashboard JSON and upload to MinIO:"
echo -e "       ${_C_CYAN}# Set MinIO env vars before using --upload (see vss_perf_common.py for defaults):"
echo -e "       #   export MINIO_ENDPOINT=<host:port>"
echo -e "       #   export MINIO_BUCKET=<bucket>"
echo -e "       #   export MINIO_ACCESS_KEY=<key>"
echo -e "       #   export MINIO_SECRET_KEY=<secret>"
echo ""
echo "       # Option A: Generate during benchmark run (after all scenarios):"
echo -e "       \$BENCH --dashboard-json vss_results.json --config-id h100"
echo -e "       # Add --upload to push to MinIO automatically:"
echo -e "       \$BENCH --dashboard-json vss_results.json --config-id h100 --upload"
echo ""
echo "       # Option B: Generate from existing report directories:"
echo -e "       python3 perf/benchmark/generate_vss_results_from_reports.py \\"
echo -e "           /path/to/rtvi-vlm-perf-report-h100/ \\"
echo -e "           /path/to/rtvi-vlm-perf-report-rtx-pro/"
echo -e "       # Add --upload to push to MinIO:"
echo -e "       python3 perf/benchmark/generate_vss_results_from_reports.py --upload \\"
echo -e "           /path/to/rtvi-vlm-perf-report-h100/"
echo ""
echo "       # Option C: Update KPI baselines in vss_perf_analyzer:"
echo -e "       python3 perf/benchmark/update_kpi_baselines.py \\"
echo -e "           --reports h100=perf/benchmark/rtvi-vlm-perf-report \\"
echo -e "           --kpi ~/VSS/vss_perf_analyzer/kpi_definitions/rtvi-vlm.yaml --dry-run${_C_RESET}"
echo ""
echo "  4. Generate XLSX report and charts:"
echo -e "       ${_C_CYAN}cd perf/benchmark"
echo -e "       python3 plot_perf_reports.py all \\"
echo -e "           --reports h100=./rtvi-vlm-perf-report \\"
echo -e "           --configs h100=rtvi_vlm_config_h100.yaml \\"
echo -e "           --output ./perf_charts"
echo -e "       python3 generate_perf_xlsx.py \\"
echo -e "           --reports H100=./rtvi-vlm-perf-report \\"
echo -e "           --configs H100=rtvi_vlm_config_h100.yaml \\"
echo -e "           --charts ./perf_charts \\"
echo -e "           --output perf_report.xlsx --release \"3.1 EA2\"${_C_RESET}"
echo ""
echo "  5. View results:"
echo -e "       ${_C_CYAN}ls perf/benchmark/rtvi-vlm-perf-report/${_C_RESET}"
echo "       # Prometheus UI: http://localhost:9090"
echo "       # VST API:       ${VST_API_BASE}/vst/api/v1/sensor/streams"
echo ""
echo -e "${_C_BOLD}============================================================${_C_RESET}"
