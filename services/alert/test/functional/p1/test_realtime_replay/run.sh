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

# Test: Realtime Replay API
# Description: End-to-end coverage for POST /api/v1/realtime/replay
#   Sub-test 1: Happy-path replay (2 rules re-onboarded)
#   Sub-test 2: Partial RTVI failure — ES record unchanged
#   Sub-test 3: Concurrent replay returns 409
#   Sub-test 4: POST during replay returns 503
#   Sub-test 5: DELETE during replay returns 503
#   Sub-test 6: GET stays available during replay
#   Sub-test 7: Feature-flag off (persistence disabled) — replay 501, CRUD works
#   Sub-test 8: AB restart preserves state
#   Sub-test 9: Realtime-specific flag off — global persistence
#               enabled but `rtvi_vlm.enable_realtime_persistence: false` →
#               replay 501, ES rules index stays empty, CRUD works in-memory
#   Sub-test 10: replay correlation_id round-trip on 200 / 501 / 409 +
#                Prometheus metrics scrape on the production scrape port
#                with delta-based assertions (multiprocess wiring lives
#                in metrics/__init__.py + enhance_alert_with_vlm.py)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

AB_HOST="${AB_HOST:-http://localhost:9080}"
RTVI_SIM_PORT="${RTVI_SIM_PORT:-8018}"
RTVI_SIM_HOST="http://localhost:${RTVI_SIM_PORT}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
ES_RULES_INDEX="ab-alert-realtime-rules"
PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
RTSP_URL="rtsp://localhost:31554/stream1"
FAILURES=0

echo "=== P1: Realtime Replay API ==="
echo "Target: $AB_HOST"
echo ""

# ── RTVI sim lifecycle ────────────────────────────────────────────────
RTVI_SIM_PID=""
if ! curl -sf "${RTVI_SIM_HOST}/v1/ready" >/dev/null 2>&1; then
    print_status "wait" "Starting RTVI VLM mock on port $RTVI_SIM_PORT..."
    RTVI_SIM_PORT=$RTVI_SIM_PORT python3 "$REPO_ROOT/test/sim_scripts/rtvi/rtvi_vlm_sim.py" \
        > "${PID_DIR}/rtvi_sim_replay.log" 2>&1 &
    RTVI_SIM_PID=$!
    for i in $(seq 1 10); do
        if curl -sf "${RTVI_SIM_HOST}/v1/ready" >/dev/null 2>&1; then
            print_status "ok" "RTVI VLM mock ready (PID=$RTVI_SIM_PID)"
            break
        fi
        sleep 1
    done
fi

cleanup_rtvi() {
    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/fault" >/dev/null 2>&1 || true
    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/delay" >/dev/null 2>&1 || true
    if [ -n "$RTVI_SIM_PID" ]; then
        kill "$RTVI_SIM_PID" 2>/dev/null || true
    fi
}
trap cleanup_rtvi EXIT

# ── Helpers ───────────────────────────────────────────────────────────
flush_rules_index() {
    curl -s -X DELETE "${ES_HOST}/${ES_RULES_INDEX}" >/dev/null 2>&1 || true
}

clear_rtvi() {
    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/calls" >/dev/null 2>&1
    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/fault" >/dev/null 2>&1
    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/delay" >/dev/null 2>&1
}

do_request() {
    local method="$1" path="$2" data="${3:-}"
    local url="$AB_HOST$path"
    if [ -n "$data" ]; then
        curl -s -X "$method" "$url" -H "Content-Type: application/json" -d "$data" 2>/dev/null || echo '{"status":"curl_error"}'
    else
        curl -s -X "$method" "$url" 2>/dev/null || echo '{"status":"curl_error"}'
    fi
}

create_rule() {
    local alert_type="${1:-collision}" sensor_id="${2:-sensor-replay-$(date +%s%N)}"
    do_request "POST" "/api/v1/realtime" "{
        \"liveStreamUrl\": \"$RTSP_URL\",
        \"sensor_id\": \"$sensor_id\",
        \"prompt\": \"Replay test rule\",
        \"alert_type\": \"$alert_type\",
        \"description\": \"replay test\"
    }"
}

