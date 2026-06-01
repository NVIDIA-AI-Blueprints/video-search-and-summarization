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
# Probe: assert_es_doc_has_key
# Polls ES UP TO ${POLL_TIMEOUT_SECONDS} for a doc matching ${SENSOR_ID},
# then asserts each key in $KEYS is present and non-null.
#
# Required PROBE_ENV_*:
#   SENSOR_ID
#   KEYS        comma-separated dot-notation keys
#
# Optional:
#   INDEX                  default mdx-vlm-incidents-*
#   LOOKBACK_MINUTES       default 5
#   POLL_TIMEOUT_SECONDS   default 120
#   POLL_INTERVAL_SECONDS  default 5
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../lib/common.sh"

NAME="es_doc_has_keys ${KEYS:-?}"

if [[ -z "${SENSOR_ID:-}" || -z "${KEYS:-}" ]]; then
    fail "$NAME" "missing required env (SENSOR_ID/KEYS)" || true
    exit 1
fi

INDEX="${INDEX:-mdx-vlm-incidents-*}"
LOOKBACK="${LOOKBACK_MINUTES:-5}"
POLL_TIMEOUT="${POLL_TIMEOUT_SECONDS:-120}"
POLL_INTERVAL="${POLL_INTERVAL_SECONDS:-5}"

query='{
  "size": 1,
  "sort": [{"timestamp": "desc"}],
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
doc=""
while (( elapsed < POLL_TIMEOUT )); do
    response=$(es_query "/${INDEX}/_search" "$query" || true)
    hits=$(echo "$response" | jq -r '.hits.total.value // .hits.total // 0' 2>/dev/null || echo 0)
    if [[ "$hits" -gt 0 ]]; then
        doc=$(echo "$response" | jq -r '.hits.hits[0]._source')
        break
    fi
    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
done

if [[ -z "$doc" ]]; then
    fail "$NAME" "no doc with sensorId=$SENSOR_ID after ${POLL_TIMEOUT}s polling" || true
    exit 1
fi

missing=()
IFS=',' read -ra wanted <<< "$KEYS"
for k in "${wanted[@]}"; do
    k="${k## }"; k="${k%% }"
    val=$(echo "$doc" | jq -r ".${k} // empty")
    [[ -z "$val" ]] && missing+=("$k")
done

if [[ ${#missing[@]} -eq 0 ]]; then
    pass "$NAME" "all ${#wanted[@]} keys present (sensor=$SENSOR_ID, after ${elapsed}s)"
else
    fail "$NAME" "missing keys: ${missing[*]} (sensor=$SENSOR_ID)" || true
    exit 1
fi
