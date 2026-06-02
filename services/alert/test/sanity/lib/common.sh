#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# =============================================================================
# Alert Bridge Sanity Check - Common Functions
# =============================================================================
# Shared utilities for all sanity checks.
# Supports HTTP-only mode (default) and SSH mode (when SSH_HOST is set).
# =============================================================================

set -eo pipefail

# Determine library directory and source config
COMMON_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SANITY_ROOT_DIR="$(dirname "$COMMON_LIB_DIR")"

# Source configuration (provides defaults for all variables)
if [[ -f "$SANITY_ROOT_DIR/config.env" ]]; then
    source "$SANITY_ROOT_DIR/config.env"
fi

# -----------------------------------------------------------------------------
# Colors and Formatting
# -----------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# -----------------------------------------------------------------------------
# Global State
# -----------------------------------------------------------------------------
SSH_ENABLED="${SSH_ENABLED:-false}"
CHECK_RESULTS=()
SCRIPT_START_TIME=""

# -----------------------------------------------------------------------------
# Logging Functions
# -----------------------------------------------------------------------------
log_info()    { echo -e "${BLUE}ℹ${NC}  $1"; }
log_success() { echo -e "${GREEN}✅${NC} $1"; }
log_fail()    { echo -e "${RED}❌${NC} $1"; }
log_skip()    { echo -e "${YELLOW}⏭️${NC}  $1"; }
log_warn()    { echo -e "${YELLOW}⚠️${NC}  $1"; }
log_debug()   { [[ "${VERBOSE:-false}" == "true" ]] && echo -e "${CYAN}🔍${NC} $1" >&2 || true; }

# -----------------------------------------------------------------------------
# Check Result Tracking
# -----------------------------------------------------------------------------
# Results are written to a temp file for cross-process communication
RESULTS_FILE="${SANITY_RESULTS_FILE:-/tmp/sanity_results_$$}"

pass() {
    local name="$1"
    local detail="${2:-}"
    log_success "${name} - ${detail}"
    echo "PASS|${name}|${detail}" >> "$RESULTS_FILE"
}

fail() {
    local name="$1"
    local detail="${2:-}"
    log_fail "${name} - ${detail}"
    echo "FAIL|${name}|${detail}" >> "$RESULTS_FILE"
    return 1
}

skip_check() {
    local name="$1"
    local reason="${2:-}"
    log_skip "${name} - ${reason}"
    echo "SKIP|${name}|${reason}" >> "$RESULTS_FILE"
}

# -----------------------------------------------------------------------------
# SSH Mode Detection and Helpers
# -----------------------------------------------------------------------------
detect_ssh_mode() {
    if [[ -n "${SSH_HOST:-}" ]]; then
        local ssh_opts="-o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no"
        [[ -n "${SSH_KEY:-}" ]] && ssh_opts="$ssh_opts -i $SSH_KEY"
        
        if ssh $ssh_opts "${SSH_USER:-ubuntu}@${SSH_HOST}" "echo ok" &>/dev/null; then
            SSH_ENABLED=true
            log_info "SSH mode: ${GREEN}ENABLED${NC} (${SSH_USER:-ubuntu}@${SSH_HOST})"
        else
            SSH_ENABLED=false
            log_warn "SSH mode: DISABLED (connection failed to ${SSH_HOST})"
        fi
    else
        SSH_ENABLED=false
        log_info "SSH mode: DISABLED (SSH_HOST not set)"
    fi
    export SSH_ENABLED
}

ssh_exec() {
    local cmd="$1"
    local ssh_opts="-o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no"
    [[ -n "${SSH_KEY:-}" ]] && ssh_opts="$ssh_opts -i $SSH_KEY"
    ssh $ssh_opts "${SSH_USER:-ubuntu}@${SSH_HOST}" "$cmd"
}

require_ssh() {
    if [[ "$SSH_ENABLED" != "true" ]]; then
        return 1
    fi
    return 0
}

require_http() {
    return 0
}

# -----------------------------------------------------------------------------
# Elasticsearch Helpers
# -----------------------------------------------------------------------------
es_url() {
    echo "http://${ES_HOST}:${ES_PORT:-9200}"
}

es_query() {
    local endpoint="$1"
    local data="${2:-}"
    local url="$(es_url)${endpoint}"
    
    log_debug "ES Query: $url"
    
    local result=""
    if [[ -n "$data" ]]; then
        result=$(curl -sf --max-time 30 "$url" -H "Content-Type: application/json" -d "$data" 2>/dev/null) || true
    else
        result=$(curl -sf --max-time 30 "$url" 2>/dev/null) || true
    fi
    
    if [[ -z "$result" ]]; then
        return 1
    fi
    echo "$result"
}

# -----------------------------------------------------------------------------
# Utility Functions
# -----------------------------------------------------------------------------
get_timestamp() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

format_duration() {
    local seconds="$1"
    if (( seconds < 60 )); then
        echo "${seconds}s"
    else
        local mins=$((seconds / 60))
        local secs=$((seconds % 60))
        echo "${mins}m ${secs}s"
    fi
}

# Count results by status
count_results() {
    local status="$1"
    local count=0
    if [[ -f "$RESULTS_FILE" ]]; then
        count=$(grep -c "^${status}|" "$RESULTS_FILE" 2>/dev/null) || count=0
    fi
    echo "$count"
}

# -----------------------------------------------------------------------------
# JSON Output Helpers
# -----------------------------------------------------------------------------
results_to_json() {
    local passed=$(count_results "PASS")
    local failed=$(count_results "FAIL")
    local skipped=$(count_results "SKIP")
    local duration=$(($(date +%s) - SCRIPT_START_TIME))
    
    cat <<EOF
{
  "timestamp": "$(get_timestamp)",
  "target": {
    "es_host": "${ES_HOST}",
    "es_port": "${ES_PORT:-9200}",
    "ssh_host": "${SSH_HOST:-null}",
    "ssh_enabled": ${SSH_ENABLED}
  },
  "summary": {
    "passed": ${passed},
    "failed": ${failed},
    "skipped": ${skipped},
    "total": $((passed + failed)),
    "duration_seconds": ${duration}
  },
  "checks": [
$(print_checks_json)
  ],
  "result": "$([ "$failed" -eq 0 ] && echo "PASS" || echo "FAIL")"
}
EOF
}

print_checks_json() {
    local first=true
    if [[ -f "$RESULTS_FILE" ]]; then
        while IFS='|' read -r status name detail; do
            [[ "$first" == "true" ]] && first=false || echo ","
            echo -n "    {\"status\": \"${status}\", \"name\": \"${name}\", \"detail\": \"${detail}\"}"
        done < "$RESULTS_FILE"
    fi
    echo ""
}