get_json() { python3 -c "import sys,json; print(json.load(sys.stdin).get('$1',''))" 2>/dev/null; }

rtvi_call_count() {
    local method="$1" path="$2"
    curl -s "${RTVI_SIM_HOST}/v1/calls?method=${method}&path=${path}" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0"
}

# Flush rules index before starting
flush_rules_index

# ===========================================================================
# Sub-test 1: Happy-path replay
# ===========================================================================
subtest_happy_path() {
    print_status "info" "Sub-test 1: Happy-path replay"
    clear_rtvi
    flush_rules_index

    local r1 r2 id1 id2
    r1=$(create_rule "fire" "sensor-fire-1")
    id1=$(echo "$r1" | get_json id)
    r2=$(create_rule "collision" "sensor-collision-1")
    id2=$(echo "$r2" | get_json id)

    if [ -z "$id1" ] || [ -z "$id2" ]; then
        print_status "fail" "Sub-test 1 FAIL: Could not create 2 rules (id1=$id1, id2=$id2)"
        return 1
    fi

    clear_rtvi
    local resp
    resp=$(do_request "POST" "/api/v1/realtime/replay")
    local replayed failed
    replayed=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('replayed',0))" 2>/dev/null)
    failed=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('failed',0))" 2>/dev/null)

    if [ "$replayed" != "2" ] || [ "$failed" != "0" ]; then
        print_status "fail" "Sub-test 1 FAIL: Expected replayed=2 failed=0, got replayed=$replayed failed=$failed"
        echo "  Response: $resp"
        return 1
    fi

    local sa_count gc_count
    sa_count=$(rtvi_call_count "POST" "streams/add")
    gc_count=$(rtvi_call_count "POST" "generate_captions")

    if [ "$sa_count" -lt 2 ] || [ "$gc_count" -lt 2 ]; then
        print_status "fail" "Sub-test 1 FAIL: RTVI calls: streams/add=$sa_count generate_captions=$gc_count (expected >=2 each)"
        return 1
    fi

    print_status "ok" "Sub-test 1 PASS: replay replayed=2, RTVI received streams/add=$sa_count generate_captions=$gc_count"
}

# ===========================================================================
# Sub-test 2: Partial RTVI failure — ES record unchanged
# ===========================================================================
subtest_partial_failure() {
    print_status "info" "Sub-test 2: Partial RTVI failure — ES record retained"
    clear_rtvi
    flush_rules_index

    local r1 id1
    r1=$(create_rule "collision" "sensor-fail-1")
    id1=$(echo "$r1" | get_json id)

    if [ -z "$id1" ]; then
        print_status "fail" "Sub-test 2 FAIL: Could not create rule"
        return 1
    fi

    local old_stream_id
    old_stream_id=$(curl -s "${ES_HOST}/${ES_RULES_INDEX}/_doc/${id1}" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('_source',{}).get('rtvi_stream_id',''))" 2>/dev/null)

    curl -s -X PUT "${RTVI_SIM_HOST}/v1/fault" \
        -H "Content-Type: application/json" \
        -d '{"endpoint": "streams_add", "status_code": 500, "body": {"error": "injected"}}' >/dev/null 2>&1

    local resp
    resp=$(do_request "POST" "/api/v1/realtime/replay")
    local replayed failed
    replayed=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('replayed',0))" 2>/dev/null)
    failed=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('failed',0))" 2>/dev/null)

    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/fault" >/dev/null 2>&1

    if [ "$replayed" != "0" ] || [ "$failed" != "1" ]; then
        print_status "fail" "Sub-test 2 FAIL: Expected replayed=0 failed=1, got replayed=$replayed failed=$failed"
        return 1
    fi

    local current_stream_id
    current_stream_id=$(curl -s "${ES_HOST}/${ES_RULES_INDEX}/_doc/${id1}" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('_source',{}).get('rtvi_stream_id',''))" 2>/dev/null)

    if [ "$current_stream_id" != "$old_stream_id" ]; then
        print_status "fail" "Sub-test 2 FAIL: ES record mutated (old=$old_stream_id, new=$current_stream_id)"
        return 1
    fi

    print_status "ok" "Sub-test 2 PASS: replay failed=1, ES record unchanged (rtvi_stream_id=$old_stream_id)"
}

