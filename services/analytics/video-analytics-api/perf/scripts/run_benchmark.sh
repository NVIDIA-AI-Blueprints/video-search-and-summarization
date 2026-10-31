#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [[ "${PERF_NATIVE_ONLY:-false}" != "true" ]]; then
    PERF_VENV_DIR="${PERF_VENV_DIR:-$PERF_DIR/setup/.venv}"
    PERF_VENV_DIR="$PERF_VENV_DIR" "$PERF_DIR/setup/setup_environment.sh"
    export PYTHONPATH="$PERF_DIR/benchmark${PYTHONPATH:+:$PYTHONPATH}"
    exec "$PERF_VENV_DIR/bin/python" \
        "$PERF_DIR/benchmark/core/vss_perf_benchmark.py" "$@"
fi

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM
    set +e
    collect_perf_diagnostics "$exit_code"
    "$PERF_DIR/scripts/teardown.sh" || true
    exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$PERF_DIR/scripts/pre_flight.sh"
"$PERF_DIR/scripts/deploy.sh"
"$PERF_DIR/scripts/post_deploy.sh"
"$PERF_DIR/scripts/measure.sh"
