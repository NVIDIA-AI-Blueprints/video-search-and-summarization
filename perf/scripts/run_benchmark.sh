#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIO=quick_test
OUTPUT_DIR="${PERF_OUTPUT_DIR:-$ROOT/results/local}"
TEARDOWN=false
while getopts 'f:c:s:t:v:l:G:p:r:I:O:dh' option; do
    case "$option" in
        f) export COMPOSE_FILE="$OPTARG" ;;
        c) export PERF_CONFIG="$OPTARG" ;;
        s) SCENARIO="$OPTARG" ;;
        t) export DEPLOY_TIMEOUT_SECONDS="$OPTARG" ;;
        r) export RELEASE="$OPTARG" ;;
        O) OUTPUT_DIR="$OPTARG" ;;
        d) TEARDOWN=true ;;
        G) [[ "$OPTARG" == auto || "$OPTARG" == none ]] || { echo 'RT-CV does not use VLM GPU overlays' >&2; exit 2; } ;;
        p) echo 'Legacy LVS -p is ignored; public origin comes from config.yaml.' >&2 ;;
        v|l|I) echo 'VLM/LLM image and placement flags do not apply to compose-base.' >&2; exit 2 ;;
        h) printf '%s\n' 'CONFIG_ID=<matrix id> VSS_PERF_VIDEO=/absolute/video.mp4 ./compose/run_benchmark.sh [-f compose.yaml] [-s quick_test] [-O result_dir] [-r release] -d'; exit 0 ;;
        *) exit 2 ;;
    esac
done
shift $((OPTIND-1))
[[ $# == 0 ]] || { echo 'Unexpected positional arguments' >&2; exit 2; }
export CONFIG_ID="${CONFIG_ID:?Set CONFIG_ID to a hardware matrix entry}"
export TRIGGERED_BY="${TRIGGERED_BY:-manual}"
export VSS_PERF_WRAPPER=true
export VSS_PERF_STAGE=pre_flight
source "$ROOT/scripts/common.sh"
mkdir -p "$OUTPUT_DIR"
ARGS=(--config "$PERF_CONFIG" --perf-config "$PERF_CONFIGS" --scenario "$SCENARIO" --config-id "$CONFIG_ID" --output-json "$OUTPUT_DIR" --triggered-by "$TRIGGERED_BY" --rt-cv-gpus "$RT_CV_DEVICE_ID")
[[ -z "${RELEASE:-}" ]] || ARGS+=(--release "$RELEASE")
[[ -z "${PIPELINE_URL:-}" ]] || ARGS+=(--pipeline-url "$PIPELINE_URL")
REQUEST_UPLOAD=false
if [[ "${UPLOAD_TO_MINIO:-false}" == true ]]; then
    REQUEST_UPLOAD=true
    if [[ "${UPLOAD_AFTER_TEARDOWN:-false}" != true ]]; then ARGS+=(--upload); fi
fi
BENCHMARK="$ROOT/benchmark/core/vss_perf_benchmark.py"
RESULT_PATH=""
STATUS=0
finish() {
    local original_status=$?
    local teardown_failed=false
    trap - EXIT
    if [[ "$TEARDOWN" == true ]]; then
        if ! "$ROOT/scripts/teardown.sh"; then
            teardown_failed=true
            if [[ -n "$RESULT_PATH" ]]; then
                "$PYTHON" "$BENCHMARK" "${ARGS[@]}" --stage-failure teardown --failure-message 'Docker Compose teardown failed' --existing-result "$RESULT_PATH" || true
            fi
            STATUS=1
        fi
    fi
    if [[ "$REQUEST_UPLOAD" == true && "${UPLOAD_AFTER_TEARDOWN:-false}" == true && -n "$RESULT_PATH" ]]; then
        local publish_args=(--upload-results-only)
        if [[ "$teardown_failed" == true ]]; then publish_args+=(--stage-failure teardown --failure-message 'Docker Compose teardown failed'); fi
        "$PYTHON" "$BENCHMARK" "${ARGS[@]}" "${publish_args[@]}" || STATUS=1
    fi
    [[ $STATUS != 0 ]] || STATUS=$original_status
    exit "$STATUS"
}
trap finish EXIT
for stage in pre_flight deploy post_deploy; do
    if ! "$ROOT/scripts/$stage.sh"; then
        "$PYTHON" "$BENCHMARK" "${ARGS[@]}" --stage-failure "$stage" --failure-message "Lifecycle stage $stage failed" > "$OUTPUT_DIR/benchmark-output.log" || STATUS=$?
        RESULT_PATH="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["result_file"])' "$OUTPUT_DIR/benchmark-output.log")"
        exit "${STATUS:-1}"
    fi
done
"$PYTHON" "$BENCHMARK" "${ARGS[@]}" > "$OUTPUT_DIR/benchmark-output.log" || STATUS=$?
RESULT_PATH="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["result_file"])' "$OUTPUT_DIR/benchmark-output.log")"
cat "$OUTPUT_DIR/benchmark-output.log"
exit "$STATUS"