# ===========================================================================
# Sub-test 3: Concurrent replay returns 409
# ===========================================================================
subtest_concurrent_409() {
    print_status "info" "Sub-test 3: Concurrent replay returns 409"
    clear_rtvi
    flush_rules_index

    local r1 id1
    r1=$(create_rule "collision" "sensor-concurrent-1")
    id1=$(echo "$r1" | get_json id)

    if [ -z "$id1" ]; then
        print_status "fail" "Sub-test 3 FAIL: Could not create rule"
        return 1
    fi

    curl -s -X PUT "${RTVI_SIM_HOST}/v1/delay" \
        -H "Content-Type: application/json" \
        -d '{"endpoint": "streams_add", "delay_seconds": 5}' >/dev/null 2>&1

    curl -s -X POST "$AB_HOST/api/v1/realtime/replay" -o /dev/null &
    local bg_pid=$!
    sleep 2

    local http_code
    http_code=$(curl -s -o /tmp/replay_concurrent.json -w "%{http_code}" \
        -X POST "$AB_HOST/api/v1/realtime/replay" 2>/dev/null)

    wait "$bg_pid" 2>/dev/null || true
    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/delay" >/dev/null 2>&1

    if [ "$http_code" = "409" ]; then
        print_status "ok" "Sub-test 3 PASS: concurrent replay returned 409"
    else
        print_status "fail" "Sub-test 3 FAIL: expected 409, got $http_code"
        return 1
    fi
}

# ===========================================================================
# Sub-test 4: POST during replay returns 503
# ===========================================================================
subtest_post_during_replay_503() {
    print_status "info" "Sub-test 4: POST during replay returns 503"
    clear_rtvi
    flush_rules_index

    local r1 id1
    r1=$(create_rule "collision" "sensor-block-post-1")
    id1=$(echo "$r1" | get_json id)

    curl -s -X PUT "${RTVI_SIM_HOST}/v1/delay" \
        -H "Content-Type: application/json" \
        -d '{"endpoint": "streams_add", "delay_seconds": 5}' >/dev/null 2>&1

    curl -s -X POST "$AB_HOST/api/v1/realtime/replay" -o /dev/null &
    local bg_pid=$!
    sleep 2

    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$AB_HOST/api/v1/realtime" \
        -H "Content-Type: application/json" \
        -d "{\"liveStreamUrl\": \"$RTSP_URL\", \"sensor_id\": \"blocked\", \"prompt\": \"x\", \"alert_type\": \"x\", \"description\": \"x\"}" 2>/dev/null)

    wait "$bg_pid" 2>/dev/null || true
    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/delay" >/dev/null 2>&1

    if [ "$http_code" = "503" ]; then
        print_status "ok" "Sub-test 4 PASS: POST during replay returned 503"
    else
        print_status "fail" "Sub-test 4 FAIL: expected 503, got $http_code"
        return 1
    fi
}

