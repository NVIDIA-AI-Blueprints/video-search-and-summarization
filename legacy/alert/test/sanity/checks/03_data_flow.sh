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
# Check 03: Recent Data Flow
# Mode: HTTP
# Description: Verify documents are being indexed recently
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="Recent Data Flow"

# Query for recent documents
query='{
  "query": {
    "range": {
      "timestamp": {
        "gte": "now-'"${MIN_RECENT_DOCS_MINUTES}"'m"
      }
    }
  },
  "size": 0
}'

response=$(es_query "/${ALERTS_INDEX_PATTERN}/_search" "$query" || true)
if [[ -z "$response" ]]; then
    fail "$CHECK_NAME" "Cannot query alerts index" || true
    exit 1
fi

total_hits=$(echo "$response" | jq -r '.hits.total.value // .hits.total // 0')

if [[ "$total_hits" -gt 0 ]]; then
    pass "$CHECK_NAME" "$total_hits docs in last ${MIN_RECENT_DOCS_MINUTES}min"
else
    # No recent data is a failure - pipeline should be producing data
    fail "$CHECK_NAME" "0 docs in last ${MIN_RECENT_DOCS_MINUTES}min - no data flow"
fi
