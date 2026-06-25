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

# Test: Alert Config API stays responsive when Redis goes unreachable
#       AT RUNTIME (regression for the Redis-client retry-storm fix).
# Description: Before the fix in ``its_redis/redis_handler.py`` /
#              ``dynamic_prompt_handler.py``, ``redis-py`` was
#              constructed without ``socket_connect_timeout`` /
#              ``socket_timeout`` and with the default ``Retry`` policy.
#              A measurement against a refused Redis port turned a
#              single ``json().set`` into ~2.6s and 5 sequential calls
#              into ~42s — the alert-config CRUD hot path went above
#              curl's default 15-second timeout.
#
#              We need a Redis instance the test can stop *after* AB
#              has started healthy (PromptManager hard-requires Redis
#              at boot), so the test brings up a dedicated Redis
#              container on a free port instead of disturbing the
#              shared ``mdx-redis``. Other services on the host are
#              unaffected.
#
# Isolation: per-run Redis container name + AB port + alert_type.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
AB_PORT="${AB_PORT:-9088}"
AB_HOST="${AB_HOST:-http://localhost:$AB_PORT}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
TEST_REDIS_PORT="${TEST_REDIS_PORT:-17000}"
REDIS_IMAGE="${REDIS_IMAGE:-redis/redis-stack-server:latest}"
BASE_CONFIG_TEMPLATE="${BASE_CONFIG:-$P1_ROOT/shared/config_base.yaml}"
TEST_CONFIG="$PID_DIR/redis_unreachable_config.yaml"

BASE="$AB_HOST/api/v1/verification/config"
RUN_ID="${RUN_ID:-redis_unreach_$(date +%s)_$$}"
ALERT_TYPE="$RUN_ID"
ES_INDEX="ab-alert_configs"
TEST_REDIS_NAME="ab-test-redis-$$"
LATENCY_BUDGET_SEC="${LATENCY_BUDGET_SEC:-5.0}"

mkdir -p "$PID_DIR"
export FASTAPI_PORT="$AB_PORT"

echo "=== P1: Alert Config Redis unreachable latency ($ALERT_TYPE) ==="

cleanup() {
    local rc=$?
    print_status "info" "Cleaning up $ALERT_TYPE"
    curl -fsS -X DELETE "$ES_HOST/$ES_INDEX/_doc/$ALERT_TYPE?refresh=true" >/dev/null 2>&1 || true
    stop_alert_bridge_local "$PID_DIR" || true
    docker rm -f "$TEST_REDIS_NAME" >/dev/null 2>&1 || true
    rm -f "$TEST_CONFIG"
    if [ $rc -ne 0 ]; then
        print_status "info" "Last 60 log lines from test AB:"
        tail -60 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
    fi
    exit $rc
}
trap cleanup EXIT

# ── 0. Prerequisites ─────────────────────────────────────────────────
print_status "wait" "Checking prerequisites"
curl -fsS "$ES_HOST/" >/dev/null || { print_status "fail" "ES unreachable at $ES_HOST"; exit 2; }
command -v docker >/dev/null 2>&1 || { print_status "fail" "docker required"; exit 2; }

# ── 1. Spin up dedicated test Redis on a free port ──────────────────
print_status "wait" "Starting dedicated test Redis on port $TEST_REDIS_PORT (image $REDIS_IMAGE)"
docker run -d --rm \
    --name "$TEST_REDIS_NAME" \
    -p "$TEST_REDIS_PORT:6379" \
    "$REDIS_IMAGE" >/dev/null
for i in $(seq 1 20); do
    if (echo > /dev/tcp/127.0.0.1/$TEST_REDIS_PORT) 2>/dev/null; then break; fi
    sleep 1
done
(echo > /dev/tcp/127.0.0.1/$TEST_REDIS_PORT) 2>/dev/null \
    || { print_status "fail" "Test Redis never came up"; exit 1; }
print_status "ok" "Test Redis running on port $TEST_REDIS_PORT"

# ── 2. Generate config pointing AB at the test Redis ────────────────
print_status "wait" "Generating test config with redis_source.port=$TEST_REDIS_PORT"
python3 - <<EOF
import yaml
with open("$BASE_CONFIG_TEMPLATE") as f:
    cfg = yaml.safe_load(f)
eb = cfg.setdefault("event_bridge", {})
src = eb.setdefault("redis_source", {})
src["host"] = "127.0.0.1"
src["port"] = int("$TEST_REDIS_PORT")
sink = eb.setdefault("redis_sink", {})
sink["host"] = "127.0.0.1"
sink["port"] = int("$TEST_REDIS_PORT")
with open("$TEST_CONFIG", "w") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)
EOF
print_status "ok" "Test config written"

