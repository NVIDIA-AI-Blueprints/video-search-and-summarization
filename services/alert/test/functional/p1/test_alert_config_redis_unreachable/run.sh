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

# Test: Alert Config API works with NO Redis present.
# Description: Redis has been removed from Alert MS. This test (formerly
#              "Redis unreachable latency") now pins the removal's core
#              guarantee: the Alert Bridge starts and the alert-config
#              CRUD API stays responsive and correct with no Redis
#              instance running anywhere. Dedup/filter state is in-process
#              and the config store is backed by Elasticsearch.
#
# Isolation: per-run AB port + alert_type.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
AB_PORT="${AB_PORT:-9088}"
AB_HOST="${AB_HOST:-http://localhost:$AB_PORT}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BASE_CONFIG="${BASE_CONFIG:-$P1_ROOT/shared/config_base.yaml}"

BASE="$AB_HOST/api/v1/verification/config"
RUN_ID="${RUN_ID:-no_redis_$(date +%s)_$$}"
ALERT_TYPE="$RUN_ID"
ES_INDEX="ab-alert_configs"
LATENCY_BUDGET_SEC="${LATENCY_BUDGET_SEC:-5.0}"

mkdir -p "$PID_DIR"
export FASTAPI_PORT="$AB_PORT"

echo "=== P1: Alert Config API works with no Redis ($ALERT_TYPE) ==="

cleanup() {
    local rc=$?
    print_status "info" "Cleaning up $ALERT_TYPE"
    curl -fsS -X DELETE "$BASE/$ALERT_TYPE" >/dev/null 2>&1 || true
    curl -fsS -X DELETE "$ES_HOST/$ES_INDEX/_doc/$ALERT_TYPE?refresh=true" >/dev/null 2>&1 || true
    stop_alert_bridge_local "$PID_DIR" || true
    if [ $rc -ne 0 ]; then
        print_status "info" "Last 60 log lines from test AB:"
        tail -60 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
    fi
    exit $rc
}
trap cleanup EXIT

within_budget() {
    python3 -c "import sys; sys.exit(0 if float('$1') < float('$LATENCY_BUDGET_SEC') else 1)"
}

# ── 0. Prerequisites ─────────────────────────────────────────────────
print_status "wait" "Checking prerequisites"
curl -fsS "$ES_HOST/" >/dev/null || { print_status "fail" "ES unreachable at $ES_HOST"; exit 2; }
# Deliberately do NOT require Redis — the point of this test is that none is needed.

# ── 1. Start AB with no Redis running ───────────────────────────────
print_status "wait" "Starting Alert Bridge on port $AB_PORT (no Redis in the stack)"
stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$BASE_CONFIG" 20
for i in $(seq 1 30); do
    if curl -fsS "$AB_HOST/health" >/dev/null 2>&1; then break; fi
    sleep 1
done
curl -fsS "$AB_HOST/health" >/dev/null || { print_status "fail" "AB never became healthy without Redis"; exit 1; }
print_status "ok" "AB healthy with no Redis present"

# ── 2. POST under latency budget ────────────────────────────────────
print_status "wait" "POST should complete inside ${LATENCY_BUDGET_SEC}s budget"
POST_TIME=$(curl -sS -o "/tmp/${RUN_ID}_post.json" -w "%{time_total}" \
    -X POST "$BASE" -H "Content-Type: application/json" \
    -d "{\"alert_type\":\"$ALERT_TYPE\",\"prompt\":\"no-redis regression\",\"system_prompt\":\"sys\",\"output_category\":\"X\"}")
print_status "info" "POST took ${POST_TIME}s"
within_budget "$POST_TIME" || { print_status "fail" "POST took ${POST_TIME}s, budget ${LATENCY_BUDGET_SEC}s"; exit 1; }
print_status "ok" "POST inside budget"

# ── 3. ES has the doc — write path completed ────────────────────────
ES_CODE=$(curl -fsS -o /dev/null -w "%{http_code}" "$ES_HOST/$ES_INDEX/_doc/$ALERT_TYPE" || echo "0")
if [ "$ES_CODE" != "200" ]; then
    print_status "fail" "ES doc not found (HTTP $ES_CODE) — write path broken"
    exit 1
fi
print_status "ok" "ES has the new doc — write path completed"

# ── 4. GET under latency budget + correct payload ───────────────────
print_status "wait" "GET should complete inside ${LATENCY_BUDGET_SEC}s budget"
GET_TIME=$(curl -sS -o "/tmp/${RUN_ID}_get.json" -w "%{time_total}" "$BASE/$ALERT_TYPE")
print_status "info" "GET took ${GET_TIME}s"
within_budget "$GET_TIME" || { print_status "fail" "GET took ${GET_TIME}s, budget ${LATENCY_BUDGET_SEC}s"; exit 1; }

GOT_PROMPT=$(python3 -c "import json; print(json.load(open('/tmp/${RUN_ID}_get.json'))['prompt'])")
if [ "$GOT_PROMPT" != "no-redis regression" ]; then
    print_status "fail" "GET returned wrong prompt: $GOT_PROMPT"
    exit 1
fi
print_status "ok" "GET payload correct — served from Elasticsearch, no Redis involved"

print_status "ok" "PASS: Alert config API is responsive and correct with no Redis present"
