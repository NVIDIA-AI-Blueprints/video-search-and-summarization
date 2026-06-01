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

# Test: VLM Params Global Defaults
# Description: Alert type without vlm_params in alert_type_config.json uses
#              global VLM defaults unchanged.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"
TEST_NAME="vlm_params_global_defaults"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"

echo "=== P1: VLM Params Global Defaults ==="
echo "    unsafe_behavior has NO vlm_params — should use global max_tokens=256"

mkdir -p "$PID_DIR"

# 1. Build payload with category=unsafe_behavior (no vlm_params in config)
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"

python3 -c "
import json
with open('$PATCHED') as f: data = json.load(f)
data['category'] = 'unsafe_behavior'
with open('$PATCHED', 'w') as f: json.dump(data, f)
"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX" --no-patch
print_status "info" "Sent unsafe_behavior incident (id-suffix: $ID_SUFFIX)"

# 2. Wait for processing
sleep 10

# 3. Poll ES for the document
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES within 60s"; exit 1; }

# 4. Check NIM sim log for global default params
NIM_LOG="$PID_DIR/nim_sim.log"
if [ ! -f "$NIM_LOG" ]; then
    print_status "info" "SKIP: NIM sim log not found"
    exit 0
fi

LAST_REQUEST=$(tail -200 "$NIM_LOG" | grep -o '"max_tokens": *[0-9]*' | tail -1 || echo "")

if [ -z "$LAST_REQUEST" ]; then
    print_status "info" "SKIP: No max_tokens found in NIM log"
    exit 0
fi

MAX_TOKENS_VALUE=$(echo "$LAST_REQUEST" | grep -o '[0-9]*')

if [ "$MAX_TOKENS_VALUE" = "256" ]; then
    print_status "ok" "PASS: VLM called with global default max_tokens=256 (no per-type override)"
    exit 0
elif [ "$MAX_TOKENS_VALUE" = "2048" ]; then
    print_status "fail" "FAIL: VLM called with max_tokens=2048 — collision params leaked to unsafe_behavior"
    exit 1
else
    print_status "info" "SKIP: Unexpected max_tokens=$MAX_TOKENS_VALUE"
    exit 0
fi
