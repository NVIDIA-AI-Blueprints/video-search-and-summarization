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
if [[ "$PROFILE1" != "warehouse_2d" ]] && [[ "$PROFILE1" != "warehouse_3d" ]] && [[ "$PROFILE1" != "smart_city" ]]; then
    echo "Invalid profile1: $PROFILE1. Must be 'warehouse_2d', 'warehouse_3d', or 'smart_city'"
    exit 1
fi

if [[ "$PROFILE2" != "kafka" ]] && [[ "$PROFILE2" != "redis" ]] && [[ "$PROFILE2" != "mqtt" ]]; then
    echo "Invalid profile2: $PROFILE2. Must be 'kafka', 'redis' or 'mqtt'"
    exit 1
fi

if [[ "$MODE" != "dev" ]] && [[ "$MODE" != "prod" ]]; then
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

# Containers run as non-root users and write integration artifacts through this
# bind mount. Keep the permission contract with the test harness instead of
# requiring CI-specific chmod setup.
mkdir -p "$MDX_DATA_DIR"
chmod -R a+rwX "$MDX_DATA_DIR"

cd "$PROJ_ROOT_DIR"
# Build the checked-out service only when its build inputs changed. Otherwise,
# exercise the currently deployed image while still running the full integration
# suite. Local invocation remains build-by-default.
BUILD_SERVICE_IMAGE="${BUILD_SERVICE_IMAGE:-true}"
if [[ "$BUILD_SERVICE_IMAGE" = "true" ]]; then
    BEHAVIOR_ANALYTICS_IMAGE="${BEHAVIOR_ANALYTICS_IMAGE:-vss-behavior-analytics:latest}"
    BUILD_TIMEOUT="${BUILD_TIMEOUT:-3600}"
    echo "Building changed behavior-analytics source as $BEHAVIOR_ANALYTICS_IMAGE (timeout ${BUILD_TIMEOUT}s)..."
    if timeout "$BUILD_TIMEOUT" docker build -t "$BEHAVIOR_ANALYTICS_IMAGE" -f docker/Dockerfile .; then
        echo "✓ Docker build completed successfully"
    else
        EXIT_CODE=$?
        if [[ $EXIT_CODE -eq 124 ]]; then
            echo "✗ Docker build timed out after $BUILD_TIMEOUT seconds"
        else
            echo "✗ Docker build failed"
        fi
        exit 1
    fi
else
    BEHAVIOR_ANALYTICS_IMAGE="${CURRENT_BEHAVIOR_ANALYTICS_IMAGE:?CURRENT_BEHAVIOR_ANALYTICS_IMAGE is required when BUILD_SERVICE_IMAGE=false}"
    echo "No behavior-analytics build-input change; pulling current image $BEHAVIOR_ANALYTICS_IMAGE"
    docker pull "$BEHAVIOR_ANALYTICS_IMAGE"
fi
export BEHAVIOR_ANALYTICS_IMAGE

cd "$MDX_SAMPLE_APPS_DIR"
echo "Starting Docker Compose services..."

# Compose command base (same for build and up)
COMPOSE_BASE="docker compose -f infra/compose.yml -f apps/mdx-apps.yml"
if [[ "$STREAMING_SERVICE" != "kafka" ]]; then
    COMPOSE_BASE="$COMPOSE_BASE --profile $STREAMING_SERVICE"
fi

COMPOSE_CMD="$COMPOSE_BASE up -d --build --force-recreate"

dump_compose_debug() {
    echo "--- Docker Compose status (debug) ---"
    (cd "$MDX_SAMPLE_APPS_DIR" && $COMPOSE_BASE ps -a 2>/dev/null) || true
    echo "--- Docker Compose logs (debug) ---"
    (cd "$MDX_SAMPLE_APPS_DIR" && $COMPOSE_BASE logs --tail=120 2>/dev/null) || true
    echo "--- mdx-kafka inspect (debug) ---"
    docker inspect mdx-kafka 2>/dev/null || true
    echo "--- end Docker Compose debug ---"
}

