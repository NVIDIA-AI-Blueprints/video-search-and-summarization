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
# Check 01: Document Parity
# Mode: HTTP
# Description: Verify latest 10 incident IDs exist in the VLM alerts index
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="Document Parity"

# Get latest 10 IDs from incidents index
response=$(es_query "/${INCIDENTS_INDEX_PATTERN}/_search" \
    '{"size": 10, "sort": [{"timestamp": "desc"}], "_source": false}' || true)
if [[ -z "$response" ]]; then
    fail "$CHECK_NAME" "Cannot query incidents index" || true
    exit 1
fi

total=$(echo "$response" | jq -r '.hits.total.value // 0')
if [[ "$total" -lt 10 ]]; then
    skip_check "$CHECK_NAME" "Fewer than 10 documents in incidents index ($total found)"
    exit 0
fi

# Check each ID in the VLM index
missing=0
checked=0
for id in $(echo "$response" | jq -r '.hits.hits[]._id'); do
    query="{\"query\": {\"ids\": {\"values\": [\"${id}\"]}}}"
    match=$(es_query "/${ALERTS_INDEX_PATTERN}/_search" "$query" || true)
    match_count=$(echo "$match" | jq -r '.hits.total.value // 0')
    if [[ "$match_count" -eq 0 ]]; then
        missing=$((missing + 1))
    fi
    checked=$((checked + 1))
done

if [[ "$missing" -eq 0 ]]; then
    pass "$CHECK_NAME" "All $checked latest IDs found in VLM index"
else
    fail "$CHECK_NAME" "$missing of $checked latest IDs not found in VLM index" || true
    exit 1
fi
