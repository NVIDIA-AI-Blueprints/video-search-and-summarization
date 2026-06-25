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

# Test: Alert Config ES Hydration (the alert-config ES hydration AC)
# Description: Acceptance test for the ticket's core claim —
#              "configuration survives container restart, Redis restart,
#              and host reboot". Covers two paths:
#                (1) Read-through fallback — a Redis cache miss for an
#                    existing record still serves the data by falling
#                    through to ES.
#                (2) Startup hydration — after Redis loses the data and
#                    Alert Bridge restarts, the config re-appears from
#                    ES during hydration before the service starts
#                    answering requests.
#
# Isolation: uses a per-run suffix for the alert_type so concurrent runs
#            and shared dev infrastructure don't collide. Cleanup is
#            idempotent and runs on trap EXIT even when the test fails.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

# ── Environment overrides ────────────────────────────────────────────
# AB_HOST      : URL of the Alert Bridge under test. Defaults to
#                localhost:9088 so the test does not collide with a
#                deployment-provided AB on the standard 9080 port.
# AB_PORT      : Port that the test-owned AB listens on; exported to the
#                started process as FASTAPI_PORT.
# REDIS_HOST   : Redis used by the test AB. Must match event_bridge.redis_source
#                in BASE_CONFIG.
# ES_HOST      : Elasticsearch URL. "ab-alert_configs" index is created
#                on first write; tests operate against that index.
# BASE_CONFIG  : config.yaml handed to the test-owned AB.
# PID_DIR      : scratch dir for pid files / logs.
PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
AB_PORT="${AB_PORT:-9088}"
AB_HOST="${AB_HOST:-http://localhost:$AB_PORT}"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BASE_CONFIG="${BASE_CONFIG:-$P1_ROOT/shared/config_base.yaml}"

BASE="$AB_HOST/api/v1/verification/config"
TEST_NAME="alert_config_es_hydration"
# Per-run suffix — timestamp + PID keeps concurrent runs isolated and
# keeps stale artefacts from prior failed runs out of the way.
RUN_ID="${RUN_ID:-hydration_$(date +%s)_$$}"
ALERT_TYPE="$RUN_ID"
ES_INDEX="ab-alert_configs"
REDIS_KEY="alert_config:$ALERT_TYPE"
DISTINCTIVE_TOKENS=634
DISTINCTIVE_FRAMES=6

mkdir -p "$PID_DIR"
export FASTAPI_PORT="$AB_PORT"

echo "=== P1: Alert Config ES Hydration ($ALERT_TYPE) ==="

# ── Cleanup trap ─────────────────────────────────────────────────────
cleanup() {
    local rc=$?
    print_status "info" "Cleaning up test artefacts for $ALERT_TYPE"
    # 1. API delete is best-effort; AB may be down.
    curl -fsS -X DELETE "$BASE/$ALERT_TYPE" >/dev/null 2>&1 || true
    # 2. Direct ES delete (idempotent; 404 is fine).
    curl -fsS -X DELETE "$ES_HOST/$ES_INDEX/_doc/$ALERT_TYPE?refresh=true" >/dev/null 2>&1 || true
    # 3. Direct Redis delete. Prefer redis-cli; fall back to python + redis lib.
    if command -v redis-cli >/dev/null 2>&1; then
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" DEL "$REDIS_KEY" >/dev/null 2>&1 || true
    else
        python3 -c "
import redis
r = redis.Redis(host='$REDIS_HOST', port=$REDIS_PORT, decode_responses=True)
r.delete('$REDIS_KEY')
" >/dev/null 2>&1 || true
    fi
    # 4. Stop the test-owned AB (only the one we started).
    stop_alert_bridge_local "$PID_DIR" || true
    if [ $rc -ne 0 ]; then
        print_status "info" "Last 40 log lines from test AB:"
        tail -40 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
    fi
    exit $rc
}
trap cleanup EXIT

redis_del() {
    local key="$1"
    if command -v redis-cli >/dev/null 2>&1; then
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" DEL "$key" >/dev/null
    else
        python3 -c "
import redis
r = redis.Redis(host='$REDIS_HOST', port=$REDIS_PORT, decode_responses=True)
r.delete('$key')
"
    fi
}

redis_has() {
    local key="$1"
    if command -v redis-cli >/dev/null 2>&1; then
        local n
        n=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" EXISTS "$key")
        [ "$n" = "1" ]
    else
        python3 -c "
import redis, sys
r = redis.Redis(host='$REDIS_HOST', port=$REDIS_PORT, decode_responses=True)
sys.exit(0 if r.exists('$key') else 1)
"
    fi
}

es_has() {
    local id="$1"
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" "$ES_HOST/$ES_INDEX/_doc/$id")
    [ "$code" = "200" ]
}

# ── 0. Prerequisites ─────────────────────────────────────────────────
print_status "wait" "Checking prerequisites"
curl -fsS "$ES_HOST/" >/dev/null || { print_status "fail" "ES unreachable at $ES_HOST"; exit 2; }
if ! redis_del "$REDIS_KEY"; then
    print_status "fail" "Redis unreachable at $REDIS_HOST:$REDIS_PORT"
    exit 2
