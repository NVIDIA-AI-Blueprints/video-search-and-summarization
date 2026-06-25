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

# =============================================================================
# Probe: assert_es_doc_count_delta
# Polls ES UP TO ${POLL_TIMEOUT_SECONDS} until at least one matching doc lands
# (or expected count is 0), then asserts final count equals EXPECTED_COUNT.
#
# Required PROBE_ENV_*:
#   SENSOR_ID
#   EXPECTED_COUNT    integer (e.g. 1 = expect 1 doc, 0 = expect filtered out)
#
# Optional:
#   INDEX                  default mdx-vlm-incidents-*
#   LOOKBACK_MINUTES       default 5
#   POLL_TIMEOUT_SECONDS   default 120
#   POLL_INTERVAL_SECONDS  default 5
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../lib/common.sh"

NAME="es_count sensor=${SENSOR_ID:-?} expect=${EXPECTED_COUNT:-?}"

if [[ -z "${SENSOR_ID:-}" || -z "${EXPECTED_COUNT:-}" ]]; then
    fail "$NAME" "missing required env (SENSOR_ID/EXPECTED_COUNT)" || true
    exit 1
fi

INDEX="${INDEX:-mdx-vlm-incidents-*}"
LOOKBACK="${LOOKBACK_MINUTES:-5}"
POLL_TIMEOUT="${POLL_TIMEOUT_SECONDS:-120}"
POLL_INTERVAL="${POLL_INTERVAL_SECONDS:-5}"

query='{
  "size": 0,
  "track_total_hits": true,
  "query": {
    "bool": {
      "must": [
        { "term":  { "sensorId.keyword": "'"$SENSOR_ID"'" } },
        { "range": { "timestamp": { "gte": "now-'"$LOOKBACK"'m" } } }
      ]
    }
  }
}'

elapsed=0
actual=0
while (( elapsed < POLL_TIMEOUT )); do
    response=$(es_query "/${INDEX}/_search" "$query" || true)
    actual=$(echo "$response" | jq -r '.hits.total.value // .hits.total // 0' 2>/dev/null || echo 0)
    # Stop polling early once we've reached/exceeded expected (or if expected=0,
    # we still need to wait the full timeout to be sure nothing arrives).
    if [[ "$EXPECTED_COUNT" -gt 0 && "$actual" -ge "$EXPECTED_COUNT" ]]; then
        break
    fi
    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
done

if [[ "$actual" -eq "$EXPECTED_COUNT" ]]; then
    pass "$NAME" "got $actual docs (after ${elapsed}s)"
else
    fail "$NAME" "got $actual docs, expected $EXPECTED_COUNT (sensor=$SENSOR_ID, ${LOOKBACK}m, polled ${elapsed}s)" || true
    exit 1
fi
