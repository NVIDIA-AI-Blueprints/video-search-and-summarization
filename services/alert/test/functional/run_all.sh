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

# Run all functional test steps
# Usage: ./run_all.sh [--step N] [--priority p0|p1|all] [--cleanup]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --step N          Run only step N (1-4, P0 only)"
    echo "  --priority LEVEL  p0 (default), p1, or all"
    echo "  --test <name>     Run only named P1 test (with --priority p1)"
    echo "  --cleanup         Run cleanup only"
    echo "  --help            Show this help"
    echo ""
    echo "Steps (P0):"
    echo "  1  Start Kafka and simulators"
    echo "  2  Start Alert Bridge"
    echo "  3  Trigger incident"
    echo "  4  Check results in Elasticsearch"
    echo ""
    echo "Examples:"
    echo "  $0                        # Run P0 steps"
    echo "  $0 --priority p1          # Run P1 functional tests"
    echo "  $0 --priority all         # Run P0 then P1"
    echo "  $0 --priority p1 --test test_redis_dedup"
    echo "  $0 --step 1               # Only start simulators"
    echo "  $0 --cleanup              # Stop all services"
}

run_step() {
    local step=$1
    local script=""
    local desc=""

    case $step in
        1)
            script="$SCRIPT_DIR/step1_start_simulators.sh"
            desc="Start Simulators"
            ;;
        2)
            script="$SCRIPT_DIR/step2_start_alert_bridge.sh"
            desc="Start Alert Bridge"
            ;;
        3)
            script="$SCRIPT_DIR/step3_trigger_incident.sh"
            desc="Trigger Incident"
            ;;
        4)
            script="$SCRIPT_DIR/step4_check_results.sh"
            desc="Check Results"
            ;;
        *)
            echo -e "${RED}Invalid step: $step${NC}"
            exit 1
            ;;
    esac

    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  Step $step: $desc${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    if [ ! -x "$script" ]; then
        chmod +x "$script"
    fi

    "$script"
}

# Parse arguments
STEP=""
PRIORITY="p0"
P1_TEST=""
CLEANUP=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --step)
            STEP="$2"
            shift 2
            ;;
        --priority)
            PRIORITY="$2"
            shift 2
            ;;
        --test)
            P1_TEST="$2"
            shift 2
            ;;
        --cleanup)
            CLEANUP=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Run cleanup only
if [ "$CLEANUP" = true ]; then
    echo -e "${YELLOW}Running cleanup...${NC}"
    "$SCRIPT_DIR/cleanup.sh"
    exit 0
fi

# Run single step (P0 only)
if [ -n "$STEP" ]; then
    run_step "$STEP"
    exit 0
fi

# Validate priority
case "$PRIORITY" in
    p0|p1|all) ;;
    *) echo -e "${RED}Invalid priority: $PRIORITY (use p0, p1, or all)${NC}"; exit 1 ;;
esac

START_TIME=$(date +%s)
EXIT_CODE=0

# --- P0 ---
if [[ "$PRIORITY" == "p0" || "$PRIORITY" == "all" ]]; then
    echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║   Alert Bridge P0 Functional Tests     ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"

    for step in 1 2 3 4; do
        run_step $step
        if [ $step -lt 4 ]; then
            sleep 2
        fi
    done
fi

# --- P1 ---
if [[ "$PRIORITY" == "p1" || "$PRIORITY" == "all" ]]; then
    P1_RUNNER="$SCRIPT_DIR/p1/run_p1.sh"
    if [ ! -f "$P1_RUNNER" ]; then
        echo -e "${RED}P1 test runner not found: $P1_RUNNER${NC}"
        exit 1
    fi
    chmod +x "$P1_RUNNER"

    P1_ARGS=()
    if [ -n "$P1_TEST" ]; then
        P1_ARGS+=(--test "$P1_TEST")
    fi
    # If P0 already ran, simulators are up — skip setup/cleanup in P1
    if [[ "$PRIORITY" == "all" ]]; then
        P1_ARGS+=(--skip-setup --skip-cleanup)
    fi

    "$P1_RUNNER" ${P1_ARGS[@]+"${P1_ARGS[@]}"} || EXIT_CODE=$?
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
if [ "$EXIT_CODE" -eq 0 ]; then
    echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║   All tests completed in ${DURATION}s             ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
else
    echo -e "${RED}╔════════════════════════════════════════╗${NC}"
    echo -e "${RED}║   Some tests FAILED (${DURATION}s)               ║${NC}"
    echo -e "${RED}╚════════════════════════════════════════╝${NC}"
fi
echo ""
echo "To clean up: $SCRIPT_DIR/cleanup.sh"
exit $EXIT_CODE