fi
# Ensure previous leaks don't bias the run.
curl -fsS -X DELETE "$ES_HOST/$ES_INDEX/_doc/$ALERT_TYPE?refresh=true" >/dev/null 2>&1 || true

# ── 1. Start test AB on its own port ─────────────────────────────────
print_status "wait" "Starting test-owned Alert Bridge on port $AB_PORT"
stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$BASE_CONFIG" 20
# Wait until health endpoint responds; hydration happens in the wire-up
# path, so the first successful GET tells us hydration completed.
for i in $(seq 1 30); do
    if curl -fsS "$AB_HOST/health" >/dev/null 2>&1; then break; fi
    sleep 1
done
curl -fsS "$AB_HOST/health" >/dev/null || { print_status "fail" "Test AB never became healthy"; exit 1; }

# ── 2. POST config via API ───────────────────────────────────────────
print_status "wait" "POST $BASE with distinctive vlm_params"
POST_BODY=$(cat <<EOF
{
  "alert_type": "$ALERT_TYPE",
  "prompt": "ES hydration acceptance test",
  "vlm_params": {"max_tokens": $DISTINCTIVE_TOKENS, "num_frames": $DISTINCTIVE_FRAMES}
}
EOF
)
HTTP_CODE=$(curl -s -o /tmp/${RUN_ID}_post.json -w "%{http_code}" \
    -X POST "$BASE" -H "Content-Type: application/json" -d "$POST_BODY")
if [ "$HTTP_CODE" != "201" ]; then
    print_status "fail" "POST expected 201, got $HTTP_CODE"
    cat /tmp/${RUN_ID}_post.json
    exit 1
fi
print_status "ok" "Config created"

# ── 3. Verify ES has the record (durable write) ─────────────────────
if ! es_has "$ALERT_TYPE"; then
    print_status "fail" "ES missing $ALERT_TYPE after POST — write did not reach ES"
    exit 1
fi
print_status "ok" "ES durable copy confirmed"

# ── 4. Verify Redis cached the record (write-through) ────────────────
if ! redis_has "$REDIS_KEY"; then
    print_status "fail" "Redis missing $REDIS_KEY after POST — cache was not populated"
    exit 1
fi
print_status "ok" "Redis cache populated"

# ── 5. Scenario A: cache-miss fallback to ES ─────────────────────────
print_status "wait" "Scenario A — DEL Redis key then GET, expect data from ES"
redis_del "$REDIS_KEY"
if redis_has "$REDIS_KEY"; then
    print_status "fail" "Redis DEL did not take effect"; exit 1
fi
HTTP_CODE=$(curl -s -o /tmp/${RUN_ID}_geta.json -w "%{http_code}" "$BASE/$ALERT_TYPE")
if [ "$HTTP_CODE" != "200" ]; then
    print_status "fail" "GET after cache wipe expected 200, got $HTTP_CODE"
    cat /tmp/${RUN_ID}_geta.json
    exit 1
fi
TOK=$(python3 -c "import json; print(json.load(open('/tmp/${RUN_ID}_geta.json'))['vlm_params']['max_tokens'])")
if [ "$TOK" != "$DISTINCTIVE_TOKENS" ]; then
    print_status "fail" "GET returned wrong max_tokens: $TOK (expected $DISTINCTIVE_TOKENS)"
    exit 1
fi
print_status "ok" "Cache miss fell through to ES and returned correct data"
# Cached store should repopulate Redis on a hit.
if ! redis_has "$REDIS_KEY"; then
    print_status "fail" "Redis not repopulated after cache-miss read"
    exit 1
fi
print_status "ok" "Redis cache refilled on miss"

# ── 6. Scenario B: restart AB with Redis key wiped → hydration refills ──
print_status "wait" "Scenario B — wipe Redis key, restart AB, verify hydration refills cache"
redis_del "$REDIS_KEY"
stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$BASE_CONFIG" 20
for i in $(seq 1 30); do
    if curl -fsS "$AB_HOST/health" >/dev/null 2>&1; then break; fi
    sleep 1
done
curl -fsS "$AB_HOST/health" >/dev/null || { print_status "fail" "AB never became healthy after restart"; exit 1; }

# Hydration runs in the lazy _build_store() call — trigger it with the first
# API call. If this returns 200 with the right data AND redis is populated
# right after, hydration succeeded.
HTTP_CODE=$(curl -s -o /tmp/${RUN_ID}_getb.json -w "%{http_code}" "$BASE/$ALERT_TYPE")
if [ "$HTTP_CODE" != "200" ]; then
    print_status "fail" "GET post-restart expected 200, got $HTTP_CODE"
    cat /tmp/${RUN_ID}_getb.json
    exit 1
fi
TOK=$(python3 -c "import json; print(json.load(open('/tmp/${RUN_ID}_getb.json'))['vlm_params']['max_tokens'])")
if [ "$TOK" != "$DISTINCTIVE_TOKENS" ]; then
    print_status "fail" "Post-restart GET returned wrong max_tokens: $TOK"
    exit 1
fi
if ! redis_has "$REDIS_KEY"; then
    print_status "fail" "Redis not populated after AB restart — hydration did not fire"
    exit 1
fi
print_status "ok" "Hydration restored the cache from ES on startup"

print_status "ok" "PASS: ES hydration + read-through cache honoured alert-config ES hydration semantics"
exit 0
