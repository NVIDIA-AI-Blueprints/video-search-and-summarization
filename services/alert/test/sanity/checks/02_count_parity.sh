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
# Check 02: Count Parity
# Mode: HTTP
# Description: Compare document counts between incidents and alerts indices
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="Count Parity"

# Get incident count
incidents_response=$(es_query "/${INCIDENTS_INDEX_PATTERN}/_count" || true)
if [[ -z "$incidents_response" ]]; then
    fail "$CHECK_NAME" "Cannot query incidents index" || true
    exit 1
fi
incidents_count=$(echo "$incidents_response" | jq -r '.count // 0')

# Get alerts count
alerts_response=$(es_query "/${ALERTS_INDEX_PATTERN}/_count" || true)
if [[ -z "$alerts_response" ]]; then
    fail "$CHECK_NAME" "Cannot query alerts index" || true
    exit 1
fi
alerts_count=$(echo "$alerts_response" | jq -r '.count // 0')

# Handle zero counts - empty indices is a failure
if [[ "$incidents_count" -eq 0 && "$alerts_count" -eq 0 ]]; then
    fail "$CHECK_NAME" "Both indices empty (0/0) - no data to verify pipeline"
    exit 1
fi

if [[ "$incidents_count" -eq 0 ]]; then
    fail "$CHECK_NAME" "Incidents index is empty but alerts has $alerts_count docs"
    exit 1
fi

# Calculate parity percentage
if [[ "$incidents_count" -gt 0 ]]; then
    parity=$(echo "scale=4; $alerts_count / $incidents_count" | bc)
    parity_pct=$(echo "scale=1; $parity * 100" | bc)
    
    # Check if within tolerance
    min_parity=$(echo "1 - $COUNT_PARITY_TOLERANCE" | bc)
    
    if (( $(echo "$parity >= $min_parity" | bc -l) )); then
        pass "$CHECK_NAME" "${parity_pct}% match ($alerts_count/$incidents_count)"
    else
        fail "$CHECK_NAME" "Only ${parity_pct}% parity ($alerts_count/$incidents_count)"
    fi
else
    fail "$CHECK_NAME" "Cannot calculate parity (incidents=0)"
fi
