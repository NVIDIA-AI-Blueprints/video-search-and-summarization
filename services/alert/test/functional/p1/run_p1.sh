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

# P1 Functional Test Orchestrator for Alert Bridge
# Usage: ./run_p1.sh [--test <name>] [--skip-setup] [--skip-cleanup] [--help]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

export PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
export ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
export BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
export TOPIC="${TOPIC:-mdx-incidents}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

KAFKA_CONTAINER="alert-agent-kafka-test"
REDIS_CONTAINER="alert-agent-redis-test"
AB_CONFIG_DIR="$SCRIPT_DIR"
DEFAULT_CONFIG="$SCRIPT_DIR/shared/config_base.yaml"

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --test <name>     Run only the named test (e.g. test_document_parity)"
    echo "  --skip-setup      Skip Phase 1 (assume simulators already running)"
    echo "  --skip-cleanup    Skip Phase 3 cleanup"
    echo "  --help            Show this help"
    echo ""
    echo "Tests:"
    for d in "$SCRIPT_DIR"/test_*/; do
        echo "  $(basename "$d")"
    done
}

print_status() {
    local status=$1 message=$2
    case "$status" in
        ok)   echo -e "${GREEN}✓${NC} $message" ;;
        fail) echo -e "${RED}✗${NC} $message" ;;
        wait) echo -e "${YELLOW}⏳${NC} $message" ;;
        info) echo -e "ℹ $message" ;;
        *)    echo "  $message" ;;
    esac
}

check_port() {
    nc -z "$1" "$2" 2>/dev/null
}

check_http() {
    curl -sf "$1" >/dev/null 2>&1
}

wait_for_service() {
    local name=$1 check_cmd=$2 timeout=${3:-30} interval=${4:-1}
    local elapsed=0
    while [ $elapsed -lt $timeout ]; do
        if eval "$check_cmd"; then return 0; fi
        sleep $interval
        elapsed=$((elapsed + interval))
    done
    return 1
}

# ─── Phase 1: Setup ───────────────────────────────────────────────────────────

phase_setup() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  Phase 1: Setup${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    mkdir -p "$PID_DIR"
    cd "$REPO_ROOT"

    # --- Redis ---
    print_status "wait" "Checking Redis on :6379..."
    if check_port 127.0.0.1 6379; then
        print_status "ok" "Redis already running on :6379"
    else
        print_status "wait" "Starting Redis container..."
        docker rm -f "$REDIS_CONTAINER" 2>/dev/null || true
        docker run -d \
            --name "$REDIS_CONTAINER" \
            -p 6379:6379 \
            redis/redis-stack-server:7.2.0-v9 >/dev/null
        echo "$REDIS_CONTAINER" > "$PID_DIR/redis_container"
        print_status "wait" "Waiting for Redis..."
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
        docker rm -f "$KAFKA_CONTAINER" 2>/dev/null || true
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
        print_status "wait" "Waiting for Kafka (up to 60s)..."
        if wait_for_service "kafka" "check_port 127.0.0.1 9092" 60; then
            sleep 5
            print_status "ok" "Kafka ready"
        else
            print_status "fail" "Kafka failed to start"
            exit 1
        fi
    fi

    # --- Kafka Topics ---
    print_status "wait" "Creating Kafka topics..."
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

    if ! check_http http://127.0.0.1:9200/health; then
        python3 test/sim_scripts/elastic/elastic_sim.py > "$PID_DIR/elastic_sim.log" 2>&1 &
        echo $! > "$PID_DIR/elastic_sim.pid"
    fi

    if ! check_port 127.0.0.1 18081; then
        python3 test/sim_scripts/nim/nim_stub_server.py > "$PID_DIR/nim_sim.log" 2>&1 &
        echo $! > "$PID_DIR/nim_sim.pid"
    fi

    if ! check_http http://127.0.0.1:30888/status; then
        python3 test/sim_scripts/vst/vst_sim.py > "$PID_DIR/vst_sim.log" 2>&1 &
        echo $! > "$PID_DIR/vst_sim.pid"
    fi

    if ! check_http http://127.0.0.1:8080/models; then
        python3 test/sim_scripts/vss/vss_sim.py > "$PID_DIR/vss_sim.log" 2>&1 &
        echo $! > "$PID_DIR/vss_sim.pid"
    fi

    sleep 3

    echo ""
    echo "Health checks:"
    HEALTH_FAILURES=0

    if wait_for_service "elastic" "check_http http://127.0.0.1:9200/health" 30; then
        print_status "ok" "Elasticsearch simulator (127.0.0.1:9200)"
    else
        print_status "fail" "Elasticsearch simulator failed"
        HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
    fi

    if wait_for_service "nim" "check_port 127.0.0.1 18081" 30; then
        print_status "ok" "NIM simulator (127.0.0.1:18081)"
    else
        print_status "fail" "NIM simulator failed"
        HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
    fi

    if wait_for_service "vst" "check_http http://127.0.0.1:30888/status" 30; then
        print_status "ok" "VST simulator (127.0.0.1:30888)"
    else
        print_status "fail" "VST simulator failed"
        HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
    fi

    if wait_for_service "vss" "check_http http://127.0.0.1:8080/models" 30; then
        print_status "ok" "VSS simulator (127.0.0.1:8080)"
    else
        print_status "fail" "VSS simulator failed"
        HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
    fi

    if [ "$HEALTH_FAILURES" -gt 0 ]; then
        print_status "fail" "$HEALTH_FAILURES simulator(s) failed health checks"
        exit 1
    fi

    print_status "ok" "Phase 1 complete — all simulators running"
}