# Timeout for compose up
COMPOSE_TIMEOUT=${COMPOSE_TIMEOUT:-3600}
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
    dump_compose_debug
    kill -TERM $COMPOSE_PID 2>/dev/null
    sleep 60
    kill -9 $COMPOSE_PID 2>/dev/null
    wait $COMPOSE_PID 2>/dev/null
    COMPOSE_EXIT=1
fi
if [[ $COMPOSE_EXIT -eq 0 ]]; then
    echo "✓ Docker Compose started successfully"
else
    if [[ $TIMED_OUT -eq 1 ]]; then
        echo "✗ Docker Compose timed out after $COMPOSE_TIMEOUT seconds"
    else
        echo "✗ Docker Compose failed to start (exit $COMPOSE_EXIT)"
    fi
    dump_compose_debug
    # Call the cleanup function based on mode
    if [[ "$MODE" = "prod" ]]; then
        cleanup_docker_environment
        if [[ $? -ne 0 ]]; then
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
if [[ "$PROFILE1" = "warehouse_2d" ]] && [[ "$PROFILE2" = "kafka" ]]; then
    EXPECTED_COUNT=5  # Main process + 4 workers
elif [[ "$PROFILE1" = "smart_city" ]] && ( [[ "$PROFILE2" = "redis" ]] || [[ "$PROFILE2" = "mqtt" ]] ); then
    EXPECTED_COUNT=2
elif [[ "$PROFILE1" = "warehouse_3d" ]]; then
    EXPECTED_COUNT=4  # Main process + 3 workers
else
    EXPECTED_COUNT=3  # Main process + 2 workers
fi

echo "Expected $EXPECTED_COUNT processes for $PROFILE1 with $PROFILE2"

if [[ "$WORKER_COUNT" -ne "$EXPECTED_COUNT" ]]; then
    echo "✗ Process count mismatch: expected $EXPECTED_COUNT but found $WORKER_COUNT"
    if [[ "$MODE" = "prod" ]]; then
        cleanup_docker_environment
        if [[ $? -ne 0 ]]; then
            echo "✗ Docker cleanup failed"
        fi
    else
        echo "Development mode: Please check the docker log and clean up the docker environment manually"
    fi
    exit 1
else
    echo "✓ Process count verified: $WORKER_COUNT processes running as expected"
fi

# Wait for the playback container to finish feeding data. Draining is handled after this, by
# stopping the app and polling Elasticsearch. Falls back to a 10-minute hard cap so a stuck playback
# never wedges the test indefinitely.
echo "Waiting for mdx-analytics-playback to exit (max 10 min)..."
PLAYBACK_WAIT_DEADLINE_SEC=600
deadline=$(( $(date +%s) + PLAYBACK_WAIT_DEADLINE_SEC ))
while true; do
    status=$(docker inspect --format '{{.State.Status}}' mdx-analytics-playback 2>/dev/null || echo "missing")
    if [[ "$status" = "exited" ]]; then
        echo "✓ mdx-analytics-playback exited"
        break
    fi
    if [[ "$(date +%s)" -ge "$deadline" ]]; then
        echo "✗ mdx-analytics-playback did not exit within ${PLAYBACK_WAIT_DEADLINE_SEC}s (last status: $status)"
        if [[ "$MODE" = "prod" ]]; then
            cleanup_docker_environment
        fi
        exit 1
    fi
    sleep 5
done

cd "$PROJ_ROOT_DIR"

TEST_HOST="${TEST_HOST:-}"
if [ -z "$TEST_HOST" ] && [ "${CI:-}" = "true" ] && [ "${DOCKER_HOST:-}" = "unix:///var/run/docker.sock" ]; then
    TEST_HOST="$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}')"
fi
TEST_HOST="${TEST_HOST:-localhost}"
ES_URL="${ES_URL:-http://${TEST_HOST}:9200}"
echo "Using Elasticsearch URL: $ES_URL"

