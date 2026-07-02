#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# run_benchmark.sh — Convenience runner for the LVS performance benchmark.
#
# IMPORTANT: Make this script executable before running:
#   chmod +x scripts/run_benchmark.sh
#
# Usage (from the skill root):
#   ./scripts/run_benchmark.sh [--scenario <name>] [--output-dir <dir>] [<extra args>]
#
# Examples:
#   ./scripts/run_benchmark.sh --scenario single_file_test
#   ./scripts/run_benchmark.sh --scenario file_burst_test
#   ./scripts/run_benchmark.sh --scenario single_file_test --output-dir vss-perf-report-v2
#   ./scripts/run_benchmark.sh --debug
#
# Required environment variables — NO defaults to prevent hitting another user's instance:
#   LVS_BACKEND              — endpoint of the LVS instance YOU deployed (from vss-deploy-profile)
#   VLM_GPUS                 — GPU index(es) running VLM in your deployment (e.g. "6")
#   LLM_GPUS                 — GPU index(es) running LLM in your deployment (e.g. "7")
#
# Optional environment variables:
#   VSS_BENCHMARK_DATA_DIR   — root directory containing test videos (default: ~/vss-benchmark-data)
#
# WARNING: On shared GPU systems multiple LVS instances may be running on different ports.
# Always set LVS_BACKEND to your own deployment's endpoint. Never assume port 38111.

set -euo pipefail

# ---------------------------------------------------------------------------
# Validate required variables — fail fast if not set
# ---------------------------------------------------------------------------
if [[ -z "${LVS_BACKEND:-}" ]]; then
    echo "ERROR: LVS_BACKEND is not set." >&2
    echo "  Set it to the endpoint of the LVS instance YOU deployed:" >&2
    echo "    export LVS_BACKEND=http://localhost:<port>" >&2
    echo "  On shared systems, do NOT assume port 38111." >&2
    exit 1
fi
if [[ -z "${VLM_GPUS:-}" || -z "${LLM_GPUS:-}" ]]; then
    echo "ERROR: VLM_GPUS and LLM_GPUS must be set to the GPU indices used by your deployment." >&2
    echo "  Example: export VLM_GPUS=6 && export LLM_GPUS=7" >&2
    exit 1
fi
if [[ -z "${LVS_CONTAINER_NAME:-}" ]]; then
    echo "ERROR: LVS_CONTAINER_NAME is not set." >&2
    echo "  Set it to YOUR LVS container name (e.g. vss-lvs or <project>-lvs-server-1)." >&2
    echo "  Required for pre-flight checks that confirm you're benchmarking YOUR instance/GPUs." >&2
    exit 1
fi
VSS_BENCHMARK_DATA_DIR="${VSS_BENCHMARK_DATA_DIR:-${HOME}/vss-benchmark-data}"

# Everything the benchmark needs lives alongside this script in scripts/
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPTS_DIR}/vss-bench-env"
CONFIG_FILE="${SCRIPTS_DIR}/config.yaml"

# Export for the Python benchmark process
export VIA_BACKEND="${LVS_BACKEND}"
export VIA_VLM_GPUS="${VLM_GPUS}"
export VIA_LLM_GPUS="${LLM_GPUS}"
export VSS_BENCHMARK_DATA_DIR

# ---------------------------------------------------------------------------
# Step 1: Pre-flight checks
# Validates config + GPU ids, confirms the backend is reachable with the dev
# /files route on, and (the strong part) verifies YOUR LVS container owns the
# backend port and the configured GPUs are the ones your VLM/LLM actually use —
# so you can't silently benchmark another tenant's instance or idle GPUs.
# ---------------------------------------------------------------------------
if ! CONFIG="${CONFIG_FILE}" "${SCRIPTS_DIR}/preflight.sh"; then
    echo "ERROR: pre-flight checks failed (see above). Aborting before benchmark." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 2: Start media server if not already running
# ---------------------------------------------------------------------------
if ! docker ps --filter "name=lvs-benchmark-media-server" --filter "status=running" \
        --format '{{.Names}}' | grep -q "lvs-benchmark-media-server"; then
    echo "Starting media server ..."
    if [[ ! -d "${VSS_BENCHMARK_DATA_DIR}/videos" ]]; then
        echo "WARNING: ${VSS_BENCHMARK_DATA_DIR}/videos does not exist."
        echo "  Set VSS_BENCHMARK_DATA_DIR and ensure videos are in the 'videos/' subdirectory."
        echo "  Continuing — media server may fail if the directory is missing."
    fi
    docker compose -f "${SCRIPTS_DIR}/media-server.yaml" up -d
    sleep 3
fi

# Verify media server health
if curl -sf "http://localhost:8888/health" > /dev/null 2>&1; then
    echo "Media server is healthy."
else
    echo "WARNING: Media server health check failed. Check that ${VSS_BENCHMARK_DATA_DIR}/videos exists."
fi

# ---------------------------------------------------------------------------
# Step 3: Create Python venv if it doesn't exist
# ---------------------------------------------------------------------------
VENV_NEW=false
if [[ ! -d "${VENV_DIR}" ]]; then
    echo "Creating Python virtual environment at ${VENV_DIR} ..."
    python3 -m venv "${VENV_DIR}"
    VENV_NEW=true
fi

# Activate venv
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# ---------------------------------------------------------------------------
# Step 4: Install requirements if venv is new
# ---------------------------------------------------------------------------
if [[ "${VENV_NEW}" == true ]]; then
    echo "Installing benchmark requirements ..."
    pip install -r "${SCRIPTS_DIR}/requirements.txt" -q
fi

# ---------------------------------------------------------------------------
# Step 5: Run the benchmark
# ---------------------------------------------------------------------------
echo ""
echo "Running LVS performance benchmark ..."
echo "  Backend:    ${LVS_BACKEND}"
echo "  VLM GPUs:   ${VLM_GPUS}"
echo "  LLM GPUs:   ${LLM_GPUS}"
echo "  Config:     ${CONFIG_FILE}"
echo "  Args:       $*"
echo ""

cd "${SCRIPTS_DIR}"
python vss_perf_benchmark.py --config "${CONFIG_FILE}" "$@"