# ── 3. Start test AB pointed at the test Redis ──────────────────────
print_status "wait" "Starting test-owned Alert Bridge on port $AB_PORT"
stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$TEST_CONFIG" 20
for i in $(seq 1 30); do
    if curl -fsS "$AB_HOST/health" >/dev/null 2>&1; then break; fi
    sleep 1
done
curl -fsS "$AB_HOST/health" >/dev/null || { print_status "fail" "AB never became healthy"; exit 1; }
print_status "ok" "AB healthy"

# ── 4. Seed a config so we have something to GET later ──────────────
curl -fsS -X POST "$BASE" \
    -H "Content-Type: application/json" \
    -d "{\"alert_type\":\"$ALERT_TYPE\",\"prompt\":\"redis-down regression\",\"system_prompt\":\"sys\",\"output_category\":\"X\"}" \
    -o /dev/null
print_status "ok" "Seeded config $ALERT_TYPE while Redis still healthy"

# ── 5. Kill the test Redis to inject the failure ────────────────────
print_status "wait" "Stopping test Redis to simulate runtime outage"
docker stop "$TEST_REDIS_NAME" >/dev/null
# Wait until the port really stops accepting; otherwise we'd race the
# kernel and the first call would still succeed by luck.
for i in $(seq 1 10); do
    if ! (echo > /dev/tcp/127.0.0.1/$TEST_REDIS_PORT) 2>/dev/null; then break; fi
    sleep 1
done
if (echo > /dev/tcp/127.0.0.1/$TEST_REDIS_PORT) 2>/dev/null; then
    print_status "fail" "Test Redis still accepting connections after stop"
    exit 1
fi
print_status "ok" "Test Redis stopped"

# ── 6. POST under latency budget ────────────────────────────────────
# Use a different alert_type so we exercise the create path (not the
# 409 existing-doc path). Timing is what matters.
print_status "wait" "POST should complete inside ${LATENCY_BUDGET_SEC}s budget"
POST_TIME=$(curl -sS -o "/tmp/${RUN_ID}_post.json" -w "%{time_total}" \
    -X POST "$BASE" \
    -H "Content-Type: application/json" \
    -d "{\"alert_type\":\"${ALERT_TYPE}_b\",\"prompt\":\"second create\",\"system_prompt\":\"sys\",\"output_category\":\"X\"}")
print_status "info" "POST took ${POST_TIME}s"
python3 -c "
import sys
t = float('$POST_TIME')
budget = float('$LATENCY_BUDGET_SEC')
sys.exit(0 if t < budget else 1)
" || { print_status "fail" "POST took ${POST_TIME}s, budget ${LATENCY_BUDGET_SEC}s — Redis retry storm is back"; exit 1; }
print_status "ok" "POST inside budget"

# ── 7. ES has the new doc — write path actually completed ───────────
ES_CODE=$(curl -fsS -o /dev/null -w "%{http_code}" "$ES_HOST/$ES_INDEX/_doc/${ALERT_TYPE}_b" || echo "0")
if [ "$ES_CODE" != "200" ]; then
    print_status "fail" "ES doc not found (HTTP $ES_CODE) — write path broken, not just slow"
    exit 1
fi
print_status "ok" "ES has the new doc — write path completed under budget"

# ── 8. GET under latency budget ──────────────────────────────────────
print_status "wait" "GET should complete inside ${LATENCY_BUDGET_SEC}s budget"
GET_TIME=$(curl -sS -o "/tmp/${RUN_ID}_get.json" -w "%{time_total}" "$BASE/$ALERT_TYPE")
print_status "info" "GET took ${GET_TIME}s"
python3 -c "
import sys
t = float('$GET_TIME')
budget = float('$LATENCY_BUDGET_SEC')
sys.exit(0 if t < budget else 1)
" || { print_status "fail" "GET took ${GET_TIME}s, budget ${LATENCY_BUDGET_SEC}s"; exit 1; }
print_status "ok" "GET inside budget"

GOT_PROMPT=$(python3 -c "import json; print(json.load(open('/tmp/${RUN_ID}_get.json'))['prompt'])")
if [ "$GOT_PROMPT" != "redis-down regression" ]; then
    print_status "fail" "GET returned wrong prompt: $GOT_PROMPT"
    exit 1
fi
print_status "ok" "GET payload correct — read fell through Redis to ES (or memory snapshot)"

# ── 9. Cleanup new doc, declare success ──────────────────────────────
curl -fsS -X DELETE "$ES_HOST/$ES_INDEX/_doc/${ALERT_TYPE}_b?refresh=true" >/dev/null 2>&1 || true
print_status "ok" "Test passed — Redis blip does not stall API"
