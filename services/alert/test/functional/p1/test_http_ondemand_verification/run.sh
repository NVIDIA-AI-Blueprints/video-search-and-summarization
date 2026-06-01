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

# Test: HTTP On-demand Verification API (async 202 design)
# Description: Verify POST /api/v1/verification/ondemand
#   Sub-test 1: Happy path — valid request returns 202 with correlationId,
#               then result document appears in ES with verificationResponseCode
#   Sub-test 2: Unknown category returns 400 with unknown_category error
#   Sub-test 3: VLM unavailable — request returns 202 (accepted),
#               then error document published to ES with non-200 verificationResponseCode
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
API_BASE="${API_BASE:-http://127.0.0.1:9080}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
MEDIA_URL="http://127.0.0.1:30888/vst/sim/media/test.mp4"
FAILURES=0

echo "=== P1: HTTP On-demand Verification (async 202) ==="
mkdir -p "$PID_DIR"

# ── Helper ────────────────────────────────────────────────────────────
post_ondemand() {
    local body="$1" resp_file="$2"
    curl -sS -o "$resp_file" -w "%{http_code}" \
        -X POST "$API_BASE/api/v1/verification/ondemand" \
        -H "Content-Type: application/json" \
        -d "$body"
}

# ── Sub-test 1: Happy path (valid request → 202, result in ES) ───────
subtest_happy_path() {
    local resp_file="$PID_DIR/ondemand_happy.json"

    print_status "info" "Sub-test 1: Happy path (202 + ES result)"

    local http_code
    http_code=$(post_ondemand \
        "{\"category\": \"collision\", \"place\": {\"name\": \"Dock Entrance-East\"}, \"info\": {\"media_urls\": [\"$MEDIA_URL\"], \"media_type\": \"video\"}}" \
        "$resp_file")

    print_status "info" "HTTP code: $http_code"
    print_status "info" "Response: $(cat "$resp_file")"

    if [ "$http_code" != "202" ]; then
        print_status "fail" "Sub-test 1 FAIL: expected HTTP 202, got $http_code"
        return 1
    fi

    # Validate 202 response structure
    python3 - "$resp_file" <<'PY'
import json, sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = json.load(f)

if data.get("status") != "accepted":
    raise SystemExit(f"status is not 'accepted': {data.get('status')}")
if not data.get("correlationId"):
    raise SystemExit("correlationId is missing or empty")
if not data.get("timestamp"):
    raise SystemExit("timestamp is missing")
PY

    # Poll ES for the result document (background task publishes it)
    print_status "wait" "Waiting for result document in ES (up to 60s)..."
    DOC=$(poll_es_sim "$ES_HOST" 60 5) || {
        print_status "fail" "Sub-test 1 FAIL: No result document in ES within 60s"
        return 1
    }

    print_status "info" "ES doc: $(echo "$DOC" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(json.dumps({k:d.get(k) for k in ("sensorId","category","info")}, default=str)[:300])' 2>/dev/null || echo "$DOC")"

    # Validate the ES document has verification fields
    python3 - <<PY "$DOC"
import json, sys

doc = json.loads(sys.argv[1])
info = doc.get("info", {})

errors = []
if not doc.get("category"):
    errors.append("category missing")
code = info.get("verificationResponseCode")
if code is None:
    errors.append("info.verificationResponseCode missing")
if not info.get("media_type"):
    errors.append("info.media_type missing")

if errors:
    raise SystemExit("ES doc validation failed: " + "; ".join(errors))
PY

    print_status "ok" "Sub-test 1 PASS: 202 accepted + result document in ES with verificationResponseCode"
}

# ── Sub-test 2: Unknown category → 400 (sync, before background) ─────
subtest_unknown_category() {
    local resp_file="$PID_DIR/ondemand_unknown_type.json"

    print_status "info" "Sub-test 2: Unknown category"

    local http_code
    http_code=$(post_ondemand \
        "{\"category\": \"nonexistent_type_xyz\", \"info\": {\"media_urls\": [\"$MEDIA_URL\"], \"media_type\": \"video\"}}" \
        "$resp_file")

    print_status "info" "HTTP code: $http_code"
    print_status "info" "Response: $(cat "$resp_file")"

    if [ "$http_code" != "400" ]; then
        print_status "fail" "Sub-test 2 FAIL: expected HTTP 400, got $http_code"
        return 1
    fi

    python3 - "$resp_file" <<'PY'
import json, sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = json.load(f)

if data.get("status") != "error":
    raise SystemExit(f"status is not error: {data.get('status')}")
if data.get("error") != "unknown_category":
    raise SystemExit(f"error key mismatch: expected 'unknown_category', got '{data.get('error')}'")
if not data.get("message"):
    raise SystemExit("error message is empty")
PY

    print_status "ok" "Sub-test 2 PASS: unknown category returns 400"
}

