#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
setup_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV_PROJECT_ENVIRONMENT="${PERF_VENV_DIR:-$setup_dir/.venv}" \
    "${PERF_UV_BIN:-uv}" sync --project "$setup_dir" --locked --no-default-groups
