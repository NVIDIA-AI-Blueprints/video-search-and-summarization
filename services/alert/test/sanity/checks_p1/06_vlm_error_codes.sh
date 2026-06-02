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
# Check 06: VLM Response Codes
# Mode: HTTP
# Description: Verify latest 10 records have valid verificationResponseCode
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="VLM Response Codes"

query='{
  "size": 10,
  "sort": [{"timestamp": "desc"}],
  "_source": ["info.verificationResponseCode"]
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

# Check each doc has a valid response code (any value — 200 is fine, non-200 is fine)
missing=0
codes_200=0
codes_non200=0

for i in $(seq 0 $((docs_sampled - 1))); do
    code=$(echo "$response" | jq -r ".hits.hits[$i]._source.info.verificationResponseCode // empty")
    if [[ -z "$code" ]]; then
        missing=$((missing + 1))
    elif [[ "$code" == "200" ]]; then
        codes_200=$((codes_200 + 1))
    else
        codes_non200=$((codes_non200 + 1))
    fi
done

if [[ "$missing" -eq 0 ]]; then
    pass "$CHECK_NAME" "All $docs_sampled docs have response code (200: $codes_200, non-200: $codes_non200)"
else
    fail "$CHECK_NAME" "$missing of $docs_sampled latest docs missing verificationResponseCode" || true
    exit 1
fi
