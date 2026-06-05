#!/usr/bin/env bash
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

# Setup or remove indexed_at pipeline for mdx-vlm-incidents-*

set -euo pipefail

HOST="${1:-}"
ACTION="${2:-}"
ES="http://${HOST}:9200"

if [ -z "$HOST" ]; then
    echo "Usage: $0 <host> [--delete]"
    echo ""
    echo "Examples:"
    echo "  $0 localhost           # Setup indexed_at pipeline"
    echo "  $0 localhost --delete  # Remove indexed_at pipeline"
    exit 1
fi

if ! command -v jq &>/dev/null; then
    echo "ERROR: jq is required but not installed"
    exit 1
fi

# Check ES connectivity
if ! curl -s -m 5 "${ES}/" &>/dev/null; then
    echo "ERROR: Failed to connect to Elasticsearch at ${ES}"
    exit 1
fi

if [ "$ACTION" = "--delete" ]; then
    echo "=== Removing indexed_at setup ==="
    echo ""
    
    # 1. Remove pipeline from existing indexes
    echo "Removing pipeline from existing indexes..."
    curl -s -X PUT "${ES}/mdx-vlm-incidents-*/_settings" \
      -H "Content-Type: application/json" \
      -d '{"index.default_pipeline": null}' 2>/dev/null || true
    echo ""
    
    # 2. Delete template
    echo "Deleting template..."
    curl -s -X DELETE "${ES}/_index_template/mdx-vlm-incidents-indexed-at"
    echo ""
    
    # 3. Delete pipeline
    echo "Deleting pipeline..."
    curl -s -X DELETE "${ES}/_ingest/pipeline/add-indexed-at"
    echo ""
    
    echo ""
    echo "=== Cleanup Done ==="
else
    echo "=== Setting up indexed_at for mdx-vlm-incidents-* ==="
    echo ""
    
    # 1. Create pipeline
    echo "Creating pipeline..."
    RESPONSE=$(curl -s -X PUT "${ES}/_ingest/pipeline/add-indexed-at" \
      -H "Content-Type: application/json" \
      -d '{"processors": [{"set": {"field": "info.indexedAt", "value": "{{_ingest.timestamp}}"}}]}')
    echo "$RESPONSE"
    if ! echo "$RESPONSE" | jq -e '.acknowledged == true' &>/dev/null; then
        echo "ERROR: Failed to create pipeline"
        exit 1
    fi
    
    # 2. Get base template settings and create new template with indexed_at added
    # NB: info is map<string,string> in protobuf; ES maps indexedAt as
    # "date" for range queries.  The ISO-8601 string satisfies both.
    echo "Creating template (inheriting from metropolis_template)..."
    BASE_SETTINGS=$(curl -s "${ES}/_index_template/metropolis_template" | \
      jq '.index_templates[0].index_template.template.settings // {}')
    
    if [ "$BASE_SETTINGS" = "{}" ]; then
        echo "WARNING: metropolis_template not found, using empty base settings"
    fi
    
    RESPONSE=$(curl -s -X PUT "${ES}/_index_template/mdx-vlm-incidents-indexed-at" \
      -H "Content-Type: application/json" \
      -d "{
        \"index_patterns\": [\"mdx-vlm-incidents-*\"],
        \"template\": {
          \"settings\": $(echo "$BASE_SETTINGS" | jq '. + {"index.default_pipeline": "add-indexed-at"}'),
          \"mappings\": {\"properties\": {\"info\": {\"properties\": {\"indexedAt\": {\"type\": \"date\"}}}}}
        },
        \"priority\": 600
      }")
    echo "$RESPONSE"
    if ! echo "$RESPONSE" | jq -e '.acknowledged == true' &>/dev/null; then
        echo "ERROR: Failed to create template"
        exit 1
    fi
    
    # 3. Apply to existing indexes
    echo "Applying to existing indexes..."
    curl -s -X PUT "${ES}/mdx-vlm-incidents-*/_settings" \
      -H "Content-Type: application/json" \
      -d '{"index.default_pipeline": "add-indexed-at"}' 2>/dev/null || true
    echo ""
    
    echo ""
    echo "=== Done ==="
fi
