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
COMPOSE_FILE="$SCRIPT_DIR/docker-compose-dev-profile-test.yaml"
REMOTE_HOST="localhost"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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
    echo "Dev Profile Test Framework"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --local              Run tests with local simulators (default)"
    echo "  --remote             Run tests with remote host (localhost)"
    echo "  --skip-simulators    Skip starting simulators (assume already running)"
    echo "  --skip-containers    Skip starting containers (assume already running)"
    echo "  --test <file>        Run specific test file only"
    echo "  --cleanup-only       Only cleanup containers and simulators"
    echo "  --help               Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --local                          # Full local test run"
    echo "  $0 --remote                         # Test against remote services"
    echo "  $0 --local --skip-simulators        # Use existing simulators"
    echo "  $0 --local --test test_incident_pipeline.py  # Run single test"
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

start_local_infrastructure() {
    print_header "Starting Docker Infrastructure (Kafka + Redis)"
    
    cd "$SCRIPT_DIR"
    
    # Start Kafka and Redis using the 'local' profile
    docker compose -f "$COMPOSE_FILE" --profile local up -d alert-bridge-kafka alert-bridge-redis
    
    # Wait for services to be healthy
    echo "Waiting for Kafka and Redis to be healthy..."
    local max_attempts=30
    local attempt=0
    
    while [ $attempt -lt $max_attempts ]; do
        local kafka_healthy=$(docker inspect --format='{{.State.Health.Status}}' alert-bridge-kafka-dev 2>/dev/null || echo "not_found")
        local redis_healthy=$(docker inspect --format='{{.State.Health.Status}}' alert-bridge-redis-dev 2>/dev/null || echo "not_found")
        
        if [ "$kafka_healthy" = "healthy" ] && [ "$redis_healthy" = "healthy" ]; then
            break
        fi
        attempt=$((attempt + 1))
        echo "  Waiting... ($attempt/$max_attempts) Kafka: $kafka_healthy, Redis: $redis_healthy"
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
    
    docker compose -f "$COMPOSE_FILE" --profile local --profile remote down --remove-orphans 2>/dev/null || true
    
    # Force remove containers if still present
    docker rm -f alert-bridge-dev alert-bridge-kafka-dev alert-bridge-redis-dev 2>/dev/null || true
    
    print_success "Infrastructure stopped"
}

check_remote_services() {
    print_header "Checking Remote Services ($REMOTE_HOST)"
    
    local all_ok=true
    
    for port in 9200 18081 30888 8080 9092 6379; do
        if nc -z -w2 "$REMOTE_HOST" "$port" 2>/dev/null; then
            print_success "Port $port responding"
        else
            print_error "Port $port not responding"
            all_ok=false
        fi
    done
    
    if [ "$all_ok" = false ]; then
        print_error "Some remote services are not available"
        return 1
    fi
    
    print_success "All remote services available"
}

start_alert_bridge_local() {
    echo "Starting Alert Bridge container (local mode)..."
    
    cd "$SCRIPT_DIR"
    
    # Stop any existing Alert Bridge container
    docker rm -f alert-bridge-dev 2>/dev/null || true
    
    # Flush Redis to avoid deduplication issues
    redis-cli -p 6379 FLUSHALL 2>/dev/null || true
    
    # Start Alert Bridge with the local profile
    DEV_PROFILE_CONFIG=config_dev_profile_sim.yaml docker compose -f "$COMPOSE_FILE" --profile local up -d alert-bridge-local
    
    # Wait for Alert Bridge to initialize
    echo "Waiting for Alert Bridge to initialize..."
    sleep 10
    
    # Check if container is running
    if docker ps | grep -q "alert-bridge-dev"; then
        print_success "Alert Bridge started (local mode)"
        return 0
    else
        print_error "Alert Bridge failed to start"
        docker logs alert-bridge-dev 2>&1 | tail -20
        return 1
    fi
}

