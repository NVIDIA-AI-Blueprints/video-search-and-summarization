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

# Test: Verdict Distribution
# Description: Verify 3 distinct incidents all receive non-null VLM verdicts
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"
TEST_NAME="verdict_distribution"

echo "=== P1: Verdict Distribution ==="

mkdir -p "$PID_DIR"

# 1. Produce 3 incidents with DIFFERENT timestamps (different fingerprints to avoid dedup)
#    Fingerprint = primaryObjectId + category + sensorId + timestamp
#    Each needs unique timestamp to create unique fingerprint
for i in 1 2 3; do
    PATCHED="$PID_DIR/patched_${TEST_NAME}_${i}.json"
    python3 -c "
import json
from datetime import datetime, timezone, timedelta
with open('$PAYLOAD') as f: data = json.load(f)
ts = datetime.now(timezone.utc) + timedelta(seconds=$i)
data['timestamp'] = ts.strftime('%Y-%m-%dT%H:%M:%S.000Z')
data['end'] = data['timestamp']
with open('$PATCHED', 'w') as f: json.dump(data, f)
"
    produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "p1_verdict_${i}" --no-patch
done
print_status "info" "Produced 3 incidents (each with unique timestamp)"

# 3. Wait for all 3 to be processed
sleep 15

# 4. Get all docs
ALL_DOCS=$(get_all_es_docs "$ES_HOST")
DOC_COUNT=$(echo "$ALL_DOCS" | python3 -c "
import sys, json
docs = json.load(sys.stdin)
print(len(docs))
" 2>/dev/null || echo "0")

if [ "$DOC_COUNT" -lt 3 ]; then
    print_status "fail" "FAIL: Expected at least 3 docs, found $DOC_COUNT"
    exit 1
fi

# 5. Check that the last 3 docs all have non-null verdict
VERDICT_CHECK=$(echo "$ALL_DOCS" | python3 -c "
import sys, json
docs = json.load(sys.stdin)
# Check last 3 docs
check_docs = docs[-3:] if len(docs) >= 3 else docs
missing = []
for i, doc in enumerate(check_docs):
    info = doc.get('info', {})
    verdict = info.get('verdict', doc.get('verdict', doc.get('vlm_verdict')))
    if verdict is None or verdict == '' or verdict == 'null':
        missing.append(i)
if missing:
    print('MISSING:' + ','.join(str(i) for i in missing))
else:
    print('OK')
" 2>/dev/null || echo "ERROR")

if [ "$VERDICT_CHECK" = "OK" ]; then
    print_status "ok" "PASS: All 3 incidents have non-null verdicts"
    exit 0
else
    print_status "fail" "FAIL: Some verdicts are null or missing: $VERDICT_CHECK"
    exit 1
fi
