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

# Step 1: Start Kafka and all simulators
# Usage: ./step1_start_simulators.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PID_DIR="${PID_DIR:-/tmp/alert_agent_functional}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

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

check_port() {
    local host=$1
    local port=$2
    nc -z "$host" "$port" 2>/dev/null
}

check_http() {
    local url=$1
    curl -sf "$url" >/dev/null 2>&1
}

wait_for_service() {
    local name=$1
    local check_cmd=$2
    local timeout=${3:-30}
    local interval=${4:-1}

    local elapsed=0
    while [ $elapsed -lt $timeout ]; do
        if eval "$check_cmd"; then
            return 0
        fi
        sleep $interval
        elapsed=$((elapsed + interval))
    done
    return 1
}

mkdir -p "$PID_DIR"

echo "=== Step 1: Starting Simulators ==="
echo ""

# --- Redis ---
print_status "wait" "Checking Redis on :6379..."

if check_port 127.0.0.1 6379; then
    print_status "ok" "Redis already running on :6379"
else
    print_status "wait" "Starting Redis container..."

    REDIS_CONTAINER="alert-agent-redis-test"

    # Clean up existing container if present
    docker rm -f "$REDIS_CONTAINER" 2>/dev/null || true

    # Start Redis (with JSON module for prompt storage)
    docker run -d \
        --name "$REDIS_CONTAINER" \
        -p 6379:6379 \
        redis/redis-stack-server:7.2.0-v9 >/dev/null

    echo "$REDIS_CONTAINER" > "$PID_DIR/redis_container"

    print_status "wait" "Waiting for Redis to be ready..."
    if wait_for_service "redis" "check_port 127.0.0.1 6379" 30; then
        print_status "ok" "Redis ready"
    else
        print_status "fail" "Redis failed to start"
        exit 1
    fi
fi

# --- Kafka ---
print_status "wait" "Checking Kafka on :9092..."

if check_port 127.0.0.1 9092; then
    print_status "ok" "Kafka already running on :9092"
else
    print_status "wait" "Starting Kafka container..."

    KAFKA_CONTAINER="alert-agent-kafka-test"

    # Clean up existing container if present
    docker rm -f "$KAFKA_CONTAINER" 2>/dev/null || true

    # Start Kafka
    docker run -d \
        --name "$KAFKA_CONTAINER" \
        -p 9092:9092 \
        -e KAFKA_BROKER_ID=1 \
        -e KAFKA_PROCESS_ROLES=broker,controller \
        -e KAFKA_NODE_ID=1 \
        -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:9093 \
        -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
        -e KAFKA_LISTENERS=PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093 \
        -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
        -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT \
        -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT \
        -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
        -e CLUSTER_ID=MkU3OEVBNTcwNTJENDM2Qk \
        confluentinc/cp-kafka:7.5.0 >/dev/null

    echo "$KAFKA_CONTAINER" > "$PID_DIR/kafka_container"

    print_status "wait" "Waiting for Kafka to be ready (up to 60s)..."
    if wait_for_service "kafka" "check_port 127.0.0.1 9092" 60; then
        # Additional wait for full initialization
        sleep 5
        print_status "ok" "Kafka ready"
    else
        print_status "fail" "Kafka failed to start"
        exit 1
    fi
fi

# --- Kafka Topics ---
print_status "wait" "Creating Kafka topics..."

# Find the Kafka container name
KAFKA_CONTAINER="${KAFKA_CONTAINER:-alert-agent-kafka-test}"
if ! docker ps -q -f name="$KAFKA_CONTAINER" | grep -q .; then
    # Try the pre-existing container name
    KAFKA_CONTAINER="alert-bridge-kafka"
fi

# Create required topics (ignore errors if they already exist)
for topic in mdx-incidents mdx-alerts; do
    docker exec "$KAFKA_CONTAINER" kafka-topics --create \
        --bootstrap-server localhost:9092 \
        --topic "$topic" \
        --partitions 1 \
        --replication-factor 1 \
        2>/dev/null || true
done
print_status "ok" "Kafka topics ready"

# --- Simulators ---
print_status "wait" "Starting simulators..."

cd "$REPO_ROOT"

# Start each simulator individually in background
# (run_simulators.py expects interactive input, so we start them directly)

# Elastic simulator
if ! check_http http://127.0.0.1:9200/health; then
    python3 test/sim_scripts/elastic/elastic_sim.py > "$PID_DIR/elastic_sim.log" 2>&1 &
    echo $! > "$PID_DIR/elastic_sim.pid"
    print_status "wait" "Starting Elasticsearch simulator..."
fi

# NIM simulator
if ! check_port 127.0.0.1 18081; then
    python3 test/sim_scripts/nim/nim_stub_server.py > "$PID_DIR/nim_sim.log" 2>&1 &
    echo $! > "$PID_DIR/nim_sim.pid"
    print_status "wait" "Starting NIM simulator..."
fi

# VST simulator
if ! check_http http://127.0.0.1:30888/status; then
    python3 test/sim_scripts/vst/vst_sim.py > "$PID_DIR/vst_sim.log" 2>&1 &
    echo $! > "$PID_DIR/vst_sim.pid"
    print_status "wait" "Starting VST simulator..."
fi

# VSS simulator
if ! check_http http://127.0.0.1:8080/models; then
    python3 test/sim_scripts/vss/vss_sim.py > "$PID_DIR/vss_sim.log" 2>&1 &
    echo $! > "$PID_DIR/vss_sim.pid"
    print_status "wait" "Starting VSS simulator..."
fi

print_status "wait" "Waiting for simulators to be ready..."
sleep 3

# --- Health checks ---
echo ""
echo "Health checks:"
HEALTH_FAILURES=0

# Elastic
if wait_for_service "elastic" "check_http http://127.0.0.1:9200/health" 30; then
    print_status "ok" "Elasticsearch simulator (127.0.0.1:9200)"
else
    print_status "fail" "Elasticsearch simulator failed"
    HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
fi

# NIM
if wait_for_service "nim" "check_port 127.0.0.1 18081" 30; then
    print_status "ok" "NIM simulator (127.0.0.1:18081)"
else
    print_status "fail" "NIM simulator failed"
    HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
fi

# VST
if wait_for_service "vst" "check_http http://127.0.0.1:30888/status" 30; then
    print_status "ok" "VST simulator (127.0.0.1:30888)"
else
    print_status "fail" "VST simulator failed"
    HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
fi

# VSS
if wait_for_service "vss" "check_http http://127.0.0.1:8080/models" 30; then
    print_status "ok" "VSS simulator (127.0.0.1:8080)"
else
    print_status "fail" "VSS simulator failed"
    HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
fi

if [ "$HEALTH_FAILURES" -gt 0 ]; then
    echo ""
    print_status "fail" "$HEALTH_FAILURES simulator(s) failed health checks"
    exit 1
fi

echo ""
print_status "ok" "Step 1 complete - all simulators running"
echo "    PID files: $PID_DIR/*_sim.pid"
echo "    Logs: $PID_DIR/*_sim.log"