# ─── Alert Bridge helpers ─────────────────────────────────────────────────────

stop_alert_bridge() {
    if [ -f "$PID_DIR/alert_bridge.pid" ]; then
        local pid
        pid=$(cat "$PID_DIR/alert_bridge.pid")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            # Poll for graceful exit. AB's signal_handler waits up to 10s for the
            # FastAPI subprocess to drain (uvicorn graceful shutdown). A 1s sleep
            # truncates that window and orphans the FastAPI subprocess, leaving
            # :9080 bound when the next AB starts — which races test API writes.
            local waited=0
            while [ $waited -lt 12 ] && kill -0 "$pid" 2>/dev/null; do
                sleep 1; waited=$((waited + 1))
            done
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$PID_DIR/alert_bridge.pid"
    fi
    # Fallback: kill any lingering AB process (incl. FastAPI subprocess via fork)
    pkill -f "enhance_alert_with_vlm.py" 2>/dev/null || true

    # Wait for FastAPI's :9080 (and Prom :9081) to be released so the next AB's
    # subprocess can bind cleanly. Without this, uvicorn fails with EADDRINUSE,
    # FastAPI subprocess dies silently, and tests POST against either a dead
    # listener (connection refused) or stale residue from the prior process —
    # which surfaces as nim_sim.log grep returning 0 in tests that check for
    # distinctive vlm_params values (hot_reload, prompt_sync, restart_persistence).
    local port_waited=0
    while [ $port_waited -lt 15 ]; do
        if ! nc -z 127.0.0.1 9080 2>/dev/null && ! nc -z 127.0.0.1 9081 2>/dev/null; then
            return 0
        fi
        sleep 1; port_waited=$((port_waited + 1))
    done
    # Last-resort hammer if something still holds the port
    fuser -k 9080/tcp 9081/tcp 2>/dev/null || true
    sleep 1
}

start_alert_bridge() {
    local config_file="$1"
    cd "$REPO_ROOT"

    if [ ! -f "$config_file" ]; then
        print_status "fail" "Config not found: $config_file"
        exit 1
    fi

    print_status "info" "Using config: $config_file"
    export CONFIG_PATH="$config_file"
    python3 enhance_alert_with_vlm.py --config "$config_file" > "$PID_DIR/alert_bridge.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_DIR/alert_bridge.pid"

    print_status "wait" "Waiting for AB startup and consumer group stabilization (15s)..."
    sleep 15

    if kill -0 "$pid" 2>/dev/null; then
        print_status "ok" "Alert Bridge running (PID $pid)"
    else
        print_status "fail" "Alert Bridge failed to start — check $PID_DIR/alert_bridge.log"
        tail -20 "$PID_DIR/alert_bridge.log" 2>/dev/null || true
        exit 1
    fi
}

# ─── Reset state between tests ────────────────────────────────────────────────

reset_test_state() {
    # Flush Redis — clears dedup keys, verdict protection markers
    docker exec "$REDIS_CONTAINER" redis-cli FLUSHALL >/dev/null 2>&1 || true
    # Clear ES sim — delete today's index so tests start with zero documents
    local today
    today=$(date -u +%Y-%m-%d)
    curl -sf -X DELETE "$ES_HOST/mdx-vlm-incidents-$today" >/dev/null 2>&1 || true
    curl -sf -X DELETE "$ES_HOST/mdx-vlm-alerts-$today" >/dev/null 2>&1 || true
    # Clear persistence layer indices — otherwise alert configs written
    # by previous tests leak across runs via ES, bypassing the Redis
    # FLUSHALL above (the alert-config ES hydration made ES the source of truth).
    curl -sf -X DELETE "$ES_HOST/ab-alert_configs" >/dev/null 2>&1 || true
}

# ─── Phase 2: Run Tests ───────────────────────────────────────────────────────