# Wait until Elasticsearch stops receiving new documents. A fixed grace period is the wrong
# instrument: it has to be long enough for the slowest host, and when it is too short the shortfall
# reads as a data mismatch rather than as a test that measured too early. Poll the indices instead,
# and move on once the counts stop changing.
wait_for_elasticsearch_to_settle() {
    local stable_needed=${ES_SETTLE_STABLE_CHECKS:-3}
    local interval=${ES_SETTLE_INTERVAL_SEC:-5}
    local deadline=$(( $(date +%s) + ${ES_SETTLE_DEADLINE_SEC:-300} ))
    local previous="" current stable=0

    while true; do
        current=$(curl -s "$ES_URL/mdx-*/_count" 2>/dev/null | python3 -c \
            'import json,sys; print(json.load(sys.stdin)["count"])' 2>/dev/null || echo "")

        if [[ -n "$current" && "$current" = "$previous" ]]; then
            stable=$((stable + 1))
            if [[ $stable -ge $stable_needed ]]; then
                echo "✓ Elasticsearch settled at $current document(s)"
                return 0
            fi
        else
            stable=0
        fi

        previous="$current"

        if [[ "$(date +%s)" -ge "$deadline" ]]; then
            # Still moving at the deadline, so any snapshot taken now is arbitrary. Report it as a
            # failure rather than extracting: comparing a knowingly-unstable index turns an
            # ingestion problem into a data mismatch, which is the harder thing to diagnose.
            echo "✗ Elasticsearch still changing after ${ES_SETTLE_DEADLINE_SEC:-300}s (last count: ${current:-unknown})"
            return 1
        fi

        sleep "$interval"
    done
}

# Shared exit path for infrastructure failures. Distinct from a comparison failure: the data was
# never in a state worth comparing, so say that rather than letting it surface as missing records.
abort_on_infrastructure_failure() {
    echo "✗ $1"
    if [[ "$MODE" = "prod" ]]; then
        cleanup_docker_environment
        if [[ $? -ne 0 ]]; then
            echo "✗ Docker cleanup failed"
        fi
    else
        echo "Development mode: Skipping cleanup to allow debugging"
    fi
    exit 1
}

# Drain what the running app still owes before stopping it. Not every output is tied to the
# holdback: space utilization is emitted on a timer for as long as the app runs, so stopping as soon
# as playback exits truncates that series -- the fixed 60s sleep this replaced was, incidentally,
# what used to let it finish.
echo "Waiting for in-flight output to settle before stopping the app..."
wait_for_elasticsearch_to_settle || abort_on_infrastructure_failure \
    "Elasticsearch never settled while the app was running; not extracting a moving target"

# Then stop the app, because under behaviorEmitOnce a track's behavior is written only when the
# track ends -- after behaviorStateValidInterval seconds of *event-time* silence. Playback exiting
# freezes each sensor's clock instead of advancing it, so tracks that were still live are never
# ended and their behaviors sit in the holdback. The app's close hook flushes them (app_scheduler.py
# runs it in each worker's finally), so a graceful SIGTERM is what puts them on the stream at all.
# -t is generous because the flush writes through the sink before it returns.
#
# A failed stop is fatal for the same reason the stop exists: without it the holdback is never
# flushed, so the run would go on to compare a behavior stream that is missing every track still
# live at the end -- an infrastructure failure wearing the costume of a data mismatch.
echo "Stopping mdx-analytics so emit-once behaviors are flushed..."
if ! docker stop -t "${APP_STOP_TIMEOUT_SEC:-60}" mdx-analytics > /dev/null 2>&1; then
    abort_on_infrastructure_failure \
        "Failed to stop mdx-analytics; emit-once behaviors were never flushed"
fi

# `docker stop` succeeding only means the container is no longer running. It sends SIGTERM, waits
# out the timeout, then SIGKILL -- and reports success either way. SIGKILL cannot be handled, so the
# close hook never runs and the holdback is lost, which is precisely the failure this stop exists to
# prevent. The exit status is what distinguishes the two: 0 for the signal handler returning
# normally, 137 (128 + SIGKILL) when Docker had to force it.
APP_EXIT_CODE=$(docker inspect -f '{{.State.ExitCode}}' mdx-analytics 2>/dev/null || echo "unknown")
if [[ "$APP_EXIT_CODE" = "0" ]]; then
    echo "✓ mdx-analytics stopped gracefully (exit 0)"
