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
# Alert Bridge Sanity Check - Master Orchestrator
# =============================================================================
#
# Usage:
#   ES_HOST=localhost ./run_sanity.sh              # HTTP-only mode
#   ES_HOST=localhost SSH_HOST=localhost ./run_sanity.sh  # Full mode
#   ES_HOST=localhost ./run_sanity.sh --json       # JSON output
#   ES_HOST=localhost ./run_sanity.sh --verbose    # Verbose mode
#
# Exit Codes:
#   0 - All checks passed (skips are OK)
#   1 - One or more checks failed
#   2 - Configuration error
#
# =============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source common functions
source "$SCRIPT_DIR/lib/common.sh"

# Source configuration
[[ -f "$SCRIPT_DIR/config.env" ]] && source "$SCRIPT_DIR/config.env"

# -----------------------------------------------------------------------------
# Argument Parsing
# -----------------------------------------------------------------------------
JSON_OUTPUT=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --json)
            JSON_OUTPUT=true
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            export VERBOSE
            shift
            ;;
        --help|-h)
            cat <<EOF
Alert Bridge Sanity Check

Usage: $0 [OPTIONS]

Options:
  --json       Output results in JSON format
  --verbose    Enable verbose/debug output
  --help       Show this help message

Environment Variables:
  ES_HOST      Elasticsearch host (required)
  ES_PORT      Elasticsearch port (default: 9200)
  SSH_HOST     SSH host for container checks (optional)
  SSH_USER     SSH user (default: ubuntu)
  SSH_KEY      Path to SSH private key (optional)

Examples:
  ES_HOST=localhost $0
  ES_HOST=localhost ES_PORT=30920 $0 --verbose
  ES_HOST=localhost SSH_HOST=localhost $0 --json
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 2
            ;;
    esac
done

# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------
if [[ -z "${ES_HOST:-}" ]]; then
    echo "ERROR: ES_HOST environment variable is required"
    echo "Usage: ES_HOST=<elasticsearch-host> $0"
    exit 2
fi

# Record start time
SCRIPT_START_TIME=$(date +%s)

# Initialize results file for cross-process communication
export SANITY_RESULTS_FILE="/tmp/sanity_results_$$"
rm -f "$SANITY_RESULTS_FILE"
touch "$SANITY_RESULTS_FILE"

# Cleanup on exit
cleanup() {
    rm -f "$SANITY_RESULTS_FILE"
}
trap cleanup EXIT

# -----------------------------------------------------------------------------
# Header
# -----------------------------------------------------------------------------
if [[ "$JSON_OUTPUT" != "true" ]]; then
    echo ""
    echo "╔════════════════════════════════════════════════════════════════════╗"
    echo "║                    ALERT BRIDGE SANITY CHECK                       ║"
    echo "╠════════════════════════════════════════════════════════════════════╣"
    printf "║  %-66s ║\n" "Target: ${ES_HOST}:${ES_PORT:-9200}"
    printf "║  %-66s ║\n" "Time:   $(date '+%Y-%m-%d %H:%M:%S')"
    echo "╚════════════════════════════════════════════════════════════════════╝"
    echo ""
fi

# -----------------------------------------------------------------------------
# SSH Mode Detection
# -----------------------------------------------------------------------------
detect_ssh_mode

if [[ "$JSON_OUTPUT" != "true" ]]; then
    echo ""
    echo "────────────────────────────────────────────────────────────────────"
    echo "Running Checks..."
    echo "────────────────────────────────────────────────────────────────────"
fi

# -----------------------------------------------------------------------------
# Execute Checks
# -----------------------------------------------------------------------------
FAILED=0

for check in "$SCRIPT_DIR/checks"/*.sh; do
    [[ -f "$check" ]] || continue
    [[ -x "$check" ]] || chmod +x "$check"
    
    if [[ "$VERBOSE" == "true" ]]; then
        log_debug "Running: $(basename "$check")"
    fi
    
    # Run check and capture exit code
    bash "$check" || FAILED=1
done

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
passed=$(count_results "PASS")
failed=$(count_results "FAIL")
skipped=$(count_results "SKIP")
duration=$(($(date +%s) - SCRIPT_START_TIME))

if [[ "$JSON_OUTPUT" == "true" ]]; then
    results_to_json
else
    echo ""
    echo "════════════════════════════════════════════════════════════════════"
    echo "                           SUMMARY"
    echo "════════════════════════════════════════════════════════════════════"
    echo ""
    echo "  ✅ Passed:  $passed"
    echo "  ❌ Failed:  $failed"
    echo "  ⏭️  Skipped: $skipped"
    echo "  ⏱️  Duration: $(format_duration $duration)"
    echo ""
    
    if [[ "$failed" -eq 0 ]]; then
        echo -e "  🎉 ${GREEN}ALL CHECKS PASSED${NC}"
    else
        echo -e "  ⚠️  ${RED}$failed CHECK(S) FAILED${NC}"
    fi
    echo ""
    echo "════════════════════════════════════════════════════════════════════"
fi

# Exit with appropriate code
[[ "$failed" -eq 0 ]] && exit 0 || exit 1
