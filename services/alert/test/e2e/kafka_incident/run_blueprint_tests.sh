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

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose-blueprint-test.yaml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Blueprint configurations
declare -A BLUEPRINTS=(
    ["warehouse"]="config_warehouse_sim.yaml"
    ["smartcity"]="config_smartcity_sim.yaml"
    ["public_safety"]="config_public_safety_sim.yaml"
)

# PID files for simulators
SIM_PIDS=()

print_header() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

usage() {
    echo "Blueprint Test Framework"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --all              Run tests for all blueprints"
    echo "  --warehouse        Run tests for warehouse blueprint"
    echo "  --smartcity        Run tests for smartcity blueprint"
    echo "  --public_safety    Run tests for public_safety blueprint"
    echo "  --skip-simulators  Skip starting simulators (assume already running)"
    echo "  --skip-containers  Skip starting containers (assume already running)"
    echo "  --cleanup-only     Only cleanup containers and simulators"
    echo "  --help             Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --all                    # Run all blueprint tests"
    echo "  $0 --warehouse --smartcity  # Run warehouse and smartcity tests"
}

start_simulators() {
    print_header "Starting Python Simulators"
    
    cd "$PROJECT_ROOT"
    
    # Start Elasticsearch simulator
    echo "Starting Elasticsearch simulator on :9200..."
    python -m test.sim_scripts.elastic.elastic_sim &
    SIM_PIDS+=($!)
    
    # Start NIM simulator
    echo "Starting NIM simulator on :18081..."
    python -m test.sim_scripts.nim.nim_stub_server &
    SIM_PIDS+=($!)
    
    # Start VST simulator
    echo "Starting VST simulator on :30888..."
    python -m test.sim_scripts.vst.vst_sim &
    SIM_PIDS+=($!)
    
    # Start VSS simulator
    echo "Starting VSS simulator on :8080..."
    python -m test.sim_scripts.vss.vss_sim &
    SIM_PIDS+=($!)
    
    # Wait for simulators to start
    echo "Waiting for simulators to initialize..."
    sleep 5
    
    print_success "All simulators started"
}

stop_simulators() {
    print_header "Stopping Python Simulators"
    
    for pid in "${SIM_PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping simulator (PID: $pid)..."
            kill "$pid" 2>/dev/null || true
        fi
    done
    
    # Kill any remaining simulator processes
    pkill -f "elastic_sim" 2>/dev/null || true
    pkill -f "nim_stub_server" 2>/dev/null || true
    pkill -f "vst_sim" 2>/dev/null || true
    pkill -f "vss_sim" 2>/dev/null || true
    
    print_success "All simulators stopped"
}

start_infrastructure() {
    print_header "Starting Docker Infrastructure (Kafka + Redis)"
    
    cd "$SCRIPT_DIR"
    
    # Start Kafka and Redis only (not Alert Bridge yet)
    docker compose -f "$COMPOSE_FILE" up -d alert-bridge-kafka alert-bridge-redis
    
    # Wait for services to be healthy
    echo "Waiting for Kafka and Redis to be healthy..."
    local max_attempts=30
    local attempt=0
    
    while [ $attempt -lt $max_attempts ]; do
        if docker compose -f "$COMPOSE_FILE" ps | grep -q "healthy"; then
            break
        fi
        attempt=$((attempt + 1))
        echo "  Waiting... ($attempt/$max_attempts)"
        sleep 2
    done
    
    if [ $attempt -eq $max_attempts ]; then
        print_error "Timeout waiting for infrastructure to be healthy"
        return 1
    fi
    
    print_success "Infrastructure is ready"
}

stop_infrastructure() {
    print_header "Stopping Docker Infrastructure"
    
    cd "$SCRIPT_DIR"
    
    docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true
    
    # Force remove containers if still present
    docker rm -f alert-bridge-test alert-bridge-kafka-test alert-bridge-redis-test 2>/dev/null || true
    
    print_success "Infrastructure stopped"
}

start_alert_bridge() {
    local blueprint=$1
    local config=${BLUEPRINTS[$blueprint]}
    
    echo "Starting Alert Bridge with $config..."
    
    cd "$SCRIPT_DIR"
    
    # Stop any existing Alert Bridge container
    docker rm -f alert-bridge-test 2>/dev/null || true
    
    # Flush Redis to avoid deduplication issues
    redis-cli -p 6379 FLUSHALL 2>/dev/null || true
    
    # Start Alert Bridge with the blueprint config
    BLUEPRINT_CONFIG="$config" docker compose -f "$COMPOSE_FILE" up -d alert-bridge
    
    # Wait for Alert Bridge to initialize
    echo "Waiting for Alert Bridge to initialize..."
    sleep 10
    
    # Check if container is running
    if docker ps | grep -q "alert-bridge-test"; then
        print_success "Alert Bridge started with $blueprint config"
        return 0
    else
        print_error "Alert Bridge failed to start"
        docker logs alert-bridge-test 2>&1 | tail -20
        return 1
    fi
}

