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

# Video Analytics API integration test: Docker Compose (Elasticsearch + video-analytics-api) + HTTP assertions.
# Usage: ./test.sh [mode]
#   mode: dev (default) or prod - cleanup always runs on exit (success or failure)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-dev}"

if [ "$MODE" != "dev" ] && [ "$MODE" != "prod" ]; then
    echo "Invalid mode: $MODE. Must be 'dev' or 'prod'"
    exit 1
fi

echo "Running video-analytics-api integration test (mode=$MODE)"
source "$SCRIPT_DIR/generate_env.sh"
. "$SCRIPT_DIR/docker_compose/.env"
source "$SCRIPT_DIR/cleanup.sh"

TEST_HOST="${TEST_HOST:-}"
if [ -z "$TEST_HOST" ] && [ "${CI:-}" = "true" ] && [ "${DOCKER_HOST:-}" = "unix:///var/run/docker.sock" ]; then
    TEST_HOST="$(ip -4 route show default 2>/dev/null | cut -d" " -f3 | head -n 1)"
fi
TEST_HOST="${TEST_HOST:-localhost}"
ES_URL="${ES_URL:-http://${TEST_HOST}:9200}"
VA_API_URL="${VA_API_URL:-http://${TEST_HOST}:8081}"
echo "Testing Elasticsearch at $ES_URL"
echo "Testing video-analytics-api at $VA_API_URL"

# Build the checked-out service only when its build inputs changed. Otherwise,
# exercise the currently deployed image while still running the full integration
# suite. Local invocation remains build-by-default.
BUILD_SERVICE_IMAGE="${BUILD_SERVICE_IMAGE:-true}"
if [ "$BUILD_SERVICE_IMAGE" = "true" ]; then
    VIDEO_ANALYTICS_API_IMAGE="${VIDEO_ANALYTICS_API_IMAGE:-video-analytics-api:integration-test}"
    cd "$VIDEO_ANALYTICS_API_ROOT"
    BUILD_TIMEOUT="${BUILD_TIMEOUT:-3600}"
    echo "Building changed video-analytics-api source as $VIDEO_ANALYTICS_API_IMAGE (timeout ${BUILD_TIMEOUT}s)..."
    if timeout "$BUILD_TIMEOUT" docker build -t "$VIDEO_ANALYTICS_API_IMAGE" -f docker/Dockerfile . ; then
        echo "✓ Docker build completed"
    else
        EXIT_CODE=$?
        if [ "${EXIT_CODE}" -eq 124 ]; then
            echo "✗ Docker build timed out after $BUILD_TIMEOUT seconds"
        else
            echo "✗ Docker build failed"
        fi
        exit 1
    fi
else
    VIDEO_ANALYTICS_API_IMAGE="${CURRENT_VIDEO_ANALYTICS_API_IMAGE:?CURRENT_VIDEO_ANALYTICS_API_IMAGE is required when BUILD_SERVICE_IMAGE=false}"
    echo "No video-analytics-api build-input change; pulling current image $VIDEO_ANALYTICS_API_IMAGE"
    docker pull "$VIDEO_ANALYTICS_API_IMAGE"
fi
export VIDEO_ANALYTICS_API_IMAGE

# Start stack
cd "$INTEGRATION_TEST_DIR/docker_compose"

# Port 9200 must be free (e.g. stop met-blueprints/mdx-elastic first if running)
if command -v ss >/dev/null 2>&1; then
    if ss -tlnp 2>/dev/null | grep -q ':9200 '; then
        echo "✗ Port 9200 is in use. Stop other Elasticsearch (e.g. met-blueprints: docker stop mdx-elastic) and retry."
        exit 1
    fi
elif command -v netstat >/dev/null 2>&1; then
    if netstat -tlnp 2>/dev/null | grep -q ':9200 '; then
        echo "✗ Port 9200 is in use. Stop other Elasticsearch (e.g. met-blueprints: docker stop mdx-elastic) and retry."
        exit 1
    fi
fi

