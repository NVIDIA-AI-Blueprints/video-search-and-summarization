#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
export VSS_PERF_STAGE=pre_flight
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
if [[ "${VSS_PERF_BOUNDED_STAGE:-}" != "$VSS_PERF_STAGE" ]]; then
    export VSS_PERF_BOUNDED_STAGE="$VSS_PERF_STAGE"
    stage_status=0
    "$PYTHON" "$VSS_PERF_ROOT/scripts/run_stage.py" "$PERF_STAGE_TIMEOUT_SECONDS" "${BASH_SOURCE[0]}" '' || stage_status=$?
    exit "$stage_status"
fi
for cmd in python3 docker nvidia-smi vss; do command -v "$cmd" >/dev/null || { echo "Missing prerequisite: $cmd" >&2; exit 2; }; done
"${DOCKER_CMD[@]}" info >/dev/null
"${DOCKER_CMD[@]}" compose version >/dev/null
nvidia-smi --id="$RT_CV_DEVICE_ID" --query-gpu=name,driver_version --format=csv,noheader
[[ -n "${VSS_PERF_STREAM_URL:-}" ]] || { echo 'Set VSS_PERF_STREAM_URL to the benchmark RTSP source, or VSS_PERF_VIDEO to a local video.' >&2; exit 2; }
[[ "$VSS_PERF_STREAM_URL" == rtsp://* || "$VSS_PERF_STREAM_URL" == rtsps://* ]] || { echo 'Benchmark source must use RTSP.' >&2; exit 2; }
if [[ -n "${VSS_PERF_VIDEO:-}" ]]; then "$PYTHON" "$VSS_PERF_ROOT/scripts/prepare_fixture.py"; fi
artifact_ref="${ARTIFACT_REF:-${IMAGE_TAG:-}}"
if [[ -n "$artifact_ref" ]]; then
    ngc_binary="$("$PYTHON" "$VSS_PERF_ROOT/scripts/ensure_ngc.py")"
    export PATH="$(dirname -- "$ngc_binary"):$PATH"
    destination="$VSS_DATA_DIR/promoted/$(date -u +%Y%m%dT%H%M%S)-$RANDOM"
    "$PYTHON" "$VSS_PERF_ROOT/scripts/promoted_artifact.py" --artifact-ref "$artifact_ref" --destination "$destination" --output-json "$VSS_DATA_DIR/promoted-artifact.json"
    source "$VSS_PERF_ROOT/scripts/common.sh"
fi
"$PYTHON" "$VSS_PERF_ROOT/scripts/prepare_runtime.py"
"${COMPOSE_CMD[@]}" config --quiet
