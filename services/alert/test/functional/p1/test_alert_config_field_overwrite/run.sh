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

# Test: Alert Config Field Overwrite (store fidelity)
# Description: Pin the contract that every field accepted by the
#              ``/api/v1/verification/config`` API lands in the store
#              (Elasticsearch — Redis has been removed) with the exact
#              value the operator sent — no fields silently dropped, no
#              stale values re-appearing after an explicit clear, and
#              ``vlm_params`` merging on PUT instead of replace.
#
#              The stored document is read back through the API GET, which
#              serves strictly from the Elasticsearch source of truth, so a
#              regression in the POST/PUT write path surfaces here.
#
# Coverage:
#   1. POST with all fields populated → every field present.
#   2. PUT each scalar field individually → updated field + every
#      other field preserved verbatim.
#   3. PUT a partial ``vlm_params`` → deep-merge (other sub-keys preserved).
#   4. PUT ``<field>: null`` → field cleared (present, value None).
#   5. DELETE → record gone.
#
# Isolation: per-run alert_type suffix; cleanup on EXIT trap.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

AB_HOST="${AB_HOST:-http://localhost:9080}"
BASE="$AB_HOST/api/v1/verification/config"

TEST_NAME="alert_config_field_overwrite"
ALERT_TYPE="field_ow_$(date +%s)_$$"

echo "=== P1: Alert Config Field Overwrite ($ALERT_TYPE) ==="

cleanup() {
    local rc=$?
    print_status "info" "Cleaning up $ALERT_TYPE"
    curl -fsS -X DELETE "$BASE/$ALERT_TYPE" >/dev/null 2>&1 || true
    exit $rc
}
trap cleanup EXIT

# ── Helper: read the stored config via the API ──────────────────────
# Echoes the raw JSON document for $ALERT_TYPE, or "<missing>" when the
# record is absent (404). Used directly in shell pipelines via $().
store_get() {
    local body http
    body=$(curl -s -o /tmp/${ALERT_TYPE}_get.json -w "%{http_code}" "$BASE/$ALERT_TYPE")
    http="$body"
    if [ "$http" = "200" ]; then
        cat /tmp/${ALERT_TYPE}_get.json
    else
        echo "<missing>"
    fi
}

# Helper: assert a field in the stored config equals the expected JSON literal.
assert_store_field() {
    local field="$1"
    local expected_json="$2"
    local label="$3"
    local got_json
    got_json=$(store_get | python3 -c "
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
        print_status "fail" "$label: store $field=$got_json, expected=$want_json"
        return 1
    fi
    print_status "ok" "$label: store $field=$got_json"
}

# ── 0. Prerequisites ────────────────────────────────────────────────
print_status "wait" "Checking prerequisites"
curl -fsS "$AB_HOST/health" >/dev/null \
    || { print_status "fail" "Alert Bridge unreachable at $AB_HOST"; exit 2; }
curl -fsS "$BASE" >/dev/null \
    || { print_status "fail" "Alert-config API unreachable at $BASE"; exit 2; }

# Pre-cleanup in case a prior aborted run left a record behind.
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

# Verify the store has every field exactly.
assert_store_field prompt              '"P0 prompt"'                "after POST" || exit 1
assert_store_field system_prompt       '"P0 system"'                "after POST" || exit 1
assert_store_field enrichment_prompt   '"P0 enrichment"'            "after POST" || exit 1
assert_store_field output_category     '"P0 Category"'              "after POST" || exit 1
assert_store_field vlm_params \
    '{"max_tokens": 256, "num_frames": 5, "temperature": 0.5}' \
    "after POST" || exit 1

# ── 2. PUT each scalar field individually ───────────────────────────
print_status "wait" "PUT prompt — only prompt updated, every other field preserved"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"prompt": "P2 prompt"}' >/dev/null
assert_store_field prompt              '"P2 prompt"'                "after PUT prompt"           || exit 1
assert_store_field system_prompt       '"P0 system"'                "after PUT prompt"           || exit 1
assert_store_field enrichment_prompt   '"P0 enrichment"'            "after PUT prompt"           || exit 1
assert_store_field output_category     '"P0 Category"'              "after PUT prompt"           || exit 1
assert_store_field vlm_params \
    '{"max_tokens": 256, "num_frames": 5, "temperature": 0.5}' \
    "after PUT prompt" || exit 1