COMPOSE_BASE="docker compose --profile elasticsearch -f $SHARED_INFRA_COMPOSE -f apps/video-analytics-api-app.yml"
COMPOSE_TIMEOUT="${COMPOSE_TIMEOUT:-3600}"
echo "Starting Docker Compose (timeout ${COMPOSE_TIMEOUT}s)..."
$COMPOSE_BASE up -d --force-recreate & COMPOSE_PID=$!
COMPOSE_EXIT=0
TIMED_OUT=0
for i in $(seq 1 "$COMPOSE_TIMEOUT"); do
    if ! kill -0 $COMPOSE_PID 2>/dev/null; then
        COMPOSE_EXIT=0
        wait $COMPOSE_PID || COMPOSE_EXIT=$?
        break
    fi
    sleep 1
done
if kill -0 $COMPOSE_PID 2>/dev/null; then
    TIMED_OUT=1
    echo "✗ Docker Compose timed out after $COMPOSE_TIMEOUT seconds"
    kill -TERM $COMPOSE_PID 2>/dev/null; sleep 5; kill -9 $COMPOSE_PID 2>/dev/null || true
    wait $COMPOSE_PID 2>/dev/null || true
    COMPOSE_EXIT=1
fi
echo "--- Elasticsearch container logs (web-api-elastic) ---"
docker logs web-api-elastic 2>&1 || true
echo "--- end logs ---"

if [ "$COMPOSE_EXIT" -ne 0 ]; then
    echo "✗ Docker Compose failed"
    cleanup_docker_environment
    exit 1
fi
echo "✓ Docker Compose started"

# Wait for Elasticsearch then video-analytics-api
echo "Waiting for Elasticsearch (up to 60s)..."
for i in $(seq 1 60); do
    if curl -sf "$ES_URL/_cluster/health" >/dev/null 2>&1; then
        echo "✓ Elasticsearch is up"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "✗ Elasticsearch did not become ready"
        echo "--- Elasticsearch container logs (web-api-elastic) ---"
        docker logs web-api-elastic 2>&1 || true
        echo "--- end logs ---"
        cleanup_docker_environment
        exit 1
    fi
    sleep 1
done

# Create ingest pipeline required for config uploads (road-network, usd-assets, etc.)
echo "Creating Elasticsearch ingest pipeline..."
if ! bash "$SCRIPT_DIR/scripts/setup_elasticsearch_ingest_pipeline.sh" "$ES_URL"; then
    echo "✗ Ingest pipeline setup failed"
    cleanup_docker_environment
    exit 1
fi

# Load Elasticsearch data dump for testing remaining endpoints (if present)
DUMP_DIR="$INTEGRATION_TEST_DIR/elasticsearch_data_dump"
if [ -d "$DUMP_DIR" ]; then
    echo "Loading Elasticsearch data dump from $DUMP_DIR..."
    if ! node "$SCRIPT_DIR/scripts/load_elasticsearch_data_dump.js" "$ES_URL" "$DUMP_DIR"; then
        echo "✗ Elasticsearch data dump load failed"
        cleanup_docker_environment
        exit 1
    fi
else
    echo "No elasticsearch_data_dump directory, skipping data load."
fi

echo "Waiting for video-analytics-api (up to 90s)..."
for i in $(seq 1 90); do
    if curl -sf "$VA_API_URL/livez" >/dev/null 2>&1; then
        echo "✓ video-analytics-api is up"
        break
    fi
    if [ "$i" -eq 90 ]; then
        echo "✗ video-analytics-api did not become ready"
        echo "--- video-analytics-api container logs (video-analytics-api-integration) ---"
        docker logs video-analytics-api-integration 2>&1 || true
        echo "--- end logs ---"
        cleanup_docker_environment
        exit 1
    fi
    sleep 1
done

# Run integration tests (includes warehouse app fixture generation)
echo "Running integration tests..."
cd "$SCRIPT_DIR"
if ! bash scripts/run_integration_tests.sh "$VA_API_URL"; then
    echo "✗ Integration tests failed"
    echo "--- video-analytics-api container logs (video-analytics-api-integration) ---"
    docker logs video-analytics-api-integration 2>&1 || true
    echo "--- end logs ---"
    cleanup_docker_environment
    exit 1
fi

echo "✅ Integration test PASSED"
cleanup_docker_environment
exit 0