# ===========================================================================
# Sub-test 5: DELETE during replay returns 503
# ===========================================================================
subtest_delete_during_replay_503() {
    print_status "info" "Sub-test 5: DELETE during replay returns 503"
    clear_rtvi
    flush_rules_index

    local r1 id1
    r1=$(create_rule "collision" "sensor-block-delete-1")
    id1=$(echo "$r1" | get_json id)

    if [ -z "$id1" ]; then
        print_status "fail" "Sub-test 5 FAIL: Could not create rule"
        return 1
    fi

    curl -s -X PUT "${RTVI_SIM_HOST}/v1/delay" \
        -H "Content-Type: application/json" \
        -d '{"endpoint": "streams_add", "delay_seconds": 5}' >/dev/null 2>&1

    curl -s -X POST "$AB_HOST/api/v1/realtime/replay" -o /dev/null &
    local bg_pid=$!
    sleep 2

    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X DELETE "$AB_HOST/api/v1/realtime/${id1}" 2>/dev/null)

    wait "$bg_pid" 2>/dev/null || true
    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/delay" >/dev/null 2>&1

    if [ "$http_code" = "503" ]; then
        print_status "ok" "Sub-test 5 PASS: DELETE during replay returned 503"
    else
        print_status "fail" "Sub-test 5 FAIL: expected 503, got $http_code"
        return 1
    fi
}

# ===========================================================================
# Sub-test 6: GET stays available during replay
# ===========================================================================
subtest_get_during_replay() {
    print_status "info" "Sub-test 6: GET stays available during replay"
    clear_rtvi
    flush_rules_index

    local r1 id1
    r1=$(create_rule "collision" "sensor-get-during-1")
    id1=$(echo "$r1" | get_json id)

    if [ -z "$id1" ]; then
        print_status "fail" "Sub-test 6 FAIL: Could not create rule"
        return 1
    fi

    curl -s -X PUT "${RTVI_SIM_HOST}/v1/delay" \
        -H "Content-Type: application/json" \
        -d '{"endpoint": "streams_add", "delay_seconds": 5}' >/dev/null 2>&1

    curl -s -X POST "$AB_HOST/api/v1/realtime/replay" -o /dev/null &
    local bg_pid=$!
    sleep 2

    local list_code get_code
    list_code=$(curl -s -o /dev/null -w "%{http_code}" "$AB_HOST/api/v1/realtime" 2>/dev/null)
    get_code=$(curl -s -o /dev/null -w "%{http_code}" "$AB_HOST/api/v1/realtime/${id1}" 2>/dev/null)

    wait "$bg_pid" 2>/dev/null || true
    curl -s -X DELETE "${RTVI_SIM_HOST}/v1/delay" >/dev/null 2>&1

    if [ "$list_code" = "200" ] && [ "$get_code" = "200" ]; then
        print_status "ok" "Sub-test 6 PASS: GET list=$list_code, GET by id=$get_code during replay"
    else
        print_status "fail" "Sub-test 6 FAIL: GET list=$list_code, GET by id=$get_code (expected both 200)"
        return 1
    fi
}

# ===========================================================================
# Sub-test 7: Feature-flag off (persistence disabled)
# ===========================================================================
subtest_feature_flag_off() {
    print_status "info" "Sub-test 7: Feature-flag off — replay 501, CRUD works in-memory"
    clear_rtvi

    local config_no_persist="$SCRIPT_DIR/config_no_persistence.yaml"
    stop_alert_bridge_local "$PID_DIR"
    start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$config_no_persist" 15

    if ! kill -0 "$(cat "$PID_DIR/alert_bridge.pid" 2>/dev/null)" 2>/dev/null; then
        print_status "fail" "Sub-test 7 FAIL: AB did not start with no-persistence config"
        return 1
    fi

    local replay_code
    replay_code=$(curl -s -o /tmp/replay_501.json -w "%{http_code}" \
        -X POST "$AB_HOST/api/v1/realtime/replay" 2>/dev/null)

    if [ "$replay_code" != "501" ]; then
        print_status "fail" "Sub-test 7 FAIL: replay expected 501, got $replay_code"
        stop_alert_bridge_local "$PID_DIR"
        return 1
    fi

    local replay_error
    replay_error=$(cat /tmp/replay_501.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null)
    if [ "$replay_error" != "persistence_disabled" ]; then
        print_status "fail" "Sub-test 7 FAIL: expected error=persistence_disabled, got=$replay_error"
        stop_alert_bridge_local "$PID_DIR"
        return 1
    fi

    local r1 id1
    r1=$(create_rule "collision" "sensor-nopersist-1")
    id1=$(echo "$r1" | get_json id)

    if [ -z "$id1" ]; then
        print_status "fail" "Sub-test 7 FAIL: POST rule failed in no-persistence mode"
        stop_alert_bridge_local "$PID_DIR"
        return 1
    fi

    local list_count
    list_count=$(do_request "GET" "/api/v1/realtime" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null)

    local del_code
    del_code=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$AB_HOST/api/v1/realtime/${id1}" 2>/dev/null)

    stop_alert_bridge_local "$PID_DIR"

    if [ "$list_count" -ge 1 ] && [ "$del_code" = "200" ]; then
        print_status "ok" "Sub-test 7 PASS: replay=501 (persistence_disabled), CRUD works in-memory (list=$list_count, delete=$del_code)"
    else
        print_status "fail" "Sub-test 7 FAIL: list_count=$list_count delete_code=$del_code"
        return 1
    fi

    local config_persist="$SCRIPT_DIR/config.yaml"
    start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$config_persist" 15
}

