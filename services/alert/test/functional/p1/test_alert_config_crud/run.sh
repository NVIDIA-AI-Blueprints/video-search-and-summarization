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

# Test: Alert Config CRUD
# Description: Exercise POST/GET/PUT/DELETE on /api/v1/verification/config
#              and verify Redis state via the same API.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

AB_HOST="${AB_HOST:-http://localhost:9080}"
BASE="$AB_HOST/api/v1/verification/config"
ALERT_TYPE="crud_test_$(date +%H%M%S)"

echo "=== P1: Alert Config CRUD ==="
print_status "info" "alert_type: $ALERT_TYPE"

# 1. POST — create new config
print_status "wait" "POST $BASE"
POST_BODY=$(cat <<EOF
{
  "alert_type": "$ALERT_TYPE",
  "prompt": "Initial prompt",
  "system_prompt": "Initial system",
  "vlm_params": {"max_tokens": 256, "num_frames": 5},
  "output_category": "Test Category"
}
EOF
)
HTTP_CODE=$(curl -s -o /tmp/crud_post.json -w "%{http_code}" \
    -X POST "$BASE" -H "Content-Type: application/json" -d "$POST_BODY")
if [ "$HTTP_CODE" != "201" ]; then
    print_status "fail" "POST expected 201, got $HTTP_CODE: $(cat /tmp/crud_post.json)"
    exit 1
fi
print_status "ok" "POST 201"

# 2. POST again — should return 409
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "$BASE" -H "Content-Type: application/json" -d "$POST_BODY")
if [ "$HTTP_CODE" != "409" ]; then
    print_status "fail" "Duplicate POST expected 409, got $HTTP_CODE"
    exit 1
fi
print_status "ok" "Duplicate POST correctly rejected (409)"

# 3. GET single — verify created data
HTTP_CODE=$(curl -s -o /tmp/crud_get.json -w "%{http_code}" "$BASE/$ALERT_TYPE")
if [ "$HTTP_CODE" != "200" ]; then
    print_status "fail" "GET expected 200, got $HTTP_CODE"
    exit 1
fi
RESULT=$(python3 -c "
import json
d = json.load(open('/tmp/crud_get.json'))
ok = d.get('alert_type') == '$ALERT_TYPE' and d.get('prompt') == 'Initial prompt' and d.get('vlm_params', {}).get('max_tokens') == 256
print('OK' if ok else 'FAIL: ' + json.dumps(d))
")
if [ "$RESULT" != "OK" ]; then
    print_status "fail" "GET payload mismatch: $RESULT"
    exit 1
fi
print_status "ok" "GET returned correct data"

# 4. GET list — should include this alert type
HTTP_CODE=$(curl -s -o /tmp/crud_list.json -w "%{http_code}" "$BASE")
if [ "$HTTP_CODE" != "200" ]; then
    print_status "fail" "GET list expected 200, got $HTTP_CODE"
    exit 1
fi
FOUND=$(python3 -c "
import json
d = json.load(open('/tmp/crud_list.json'))
configs = d.get('configs', [])
print('YES' if any(c.get('alert_type') == '$ALERT_TYPE' for c in configs) else 'NO')
")
if [ "$FOUND" != "YES" ]; then
    print_status "fail" "Alert type not in list response"
    exit 1
fi
print_status "ok" "GET list includes alert_type"

# 5. PUT — partial update (only vlm_params + prompt)
PUT_BODY='{"prompt": "Updated prompt", "vlm_params": {"max_tokens": 1024}}'
HTTP_CODE=$(curl -s -o /tmp/crud_put.json -w "%{http_code}" \
    -X PUT "$BASE/$ALERT_TYPE" -H "Content-Type: application/json" -d "$PUT_BODY")
if [ "$HTTP_CODE" != "200" ]; then
    print_status "fail" "PUT expected 200, got $HTTP_CODE"
    exit 1
fi
RESULT=$(python3 -c "
import json
d = json.load(open('/tmp/crud_put.json'))
vp = d.get('vlm_params', {})
# Deep merge: max_tokens updated, num_frames preserved
ok = d.get('prompt') == 'Updated prompt' and vp.get('max_tokens') == 1024 and vp.get('num_frames') == 5 and d.get('system_prompt') == 'Initial system'
print('OK' if ok else 'FAIL: ' + json.dumps(d))
")
if [ "$RESULT" != "OK" ]; then
    print_status "fail" "PUT deep merge failed: $RESULT"
    exit 1
fi
print_status "ok" "PUT deep-merge applied correctly"

# 6. DELETE — remove config
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$BASE/$ALERT_TYPE")
if [ "$HTTP_CODE" != "200" ]; then
    print_status "fail" "DELETE expected 200, got $HTTP_CODE"
    exit 1
fi
print_status "ok" "DELETE 200"

# 7. GET after delete — should return 404
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/$ALERT_TYPE")
if [ "$HTTP_CODE" != "404" ]; then
    print_status "fail" "GET after DELETE expected 404, got $HTTP_CODE"
    exit 1
fi
print_status "ok" "GET after DELETE returns 404"

# 8. Validation: typo in vlm_params should be rejected (extra=forbid)
BAD_BODY=$(cat <<EOF
{
  "alert_type": "${ALERT_TYPE}_bad",
  "prompt": "test",
  "vlm_params": {"max_token": 256}
}
EOF
)
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "$BASE" -H "Content-Type: application/json" -d "$BAD_BODY")
if [ "$HTTP_CODE" != "422" ]; then
    print_status "fail" "Typo vlm_params expected 422, got $HTTP_CODE"
    exit 1
fi
print_status "ok" "Typo in vlm_params rejected with 422"

print_status "ok" "PASS: All CRUD operations behave correctly"
exit 0