elif [[ "$APP_EXIT_CODE" = "137" ]]; then
    abort_on_infrastructure_failure \
        "mdx-analytics was SIGKILLed after the ${APP_STOP_TIMEOUT_SEC:-60}s timeout (exit 137); the close hook never ran, so emit-once behaviors were never flushed"
else
    abort_on_infrastructure_failure \
        "mdx-analytics exited $APP_EXIT_CODE rather than 0; the close hook cannot be assumed to have flushed emit-once behaviors"
fi

# Exit 0 is the parent's verdict, and the parent reports success even when it had to kill a worker.
# MultiprocessingScheduler.shutdown gives each worker SHUTDOWN_TIMEOUT_SECONDS after SIGTERM, then
# SIGKILLs whatever is still alive and carries on -- so a behavior worker can lose its close hook,
# and its share of the holdback, without leaving a trace in the exit status. The scheduler does say
# so in the log, which is the only place that distinction survives.
if docker logs mdx-analytics 2>&1 | grep -q "did not terminate gracefully, forcing kill"; then
    abort_on_infrastructure_failure \
        "a worker was SIGKILLed during shutdown; its emit-once behaviors were never flushed (see 'forcing kill' in mdx-analytics logs)"
fi
echo "✓ every worker shut down without being force-killed"

# The MQTT sink cannot recover a publish it failed to drain -- by the time close() runs, a broker
# that is not answering will not answer -- so it logs and lets shutdown finish. That leaves the run
# exiting 0 through both checks above with behaviors missing, which would surface as a comparison
# mismatch. The likeliest cause is not a dead broker but a drain window too short for a loaded host,
# and that is worth naming rather than inferring from absent records.
if docker logs mdx-analytics 2>&1 | grep -q "Queued MQTT messages"; then
    abort_on_infrastructure_failure \
        "the MQTT sink could not confirm delivery before shutdown; behaviors are missing (see 'Queued MQTT messages' in mdx-analytics logs)"
fi

echo "Waiting for the flush to land in Elasticsearch..."
wait_for_elasticsearch_to_settle || abort_on_infrastructure_failure \
    "Elasticsearch never settled after the flush; extracted data would be incomplete"

# Define which data types to dump/compare for each profile
get_data_types_for_profile() {
    local profile=$APP_NAME$APP_MODE
    case $profile in
        "warehouse_2d")
            echo "mdx-behavior-data.json mdx-events-data.json mdx-frames-data.json mdx-incidents-data.json mdx-raw-data.json"
            ;;
        # Space utilization is not compared for warehouse_3d: it is emitted on a
        # timer rather than per frame, so its record spacing and count track host
        # load. It has failed in two unrelated ways -- a 266ms timestamp delta
        # against a 100ms tolerance, and a short extract (738 of 741 records) --
        # while passing in between, so it reports host timing rather than a
        # regression in this service.
        "warehouse_3d")
            echo "mdx-behavior-data.json mdx-events-data.json mdx-frames-data.json mdx-incidents-data.json"
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
    local dump_args=()

    case $data_type in
        "mdx-raw-data.json")
            elasticsearch_index="mdx-raw*"
            dump_args=(--scroll 10m)
            ;;
        "mdx-frames-data.json")
            elasticsearch_index="mdx-frames*"
            dump_args=(--scroll 10m)
            ;;
        "mdx-behavior-data.json")
            elasticsearch_index="mdx-behavior*"
            ;;
        "mdx-events-data.json")
            elasticsearch_index="mdx-events*"
            ;;
        "mdx-alerts-data.json")
            elasticsearch_index="mdx-alerts*"
            ;;
        "mdx-incidents-data.json")
            elasticsearch_index="mdx-incidents*"
            ;;
        "mdx-space-utilization-data.json")
            elasticsearch_index="mdx-space-utilization*"
            ;;
        *)
            echo "Unknown data type: $data_type"
            return 1
            ;;
    esac

    echo "Extracting $data_type from $elasticsearch_index..."
    ELASTICDUMP_TIMEOUT=180
    ELASTICDUMP_OUTPUT=$(
        timeout "$ELASTICDUMP_TIMEOUT" python3 tests/integration/dump_es_data.py \
            --url "$ES_URL" \
            --index "$elasticsearch_index" \
            --output "tests/integration/docker_compose/apps_data/data_log/tmp/$data_type" \
            "${dump_args[@]}" 2>&1
    )
    EXIT_CODE=$?

    if [[ $EXIT_CODE -eq 0 ]]; then
        echo "✓ $data_type extraction complete"
        return 0
    elif [[ $EXIT_CODE -eq 124 ]]; then
        echo "✗ $data_type extraction timed out after $ELASTICDUMP_TIMEOUT seconds"
        return 1
    else
        echo "✗ $data_type extraction failed:"
        echo "$ELASTICDUMP_OUTPUT"
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

