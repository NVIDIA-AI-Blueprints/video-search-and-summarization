#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PERF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${PERF_CONFIG:-$PERF_ROOT/configs/config.yaml}"
output="${PERF_OUTPUT_DIR:-$PERF_ROOT/results}"
config_id="${PERF_CONFIG_ID:-rtvi-embed}"
scenario=""
triggered_by="${TRIGGERED_BY:-manual}"
pipeline_url="${PIPELINE_URL:-}"
release="${RELEASE:-}"
compose_file="${COMPOSE_FILE:-$PERF_ROOT/setup/compose/compose.perf.yaml}"
gpus="${VLM_GPUS:-0}"

while (($#)); do
    case "$1" in
        --config) config="$2"; shift 2 ;;
        --output-json) output="$2"; shift 2 ;;
        --config-id) config_id="$2"; shift 2 ;;
        --scenario) scenario="$2"; shift 2 ;;
        --triggered-by) triggered_by="$2"; shift 2 ;;
        --pipeline-url) pipeline_url="$2"; shift 2 ;;
        --release) release="$2"; shift 2 ;;
        --compose-file) compose_file="$2"; shift 2 ;;
        --vlm-gpus) gpus="$2"; shift 2 ;;
        --llm-gpus|--vlm-model|--llm-model|--vision-input-tokens|--gpu-model) shift 2 ;;
        *) echo "Unknown RTVI Embed benchmark argument: $1" >&2; exit 2 ;;
    esac
done

mkdir -p "$output/native" "$output/vss-perf-results"
runtime_config="$output/native/runtime-config.yaml"
PERF_VENV_DIR="${PERF_VENV_DIR:-$PERF_ROOT/setup/.venv}"
PERF_VENV_DIR="$PERF_VENV_DIR" "$PERF_ROOT/setup/setup_environment.sh"
"$PERF_VENV_DIR/bin/python" "$PERF_ROOT/scripts/prepare_runtime_config.py" \
    --source "$config" --destination "$runtime_config" \
    --native-output "$output/native" --release "$release" --gpus "$gpus"

export RTVI_IMAGE="${IMAGE_TAG:-${RTVI_IMAGE:-}}"
export NVIDIA_VISIBLE_DEVICES="$gpus"
export BENCHMARK_CONFIG="$runtime_config"
export COMPOSE_PERF_YAML="$compose_file"
export PROMETHEUS_CONFIG="$PERF_ROOT/setup/compose/prometheus.perf.yml"
export DCGM_METRICS_CONFIG="$PERF_ROOT/dcgm/dcgm-metrics-config.csv"
export ENV_PERF_FILE="$output/native/.env.perf"
export VENV_DIR="$PERF_VENV_DIR"
export PERF_VENV_PREPARED=true
export VST_DIR="${VST_DIR:-$output/native/vst_package}"
export COMPOSE_PROJECT_NAME="rtvi-embed-perf-${BUILD_NUMBER:-$(id -u)}"
export VST_COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME}-vst"

collect_diagnostics() {
    local status="${1:-1}" outcome=failure
    [[ "$status" -eq 0 ]] && outcome=success
    [[ -x "${PERF_DIAGNOSTICS_COLLECTOR:-}" ]] || return 0
    "$PERF_DIAGNOSTICS_COLLECTOR" --output "${PERF_DIAGNOSTICS_DIR:-$output/diagnostics}" \
        --outcome "$outcome" --service RTVI-Embed --since "${PERF_RUN_STARTED_AT:-}" \
        --compose-project "$COMPOSE_PROJECT_NAME" || true
    "$PERF_DIAGNOSTICS_COLLECTOR" --output "${PERF_DIAGNOSTICS_DIR:-$output/diagnostics}" \
        --outcome "$outcome" --service RTVI-Embed --since "${PERF_RUN_STARTED_AT:-}" \
        --compose-project "$VST_COMPOSE_PROJECT" || true
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    set +e
    collect_diagnostics "$status"
    "$PERF_ROOT/scripts/native_teardown_perf_env.sh"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$PERF_ROOT/scripts/native_setup_perf_env.sh"

args=(--config "$runtime_config" --output-json "$output/vss-perf-results" \
      --config-id "$config_id")
[[ -z "$scenario" ]] || args+=(--scenario "$scenario")
"$PERF_VENV_DIR/bin/python" "$PERF_ROOT/benchmark/rtvi_perf_benchmark.py" "${args[@]}"
"$PERF_VENV_DIR/bin/python" "$PERF_ROOT/scripts/finalize_result_metadata.py" \
    --results "$output/vss-perf-results" --triggered-by "$triggered_by" \
    --pipeline-url "$pipeline_url"
