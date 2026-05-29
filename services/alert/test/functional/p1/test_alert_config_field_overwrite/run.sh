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

# Test: Alert Config Field Overwrite (Redis cache fidelity)
# Description: Pin the contract that every field accepted by the
#              ``/api/v1/verification/config`` API lands in Redis with
#              the exact value the operator sent — no fields silently
#              dropped, no stale values re-appearing after an explicit
#              clear, and ``vlm_params`` merging on PUT instead of
#              replace.
#
#              Existing P1 tests cover end-to-end semantics
#              (hot-reload propagation through the sink, prompt sync to
#              the VLM call). This test sits one layer below: it reads
#              the Redis JSON document directly, so a regression that
#              breaks the POST/PUT → cache write path surfaces here
#              even when downstream consumers happen to mask it.
#
# Coverage:
#   1. POST with all fields populated → every field present in Redis.
#   2. PUT each scalar field individually → updated field + every
#      other field preserved verbatim.
#   3. PUT a partial ``vlm_params`` → deep-merge in Redis (other
#      sub-keys preserved).
#   4. PUT ``<field>: null`` → field cleared in Redis (key present,
#      value explicitly None).
#   5. DELETE → Redis key gone.
#
# Isolation: per-run alert_type suffix; cleanup on EXIT trap.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

AB_HOST="${AB_HOST:-http://localhost:9080}"
BASE="$AB_HOST/api/v1/verification/config"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"

TEST_NAME="alert_config_field_overwrite"
ALERT_TYPE="field_ow_$(date +%s)_$$"
REDIS_KEY="alert_config:$ALERT_TYPE"

echo "=== P1: Alert Config Field Overwrite ($ALERT_TYPE) ==="

cleanup() {
    local rc=$?
    print_status "info" "Cleaning up $ALERT_TYPE"
    curl -fsS -X DELETE "$BASE/$ALERT_TYPE" >/dev/null 2>&1 || true
    # Defensive Redis del — if API delete failed (e.g., 5xx mid-test)
    # the key would otherwise leak to subsequent runs.
    python3 -c "
import redis
r = redis.Redis(host='$REDIS_HOST', port=$REDIS_PORT, decode_responses=True)
try: r.json().delete('$REDIS_KEY')
except Exception: r.delete('$REDIS_KEY')
" 2>/dev/null || true
    exit $rc
}
trap cleanup EXIT

# ── Helper: read the Redis cache as a Python literal ────────────────
# Echoes the raw JSON document at $REDIS_KEY, or "<missing>" when the
# key is absent. Used directly in shell pipelines via $().
redis_get() {
    python3 -c "
import json, redis, sys
r = redis.Redis(host='$REDIS_HOST', port=$REDIS_PORT, decode_responses=True)
v = r.json().get('$REDIS_KEY')
if v is None:
    print('<missing>')
else:
    print(json.dumps(v))
"
}

# Helper: assert a field in Redis equals the expected JSON literal.
# Emits a clean ok/fail line via print_status.
assert_redis_field() {
    local field="$1"
    local expected_json="$2"
    local label="$3"
    local got_json
    got_json=$(redis_get | python3 -c "
import json, sys
doc = json.loads(sys.stdin.read())
print(json.dumps(doc.get('$field'), sort_keys=True))
")
    local want_json
    want_json=$(python3 -c "
import json
print(json.dumps($expected_json, sort_keys=True))
")
    if [ "$got_json" != "$want_json" ]; then
        print_status "fail" "$label: Redis $field=$got_json, expected=$want_json"
        return 1
    fi
    print_status "ok" "$label: Redis $field=$got_json"
}

# ── 0. Prerequisites ────────────────────────────────────────────────
print_status "wait" "Checking prerequisites"
curl -fsS "$AB_HOST/health" >/dev/null \
    || { print_status "fail" "Alert Bridge unreachable at $AB_HOST"; exit 2; }
python3 -c "
import redis
redis.Redis(host='$REDIS_HOST', port=$REDIS_PORT).ping()
" >/dev/null 2>&1 \
    || { print_status "fail" "Redis unreachable at $REDIS_HOST:$REDIS_PORT"; exit 2; }

# Pre-cleanup in case a prior aborted run left a key behind.
curl -fsS -X DELETE "$BASE/$ALERT_TYPE" >/dev/null 2>&1 || true

# ── 1. POST with all fields populated ───────────────────────────────
print_status "wait" "POST with all fields populated"
POST_BODY=$(cat <<EOF
{
  "alert_type": "$ALERT_TYPE",
  "prompt": "P0 prompt",
  "system_prompt": "P0 system",
  "enrichment_prompt": "P0 enrichment",
  "vlm_params": {"max_tokens": 256, "num_frames": 5, "temperature": 0.5},
  "output_category": "P0 Category"
}
EOF
)
HTTP_CODE=$(curl -s -o /tmp/${ALERT_TYPE}_post.json -w "%{http_code}" \
    -X POST "$BASE" -H "Content-Type: application/json" -d "$POST_BODY")
if [ "$HTTP_CODE" != "201" ]; then
    print_status "fail" "POST expected 201, got $HTTP_CODE: $(cat /tmp/${ALERT_TYPE}_post.json)"
    exit 1
fi
print_status "ok" "POST 201"

# Verify Redis has every field exactly.
assert_redis_field prompt              '"P0 prompt"'                "after POST" || exit 1
assert_redis_field system_prompt       '"P0 system"'                "after POST" || exit 1
assert_redis_field enrichment_prompt   '"P0 enrichment"'            "after POST" || exit 1
assert_redis_field output_category     '"P0 Category"'              "after POST" || exit 1
assert_redis_field vlm_params \
    '{"max_tokens": 256, "num_frames": 5, "temperature": 0.5}' \
    "after POST" || exit 1

# ── 2. PUT each scalar field individually ───────────────────────────
print_status "wait" "PUT prompt — only prompt updated, every other field preserved"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"prompt": "P2 prompt"}' >/dev/null
assert_redis_field prompt              '"P2 prompt"'                "after PUT prompt"           || exit 1
assert_redis_field system_prompt       '"P0 system"'                "after PUT prompt"           || exit 1
assert_redis_field enrichment_prompt   '"P0 enrichment"'            "after PUT prompt"           || exit 1
assert_redis_field output_category     '"P0 Category"'              "after PUT prompt"           || exit 1
assert_redis_field vlm_params \
    '{"max_tokens": 256, "num_frames": 5, "temperature": 0.5}' \
    "after PUT prompt" || exit 1

print_status "wait" "PUT system_prompt — isolated update"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"system_prompt": "P2 system"}' >/dev/null
assert_redis_field prompt              '"P2 prompt"'                "after PUT system_prompt"    || exit 1
assert_redis_field system_prompt       '"P2 system"'                "after PUT system_prompt"    || exit 1
assert_redis_field enrichment_prompt   '"P0 enrichment"'            "after PUT system_prompt"    || exit 1
assert_redis_field output_category     '"P0 Category"'              "after PUT system_prompt"    || exit 1

