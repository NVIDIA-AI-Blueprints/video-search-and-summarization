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
# Check 05: Document Schema
# Mode: HTTP
# Description: Verify latest 10 records have all required fields
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="Document Schema"

query='{
  "size": 10,
  "sort": [{"timestamp": "desc"}]
}'

response=$(es_query "/${ALERTS_INDEX_PATTERN}/_search" "$query" || true)
if [[ -z "$response" ]]; then
    fail "$CHECK_NAME" "Cannot query alerts index" || true
    exit 1
fi

docs_sampled=$(echo "$response" | jq -r '.hits.hits | length')
if [[ "$docs_sampled" -lt 10 ]]; then
    skip_check "$CHECK_NAME" "Fewer than 10 documents ($docs_sampled found)"
    exit 0
fi

# Required fields
REQUIRED_FIELDS=("sensorId" "category" "timestamp")
# Option B: default verification path emits info.reasoning,
# pluggable-parser path emits info.vlm_response; the two are disjoint.
# For each doc we accept either one under the "narrative" slot.
REQUIRED_INFO_FIELDS=("verdict" "verificationResponseCode" "verificationResponseStatus")
NARRATIVE_INFO_FIELDS=("reasoning" "vlm_response")

docs_with_issues=0
all_missing=""

for i in $(seq 0 $((docs_sampled - 1))); do
    doc=$(echo "$response" | jq -r ".hits.hits[$i]._source")
    doc_missing=""

    for field in "${REQUIRED_FIELDS[@]}"; do
        val=$(echo "$doc" | jq -r ".${field} // empty")
        [[ -z "$val" ]] && doc_missing="${doc_missing} ${field}"
    done

    for field in "${REQUIRED_INFO_FIELDS[@]}"; do
        val=$(echo "$doc" | jq -r ".info.${field} // empty")
        [[ -z "$val" ]] && doc_missing="${doc_missing} info.${field}"
    done

    # Option B: at least one narrative field (reasoning OR vlm_response) present.
    narrative_ok=0
    for field in "${NARRATIVE_INFO_FIELDS[@]}"; do
        val=$(echo "$doc" | jq -r ".info.${field} // empty")
        [[ -n "$val" ]] && narrative_ok=1
    done
    if [[ "$narrative_ok" -eq 0 ]]; then
        doc_missing="${doc_missing} info.reasoning|vlm_response"
    fi

    if [[ -n "$doc_missing" ]]; then
        ((docs_with_issues++))
        all_missing="${all_missing}${doc_missing}"
    fi
done

if [[ "$docs_with_issues" -eq 0 ]]; then
    pass "$CHECK_NAME" "All $docs_sampled latest docs have all 7 required fields"
else
    # Deduplicate missing field names
    unique_missing=$(echo "$all_missing" | tr ' ' '\n' | sort -u | tr '\n' ' ')
    fail "$CHECK_NAME" "$docs_with_issues of $docs_sampled docs missing fields:${unique_missing}" || true
    exit 1
fi
