#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV_BIN="${PERF_UV_BIN:-uv}"
PERF_VENV_DIR="${PERF_VENV_DIR:-$SETUP_DIR/.venv}"

if [[ "$UV_BIN" == */* ]]; then
    if [[ ! -x "$UV_BIN" ]]; then
        echo "Configured uv executable is not available: $UV_BIN" >&2
        exit 1
    fi
elif ! command -v "$UV_BIN" >/dev/null 2>&1; then
    echo "uv is required to prepare the performance benchmark environment" >&2
    exit 1
fi

UV_PROJECT_ENVIRONMENT="$PERF_VENV_DIR" \
    "$UV_BIN" sync \
    --project "$SETUP_DIR" \
    --locked \
    --no-default-groups
