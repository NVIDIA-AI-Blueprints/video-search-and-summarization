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
# Probe: assert_es_field_value
# Polls ES UP TO ${POLL_TIMEOUT_SECONDS} for a doc matching ${SENSOR_ID},
# then asserts ${FIELD} equals ${EXPECTED_VALUE}.
#
# Required PROBE_ENV_*:
#   SENSOR_ID         exact sensorId scope (synthetic, RUN_ID-scoped)
#   FIELD             dot-notation field on _source (e.g. "category", "info.verdict")
#   EXPECTED_VALUE    exact string match
#
# Optional:
#   INDEX                  default mdx-vlm-incidents-*
#   LOOKBACK_MINUTES       default 5
#   POLL_TIMEOUT_SECONDS   default 120  (max wall-time waiting for doc to land)
#   POLL_INTERVAL_SECONDS  default 5    (between ES queries while waiting)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../lib/common.sh"

NAME="es_field ${FIELD:-?}=${EXPECTED_VALUE:-?}"

if [[ -z "${SENSOR_ID:-}" || -z "${FIELD:-}" || -z "${EXPECTED_VALUE:-}" ]]; then
    fail "$NAME" "missing required env (SENSOR_ID/FIELD/EXPECTED_VALUE)" || true
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
while (( elapsed < POLL_TIMEOUT )); do
    response=$(es_query "/${INDEX}/_search" "$query" || true)
    hits=$(echo "$response" | jq -r '.hits.total.value // .hits.total // 0' 2>/dev/null || echo 0)
    if [[ "$hits" -gt 0 ]]; then
        actual=$(echo "$response" | jq -r ".hits.hits[0]._source.${FIELD} // empty")
        if [[ "$actual" == "$EXPECTED_VALUE" ]]; then
            pass "$NAME" "doc for sensor=$SENSOR_ID has $FIELD=\"$actual\" (after ${elapsed}s)"
            exit 0
        else
            fail "$NAME" "got $FIELD=\"$actual\" expected \"$EXPECTED_VALUE\" (sensor=$SENSOR_ID)" || true
            exit 1
        fi
    fi
    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
done

fail "$NAME" "no doc with sensorId=$SENSOR_ID after ${POLL_TIMEOUT}s polling" || true
exit 1
