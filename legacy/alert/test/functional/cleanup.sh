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

# Cleanup: Stop all services started by functional tests
# Usage: ./cleanup.sh
set -euo pipefail

PID_DIR="${PID_DIR:-/tmp/alert_agent_functional}"

# Colors
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

echo "=== Cleanup ==="
echo ""

# Kill Alert Bridge (source mode)
if [ -f "$PID_DIR/alert_bridge.pid" ]; then
    AB_PID=$(cat "$PID_DIR/alert_bridge.pid")
    if kill -0 "$AB_PID" 2>/dev/null; then
        print_status "wait" "Stopping Alert Bridge (PID $AB_PID)..."
        kill "$AB_PID" 2>/dev/null || true
        sleep 1
        kill -9 "$AB_PID" 2>/dev/null || true
        print_status "ok" "Alert Bridge stopped"
    else
        print_status "ok" "Alert Bridge already stopped"
    fi
    rm -f "$PID_DIR/alert_bridge.pid"
fi

# Kill Alert Bridge (Docker mode)
if [ -f "$PID_DIR/alert_bridge_container" ]; then
    CONTAINER=$(cat "$PID_DIR/alert_bridge_container")
    if docker ps -q -f name="$CONTAINER" | grep -q .; then
        print_status "wait" "Stopping Alert Bridge container..."
        docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
        print_status "ok" "Alert Bridge container stopped"
    else
        print_status "ok" "Alert Bridge container already stopped"
    fi
    rm -f "$PID_DIR/alert_bridge_container"
fi

# Kill individual simulator processes
for sim in elastic_sim nim_sim vst_sim vss_sim; do
    if [ -f "$PID_DIR/${sim}.pid" ]; then
        SIM_PID=$(cat "$PID_DIR/${sim}.pid")
        if kill -0 "$SIM_PID" 2>/dev/null; then
            print_status "wait" "Stopping $sim (PID $SIM_PID)..."
            kill "$SIM_PID" 2>/dev/null || true
            sleep 1
            kill -9 "$SIM_PID" 2>/dev/null || true
            print_status "ok" "$sim stopped"
        fi
        rm -f "$PID_DIR/${sim}.pid"
    fi
done

# Legacy: Kill old-style simulators.pid if exists
if [ -f "$PID_DIR/simulators.pid" ]; then
    SIM_PID=$(cat "$PID_DIR/simulators.pid")
    if kill -0 "$SIM_PID" 2>/dev/null; then
        print_status "wait" "Stopping simulators (PID $SIM_PID)..."
        kill -- -"$SIM_PID" 2>/dev/null || kill "$SIM_PID" 2>/dev/null || true
        sleep 1
        kill -9 "$SIM_PID" 2>/dev/null || true
        print_status "ok" "Simulators stopped"
    fi
    rm -f "$PID_DIR/simulators.pid"
fi

# Kill any remaining simulator processes (fallback)
print_status "wait" "Checking for remaining simulator processes..."
pkill -f "elastic_sim.py" 2>/dev/null || true
pkill -f "nim_stub_server.py" 2>/dev/null || true
pkill -f "vst_sim.py" 2>/dev/null || true
pkill -f "vss_sim.py" 2>/dev/null || true
pkill -f "run_simulators" 2>/dev/null || true
print_status "ok" "Simulator processes cleaned up"

# Kill any remaining Alert Bridge processes (fallback)
print_status "wait" "Checking for remaining Alert Bridge processes..."
pkill -f "enhance_alert_with_vlm.py" 2>/dev/null || true
print_status "ok" "Alert Bridge processes cleaned up"

# Stop Kafka container
if [ -f "$PID_DIR/kafka_container" ]; then
    KAFKA_CONTAINER=$(cat "$PID_DIR/kafka_container")
    if docker ps -q -f name="$KAFKA_CONTAINER" | grep -q .; then
        print_status "wait" "Stopping Kafka container..."
        docker rm -f "$KAFKA_CONTAINER" >/dev/null 2>&1 || true
        print_status "ok" "Kafka container stopped"
    else
        print_status "ok" "Kafka container already stopped"
    fi
    rm -f "$PID_DIR/kafka_container"
fi

# Also try the default Kafka container name
KAFKA_DEFAULT="alert-agent-kafka-test"
if docker ps -q -f name="$KAFKA_DEFAULT" | grep -q .; then
    print_status "wait" "Stopping default Kafka container..."
    docker rm -f "$KAFKA_DEFAULT" >/dev/null 2>&1 || true
    print_status "ok" "Default Kafka container stopped"
fi

# Stop Redis container
if [ -f "$PID_DIR/redis_container" ]; then
    REDIS_CONTAINER=$(cat "$PID_DIR/redis_container")
    if docker ps -q -f name="$REDIS_CONTAINER" | grep -q .; then
        print_status "wait" "Stopping Redis container..."
        docker rm -f "$REDIS_CONTAINER" >/dev/null 2>&1 || true
        print_status "ok" "Redis container stopped"
    else
        print_status "ok" "Redis container already stopped"
    fi
    rm -f "$PID_DIR/redis_container"
fi

# Also try the default Redis container name
REDIS_DEFAULT="alert-agent-redis-test"
if docker ps -q -f name="$REDIS_DEFAULT" | grep -q .; then
    print_status "wait" "Stopping default Redis container..."
    docker rm -f "$REDIS_DEFAULT" >/dev/null 2>&1 || true
    print_status "ok" "Default Redis container stopped"
fi

# Clean up PID directory
if [ -d "$PID_DIR" ]; then
    rm -f "$PID_DIR/incident_id_suffix" 2>/dev/null || true
    rm -f "$PID_DIR/alert_bridge.log" 2>/dev/null || true

    # Only remove directory if empty
    rmdir "$PID_DIR" 2>/dev/null || true
fi

echo ""
print_status "ok" "Cleanup complete"
