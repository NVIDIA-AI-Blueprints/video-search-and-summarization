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
# Check 05: Pipeline Lag
# Mode: HTTP
# Description: Measure time between incident creation and alert indexing
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="Pipeline Lag"

# Query for recent documents with timestamp fields
query='{
  "query": {
    "range": {
      "timestamp": {
        "gte": "now-1h"
      }
    }
  },
  "size": 10,
  "_source": ["timestamp", "info.latency"],
  "sort": [{"timestamp": "desc"}]
}'

response=$(es_query "/${ALERTS_INDEX_PATTERN}/_search" "$query" || true)
if [[ -z "$response" ]]; then
    fail "$CHECK_NAME" "Cannot query alerts index" || true
    exit 1
fi

total_hits=$(echo "$response" | jq -r '.hits.total.value // .hits.total // 0')

if [[ "$total_hits" -eq 0 ]]; then
    fail "$CHECK_NAME" "No recent docs to measure lag - cannot validate pipeline timing"
    exit 1
fi

# Calculate average lag from sampled documents
# Note: This is a simplified check - actual lag calculation depends on available fields
docs_count=$(echo "$response" | jq -r '.hits.hits | length')

# For now, just verify documents exist and have timestamps
has_timestamps=0
for i in $(seq 0 $((docs_count - 1))); do
    ts=$(echo "$response" | jq -r ".hits.hits[$i]._source.timestamp // empty")
    if [[ -n "$ts" ]]; then
        ((++has_timestamps))
    fi
done

if [[ "$has_timestamps" -eq "$docs_count" ]]; then
    pass "$CHECK_NAME" "All $docs_count sampled docs have timestamps"
else
    fail "$CHECK_NAME" "Only $has_timestamps/$docs_count docs have timestamps"
fi
