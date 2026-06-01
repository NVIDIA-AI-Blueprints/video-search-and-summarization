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

# Step 3: Trigger an incident by sending to Kafka
# Usage: ./step3_trigger_incident.sh
#        ./step3_trigger_incident.sh --payload path/to/custom.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PID_DIR="${PID_DIR:-/tmp/alert_agent_functional}"

# Default payload
PAYLOAD="${PAYLOAD:-test/protobuf/test_data/sample_incident.json}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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
    else
        echo "  $message"
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --payload)
            PAYLOAD="$2"
            shift 2
            ;;
        --bootstrap)
            BOOTSTRAP="$2"
            shift 2
            ;;
        --topic)
            TOPIC="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--payload FILE] [--bootstrap HOST:PORT] [--topic TOPIC]"
            exit 1
            ;;
    esac
done

mkdir -p "$PID_DIR"

echo "=== Step 3: Trigger Incident ==="
echo ""

cd "$REPO_ROOT"

# Generate unique ID suffix based on timestamp (no leading hyphen to avoid argparse issues)
ID_SUFFIX="_test_$(date +%Y%m%d_%H%M%S)"
echo "$ID_SUFFIX" > "$PID_DIR/incident_id_suffix"

# Patch the payload timestamps to today's UTC date so the ES daily index
# (mdx-vlm-incidents-YYYY-MM-DD) uses today's date and step 4 can find it.
PATCHED_PAYLOAD="$PID_DIR/patched_incident.json"
python3 -c "
import json, sys
from datetime import datetime, timezone
with open('$PAYLOAD') as f:
    data = json.load(f)
now = datetime.now(timezone.utc)
today = now.strftime('%Y-%m-%dT%H:%M:%S.000Z')
data['timestamp'] = today
data['end'] = today
with open('$PATCHED_PAYLOAD', 'w') as f:
    json.dump(data, f, indent=2)
"

print_status "info" "Payload: $PAYLOAD (patched timestamps to today)"
print_status "info" "Bootstrap: $BOOTSTRAP"
print_status "info" "Topic: $TOPIC"
print_status "info" "ID Suffix: $ID_SUFFIX"
echo ""

print_status "wait" "Sending incident to Kafka..."

if python3 test/protobuf/produce_incident.py \
    --bootstrap "$BOOTSTRAP" \
    --topic "$TOPIC" \
    --payload "$PATCHED_PAYLOAD" \
    --id-suffix "$ID_SUFFIX"; then

    print_status "ok" "Incident sent successfully"
    echo ""
    echo "    ID Suffix: $ID_SUFFIX"
    echo "    Saved to: $PID_DIR/incident_id_suffix"

    # --- Verify message is readable on the topic ---
    KAFKA_CONTAINER="${KAFKA_CONTAINER:-alert-agent-kafka-test}"
    print_status "wait" "Verifying message on topic $TOPIC ..."
    MSG_COUNT=$(docker exec "$KAFKA_CONTAINER" kafka-console-consumer \
        --bootstrap-server localhost:9092 \
        --topic "$TOPIC" \
        --from-beginning \
        --max-messages 1 \
        --timeout-ms 10000 2>/dev/null | wc -l)

    if [ "$MSG_COUNT" -ge 1 ]; then
        print_status "ok" "Verified: $MSG_COUNT message(s) readable on $TOPIC"
    else
        print_status "fail" "Message NOT readable on $TOPIC (kafka-console-consumer returned 0 messages)"
        exit 1
    fi

    echo ""
    print_status "ok" "Step 3 complete - incident triggered"
else
    print_status "fail" "Failed to send incident"
    exit 1
fi
