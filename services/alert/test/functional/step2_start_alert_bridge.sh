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

# Step 2: Start Alert Bridge
# Usage: ./step2_start_alert_bridge.sh
#        USE_DOCKER=true ./step2_start_alert_bridge.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PID_DIR="${PID_DIR:-/tmp/alert_agent_functional}"
CONFIG_FILE="${CONFIG_FILE:-test/e2e/kafka_incident/config_sim.yaml}"

# Docker settings
USE_DOCKER="${USE_DOCKER:-false}"
DOCKER_IMAGE="${DOCKER_IMAGE:-alert-bridge:local}"
DOCKER_CONTAINER="alert-bridge-functional-test"

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

mkdir -p "$PID_DIR"

echo "=== Step 2: Starting Alert Bridge ==="
echo ""

cd "$REPO_ROOT"

if [ "$USE_DOCKER" = "true" ]; then
    # --- Docker mode ---
    print_status "wait" "Running Alert Bridge in Docker..."

    # Check if image exists, build if not
    if ! docker image inspect "$DOCKER_IMAGE" >/dev/null 2>&1; then
        print_status "wait" "Building Docker image..."
        docker build -t "$DOCKER_IMAGE" .
    fi

    # Remove existing container
    docker rm -f "$DOCKER_CONTAINER" 2>/dev/null || true

    # Start container with host network for simulator access
    docker run -d \
        --name "$DOCKER_CONTAINER" \
        --network host \
        -v "$REPO_ROOT/$CONFIG_FILE:/app/config.yaml:ro" \
        "$DOCKER_IMAGE" \
        --config /app/config.yaml

    echo "$DOCKER_CONTAINER" > "$PID_DIR/alert_bridge_container"

    print_status "wait" "Waiting for container startup..."
    sleep 5

    if docker ps | grep -q "$DOCKER_CONTAINER"; then
        CONTAINER_ID=$(docker ps -q -f name="$DOCKER_CONTAINER")
        print_status "ok" "Alert Bridge running in Docker"
        echo "    Container: $DOCKER_CONTAINER"
        echo "    Container ID: $CONTAINER_ID"
    else
        print_status "fail" "Alert Bridge container failed to start"
        echo "    Check logs: docker logs $DOCKER_CONTAINER"
        exit 1
    fi
else
    # --- Source mode (default) ---
    print_status "wait" "Running Alert Bridge from source..."

    # Check config file exists
    if [ ! -f "$CONFIG_FILE" ]; then
        print_status "fail" "Config file not found: $CONFIG_FILE"
        exit 1
    fi

    print_status "info" "Using config: $CONFIG_FILE"

    # Start Alert Bridge in background
    python3 enhance_alert_with_vlm.py --config "$CONFIG_FILE" > "$PID_DIR/alert_bridge.log" 2>&1 &
    AB_PID=$!
    echo "$AB_PID" > "$PID_DIR/alert_bridge.pid"

    # Allow enough time for Kafka consumer group rebalance and offset resolution.
    # The bridge creates two consumers in the same group which triggers a rebalance;
    # "latest" offset must be resolved BEFORE any message is produced in step 3.
    print_status "wait" "Waiting for startup and consumer group stabilization (15s)..."
    sleep 15

    # Check if still running
    if kill -0 "$AB_PID" 2>/dev/null; then
        print_status "ok" "Alert Bridge running"
        echo "    PID: $AB_PID"
        echo "    Config: $CONFIG_FILE"
        echo "    Log: $PID_DIR/alert_bridge.log"
    else
        print_status "fail" "Alert Bridge failed to start"
        echo "    Check log: $PID_DIR/alert_bridge.log"
        tail -20 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
        exit 1
    fi
fi

echo ""
print_status "ok" "Step 2 complete - Alert Bridge is running"