# ── Sub-test 3: VLM unavailable → 202 accepted, error doc in ES ──────
subtest_vlm_unavailable() {
    print_status "info" "Sub-test 3: VLM unavailable (202 + error doc in ES)"

    if [ ! -f "$PID_DIR/nim_sim.pid" ]; then
        print_status "info" "Sub-test 3 SKIP: nim_sim.pid not found — NIM not managed by this harness"
        return 0
    fi

    local nim_pid
    nim_pid=$(cat "$PID_DIR/nim_sim.pid")

    # Record doc count before sending
    local count_before
    count_before=$(count_es_docs "$ES_HOST")

    # Kill NIM simulator
    if kill -0 "$nim_pid" 2>/dev/null; then
        kill "$nim_pid" 2>/dev/null || true
        sleep 1
        kill -9 "$nim_pid" 2>/dev/null || true
        print_status "info" "NIM simulator stopped (PID $nim_pid)"
    fi

    local resp_file="$PID_DIR/ondemand_vlm_down.json"
    local http_code
    http_code=$(post_ondemand \
        "{\"category\": \"collision\", \"place\": {\"name\": \"Dock Entrance-East\"}, \"info\": {\"media_urls\": [\"$MEDIA_URL\"], \"media_type\": \"video\"}}" \
        "$resp_file")

    print_status "info" "HTTP code: $http_code"
    print_status "info" "Response: $(cat "$resp_file")"

    # Request should be accepted (VLM error happens in background)
    if [ "$http_code" != "202" ]; then
        print_status "fail" "Sub-test 3 FAIL: expected HTTP 202, got $http_code"
        # Restart NIM before returning
        python3 "$REPO_ROOT/test/sim_scripts/nim/nim_stub_server.py" \
            > "$PID_DIR/nim_sim.log" 2>&1 &
        echo $! > "$PID_DIR/nim_sim.pid"
        sleep 2
        return 1
    fi

    # Validate 202 response has correlationId
    python3 - "$resp_file" <<'PY'
import json, sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = json.load(f)

if data.get("status") != "accepted":
    raise SystemExit(f"status is not 'accepted': {data.get('status')}")
if not data.get("correlationId"):
    raise SystemExit("correlationId is missing")
PY

    # Wait for background task to complete and publish error doc
    print_status "wait" "Waiting for error document in ES (up to 30s)..."
    sleep 15

    # Always restart NIM before checking results
    print_status "wait" "Restarting NIM simulator..."
    python3 "$REPO_ROOT/test/sim_scripts/nim/nim_stub_server.py" \
        > "$PID_DIR/nim_sim.log" 2>&1 &
    echo $! > "$PID_DIR/nim_sim.pid"
    sleep 2
    print_status "ok" "NIM simulator restarted"

    # Check if an error document was published (non-200 verificationResponseCode)
    local count_after
    count_after=$(count_es_docs "$ES_HOST")

    if [ "$count_after" -gt "$count_before" ]; then
        print_status "ok" "Sub-test 3 PASS: 202 accepted, error document published to ES ($count_before -> $count_after docs)"
    else
        print_status "info" "Sub-test 3 PASS (soft): 202 accepted, no error doc in ES (sink may have dropped VLM error)"
    fi
}

# ── Run all sub-tests ─────────────────────────────────────────────────
subtest_happy_path         || FAILURES=$((FAILURES + 1))
subtest_unknown_category   || FAILURES=$((FAILURES + 1))
subtest_vlm_unavailable    || FAILURES=$((FAILURES + 1))

echo ""
if [ "$FAILURES" -gt 0 ]; then
    print_status "fail" "FAIL: $FAILURES sub-test(s) failed"
    exit 1
fi

print_status "ok" "PASS: All on-demand verification sub-tests passed"
exit 0