stop_alert_bridge() {
    echo "Stopping Alert Bridge container..."
    docker rm -f alert-bridge-test 2>/dev/null || true
}

run_tests_for_blueprint() {
    local blueprint=$1
    local config=${BLUEPRINTS[$blueprint]}
    local exit_code=0
    
    print_header "Testing Blueprint: $blueprint"
    echo "Config: $config"
    
    # Start Alert Bridge with this blueprint's config
    if ! start_alert_bridge "$blueprint"; then
        return 1
    fi
    
    cd "$SCRIPT_DIR"
    
    # Run incident pipeline tests
    echo ""
    echo "Running incident pipeline tests..."
    if pytest test_incident_pipeline.py --use-real-endpoints -v -s --tb=short; then
        print_success "Incident pipeline tests passed"
    else
        print_error "Incident pipeline tests failed"
        exit_code=1
    fi
    
    # Run alert pipeline tests
    echo ""
    echo "Running alert pipeline tests..."
    if pytest test_alert_pipeline.py --use-real-endpoints -v -s --tb=short; then
        print_success "Alert pipeline tests passed"
    else
        print_error "Alert pipeline tests failed"
        exit_code=1
    fi
    
    # Stop Alert Bridge
    stop_alert_bridge
    
    return $exit_code
}

cleanup() {
    print_header "Cleanup"
    stop_alert_bridge
    stop_infrastructure
    stop_simulators
    print_success "Cleanup complete"
}

# Trap for cleanup on exit
trap cleanup EXIT

# Parse arguments
BLUEPRINTS_TO_RUN=()
SKIP_SIMULATORS=false
SKIP_CONTAINERS=false
CLEANUP_ONLY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --all)
            BLUEPRINTS_TO_RUN=("warehouse" "smartcity" "public_safety")
            shift
            ;;
        --warehouse)
            BLUEPRINTS_TO_RUN+=("warehouse")
            shift
            ;;
        --smartcity)
            BLUEPRINTS_TO_RUN+=("smartcity")
            shift
            ;;
        --public_safety)
            BLUEPRINTS_TO_RUN+=("public_safety")
            shift
            ;;
        --skip-simulators)
            SKIP_SIMULATORS=true
            shift
            ;;
        --skip-containers)
            SKIP_CONTAINERS=true
            shift
            ;;
        --cleanup-only)
            CLEANUP_ONLY=true
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Handle cleanup-only mode
if [ "$CLEANUP_ONLY" = true ]; then
    cleanup
    exit 0
fi

# Check if any blueprints selected
if [ ${#BLUEPRINTS_TO_RUN[@]} -eq 0 ]; then
    echo "No blueprints selected. Use --all or specify blueprints."
    usage
    exit 1
fi

# Main execution
print_header "Blueprint Test Framework"
echo "Blueprints to test: ${BLUEPRINTS_TO_RUN[*]}"
echo ""

# Start simulators
if [ "$SKIP_SIMULATORS" = false ]; then
    start_simulators
fi

# Start infrastructure
if [ "$SKIP_CONTAINERS" = false ]; then
    start_infrastructure
fi

# Run tests for each blueprint
RESULTS=()
OVERALL_EXIT_CODE=0

for blueprint in "${BLUEPRINTS_TO_RUN[@]}"; do
    if run_tests_for_blueprint "$blueprint"; then
        RESULTS+=("$blueprint: PASSED")
    else
        RESULTS+=("$blueprint: FAILED")
        OVERALL_EXIT_CODE=1
    fi
    echo ""
done

# Print summary
print_header "Test Summary"
for result in "${RESULTS[@]}"; do
    if [[ $result == *"PASSED"* ]]; then
        print_success "$result"
    else
        print_error "$result"
    fi
done

echo ""
if [ $OVERALL_EXIT_CODE -eq 0 ]; then
    print_success "All blueprint tests PASSED"
else
    print_error "Some blueprint tests FAILED"
fi

exit $OVERALL_EXIT_CODE
