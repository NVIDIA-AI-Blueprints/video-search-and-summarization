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
# Check 07: Verdict Protection Fingerprints
# Mode: HTTP
# Description: Verify verdict protection fingerprints are present at scale
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
source "$SCRIPT_DIR/config_p1.env"

CHECK_NAME="Verdict Protection"

# Get total count
total_response=$(es_query "/${ALERTS_INDEX_PATTERN}/_count" || true)
if [[ -z "$total_response" ]]; then
    fail "$CHECK_NAME" "Cannot query alerts index" || true
    exit 1
fi

total=$(echo "$total_response" | jq -r '.count // 0')

if [[ "$total" -lt 100 ]]; then
    skip_check "$CHECK_NAME" "Too few documents to assess protection fingerprints ($total found, need 100)"
    exit 0
fi

# Count docs with protectionFingerprint
protection_query='{
  "query": {
    "exists": {
      "field": "info.protectionFingerprint"
    }
  }
}'

protection_response=$(es_query "/${ALERTS_INDEX_PATTERN}/_count" "$protection_query" || true)
if [[ -z "$protection_response" ]]; then
    fail "$CHECK_NAME" "Cannot query for protectionFingerprint" || true
    exit 1
fi

protection_count=$(echo "$protection_response" | jq -r '.count // 0')

if [[ "$protection_count" -ge 1 ]]; then
    pass "$CHECK_NAME" "$protection_count/$total docs have protectionFingerprint"
else
    fail "$CHECK_NAME" "No protectionFingerprint found in $total docs" || true
    exit 1
fi