print_status "wait" "PUT enrichment_prompt — isolated update"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"enrichment_prompt": "P2 enrichment"}' >/dev/null
assert_redis_field enrichment_prompt   '"P2 enrichment"'            "after PUT enrichment_prompt" || exit 1
assert_redis_field prompt              '"P2 prompt"'                "after PUT enrichment_prompt" || exit 1

print_status "wait" "PUT output_category — isolated update (regression for fix_output_category_reload)"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"output_category": "P2 Category"}' >/dev/null
assert_redis_field output_category     '"P2 Category"'              "after PUT output_category"  || exit 1
assert_redis_field prompt              '"P2 prompt"'                "after PUT output_category"  || exit 1
assert_redis_field vlm_params \
    '{"max_tokens": 256, "num_frames": 5, "temperature": 0.5}' \
    "after PUT output_category" || exit 1

# ── 3. vlm_params partial PUT — deep-merge in Redis ─────────────────
print_status "wait" "PUT vlm_params={temperature: 0.9} — deep-merge: max_tokens/num_frames preserved"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"vlm_params": {"temperature": 0.9}}' >/dev/null
assert_redis_field vlm_params \
    '{"max_tokens": 256, "num_frames": 5, "temperature": 0.9}' \
    "after PUT vlm_params partial" || exit 1

# ── 4. PUT null on optional fields — value cleared in Redis ─────────
print_status "wait" "PUT system_prompt=null — cleared in Redis"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"system_prompt": null}' >/dev/null
assert_redis_field system_prompt       'None'                       "after PUT system_prompt=null" || exit 1
# Other fields untouched.
assert_redis_field prompt              '"P2 prompt"'                "after PUT system_prompt=null" || exit 1
assert_redis_field output_category     '"P2 Category"'              "after PUT system_prompt=null" || exit 1

print_status "wait" "PUT enrichment_prompt=null — cleared in Redis"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"enrichment_prompt": null}' >/dev/null
assert_redis_field enrichment_prompt   'None'                       "after PUT enrichment_prompt=null" || exit 1

print_status "wait" "PUT output_category=null — cleared in Redis (regression for fix_output_category_reload)"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"output_category": null}' >/dev/null
assert_redis_field output_category     'None'                       "after PUT output_category=null" || exit 1

print_status "wait" "PUT vlm_params=null — cleared in Redis"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"vlm_params": null}' >/dev/null
assert_redis_field vlm_params          'None'                       "after PUT vlm_params=null" || exit 1
# ``prompt`` still required and unchanged.
assert_redis_field prompt              '"P2 prompt"'                "after PUT vlm_params=null" || exit 1

# ── 5. DELETE — Redis key gone ──────────────────────────────────────
print_status "wait" "DELETE — Redis key removed"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$BASE/$ALERT_TYPE")
if [ "$HTTP_CODE" != "200" ]; then
    print_status "fail" "DELETE expected 200, got $HTTP_CODE"
    exit 1
fi
GOT=$(redis_get)
if [ "$GOT" != "<missing>" ]; then
    print_status "fail" "Redis key still present after DELETE: $GOT"
    exit 1
fi
print_status "ok" "Redis key removed after DELETE"

print_status "ok" "PASS: every field overwrites the Redis cache verbatim"
exit 0