start_alert_bridge_remote() {
    echo "Starting Alert Bridge container (remote mode)..."
    
    cd "$SCRIPT_DIR"
    
    # Stop any existing Alert Bridge container
    docker rm -f alert-bridge-dev 2>/dev/null || true
    
    # Flush remote Redis to avoid deduplication issues
    redis-cli -h "$REMOTE_HOST" -p 6379 FLUSHALL 2>/dev/null || print_warning "Could not flush remote Redis"
    
    # Start Alert Bridge with the remote profile
    docker compose -f "$COMPOSE_FILE" --profile remote up -d alert-bridge-remote
    
    # Wait for Alert Bridge to initialize
    echo "Waiting for Alert Bridge to initialize..."
    sleep 10
    
    # Check if container is running
    if docker ps | grep -q "alert-bridge-dev"; then
        print_success "Alert Bridge started (remote mode)"
        return 0
    else
        print_error "Alert Bridge failed to start"
        docker logs alert-bridge-dev 2>&1 | tail -20
        return 1
    fi
}

stop_alert_bridge() {
    echo "Stopping Alert Bridge container..."
    docker rm -f alert-bridge-dev 2>/dev/null || true
}

run_tests() {
    local test_file="${1:-}"
    local exit_code=0
    
    cd "$SCRIPT_DIR"
    
    if [ -n "$test_file" ]; then
        echo ""
        echo "Running $test_file..."
        if pytest "$test_file" --use-real-endpoints -v -s --tb=short; then
            print_success "$test_file passed"
        else
            print_error "$test_file failed"
            exit_code=1
        fi
    else
        # Run incident pipeline tests
        echo ""
        echo "Running test_incident_pipeline.py..."
        if pytest test_incident_pipeline.py --use-real-endpoints -v -s --tb=short; then
            print_success "Incident pipeline tests passed"
        else
            print_error "Incident pipeline tests failed"
            exit_code=1
        fi
        
        # Run alert pipeline tests
        echo ""
        echo "Running test_alert_pipeline.py..."
        if pytest test_alert_pipeline.py --use-real-endpoints -v -s --tb=short; then
            print_success "Alert pipeline tests passed"
        else
            print_error "Alert pipeline tests failed"
            exit_code=1
        fi
    fi
    
    return $exit_code
}

cleanup() {
    print_header "Cleanup"
    stop_alert_bridge
    stop_infrastructure
    if [ "$MODE" = "local" ] && [ "$SKIP_SIMULATORS" = false ]; then
        stop_simulators
    fi
    print_success "Cleanup complete"
}

# Parse arguments
MODE="local"
SKIP_SIMULATORS=false
SKIP_CONTAINERS=false
CLEANUP_ONLY=false
TEST_FILE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --local)
            MODE="local"
            shift
            ;;
        --remote)
            MODE="remote"
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
        --test)
            TEST_FILE="$2"
            shift 2
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

# Trap for cleanup on exit
trap cleanup EXIT

# Handle cleanup-only mode
if [ "$CLEANUP_ONLY" = true ]; then
    cleanup
    exit 0
fi

# Main execution
print_header "Dev Profile Test Framework"
echo "Mode: $MODE"
echo ""

EXIT_CODE=0

if [ "$MODE" = "local" ]; then
    # Local mode: start simulators and containers
    
    if [ "$SKIP_SIMULATORS" = false ]; then
        start_simulators
    else
        print_warning "Skipping simulator startup (--skip-simulators)"
    fi
    
    if [ "$SKIP_CONTAINERS" = false ]; then
        start_local_infrastructure
    else
        print_warning "Skipping container startup (--skip-containers)"
    fi
    
    if ! start_alert_bridge_local; then
        exit 1
    fi
    
elif [ "$MODE" = "remote" ]; then
    # Remote mode: verify remote services and start Alert Bridge only
    
    if ! check_remote_services; then
        print_error "Remote services not available. Exiting."
        exit 1
    fi
    
    if ! start_alert_bridge_remote; then
        exit 1
    fi
fi

# Run tests
print_header "Running Tests"
if ! run_tests "$TEST_FILE"; then
    EXIT_CODE=1
fi

# Print summary
echo ""
print_header "Test Summary"
echo "Mode: $MODE"
if [ $EXIT_CODE -eq 0 ]; then
    print_success "All tests PASSED"
else
    print_error "Some tests FAILED"
fi

echo ""
echo "Check indices for results:"
echo "  - dev-vlm-incidents-*"
echo "  - dev-vlm-alerts-*"

exit $EXIT_CODE
