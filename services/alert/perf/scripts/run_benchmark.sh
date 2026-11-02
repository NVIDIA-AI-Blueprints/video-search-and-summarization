#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
perf_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PERF_VENV_DIR="${PERF_VENV_DIR:-$perf_root/setup/.venv}"
"$perf_root/setup/setup_environment.sh"
exec "$PERF_VENV_DIR/bin/python" "$perf_root/benchmark/core/vss_perf_benchmark.py" "$@"
