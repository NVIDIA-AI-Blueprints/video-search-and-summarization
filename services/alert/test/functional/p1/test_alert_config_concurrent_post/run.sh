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

# Test: Alert Config concurrent POST atomic create (alert-config ES hydration multi-replica safety)
# Description: When two or more replicas race to create the same alert_type
#              in the same instant, exactly ONE must succeed (201). Every
#              other replica must see a clean 409 ``config_exists`` from
#              ES ``op_type=create`` — never a 500, never a silent
#              overwrite. This is the property that makes "ES is the source
#              of truth" meaningful when the alerts microservice is scaled
#              past one replica (raised by bpisupati on Mar 29 in
#              the alert-config ES hydration).
#
# Isolation: per-run alert_type suffix; cleanup on EXIT trap.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
AB_PORT="${AB_PORT:-9088}"
AB_HOST="${AB_HOST:-http://localhost:$AB_PORT}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"
BASE_CONFIG="${BASE_CONFIG:-$P1_ROOT/shared/config_base.yaml}"

BASE="$AB_HOST/api/v1/verification/config"
RUN_ID="${RUN_ID:-concurrent_post_$(date +%s)_$$}"
ALERT_TYPE="$RUN_ID"
ES_INDEX="ab-alert_configs"
REDIS_KEY="alert_config:$ALERT_TYPE"
NUM_WRITERS=5

mkdir -p "$PID_DIR"
export FASTAPI_PORT="$AB_PORT"

echo "=== P1: Alert Config Concurrent POST ($ALERT_TYPE) ==="

cleanup() {
    local rc=$?
    print_status "info" "Cleaning up $ALERT_TYPE"
    curl -fsS -X DELETE "$BASE/$ALERT_TYPE" >/dev/null 2>&1 || true
    curl -fsS -X DELETE "$ES_HOST/$ES_INDEX/_doc/$ALERT_TYPE?refresh=true" >/dev/null 2>&1 || true
    if command -v redis-cli >/dev/null 2>&1; then
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" DEL "$REDIS_KEY" >/dev/null 2>&1 || true
    fi
    stop_alert_bridge_local "$PID_DIR" || true
    if [ $rc -ne 0 ]; then
        print_status "info" "Last 40 log lines from test AB:"
        tail -40 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
    fi
    exit $rc
}
trap cleanup EXIT

# ── 0. Prerequisites ─────────────────────────────────────────────────
print_status "wait" "Checking prerequisites"
curl -fsS "$ES_HOST/" >/dev/null || { print_status "fail" "ES unreachable at $ES_HOST"; exit 2; }

# ── 1. Start test AB on its own port ─────────────────────────────────
print_status "wait" "Starting test-owned Alert Bridge on port $AB_PORT"
stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$BASE_CONFIG" 20
for i in $(seq 1 30); do
    if curl -fsS "$AB_HOST/health" >/dev/null 2>&1; then break; fi
    sleep 1
done
curl -fsS "$AB_HOST/health" >/dev/null || { print_status "fail" "AB never became healthy"; exit 1; }
print_status "ok" "AB healthy on $AB_HOST"

# ── 2. Launch N parallel POSTs same alert_type ──────────────────────
print_status "wait" "Firing $NUM_WRITERS concurrent POSTs for the same alert_type"
PIDS=()
for i in $(seq 1 $NUM_WRITERS); do
    (
        # No ``-f`` here: with ``-f`` curl exits before writing
        # ``%{http_code}``, so the 4 expected 409 conflicts would all
        # show up as 0 and the assertion below would treat them as
        # 5xx leaks.
        curl -sS -X POST "$BASE" \
            -H "Content-Type: application/json" \
            -d "{\"alert_type\":\"$ALERT_TYPE\",\"prompt\":\"writer_$i\",\"system_prompt\":\"sys\",\"output_category\":\"Cat$i\"}" \
            -o "/tmp/${RUN_ID}_w$i.json" \
            -w "%{http_code}\n" >> "/tmp/${RUN_ID}_codes.txt" 2>/dev/null
    ) &
    PIDS+=($!)
done
for pid in "${PIDS[@]}"; do wait "$pid" || true; done

# ── 3. Tally outcomes ────────────────────────────────────────────────
WIN_COUNT=$(grep -c '^201$' "/tmp/${RUN_ID}_codes.txt" || true)
CONFLICT_COUNT=$(grep -c '^409$' "/tmp/${RUN_ID}_codes.txt" || true)
OTHER_COUNT=$(grep -cvE '^(201|409)$' "/tmp/${RUN_ID}_codes.txt" || true)

print_status "info" "201 winners: $WIN_COUNT, 409 conflicts: $CONFLICT_COUNT, other: $OTHER_COUNT"

if [ "$WIN_COUNT" != "1" ]; then
    print_status "fail" "Expected exactly 1 winner (201), got $WIN_COUNT"
    cat "/tmp/${RUN_ID}_codes.txt"
    exit 1
fi
EXPECTED_CONFLICTS=$((NUM_WRITERS - 1))
if [ "$CONFLICT_COUNT" != "$EXPECTED_CONFLICTS" ]; then
    print_status "fail" "Expected $EXPECTED_CONFLICTS conflicts (409), got $CONFLICT_COUNT"
    cat "/tmp/${RUN_ID}_codes.txt"
    exit 1
fi
if [ "$OTHER_COUNT" != "0" ]; then
    print_status "fail" "$OTHER_COUNT writers got non-201/non-409 responses (5xx leak?)"
    cat "/tmp/${RUN_ID}_codes.txt"
    exit 1
fi
print_status "ok" "Atomic create: 1 winner + $EXPECTED_CONFLICTS×409, no 5xx leak"

# ── 4. Verify the surviving doc is one of the writer's payloads ──────
print_status "wait" "Verifying ES doc matches the winner's payload"
ES_PROMPT=$(curl -fsS "$ES_HOST/$ES_INDEX/_doc/$ALERT_TYPE" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['_source']['prompt'])")
case "$ES_PROMPT" in
    writer_1|writer_2|writer_3|writer_4|writer_5) ;;
    *)
        print_status "fail" "ES prompt is unexpected: $ES_PROMPT"
        exit 1
        ;;
esac
print_status "ok" "ES record matches a writer's payload: $ES_PROMPT"

# ── 5. ES version 1 — proves no double-write under the hood ──────────
ES_VERSION=$(curl -fsS "$ES_HOST/$ES_INDEX/_doc/$ALERT_TYPE" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['_version'])")
if [ "$ES_VERSION" != "1" ]; then
    print_status "fail" "ES _version=$ES_VERSION (expected 1; means a writer overwrote the winner)"
    exit 1
fi
print_status "ok" "ES _version=1 — no silent overwrite by a 409 writer"

print_status "ok" "Test passed"
