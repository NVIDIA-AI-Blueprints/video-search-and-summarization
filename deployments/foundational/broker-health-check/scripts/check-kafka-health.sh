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

set -euo pipefail

echo " Kafka health check service started..."

# Configuration with defaults
MAX_RETRIES=${MAX_RETRIES:-60}                        # Max retries for broker and topics
RETRY_INTERVAL=${RETRY_INTERVAL:-2}                   # Seconds between retries
KAFKA_HOST=${BOOTSTRAP_HOST:-localhost}
KAFKA_PORT=${KAFKA_PORT:-9092}

echo "Configuration:"
echo "  MAX_RETRIES: $MAX_RETRIES ($(($MAX_RETRIES * $RETRY_INTERVAL))s timeout)"
echo "  RETRY_INTERVAL: ${RETRY_INTERVAL}s"
echo "  KAFKA_HOST: $KAFKA_HOST"
echo "  KAFKA_PORT: $KAFKA_PORT"

# Add jq to PATH if it exists
if [ -f /home/appuser/jqbin/jq ]; then
    export PATH="/home/appuser/jqbin:${PATH}"
fi

# Function to parse topics/streams from JSON environment variable
parse_topics_from_json() {
    local json_var="$1"
    if [ -n "$json_var" ]; then
        echo "$json_var" | jq -r '.[].name'
    fi
}

echo "Waiting for Kafka topics to be created..."

# Parse Kafka topics from environment variable
if [ -n "$KAFKA_TOPICS" ]; then
    echo "Parsing topics from KAFKA_TOPICS environment variable..."
    readarray -t REQUIRED_KAFKA_TOPICS < <(parse_topics_from_json "$KAFKA_TOPICS")
    echo "Found ${#REQUIRED_KAFKA_TOPICS[@]} topics to check"
else
    echo "WARNING: No KAFKA_TOPICS environment variable found, skipping topic validation"
    REQUIRED_KAFKA_TOPICS=()
fi

# Wait for Kafka to be reachable first
kafka_retry_count=0

echo "Waiting for Kafka at $KAFKA_HOST:$KAFKA_PORT (max ${MAX_RETRIES} retries)..."

while [ $kafka_retry_count -lt $MAX_RETRIES ]; do
    if kafka-broker-api-versions --bootstrap-server $KAFKA_HOST:$KAFKA_PORT >/dev/null 2>&1; then
        echo "✓ Kafka broker is reachable after $kafka_retry_count retries"
        break
    fi
    
    kafka_retry_count=$((kafka_retry_count + 1))
    echo "[$kafka_retry_count/$MAX_RETRIES] Waiting for Kafka to be ready..."
    sleep $RETRY_INTERVAL
done

if [ $kafka_retry_count -eq $MAX_RETRIES ]; then
    echo "❌ ERROR: Kafka broker at $KAFKA_HOST:$KAFKA_PORT is not reachable after $MAX_RETRIES retries with $RETRY_INTERVAL seconds interval"
    exit 1
fi

# If we have topics to check, wait for them to exist
if [ ${#REQUIRED_KAFKA_TOPICS[@]} -gt 0 ]; then
    echo "Checking for required Kafka topics: ${REQUIRED_KAFKA_TOPICS[*]}"
    topic_retry_count=0
    
    while [ $topic_retry_count -lt $MAX_RETRIES ]; do
        missing_topics=()
        
        for topic in "${REQUIRED_KAFKA_TOPICS[@]}"; do
            if ! kafka-topics --bootstrap-server $KAFKA_HOST:$KAFKA_PORT --list 2>/dev/null | grep -q "^${topic}$"; then
                missing_topics+=("$topic")
            fi
        done
        
        if [ ${#missing_topics[@]} -eq 0 ]; then
            echo "✓ All required Kafka topics are present after $topic_retry_count retries"
            
            # List all topics for verification
            echo "Current Kafka topics:"
            kafka-topics --bootstrap-server $KAFKA_HOST:$KAFKA_PORT --list 2>/dev/null | while read topic; do
                echo "  - $topic"
            done
            break
        else
            topic_retry_count=$((topic_retry_count + 1))
            echo "[$topic_retry_count/$MAX_RETRIES] Waiting for missing topics: ${missing_topics[*]}"
            sleep $RETRY_INTERVAL
        fi
    done
    
    if [ $topic_retry_count -eq $MAX_RETRIES ]; then
        echo "❌ ERROR: Required Kafka topics not created after $MAX_RETRIES retries with $RETRY_INTERVAL seconds interval"
        echo "Missing topics: ${missing_topics[*]}"
        echo ""
        echo "Existing topics:"
        kafka-topics --bootstrap-server $KAFKA_HOST:$KAFKA_PORT --list 2>/dev/null | while read topic; do
            echo "  - $topic"
        done
        exit 1
    fi
else
    echo "No topics to validate, listing existing topics:"
    kafka-topics --bootstrap-server $KAFKA_HOST:$KAFKA_PORT --list 2>/dev/null | while read topic; do
        echo "  - $topic"
    done
fi

echo "✅ Kafka health check completed successfully"
exit 0

