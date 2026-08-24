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
# Check 02: Verdict Validation
# Mode: HTTP
# Description: Verify latest 10 records have valid non-null verdict values
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="Verdict Validation"

query='{
  "size": 10,
  "sort": [{"timestamp": "desc"}],
  "_source": ["info.verdict"]
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

# Count docs with valid non-null verdict
valid=0
invalid=0
for i in $(seq 0 $((docs_sampled - 1))); do
    verdict=$(echo "$response" | jq -r ".hits.hits[$i]._source.info.verdict // empty")
    if [[ -n "$verdict" ]]; then
        valid=$((valid + 1))
    else
        invalid=$((invalid + 1))
    fi
done

if [[ "$invalid" -eq 0 ]]; then
    pass "$CHECK_NAME" "All $valid latest docs have valid verdict"
else
    fail "$CHECK_NAME" "$invalid of $docs_sampled latest docs missing verdict" || true
    exit 1
fi
