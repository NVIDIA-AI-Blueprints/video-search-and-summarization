#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
# teardown_perf_env.sh — Tear down all services started by setup_perf_env.sh
#
# Stops in reverse startup order:
#   1. RTVI VLM service (compose.perf.yaml)
#   2. VST
#   3. nvstreamer
#   4. sys_cache_cleaner (DGX Spark / Jetson only)
#
# All steps are idempotent — safe to run even if a service is not running.
#
# Optional environment variable overrides (same defaults as setup_perf_env.sh):
#   VST_DIR            — VST package directory (default: ~/rtvi-perf/vst_package)
#   COMPOSE_PERF_YAML  — path to compose.perf.yaml
#   ENV_PERF_FILE      — path to .env.perf (used for --env-file if present)
# =============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VST_DIR="${VST_DIR:-${HOME}/rtvi-perf/vst_package}"
VST_COMPOSE_PROJECT="${VST_COMPOSE_PROJECT:-rtvi-perf-vst}"

COMPOSE_PERF_YAML="${COMPOSE_PERF_YAML:-${REPO_ROOT}/docker/compose.perf.yaml}"
ENV_PERF_FILE="${ENV_PERF_FILE:-${REPO_ROOT}/docker/.env.perf}"

# Colors — disabled when stdout is not a terminal
if [[ -t 1 ]]; then
    _C_RESET='\033[0m'
    _C_CYAN='\033[0;36m'
    _C_BOLD_CYAN='\033[1;36m'
    _C_YELLOW='\033[0;33m'
    _C_GREEN='\033[0;32m'
    _C_BOLD='\033[1m'
else
    _C_RESET='' _C_CYAN='' _C_BOLD_CYAN='' _C_YELLOW='' _C_GREEN='' _C_BOLD=''
fi

log()  {
    if [[ "$*" == Step* ]]; then
        echo -e "${_C_BOLD_CYAN}[teardown_perf_env]${_C_RESET} ${_C_BOLD}$*${_C_RESET}"
    else
        echo -e "${_C_CYAN}[teardown_perf_env]${_C_RESET} $*"
    fi
}
warn() { echo -e "${_C_YELLOW}[teardown_perf_env] WARNING:${_C_RESET} $*" >&2; }

echo ""
echo -e "${_C_BOLD}============================================================${_C_RESET}"
echo -e "${_C_BOLD}  RTVI VLM Performance Environment — Teardown${_C_RESET}"
echo -e "${_C_BOLD}============================================================${_C_RESET}"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Stop RTVI VLM service
# ---------------------------------------------------------------------------
log "Step 1/4: Stopping RTVI VLM service..."
if [[ ! -f "${COMPOSE_PERF_YAML}" ]]; then
    warn "  compose.perf.yaml not found at ${COMPOSE_PERF_YAML} — skipping."
else
    if [[ -f "${ENV_PERF_FILE}" ]]; then
        docker compose -f "${COMPOSE_PERF_YAML}" --env-file "${ENV_PERF_FILE}" down 2>/dev/null \
            && log "  ${_C_GREEN}RTVI VLM stopped.${_C_RESET}" \
            || warn "  docker compose down returned non-zero (service may not have been running)."
    else
        docker compose -f "${COMPOSE_PERF_YAML}" down 2>/dev/null \
            && log "  ${_C_GREEN}RTVI VLM stopped.${_C_RESET}" \
            || warn "  docker compose down returned non-zero (service may not have been running)."
    fi
fi

# ---------------------------------------------------------------------------
# Step 2: Stop VST
# ---------------------------------------------------------------------------
log "Step 2/4: Stopping VST..."
if [[ ! -f "${VST_DIR}/deploy.sh" ]]; then
    warn "  deploy.sh not found at ${VST_DIR}/deploy.sh — skipping."
else
    (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" bash deploy.sh down vst 2>/dev/null) \
        && log "  ${_C_GREEN}VST stopped.${_C_RESET}" \
        || warn "  deploy.sh down vst returned non-zero (may not have been running)."
fi

# ---------------------------------------------------------------------------
# Step 3: Stop nvstreamer
# ---------------------------------------------------------------------------
log "Step 3/4: Stopping nvstreamer..."
if [[ ! -f "${VST_DIR}/deploy.sh" ]]; then
    warn "  deploy.sh not found — skipping."
else
    (cd "${VST_DIR}" && COMPOSE_PROJECT_NAME="${VST_COMPOSE_PROJECT}" bash deploy.sh down nvstreamer 2>/dev/null) \
        && log "  ${_C_GREEN}nvstreamer stopped.${_C_RESET}" \
        || warn "  deploy.sh down nvstreamer returned non-zero (may not have been running)."
fi

# ---------------------------------------------------------------------------
# Step 4: Stop sys_cache_cleaner
# ---------------------------------------------------------------------------
log "Step 4/4: Stopping sys_cache_cleaner..."
PID_FILE="/tmp/sys_cache_cleaner.pid"
if [[ ! -f "${PID_FILE}" ]]; then
    log "  No cache cleaner PID file found — skipping."
else
    PID=$(cat "${PID_FILE}" 2>/dev/null || true)
    if [[ -n "${PID}" ]]; then
        sudo kill "${PID}" 2>/dev/null \
            && log "  ${_C_GREEN}Cache cleaner (PID ${PID}) stopped.${_C_RESET}" \
            || warn "  Could not kill PID ${PID} (may have already exited)."
        rm -f "${PID_FILE}"
    else
        warn "  PID file empty — skipping."
        rm -f "${PID_FILE}"
    fi
fi

echo ""
echo -e "${_C_BOLD}============================================================${_C_RESET}"
echo -e "${_C_GREEN}  Teardown complete.${_C_RESET}"
echo -e "${_C_BOLD}============================================================${_C_RESET}"
echo ""
