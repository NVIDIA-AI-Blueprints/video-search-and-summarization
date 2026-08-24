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

# Test: Redis Deduplication
# Description: Verify duplicate incident (same id-suffix) produces only 1 ES document
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"
TEST_NAME="redis_dedup"
# Fixed suffix — both sends must be identical to trigger dedup
ID_SUFFIX="p1_dedup_fixed"

echo "=== P1: Redis Deduplication ==="

mkdir -p "$PID_DIR"

# 1. Record baseline doc count before this test
BEFORE=$(count_es_docs "$ES_HOST")
print_status "info" "Docs in ES before test: $BEFORE"

# 2. Patch timestamps + produce SAME incident TWICE with SAME id-suffix
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"

produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX"
print_status "info" "Sent incident #1 (id-suffix: $ID_SUFFIX)"

produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX"
print_status "info" "Sent incident #2 (same id-suffix: $ID_SUFFIX)"

# 3. Wait for processing
sleep 15

# 4. Count docs added
AFTER=$(count_es_docs "$ES_HOST")
ADDED=$((AFTER - BEFORE))
print_status "info" "Docs added: $ADDED (before=$BEFORE after=$AFTER)"

# 5. Assert only 1 new doc (duplicate was dropped by Redis dedup)
if [ "$ADDED" -eq 1 ]; then
    print_status "ok" "PASS: Duplicate dropped — only 1 document indexed"
    exit 0
elif [ "$ADDED" -eq 0 ]; then
    print_status "fail" "FAIL: No document indexed (both were dropped)"
    exit 1
else
    print_status "fail" "FAIL: Expected 1 doc, got $ADDED — dedup did not fire"
    exit 1
fi
