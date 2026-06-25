#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Usage: ./test.sh [profile1] [profile2] [mode]
# This script performs the integration test
# profile1: app names - warehouse_2d (default), warehouse_3d, smart_city
# profile2: streaming service - kafka (default), redis, mqtt
# mode: dev (default) - no cleanup on failure, prod - cleanup even on failure

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROFILE1=${1:-warehouse_2d}  # Use first argument or default to warehouse_2d
PROFILE2=${2:-kafka}  # Use second argument or default to kafka
MODE=${3:-dev}  # Use third argument or default to dev

# Validate profile1, profile2 and mode
if [ "$PROFILE1" != "warehouse_2d" ] && [ "$PROFILE1" != "warehouse_3d" ] && [ "$PROFILE1" != "smart_city" ]; then
    echo "Invalid profile1: $PROFILE1. Must be 'warehouse_2d', 'warehouse_3d', or 'smart_city'"
    exit 1
fi

if [ "$PROFILE2" != "kafka" ] && [ "$PROFILE2" != "redis" ] && [ "$PROFILE2" != "mqtt" ]; then
    echo "Invalid profile2: $PROFILE2. Must be 'kafka', 'redis' or 'mqtt'"
    exit 1
fi

if [ "$MODE" != "dev" ] && [ "$MODE" != "prod" ]; then
    echo "Invalid mode: $MODE. Must be 'dev' or 'prod'"
    exit 1
fi

echo "Running in $MODE mode with $PROFILE1 and $PROFILE2"

# Generate the .env file using the separate script
source "$SCRIPT_DIR/generate_env.sh"

# Source the environment file with proper path resolution
. "$SCRIPT_DIR/docker_compose/infra/.env"

# Source the cleanup script
source "$SCRIPT_DIR/cleanup.sh"

cd "$PROJ_ROOT_DIR"
echo "Building Docker image..."
# Set timeout for docker build (10 minutes)
BUILD_TIMEOUT=900

if timeout $BUILD_TIMEOUT docker build -t py-behavior-analytics -f docker/Dockerfile . > /dev/null 2>&1; then
    echo "✓ Docker build completed successfully"
else
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 124 ]; then
        echo "✗ Docker build timed out after $BUILD_TIMEOUT seconds"
    else
        echo "✗ Docker build failed"
    fi
    # Show the error by running the command again without suppressing output (with shorter timeout)
    timeout $BUILD_TIMEOUT docker build -t py-behavior-analytics -f docker/Dockerfile .
    exit 1
fi

cd "$MDX_SAMPLE_APPS_DIR"
echo "Starting Docker Compose services..."

# Compose command base (same for build and up)
COMPOSE_BASE="docker compose -f infra/compose.yml -f apps/mdx-apps.yml"
if [ "$STREAMING_SERVICE" != "kafka" ]; then
    COMPOSE_BASE="$COMPOSE_BASE --profile $STREAMING_SERVICE"
fi

# Pre-build elasticsearch-init so it does not block compose up (often slow/hanging in CI)
echo "Pre-building elasticsearch-init-container (timeout 600s)..."
if ! timeout 600 $COMPOSE_BASE build elasticsearch-init-container; then
    echo "✗ Pre-build of elasticsearch-init-container failed or timed out"
    exit 1
fi
echo "✓ elasticsearch-init-container image ready"

COMPOSE_CMD="$COMPOSE_BASE up -d --build --force-recreate"

# Timeout for compose up (CI sets COMPOSE_TIMEOUT e.g. 1800)
COMPOSE_TIMEOUT=${COMPOSE_TIMEOUT:-300}
echo "Running: $COMPOSE_CMD (timeout ${COMPOSE_TIMEOUT}s)..."
$COMPOSE_CMD & COMPOSE_PID=$!
COMPOSE_EXIT=0
TIMED_OUT=0
for i in $(seq 1 $COMPOSE_TIMEOUT); do
    if ! kill -0 $COMPOSE_PID 2>/dev/null; then
        wait $COMPOSE_PID
        COMPOSE_EXIT=$?
        break
    fi
    sleep 1
