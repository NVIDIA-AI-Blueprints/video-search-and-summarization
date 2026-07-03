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

# Test: Alert Config ES Durability + Restart Hydration
# Description: Acceptance test for "configuration survives container
#              restart and host reboot". Redis has been removed — the
#              alert-config cache is now an in-process store and
#              Elasticsearch is the durable source of truth. Covers:
#                (1) Durable write — a POST lands in ES.
#                (2) Read-through — a GET returns the record (served from
#                    ES; the default in-process cache is read-through).
#                (3) Restart hydration — after the Alert Bridge restarts
#                    (in-process cache is empty), the config re-appears
#                    from ES during hydration before the service starts
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
# AB_PORT      : Port that the test-owned AB listens on; exported as FASTAPI_PORT.
# ES_HOST      : Elasticsearch URL. "ab-alert_configs" index is created
#                on first write; tests operate against that index.
# BASE_CONFIG  : config.yaml handed to the test-owned AB.
# PID_DIR      : scratch dir for pid files / logs.
PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
AB_PORT="${AB_PORT:-9088}"
AB_HOST="${AB_HOST:-http://localhost:$AB_PORT}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BASE_CONFIG="${BASE_CONFIG:-$P1_ROOT/shared/config_base.yaml}"

BASE="$AB_HOST/api/v1/verification/config"
TEST_NAME="alert_config_es_hydration"
RUN_ID="${RUN_ID:-hydration_$(date +%s)_$$}"
ALERT_TYPE="$RUN_ID"
ES_INDEX="ab-alert_configs"
DISTINCTIVE_TOKENS=634
DISTINCTIVE_FRAMES=6

mkdir -p "$PID_DIR"
export FASTAPI_PORT="$AB_PORT"

echo "=== P1: Alert Config ES Durability + Restart Hydration ($ALERT_TYPE) ==="

# ── Cleanup trap ─────────────────────────────────────────────────────
cleanup() {
    local rc=$?
    print_status "info" "Cleaning up test artefacts for $ALERT_TYPE"
    curl -fsS -X DELETE "$BASE/$ALERT_TYPE" >/dev/null 2>&1 || true
    curl -fsS -X DELETE "$ES_HOST/$ES_INDEX/_doc/$ALERT_TYPE?refresh=true" >/dev/null 2>&1 || true
    stop_alert_bridge_local "$PID_DIR" || true
    if [ $rc -ne 0 ]; then
        print_status "info" "Last 40 log lines from test AB:"
        tail -40 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
    fi
    exit $rc
}
trap cleanup EXIT

es_has() {
    local id="$1"
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" "$ES_HOST/$ES_INDEX/_doc/$id")
    [ "$code" = "200" ]
}

get_tokens() {
    python3 -c "import json; print(json.load(open('$1'))['vlm_params']['max_tokens'])"
}

# ── 0. Prerequisites ─────────────────────────────────────────────────
print_status "wait" "Checking prerequisites"
curl -fsS "$ES_HOST/" >/dev/null || { print_status "fail" "ES unreachable at $ES_HOST"; exit 2; }
# Ensure previous leaks don't bias the run.
curl -fsS -X DELETE "$ES_HOST/$ES_INDEX/_doc/$ALERT_TYPE?refresh=true" >/dev/null 2>&1 || true

# ── 1. Start test AB on its own port ─────────────────────────────────
print_status "wait" "Starting test-owned Alert Bridge on port $AB_PORT"
stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$BASE_CONFIG" 20
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

# ── 4. Scenario A: read-through GET returns correct data ─────────────
print_status "wait" "Scenario A — GET returns data served from ES"
HTTP_CODE=$(curl -s -o /tmp/${RUN_ID}_geta.json -w "%{http_code}" "$BASE/$ALERT_TYPE")
if [ "$HTTP_CODE" != "200" ]; then
    print_status "fail" "GET expected 200, got $HTTP_CODE"
    cat /tmp/${RUN_ID}_geta.json
    exit 1
fi
TOK=$(get_tokens "/tmp/${RUN_ID}_geta.json")
if [ "$TOK" != "$DISTINCTIVE_TOKENS" ]; then
    print_status "fail" "GET returned wrong max_tokens: $TOK (expected $DISTINCTIVE_TOKENS)"
    exit 1
fi
print_status "ok" "GET returned correct data from ES"

# ── 5. Scenario B: restart AB → hydration refills from ES ───────────
print_status "wait" "Scenario B — restart AB (in-process cache empty), verify hydration from ES"
stop_alert_bridge_local "$PID_DIR"
start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$BASE_CONFIG" 20
for i in $(seq 1 30); do
    if curl -fsS "$AB_HOST/health" >/dev/null 2>&1; then break; fi
    sleep 1
done
curl -fsS "$AB_HOST/health" >/dev/null || { print_status "fail" "AB never became healthy after restart"; exit 1; }

HTTP_CODE=$(curl -s -o /tmp/${RUN_ID}_getb.json -w "%{http_code}" "$BASE/$ALERT_TYPE")
if [ "$HTTP_CODE" != "200" ]; then
    print_status "fail" "GET post-restart expected 200, got $HTTP_CODE"
    cat /tmp/${RUN_ID}_getb.json
    exit 1
fi
TOK=$(get_tokens "/tmp/${RUN_ID}_getb.json")
if [ "$TOK" != "$DISTINCTIVE_TOKENS" ]; then
    print_status "fail" "Post-restart GET returned wrong max_tokens: $TOK"
    exit 1
fi
print_status "ok" "Config survived restart — hydrated from ES"

print_status "ok" "PASS: ES durability + restart hydration honoured (no Redis)"
exit 0
