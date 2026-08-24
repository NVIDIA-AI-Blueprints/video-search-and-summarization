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
# Check 04: Verification Response Codes
# Mode: HTTP
# Description: Sample recent documents and verify VLM responses are present
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="Verification Codes"

# Query for recent documents with VLM response fields
query='{
  "query": {
    "range": {
      "timestamp": {
        "gte": "now-1h"
      }
    }
  },
  "size": '"${VERIFICATION_SAMPLE_SIZE}"',
  "_source": ["info.verdict", "info.reasoning", "info.vlm_response", "info.verificationResponseCode", "info.verificationResponseStatus"]
}'

response=$(es_query "/${ALERTS_INDEX_PATTERN}/_search" "$query" || true)
if [[ -z "$response" ]]; then
    fail "$CHECK_NAME" "Cannot query alerts index" || true
    exit 1
fi

total_hits=$(echo "$response" | jq -r '.hits.total.value // .hits.total // 0')

if [[ "$total_hits" -eq 0 ]]; then
    fail "$CHECK_NAME" "No recent docs to verify - cannot validate VLM responses"
    exit 1
fi

# Count documents with valid VLM response
docs_with_vlm=0
docs_sampled=$(echo "$response" | jq -r '.hits.hits | length')

for i in $(seq 0 $((docs_sampled - 1))); do
    doc=$(echo "$response" | jq -r ".hits.hits[$i]._source")
    
    # Check for any VLM response indicator.
    # Option B: default path emits info.reasoning,
    # pluggable-parser path emits info.vlm_response — accept either.
    has_verdict=$(echo "$doc" | jq -r '.info.verdict // empty')
    has_reasoning=$(echo "$doc" | jq -r '.info.reasoning // empty')
    has_vlm_response=$(echo "$doc" | jq -r '.info.vlm_response // empty')
    has_response_code=$(echo "$doc" | jq -r '.info.verificationResponseCode // empty')

    if [[ -n "$has_verdict" || -n "$has_reasoning" || -n "$has_vlm_response" || -n "$has_response_code" ]]; then
        ((++docs_with_vlm))
    fi
done

if [[ "$docs_sampled" -gt 0 ]]; then
    pct=$((docs_with_vlm * 100 / docs_sampled))
    
    if [[ "$docs_with_vlm" -eq "$docs_sampled" ]]; then
        pass "$CHECK_NAME" "100% valid ($docs_with_vlm/$docs_sampled sampled)"
    elif [[ "$pct" -ge 80 ]]; then
        pass "$CHECK_NAME" "${pct}% valid ($docs_with_vlm/$docs_sampled sampled)"
    else
        fail "$CHECK_NAME" "Only ${pct}% have VLM response ($docs_with_vlm/$docs_sampled)"
    fi
else
    fail "$CHECK_NAME" "No documents to sample - cannot validate VLM responses"
fi
