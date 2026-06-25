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
# Check 01: Elasticsearch Connectivity
# Mode: HTTP
# Description: Verify Elasticsearch cluster is healthy and reachable
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="Elasticsearch Connectivity"

# Execute check
response=$(es_query "/_cluster/health" || true)
if [[ -z "$response" ]]; then
    fail "$CHECK_NAME" "Cannot connect to Elasticsearch at $(es_url)" || true
    exit 1
fi

status=$(echo "$response" | jq -r '.status // "unknown"')
cluster_name=$(echo "$response" | jq -r '.cluster_name // "unknown"')
node_count=$(echo "$response" | jq -r '.number_of_nodes // 0')

if [[ "$status" == "green" || "$status" == "yellow" ]]; then
    pass "$CHECK_NAME" "Cluster '$cluster_name' is $status ($node_count nodes)"
else
    fail "$CHECK_NAME" "Cluster status is '$status' (expected green/yellow)"
fi
