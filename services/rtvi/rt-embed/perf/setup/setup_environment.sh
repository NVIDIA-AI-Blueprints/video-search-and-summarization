#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV_BIN="${PERF_UV_BIN:-uv}"
PERF_VENV_DIR="${PERF_VENV_DIR:-$SETUP_DIR/.venv}"

command -v "$UV_BIN" >/dev/null 2>&1 || { echo "uv is required" >&2; exit 1; }
UV_PROJECT_ENVIRONMENT="$PERF_VENV_DIR" "$UV_BIN" sync \
    --project "$SETUP_DIR" --locked --no-default-groups