phase_run_tests() {
    local filter="$1"

    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  Phase 2: Run Tests${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    local pass=0 fail=0 skip=0
    declare -a results=()

    for test_dir in "$SCRIPT_DIR"/test_*/; do
        local test_name
        test_name=$(basename "$test_dir")

        # Apply --test filter
        if [ -n "$filter" ] && [ "$test_name" != "$filter" ]; then
            continue
        fi

        local config="$test_dir/config.yaml"
        local run_script="$test_dir/run.sh"

        if [ ! -f "$run_script" ]; then
            print_status "info" "Skipping $test_name (no run.sh)"
            continue
        fi

        echo ""
        echo -e "${BLUE}  Running: $test_name${NC}"

        # Use test-specific config if present, otherwise default base config
        if [ ! -f "$config" ]; then
            config="$DEFAULT_CONFIG"
        fi

        # IMPORTANT: stop AB BEFORE reset. If reset runs while old AB is alive,
        # the old AB can still flush in-flight responses to ES (re-creating the
        # mdx-vlm-incidents-<today> index after we just DELETE'd it) or refresh
        # its alert_config Redis cache after FLUSHALL — leaking state into the
        # next test.
        stop_alert_bridge

        # Now safe to reset: nothing is writing back.
        reset_test_state

        start_alert_bridge "$config"

        # Run the test
        local exit_code=0
        bash "$run_script" || exit_code=$?

        if [ $exit_code -eq 0 ]; then
            print_status "ok" "$test_name: PASS"
            pass=$((pass + 1))
            results+=("PASS  $test_name")
        else
            print_status "fail" "$test_name: FAIL (exit $exit_code)"
            fail=$((fail + 1))
            results+=("FAIL  $test_name")
        fi
    done

    # Summary
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  Results${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    for r in "${results[@]}"; do
        if [[ "$r" == PASS* ]]; then
            echo -e "  ${GREEN}$r${NC}"
        else
            echo -e "  ${RED}$r${NC}"
        fi
    done
    echo ""
    echo "  Passed: $pass  Failed: $fail"

    if [ "$fail" -gt 0 ]; then
        return 1
    fi
    return 0
}

# ─── Phase 3: Cleanup ─────────────────────────────────────────────────────────

phase_cleanup() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  Phase 3: Cleanup${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    stop_alert_bridge

    # Kill individual simulator processes
    for sim in elastic_sim nim_sim vst_sim vss_sim; do
        if [ -f "$PID_DIR/${sim}.pid" ]; then
            local pid
            pid=$(cat "$PID_DIR/${sim}.pid")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
                sleep 1
                kill -9 "$pid" 2>/dev/null || true
                print_status "ok" "$sim stopped"
            fi
            rm -f "$PID_DIR/${sim}.pid"
        fi
    done

    # Fallback: kill by script name
    pkill -f "elastic_sim.py"     2>/dev/null || true
    pkill -f "nim_stub_server.py" 2>/dev/null || true
    pkill -f "vst_sim.py"         2>/dev/null || true
    pkill -f "vss_sim.py"         2>/dev/null || true
    print_status "ok" "Simulator processes cleaned up"

    # Stop Kafka
    if [ -f "$PID_DIR/kafka_container" ]; then
        local kc
        kc=$(cat "$PID_DIR/kafka_container")
        docker rm -f "$kc" >/dev/null 2>&1 || true
        rm -f "$PID_DIR/kafka_container"
        print_status "ok" "Kafka container stopped"
    fi
    if docker ps -q -f name="$KAFKA_CONTAINER" | grep -q .; then
        docker rm -f "$KAFKA_CONTAINER" >/dev/null 2>&1 || true
    fi

    # Stop Redis
    if [ -f "$PID_DIR/redis_container" ]; then
        local rc
        rc=$(cat "$PID_DIR/redis_container")
        docker rm -f "$rc" >/dev/null 2>&1 || true
        rm -f "$PID_DIR/redis_container"
        print_status "ok" "Redis container stopped"
    fi
    if docker ps -q -f name="$REDIS_CONTAINER" | grep -q .; then
        docker rm -f "$REDIS_CONTAINER" >/dev/null 2>&1 || true
    fi

    # Clean up PID dir
    rm -f "$PID_DIR"/*.log "$PID_DIR"/*.json "$PID_DIR"/*.pid 2>/dev/null || true
    rmdir "$PID_DIR" 2>/dev/null || true

    print_status "ok" "Cleanup complete"
}

# ─── Argument parsing ─────────────────────────────────────────────────────────

FILTER=""
SKIP_SETUP=false
SKIP_CLEANUP=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --test)
            FILTER="$2"
            shift 2
            ;;
        --skip-setup)
            SKIP_SETUP=true
            shift
            ;;
        --skip-cleanup)
            SKIP_CLEANUP=true
            shift
            ;;
        --help|-h)
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

# ─── Main ─────────────────────────────────────────────────────────────────────

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Alert Bridge P1 Functional Test Suite ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"

START_TIME=$(date +%s)

if [ "$SKIP_SETUP" = false ]; then
    phase_setup
fi

TESTS_EXIT=0
phase_run_tests "$FILTER" || TESTS_EXIT=$?

if [ "$SKIP_CLEANUP" = false ]; then
    phase_cleanup
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
if [ "$TESTS_EXIT" -eq 0 ]; then
    echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║  All P1 tests PASSED in ${DURATION}s             ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
else
    echo -e "${RED}╔════════════════════════════════════════╗${NC}"
    echo -e "${RED}║  Some P1 tests FAILED (${DURATION}s)             ║${NC}"
    echo -e "${RED}╚════════════════════════════════════════╝${NC}"
fi

exit $TESTS_EXIT