print_status "wait" "PUT system_prompt — isolated update"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"system_prompt": "P2 system"}' >/dev/null
assert_store_field prompt              '"P2 prompt"'                "after PUT system_prompt"    || exit 1
assert_store_field system_prompt       '"P2 system"'                "after PUT system_prompt"    || exit 1
assert_store_field enrichment_prompt   '"P0 enrichment"'            "after PUT system_prompt"    || exit 1
assert_store_field output_category     '"P0 Category"'              "after PUT system_prompt"    || exit 1

print_status "wait" "PUT enrichment_prompt — isolated update"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"enrichment_prompt": "P2 enrichment"}' >/dev/null
assert_store_field enrichment_prompt   '"P2 enrichment"'            "after PUT enrichment_prompt" || exit 1
assert_store_field prompt              '"P2 prompt"'                "after PUT enrichment_prompt" || exit 1

print_status "wait" "PUT output_category — isolated update (regression for fix_output_category_reload)"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"output_category": "P2 Category"}' >/dev/null
assert_store_field output_category     '"P2 Category"'              "after PUT output_category"  || exit 1
assert_store_field prompt              '"P2 prompt"'                "after PUT output_category"  || exit 1
assert_store_field vlm_params \
    '{"max_tokens": 256, "num_frames": 5, "temperature": 0.5}' \
    "after PUT output_category" || exit 1

# ── 3. vlm_params partial PUT — deep-merge ──────────────────────────
print_status "wait" "PUT vlm_params={temperature: 0.9} — deep-merge: max_tokens/num_frames preserved"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"vlm_params": {"temperature": 0.9}}' >/dev/null
assert_store_field vlm_params \
    '{"max_tokens": 256, "num_frames": 5, "temperature": 0.9}' \
    "after PUT vlm_params partial" || exit 1

# ── 4. PUT null on optional fields — value cleared ──────────────────
print_status "wait" "PUT system_prompt=null — cleared"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"system_prompt": null}' >/dev/null
assert_store_field system_prompt       'None'                       "after PUT system_prompt=null" || exit 1
assert_store_field prompt              '"P2 prompt"'                "after PUT system_prompt=null" || exit 1
assert_store_field output_category     '"P2 Category"'              "after PUT system_prompt=null" || exit 1

print_status "wait" "PUT enrichment_prompt=null — cleared"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"enrichment_prompt": null}' >/dev/null
assert_store_field enrichment_prompt   'None'                       "after PUT enrichment_prompt=null" || exit 1

print_status "wait" "PUT output_category=null — cleared (regression for fix_output_category_reload)"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"output_category": null}' >/dev/null
assert_store_field output_category     'None'                       "after PUT output_category=null" || exit 1

print_status "wait" "PUT vlm_params=null — cleared"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"vlm_params": null}' >/dev/null
assert_store_field vlm_params          'None'                       "after PUT vlm_params=null" || exit 1
assert_store_field prompt              '"P2 prompt"'                "after PUT vlm_params=null" || exit 1

# ── 5. DELETE — record gone ─────────────────────────────────────────
print_status "wait" "DELETE — record removed"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$BASE/$ALERT_TYPE")
if [ "$HTTP_CODE" != "200" ]; then
    print_status "fail" "DELETE expected 200, got $HTTP_CODE"
    exit 1
fi
GOT=$(store_get)
if [ "$GOT" != "<missing>" ]; then
    print_status "fail" "Record still present after DELETE: $GOT"
    exit 1
fi
print_status "ok" "Record removed after DELETE"

print_status "ok" "PASS: every field overwrites the stored config verbatim"
exit 0
