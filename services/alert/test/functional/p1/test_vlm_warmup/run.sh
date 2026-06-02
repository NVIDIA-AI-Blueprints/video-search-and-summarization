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

# Test: VLM Warmup
# Description: Verify VLM warmup log message appears before the anomaly processing
#              loop log message, confirming correct startup sequencing.
#              Log-based test — no incidents are produced.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
AB_LOG="$PID_DIR/alert_bridge.log"
TEST_NAME="vlm_warmup"

echo "=== P1: VLM Warmup ==="

# 1. Verify the AB log file exists
if [ ! -f "$AB_LOG" ]; then
    print_status "info" "SKIP: AB log not found at $AB_LOG — cannot inspect startup sequence"
    exit 0
fi

# 2. Find line numbers for the warmup marker and the processing loop marker
#    Warmup marker: "Starting VLM warmup" (from vlm/warmup.py)
#    Processing loop marker: "Starting anomaly processing loop" (from enhance_alert_with_vlm.py)
WARMUP_LINE=$(python3 -c "
import sys
marker = 'Starting VLM warmup'
with open('$AB_LOG') as f:
    for i, line in enumerate(f, 1):
        if marker in line:
            print(i)
            sys.exit(0)
" 2>/dev/null || echo "")

LOOP_LINE=$(python3 -c "
import sys
marker = 'Starting anomaly processing loop'
with open('$AB_LOG') as f:
    for i, line in enumerate(f, 1):
        if marker in line:
            print(i)
            sys.exit(0)
" 2>/dev/null || echo "")

print_status "info" "Warmup marker line: ${WARMUP_LINE:-not found}"
print_status "info" "Processing loop marker line: ${LOOP_LINE:-not found}"

# 3. If warmup marker absent, skip (warmup may be disabled via VLM_WARMUP_ENABLED=false
#    or the warmup video was not found)
if [ -z "$WARMUP_LINE" ]; then
    print_status "info" "SKIP: 'Starting VLM warmup' not found in AB log — warmup may be disabled or video missing"
    exit 0
fi

# 4. If processing loop marker absent, skip (AB may not have reached that point yet)
if [ -z "$LOOP_LINE" ]; then
    print_status "info" "SKIP: 'Starting anomaly processing loop' not found in AB log — AB may not have started processing yet"
    exit 0
fi

# 5. Assert order: warmup line must precede the processing loop line
if [ "$WARMUP_LINE" -lt "$LOOP_LINE" ]; then
    print_status "ok" "PASS: VLM warmup (line $WARMUP_LINE) logged before anomaly processing loop (line $LOOP_LINE)"
    exit 0
else
    print_status "fail" "FAIL: Anomaly processing loop (line $LOOP_LINE) started before VLM warmup (line $WARMUP_LINE) — wrong startup order"
    exit 1
fi