done
if kill -0 $COMPOSE_PID 2>/dev/null; then
    TIMED_OUT=1
    echo "✗ Docker Compose timed out after $COMPOSE_TIMEOUT seconds - killing process"
    echo "--- Docker Compose status at timeout (debug) ---"
    (cd "$MDX_SAMPLE_APPS_DIR" && $COMPOSE_BASE ps -a 2>/dev/null) || true
    (cd "$MDX_SAMPLE_APPS_DIR" && $COMPOSE_BASE logs --tail=80 2>/dev/null) || true
    echo "--- end status ---"
    kill -TERM $COMPOSE_PID 2>/dev/null
    sleep 60
    kill -9 $COMPOSE_PID 2>/dev/null
    wait $COMPOSE_PID 2>/dev/null
    COMPOSE_EXIT=1
fi
if [ $COMPOSE_EXIT -eq 0 ]; then
    echo "✓ Docker Compose started successfully"
else
    if [ $TIMED_OUT -eq 1 ]; then
        echo "✗ Docker Compose timed out after $COMPOSE_TIMEOUT seconds"
    else
        echo "✗ Docker Compose failed to start (exit $COMPOSE_EXIT)"
    fi
    # Call the cleanup function based on mode
    if [ "$MODE" = "prod" ]; then
        cleanup_docker_environment
        if [ $? -ne 0 ]; then
            echo "✗ Docker cleanup failed"
        fi
    else
        echo "Development mode: Skipping cleanup to allow debugging"
    fi
    exit 1
fi

# Check if expected number of processes are running
sleep 50s
echo "Checking if expected processes are running..."
# When app runs in Docker (e.g. CI), count processes inside containers; otherwise count on host.
# The parent main_*_app.py launches workers via multiprocessing spawn; spawn workers have a
# distinct cmdline (`from multiprocessing.spawn import spawn_main`) — match both, but exclude
# the helper resource_tracker process spawn also creates.
count_processes() {
    local pattern="$1"
    local combined="(${pattern})|(multiprocessing\\.spawn)"
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx mdx-analytics; then
        local n=0
        for c in mdx-analytics mdx-analytics-playback; do
            if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$c"; then
                n=$((n + $(docker top "$c" 2>/dev/null | grep -E "$combined" | grep -v grep | grep -v "resource_tracker" | wc -l)))
            fi
        done
        echo $n
    else
        ps aux | grep -E "$combined" | grep -v grep | grep -v "resource_tracker" | wc -l
    fi
}
case $PROFILE1 in
    "warehouse_2d"|"warehouse_3d")
        WORKER_COUNT=$(count_processes "python.*analytics/main_analytics")
        ;;
    "smart_city")
        WORKER_COUNT=$(count_processes "python.*smart_city/main_smart_city")
        ;;
    *)
        WORKER_COUNT=$(count_processes "python.*main_.*_app.py")
        ;;
esac

# Determine expected process count based on profiles
if [ "$PROFILE1" = "warehouse_2d" ] && [ "$PROFILE2" = "kafka" ]; then
    EXPECTED_COUNT=5  # Main process + 4 workers
elif [ "$PROFILE1" = "smart_city" ] && ( [ "$PROFILE2" = "redis" ] || [ "$PROFILE2" = "mqtt" ] ); then
    EXPECTED_COUNT=2
elif [ "$PROFILE1" = "warehouse_3d" ]; then
    EXPECTED_COUNT=4  # Main process + 3 workers
else
    EXPECTED_COUNT=3  # Main process + 2 workers
fi

echo "Expected $EXPECTED_COUNT processes for $PROFILE1 with $PROFILE2"