# ===========================================================================
# Sub-test 8: AB restart preserves state
# ===========================================================================
subtest_restart_preserves_state() {
    print_status "info" "Sub-test 8: AB restart preserves state"
    clear_rtvi
    flush_rules_index

    local r1 id1
    r1=$(create_rule "collision" "sensor-restart-1")
    id1=$(echo "$r1" | get_json id)

    if [ -z "$id1" ]; then
        print_status "fail" "Sub-test 8 FAIL: Could not create rule"
        return 1
    fi

    local es_found
    es_found=$(curl -s "${ES_HOST}/${ES_RULES_INDEX}/_doc/${id1}" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('found', False))" 2>/dev/null)

    if [ "$es_found" != "True" ]; then
        print_status "fail" "Sub-test 8 FAIL: Rule not persisted in ES before restart"
        return 1
    fi

    stop_alert_bridge_local "$PID_DIR"
    sleep 2
    start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$SCRIPT_DIR/config.yaml" 15

    if ! kill -0 "$(cat "$PID_DIR/alert_bridge.pid" 2>/dev/null)" 2>/dev/null; then
        print_status "fail" "Sub-test 8 FAIL: AB did not restart"
        return 1
    fi

    local list_resp list_count found_rule
    list_resp=$(do_request "GET" "/api/v1/realtime")
    list_count=$(echo "$list_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null)
    found_rule=$(echo "$list_resp" | python3 -c "
import sys, json
data = json.load(sys.stdin)
found = any(r.get('id') == '$id1' for r in data.get('rules', []))
print('yes' if found else 'no')
" 2>/dev/null)

    if [ "$list_count" -ge 1 ] && [ "$found_rule" = "yes" ]; then
        print_status "ok" "Sub-test 8 PASS: rule $id1 survived AB restart (count=$list_count)"
    else
        print_status "fail" "Sub-test 8 FAIL: rule not found after restart (count=$list_count, found=$found_rule)"
        return 1
    fi
}

# ===========================================================================
# Sub-test 9: Realtime-specific flag off
#
# `persistence.enabled: true` (global persistence is up so alert configs /
# prompts still persist), but `rtvi_vlm.enable_realtime_persistence: false`
# so realtime rules go in-memory ONLY and replay returns 501.
# ===========================================================================
subtest_realtime_flag_off() {
    print_status "info" "Sub-test 9: realtime-specific flag off"
    clear_rtvi
    flush_rules_index

    local config_no_realtime="$SCRIPT_DIR/config_no_realtime_persistence.yaml"
    stop_alert_bridge_local "$PID_DIR"
    start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$config_no_realtime" 15

    if ! kill -0 "$(cat "$PID_DIR/alert_bridge.pid" 2>/dev/null)" 2>/dev/null; then
        print_status "fail" "Sub-test 9 FAIL: AB did not start with no-realtime-persistence config"
        return 1
    fi

    # 1) Replay must short-circuit to 501 *and* echo a 32-char
    # correlation_id so operators can grep the rejection log.
    local replay_code replay_error replay_cid
    replay_code=$(curl -s -o /tmp/replay_501_realtime.json -w "%{http_code}" \
        -X POST "$AB_HOST/api/v1/realtime/replay" 2>/dev/null)
    replay_error=$(cat /tmp/replay_501_realtime.json \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null)
    replay_cid=$(cat /tmp/replay_501_realtime.json \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('correlation_id',''))" 2>/dev/null)

    if [ "$replay_code" != "501" ] || [ "$replay_error" != "persistence_disabled" ]; then
        print_status "fail" "Sub-test 9 FAIL: replay expected 501 persistence_disabled, got $replay_code $replay_error"
        stop_alert_bridge_local "$PID_DIR"
        return 1
    fi
    if [ -z "$replay_cid" ] || [ "${#replay_cid}" -ne 32 ]; then
        print_status "fail" "Sub-test 9 FAIL: 501 response did not echo a 32-char correlation_id (got '$replay_cid')"
        stop_alert_bridge_local "$PID_DIR"
        return 1
    fi

    # 2) POST a rule — must succeed (in-memory) and NOT touch ES rules index.
    local r1 id1
    r1=$(create_rule "collision" "sensor-realtime-flag-off-1")
    id1=$(echo "$r1" | get_json id)

    if [ -z "$id1" ]; then
        print_status "fail" "Sub-test 9 FAIL: POST rule failed in flag-off mode"
        stop_alert_bridge_local "$PID_DIR"
        return 1
    fi

    # 3) Confirm rule is NOT in the ES rules index.
    local es_found
    es_found=$(curl -s "${ES_HOST}/${ES_RULES_INDEX}/_doc/${id1}" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('found', False))" 2>/dev/null)

    if [ "$es_found" = "True" ]; then
        print_status "fail" "Sub-test 9 FAIL: rule $id1 leaked into ES — flag did not gate writes"
        stop_alert_bridge_local "$PID_DIR"
        return 1
    fi

    # 4) Listing returns it from in-memory; DELETE works.
    local list_count
    list_count=$(do_request "GET" "/api/v1/realtime" \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null)

    local del_code
    del_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X DELETE "$AB_HOST/api/v1/realtime/${id1}" 2>/dev/null)

    stop_alert_bridge_local "$PID_DIR"
    start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$SCRIPT_DIR/config.yaml" 15

    if [ "$list_count" -ge 1 ] && [ "$del_code" = "200" ]; then
        print_status "ok" "Sub-test 9 PASS: replay=501, ES untouched, in-memory CRUD works (list=$list_count, delete=$del_code)"
    else
        print_status "fail" "Sub-test 9 FAIL: list_count=$list_count delete_code=$del_code"
        return 1
    fi
}

# ===========================================================================
# Sub-test 10: replay correlation_id round-trip + Prometheus scrape (deltas)
# ===========================================================================
# Part A — correlation_id round-trip on the 200 success path. The 501 path
# is verified inside sub-test 9 (which already exercises the
# persistence-disabled config).
#
# Part B — Prometheus scrape on the real scrape port (PROMETHEUS_PORT,
# default 9081).  The FastAPI route handlers run in a child process and
# the Prometheus HTTP server runs in the parent; metrics flow across
# that boundary via the prometheus_client multiprocess back-end wired
# up in ``enhance_alert_with_vlm.py`` (PROMETHEUS_MULTIPROC_DIR +
# MultiProcessCollector + ``multiprocess_mode='livesum'`` on every
# Gauge in ``metrics/prometheus_metrics.py``).  Assertions are
# **delta-based** so the test does not depend on whatever the metric
# values happened to be when the scrape server bound the port — this
# is the only reliable shape for a counter-based test that runs after
# other sub-tests have already exercised the same endpoints.
subtest_metrics_and_correlation_id() {
    print_status "info" "Sub-test 10: replay correlation_id round-trip + Prometheus scrape (deltas)"
    clear_rtvi
    flush_rules_index

    # Part B scrapes the production scrape port; AB only binds it when
    # PROMETHEUS_METRICS_ENABLED is true.  Export here so the AB
    # restart at the top of Part B inherits it; previous sub-tests
    # don't depend on metrics being on so leaking the env var to
    # whatever runs after this subtest is safe.
    export PROMETHEUS_METRICS_ENABLED=true

    # ── Part A: correlation_id round-trip on success ────────────────
    local r1 id1
    r1=$(create_rule "collision" "sensor-metrics-1")
    id1=$(echo "$r1" | get_json id)
    if [ -z "$id1" ]; then
        print_status "fail" "Sub-test 10 FAIL: Could not create rule"
        return 1
    fi

    local replay_resp cid_success
    replay_resp=$(curl -s -X POST "$AB_HOST/api/v1/realtime/replay" 2>/dev/null)
    cid_success=$(echo "$replay_resp" \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('correlation_id',''))" 2>/dev/null)
    if [ -z "$cid_success" ] || [ "${#cid_success}" -ne 32 ]; then
        print_status "fail" "Sub-test 10 FAIL: 200 response did not echo a 32-char correlation_id (got '$cid_success')"
        return 1
    fi

    # ── Part B: Prometheus scrape (delta-based) ─────────────────────
    local prom_port="${PROMETHEUS_PORT:-9081}"

    # Restart AB with PROMETHEUS_METRICS_ENABLED=true (exported above)
    # so the new process binds the scrape port and wires up the
    # multiprocess registry.  Earlier sub-tests run with metrics off
    # by default; we don't reuse that AB because nothing would be
    # listening on prom_port.
    stop_alert_bridge_local "$PID_DIR"
    start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$SCRIPT_DIR/config.yaml" 15

    # Helper: read the current value of a labelled (or label-free)
    # counter/gauge from the scrape endpoint.  Returns 0 when the
    # series does not yet exist (warm_startup_labels covers most
    # series at boot, but the new realtime persistence metrics are
    # only created on first inc).
    _scrape_value() {
        local metric_pattern="$1"
        curl -s "http://localhost:${prom_port}/metrics" 2>/dev/null \
            | grep -E "^${metric_pattern}( |\\{)" \
            | tail -1 \
            | awk '{print $NF}' \
            | python3 -c "
import sys
v = sys.stdin.read().strip()
print(float(v) if v else 0.0)
" 2>/dev/null || echo "0.0"
    }

    # Sanity: scrape port must be reachable on the metrics-enabled AB.
    if ! curl -fsS "http://localhost:${prom_port}/metrics" >/dev/null 2>&1; then
        print_status "fail" "Sub-test 10 FAIL (Part B): scrape port ${prom_port} not reachable — is PROMETHEUS_METRICS_ENABLED honoured?"
        return 1
    fi

    # Snapshot every metric we are about to move.  Counters are
    # monotonic, so the delta after our actions is exactly the
    # contribution of *this* sub-test.  The fresh AB starts with
    # warmed-up label series at 0 plus whatever Part A left in ES
    # (rules persisted before metrics were enabled don't show up in
    # the persisted counter — that's expected since the counter
    # tracks process-lifetime increments, not historical inventory).
    local persisted_before replay_success_before failures_before
    persisted_before=$(_scrape_value "alert_bridge_realtime_rules_persisted_total")
    replay_success_before=$(_scrape_value 'alert_bridge_replay_invocations_total\{outcome="success"\}')
    failures_before=$(_scrape_value "alert_bridge_replay_rule_failures_total")

    # Create one rule on the metrics-enabled AB → goes through Step 4
    # (PENDING→ACTIVE update) so REALTIME_RULES_PERSISTED += 1.
    local r2 id2
    r2=$(create_rule "person" "sensor-metrics-2")
    id2=$(echo "$r2" | get_json id)
    if [ -z "$id2" ]; then
        print_status "fail" "Sub-test 10 FAIL (Part B): Could not create second rule"
        return 1
    fi

    # Trigger replay; existing rules (Part A's id1 + this id2) are
    # all in ES so the replay loop runs and reports success.
    local replay_resp2
    replay_resp2=$(curl -s -X POST "$AB_HOST/api/v1/realtime/replay" 2>/dev/null)

    # Allow the parent's MultiProcessCollector one scrape cycle to
    # pick up the child's mmap writes — this is essentially instant
    # but a single retry guards against scheduler hiccups in CI.
    sleep 1

    local persisted_after replay_success_after failures_after count_after
    persisted_after=$(_scrape_value "alert_bridge_realtime_rules_persisted_total")
    replay_success_after=$(_scrape_value 'alert_bridge_replay_invocations_total\{outcome="success"\}')
    failures_after=$(_scrape_value "alert_bridge_replay_rule_failures_total")
    count_after=$(_scrape_value "alert_bridge_realtime_rules_count")

    # Sanity: persisted moved by AT LEAST 1 (our new rule).  Use awk
    # for float comparison portability.
    local persisted_delta replay_success_delta failures_delta
    persisted_delta=$(awk "BEGIN { print ${persisted_after} - ${persisted_before} }")
    replay_success_delta=$(awk "BEGIN { print ${replay_success_after} - ${replay_success_before} }")
    failures_delta=$(awk "BEGIN { print ${failures_after} - ${failures_before} }")

    local missing=""
    if ! awk "BEGIN { exit !(${persisted_delta} >= 1) }"; then
        missing="$missing persisted_total(delta=${persisted_delta},want>=1)"
    fi
    if ! awk "BEGIN { exit !(${replay_success_delta} >= 1) }"; then
        missing="$missing replay_invocations{success}(delta=${replay_success_delta},want>=1)"
    fi
    if ! awk "BEGIN { exit !(${failures_delta} == 0) }"; then
        missing="$missing replay_rule_failures(delta=${failures_delta},want=0)"
    fi
    # Gauge: rules_count must exist in the scrape and be > 0 after
    # we created at least one rule that survived the restart+replay.
    if ! awk "BEGIN { exit !(${count_after} >= 1) }"; then
        missing="$missing realtime_rules_count(after=${count_after},want>=1)"
    fi

    if [ -n "$missing" ]; then
        print_status "fail" "Sub-test 10 FAIL (Part B): metric deltas wrong on prom port $prom_port:$missing"
        return 1
    fi
    print_status "ok" "Sub-test 10 PASS: correlation_id round-trip OK; metric deltas verified on port $prom_port (persisted+${persisted_delta}, replay_success+${replay_success_delta}, failures+${failures_delta}, rules_count=${count_after})"
}

# ── Run all sub-tests ─────────────────────────────────────────────────
subtest_happy_path                  || FAILURES=$((FAILURES + 1))
subtest_partial_failure             || FAILURES=$((FAILURES + 1))
subtest_concurrent_409              || FAILURES=$((FAILURES + 1))
subtest_post_during_replay_503      || FAILURES=$((FAILURES + 1))
subtest_delete_during_replay_503    || FAILURES=$((FAILURES + 1))
subtest_get_during_replay           || FAILURES=$((FAILURES + 1))
subtest_feature_flag_off            || FAILURES=$((FAILURES + 1))
subtest_restart_preserves_state     || FAILURES=$((FAILURES + 1))
subtest_realtime_flag_off           || FAILURES=$((FAILURES + 1))
subtest_metrics_and_correlation_id  || FAILURES=$((FAILURES + 1))

echo ""
echo "============================================================"
TOTAL=10
if [ "$FAILURES" -gt 0 ]; then
    print_status "fail" "RESULTS: $((TOTAL - FAILURES))/${TOTAL} passed, $FAILURES failed"
    exit 1
fi

print_status "ok" "RESULTS: ${TOTAL}/${TOTAL} passed — ALL REPLAY TESTS PASSED"
exit 0