if [[ "$EXTRACTION_FAILED" = true ]]; then
    echo "✗ Some data extractions failed"
    if [[ "$MODE" = "prod" ]]; then
        cleanup_docker_environment
        if [[ $? -ne 0 ]]; then
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
    BASELINE_FILE="tests/integration/expected_output/$APP_NAME$APP_MODE/$data_type"
    EXTRACTED_FILE="tests/integration/docker_compose/apps_data/data_log/tmp/$data_type"

    if [[ ! -f "$BASELINE_FILE" ]]; then
        echo "✗ $data_type comparison failed: missing baseline $BASELINE_FILE"
        COMPARISON_OUTPUTS+=("Missing baseline file: $BASELINE_FILE")
        COMPARISON_RESULTS+=("fail")
        continue
    fi

    if [[ ! -f "$EXTRACTED_FILE" ]]; then
        echo "✗ $data_type comparison failed: missing extracted data $EXTRACTED_FILE"
        COMPARISON_OUTPUTS+=("Missing extracted file: $EXTRACTED_FILE")
        COMPARISON_RESULTS+=("fail")
        continue
    fi

    BASELINE_LINES=$(wc -l < "$BASELINE_FILE")
    EXTRACTED_LINES=$(wc -l < "$EXTRACTED_FILE")
    if [[ "$EXTRACTED_LINES" -lt "$BASELINE_LINES" ]]; then
        echo "✗ $data_type comparison failed: extracted has $EXTRACTED_LINES records, baseline has $BASELINE_LINES"
        COMPARISON_OUTPUTS+=("Record count mismatch: extracted=$EXTRACTED_LINES baseline=$BASELINE_LINES")
        COMPARISON_RESULTS+=("fail")
        continue
    fi

    COMPARISON_OUTPUT=$(python3 tests/integration/docker_compose/infra/scripts/compare_mdx_data.py "$BASELINE_FILE" "$EXTRACTED_FILE" 2>&1)
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
    if [[ "$result" = "fail" ]]; then
        ALL_PASSED=false
        break
    fi
done

if [[ "$ALL_PASSED" = true ]]; then
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
if [[ "$COMPARISON_RESULT" = "fail" ]]; then
    echo "❌ Test FAILED for $PROFILE1 $PROFILE2"
    if [[ "$MODE" = "prod" ]]; then
        cleanup_docker_environment
        if [[ $? -ne 0 ]]; then
            echo "Docker cleanup failed"
        fi
    else
        echo "Development mode: Please check the comparison results above and clean up the docker environment manually"
    fi
    exit 1
else
    echo "✅ Test PASSED for $PROFILE1 $PROFILE2"
    cleanup_docker_environment
    if [[ $? -ne 0 ]]; then
        echo "Docker cleanup failed"
        exit 1
    fi
    exit 0
fi