if [ "$WORKER_COUNT" -ne "$EXPECTED_COUNT" ]; then
    echo "✗ Process count mismatch: expected $EXPECTED_COUNT but found $WORKER_COUNT"
    if [ "$MODE" = "prod" ]; then
        cleanup_docker_environment
        if [ $? -ne 0 ]; then
            echo "✗ Docker cleanup failed"
        fi
    else
        echo "Development mode: Please check the docker log and clean up the docker environment manually"
    fi
    exit 1
else
    echo "✓ Process count verified: $WORKER_COUNT processes running as expected"
fi

# Wait for the playback container to finish feeding data, then add a small
# grace period so workers + Logstash can drain whatever is in flight before we
# extract from Elasticsearch. Falls back to a 10-minute hard cap so a stuck
# playback never wedges the test indefinitely.
echo "Waiting for mdx-analytics-playback to exit (max 10 min)..."
PLAYBACK_WAIT_DEADLINE_SEC=600
PLAYBACK_GRACE_SEC=60
deadline=$(( $(date +%s) + PLAYBACK_WAIT_DEADLINE_SEC ))
while true; do
    status=$(docker inspect --format '{{.State.Status}}' mdx-analytics-playback 2>/dev/null || echo "missing")
    if [ "$status" = "exited" ]; then
        echo "✓ mdx-analytics-playback exited"
        break
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "✗ mdx-analytics-playback did not exit within ${PLAYBACK_WAIT_DEADLINE_SEC}s (last status: $status)"
        if [ "$MODE" = "prod" ]; then
            cleanup_docker_environment
        fi
        exit 1
    fi
    sleep 5
done

echo "Waiting ${PLAYBACK_GRACE_SEC}s grace period for in-flight messages to drain..."
sleep $PLAYBACK_GRACE_SEC
echo "Wait complete, continuing..."

cd "$PROJ_ROOT_DIR"

# Define which data types to dump/compare for each profile
get_data_types_for_profile() {
    local profile=$APP_NAME$APP_MODE
    case $profile in
        "warehouse_2d")
            echo "mdx-behavior-data.json mdx-events-data.json mdx-frames-data.json mdx-incidents-data.json mdx-raw-data.json"
            ;;
        "warehouse_3d")
            echo "mdx-behavior-data.json mdx-events-data.json mdx-frames-data.json mdx-space-utilization-data.json"
            ;;
        "smart_city")
            echo "mdx-behavior-data.json mdx-raw-data.json"
            ;;
        *)
            echo "mdx-behavior-data.json mdx-events-data.json mdx-frames-data.json"
            ;;
    esac
}

# Function to extract data from Elasticsearch based on data type
extract_data_type() {
    local data_type=$1
    local elasticsearch_index=""
    local limit_params=""
    
    case $data_type in
        "mdx-raw-data.json")
            elasticsearch_index="mdx-raw*"
            limit_params="--limit=1000 --scrollTime=10m"
            ;;
        "mdx-frames-data.json")
            elasticsearch_index="mdx-frames*"
            limit_params="--limit=1000 --scrollTime=10m"
            ;;
        "mdx-behavior-data.json")
            elasticsearch_index="mdx-behavior*"
            limit_params=""
            ;;
        "mdx-events-data.json")
            elasticsearch_index="mdx-events*"
            limit_params=""
            ;;
        "mdx-alerts-data.json")
            elasticsearch_index="mdx-alerts*"
            limit_params=""
            ;;
        "mdx-incidents-data.json")
            elasticsearch_index="mdx-incidents*"
            limit_params=""
            ;;
        "mdx-space-utilization-data.json")
            elasticsearch_index="mdx-space-utilization*"
            limit_params=""
            ;;
        *)
            echo "Unknown data type: $data_type"
            return 1
            ;;
    esac
    
    echo "Extracting $data_type from $elasticsearch_index..."
    # Set timeout for elasticdump (3 minutes)
    ELASTICDUMP_TIMEOUT=180
    # Use npx to run elasticdump without requiring global installation
    ELASTICDUM_OUTPUT=$(timeout $ELASTICDUMP_TIMEOUT npx --yes elasticdump --input=http://localhost:9200/$elasticsearch_index --output=tests/integration/docker_compose/apps_data/data_log/tmp/$data_type --type=data $limit_params 2>&1)
    EXIT_CODE=$?
    
    if [ $EXIT_CODE -eq 0 ]; then
        echo "✓ $data_type extraction complete"
        return 0
    elif [ $EXIT_CODE -eq 124 ]; then
        echo "✗ $data_type extraction timed out after $ELASTICDUMP_TIMEOUT seconds"
        return 1
    else
        echo "✗ $data_type extraction failed:"
        echo "$ELASTICDUM_OUTPUT"
        return 1
    fi
}

