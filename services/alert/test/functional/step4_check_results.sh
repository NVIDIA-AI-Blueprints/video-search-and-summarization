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

# Step 4: Check results in Elasticsearch
# Usage: ./step4_check_results.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PID_DIR="${PID_DIR:-/tmp/alert_agent_functional}"

# Elasticsearch settings
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
TIMEOUT="${TIMEOUT:-60}"
INTERVAL="${INTERVAL:-5}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() {
    local status=$1
    local message=$2
    if [ "$status" = "ok" ]; then
        echo -e "${GREEN}✓${NC} $message"
    elif [ "$status" = "fail" ]; then
        echo -e "${RED}✗${NC} $message"
    elif [ "$status" = "wait" ]; then
        echo -e "${YELLOW}⏳${NC} $message"
    elif [ "$status" = "info" ]; then
        echo -e "${BLUE}ℹ${NC} $message"
    else
        echo "  $message"
    fi
}

echo "=== Step 4: Check Results ==="
echo ""

# Get the incident ID suffix from Step 3 if available
ID_SUFFIX=""
if [ -f "$PID_DIR/incident_id_suffix" ]; then
    ID_SUFFIX=$(cat "$PID_DIR/incident_id_suffix")
    print_status "info" "Looking for incident with suffix: $ID_SUFFIX"
fi

# Index to check (today's UTC date — incident timestamps are patched to today in step 3)
TODAY=$(date -u +%Y-%m-%d)
INDICES=(
    "mdx-vlm-incidents-$TODAY"
)

print_status "info" "Elasticsearch: $ES_HOST"
print_status "info" "Timeout: ${TIMEOUT}s (checking every ${INTERVAL}s)"
print_status "info" "Indices to check: ${INDICES[*]}"
echo ""

# Poll for results
print_status "wait" "Polling for results..."

ELAPSED=0
FOUND=false
RESULT_INDEX=""
RESULT_DATA=""

while [ $ELAPSED -lt $TIMEOUT ]; do
    for INDEX in "${INDICES[@]}"; do
        RESPONSE=$(curl -sf "$ES_HOST/$INDEX/_all" 2>/dev/null || echo "")

        if [ -n "$RESPONSE" ] && [ "$RESPONSE" != "[]" ] && [ "$RESPONSE" != "{}" ]; then
            DOC_COUNT=$(echo "$RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if isinstance(data, list):
        print(len(data))
    elif isinstance(data, dict):
        if 'documents' in data:
            print(len(data['documents']))
        elif 'hits' in data:
            print(data['hits'].get('total', {}).get('value', len(data['hits'].get('hits', []))))
        else:
            print(len(data) if data else 0)
    else:
        print(0)
except:
    print(0)
" 2>/dev/null || echo "0")

            if [ "$DOC_COUNT" -gt 0 ]; then
                FOUND=true
                RESULT_INDEX="$INDEX"
                RESULT_DATA="$RESPONSE"
                break 2
            fi
        fi
    done

    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
    echo -ne "\r  Elapsed: ${ELAPSED}s / ${TIMEOUT}s"
done

echo ""
echo ""

if [ "$FOUND" = true ]; then
    print_status "ok" "Documents found in $RESULT_INDEX"
    echo ""

    # Parse and display results
    echo "=== Results ==="
    echo "$RESULT_DATA" | python3 -c "
import sys
import json

try:
    data = json.load(sys.stdin)

    # Handle different response formats from ES simulator
    docs = []
    if isinstance(data, list):
        docs = data
    elif isinstance(data, dict):
        if 'documents' in data:
            # Simulator format: {documents: [{_source: {...}}, ...]}
            docs = [d.get('_source', d) for d in data['documents']]
        elif 'hits' in data:
            # Standard ES format: {hits: {hits: [{_source: {...}}, ...]}}
            docs = [hit.get('_source', hit) for hit in data['hits'].get('hits', [])]
        else:
            docs = [data]

    for i, doc in enumerate(docs[:5]):  # Show first 5
        print(f'Document {i+1}:')

        # Extract key fields (handle nested info object)
        sensor_id = doc.get('sensorId', doc.get('sensor_id', 'N/A'))
        category = doc.get('category', doc.get('incidentType', 'N/A'))
        info = doc.get('info', {})
        verdict = doc.get('verdict', info.get('verdict', doc.get('vlm_verdict', 'N/A')))
        vlm_response = info.get('vlm_response', doc.get('vlm_response', info.get('reasoning', doc.get('reasoning', ''))))

        print(f'  Sensor ID: {sensor_id}')
        print(f'  Category: {category}')
        print(f'  Verdict: {verdict}')

        if vlm_response:
            # Truncate to 100 chars
            if len(vlm_response) > 100:
                vlm_response = vlm_response[:100] + '...'
            print(f'  VLM response: {vlm_response}')
        print()

    if len(docs) > 5:
        print(f'  ... and {len(docs) - 5} more documents')

except Exception as e:
    print(f'Error parsing response: {e}')
    print('Raw response:')
    print(sys.stdin.read()[:500])
"

    echo ""
    echo -e "${GREEN}════════════════════════════════════════${NC}"
    echo -e "${GREEN}  PASS - Functional test completed!     ${NC}"
    echo -e "${GREEN}════════════════════════════════════════${NC}"
    exit 0
else
    print_status "fail" "No documents found within ${TIMEOUT}s"
    echo ""

    # Debug info
    print_status "info" "Debug: All indices in ES simulator..."
    curl -sf "$ES_HOST/status" 2>/dev/null || echo "    (no response from /status)"
    echo ""

    # Show Kafka topic offset (is the message still there?)
    KAFKA_CONTAINER="${KAFKA_CONTAINER:-alert-agent-kafka-test}"
    print_status "info" "Debug: Kafka consumer group lag..."
    docker exec "$KAFKA_CONTAINER" kafka-consumer-groups \
        --bootstrap-server localhost:9092 \
        --describe --group alert-bridge-vlm-group 2>/dev/null || echo "    (no consumer group info)"
    echo ""

    # Show tail of Alert Bridge log
    print_status "info" "Debug: Last 30 lines of Alert Bridge log..."
    tail -30 "$PID_DIR/alert_bridge.log" 2>/dev/null || echo "    (no log file)"
    echo ""

    # Check if Alert Bridge process is alive
    if [ -f "$PID_DIR/alert_bridge.pid" ]; then
        AB_PID=$(cat "$PID_DIR/alert_bridge.pid")
        if kill -0 "$AB_PID" 2>/dev/null; then
            print_status "info" "Alert Bridge still running (PID $AB_PID)"
        else
            print_status "fail" "Alert Bridge process DEAD (PID $AB_PID)"
        fi
    fi

    echo ""
    echo -e "${RED}════════════════════════════════════════${NC}"
    echo -e "${RED}  FAIL - No results found               ${NC}"
    echo -e "${RED}════════════════════════════════════════${NC}"
    exit 1
fi