echo "Extracting data from Elasticsearch for profile: $APP_NAME$APP_MODE"
DATA_TYPES_TO_DUMP=$(get_data_types_for_profile $APP_NAME$APP_MODE)
echo "Data types to extract: $DATA_TYPES_TO_DUMP"

# Extract only the required data types for this profile
EXTRACTION_FAILED=false
for data_type in $DATA_TYPES_TO_DUMP; do
    if ! extract_data_type $data_type; then
        EXTRACTION_FAILED=true
    fi
done

if [ "$EXTRACTION_FAILED" = true ]; then
    echo "✗ Some data extractions failed"
    if [ "$MODE" = "prod" ]; then
        cleanup_docker_environment
        if [ $? -ne 0 ]; then
            echo "✗ Docker cleanup failed"
        fi
    else
        echo "Development mode: Please check the extraction results above and clean up the docker environment manually"
    fi
    exit 1
fi

echo "Running data comparison for profile: $APP_NAME$APP_MODE"
DATA_TYPES=$(get_data_types_for_profile $APP_NAME$APP_MODE)
echo "Data to compare: $DATA_TYPES"

# Run comparisons for the selected data types
COMPARISON_OUTPUTS=()
COMPARISON_RESULTS=()
for data_type in $DATA_TYPES; do
    echo "Comparing $data_type..."
    COMPARISON_OUTPUT=$(python3 tests/integration/docker_compose/infra/scripts/compare_mdx_data.py tests/integration/expected_output/$APP_NAME$APP_MODE/$data_type tests/integration/docker_compose/apps_data/data_log/tmp/$data_type 2>&1)
    COMPARISON_OUTPUTS+=("$COMPARISON_OUTPUT")
    
    if echo "$COMPARISON_OUTPUT" | tail -1 | grep -q "pass"; then
        echo "✓ $data_type comparison passed"
        COMPARISON_RESULTS+=("pass")
    else
        echo "✗ $data_type comparison failed"
        COMPARISON_RESULTS+=("fail")
    fi
done

# Check overall test result
ALL_PASSED=true
for result in "${COMPARISON_RESULTS[@]}"; do
    if [ "$result" = "fail" ]; then
        ALL_PASSED=false
        break
    fi
done

if [ "$ALL_PASSED" = true ]; then
    COMPARISON_RESULT="pass"
else
    COMPARISON_RESULT="fail"
    echo "Detailed comparison results:"
    for i in "${!COMPARISON_OUTPUTS[@]}"; do
        echo "--- Comparison $((i+1)) ---"
        echo "${COMPARISON_OUTPUTS[$i]}"
    done
fi

# Exit with appropriate code based on test result
if [ "$COMPARISON_RESULT" = "fail" ]; then
    echo "❌ Test FAILED for $PROFILE1 $PROFILE2"
    if [ "$MODE" = "prod" ]; then
        cleanup_docker_environment
        if [ $? -ne 0 ]; then
            echo "Docker cleanup failed"
        fi
    else
        echo "Development mode: Please check the comparison results above and clean up the docker environment manually"
    fi
    exit 1
else
    echo "✅ Test PASSED for $PROFILE1 $PROFILE2"
    cleanup_docker_environment
    if [ $? -ne 0 ]; then
        echo "Docker cleanup failed"
        exit 1
    fi
    exit 0
fi
