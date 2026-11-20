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

# Shared Video Analytics API stack helpers. Source after generate_env.sh.
# Used by test.sh (integration) and services/analytics/video-analytics-api/perf.

va_api_compose_file_args() {
    local extra
    printf -- '-f %s -f %s -f %s' \
        "$INFRA_DIR/compose.yml" \
        "$INTEGRATION_TEST_DIR/docker_compose/infra/video-analytics-api-infra.yml" \
        "$INTEGRATION_TEST_DIR/docker_compose/apps/video-analytics-api-app.yml"
    for extra in ${COMPOSE_EXTRA_FILES:-}; do
        printf -- ' -f %s' "$extra"
    done
}

va_api_compose() {
    # shellcheck disable=SC2046,SC2086
    docker compose --project-directory "$INFRA_DIR" \
        ${COMPOSE_ENV_FILE:+--env-file "$COMPOSE_ENV_FILE"} \
        ${COMPOSE_PROJECT_NAME:+-p "$COMPOSE_PROJECT_NAME"} \
        $(va_api_compose_file_args) \
        "$@"
}

va_api_dump_service_logs() {
    local es_container="${ES_CONTAINER:-elasticsearch}"
    local init_container="${ES_INIT_CONTAINER:-vss-elasticsearch-init}"
    local app_container="${APP_CONTAINER:-video-analytics-api-integration}"
    echo "--- Elasticsearch container logs ($es_container) ---"
    docker logs "$es_container" 2>&1 || true
    echo "--- init container logs ($init_container) ---"
    docker logs "$init_container" 2>&1 || true
    echo "--- video-analytics-api container logs ($app_container) ---"
    docker logs "$app_container" 2>&1 || true
    echo "--- end logs ---"
}

va_api_wait_for_elasticsearch() {
    local url="${1:-${ES_URL}}"
    local attempts="${2:-60}"
    local sleep_s="${3:-1}"
    local i status
    echo "Waiting for Elasticsearch at $url..."
    for i in $(seq 1 "$attempts"); do
        if [[ -n "${ES_CONTAINER:-}" ]]; then
            status="$(docker inspect -f '{{.State.Status}}' "$ES_CONTAINER" 2>/dev/null || echo missing)"
            if [[ "$status" == "exited" || "$status" == "dead" || "$status" == "missing" ]]; then
                echo "✗ Elasticsearch container $status"
                va_api_dump_service_logs
                return 1
            fi
        fi
        if curl -sf "$url/_cluster/health" >/dev/null 2>&1; then
            echo "✓ Elasticsearch is up"
            return 0
        fi
        if [[ "$i" -eq "$attempts" ]]; then
            echo "✗ Elasticsearch did not become ready"
            va_api_dump_service_logs
            return 1
        fi
        sleep "$sleep_s"
    done
}

va_api_verify_ingest_pipeline() {
    local url="${1:-${ES_URL}}"
    echo "Verifying Elasticsearch ingest pipeline..."
    if ! curl -sf "$url/_ingest/pipeline/insertion-timestamp-pipeline" >/dev/null; then
        echo "✗ insertion-timestamp-pipeline missing; elasticsearch-init-container did not complete"
        va_api_dump_service_logs
        return 1
    fi
    echo "✓ Ingest pipeline present"
}

va_api_load_elasticsearch_dumps() {
    local url="${1:-${ES_URL}}"
    local dump_dir="${2:-$INTEGRATION_TEST_DIR/elasticsearch_data_dump}"
    local script="$INTEGRATION_TEST_DIR/scripts/load_elasticsearch_data_dump.js"
    if [[ ! -d "$dump_dir" ]]; then
        echo "No elasticsearch_data_dump directory, skipping data load."
        return 0
    fi
    if [[ ! -f "$script" ]]; then
        echo "✗ Missing dump loader $script" >&2
        return 1
    fi
    echo "Loading Elasticsearch data dump from $dump_dir..."
    if command -v node >/dev/null 2>&1; then
        node "$script" "$url" "$dump_dir"
        return
    fi
    if [[ -z "${VIDEO_ANALYTICS_API_IMAGE:-}" ]]; then
        echo "✗ Node is not on PATH and VIDEO_ANALYTICS_API_IMAGE is unset; cannot run $script" >&2
        return 1
    fi
    # Distroless API image already has Node; reuse it instead of a second loader.
    docker run --rm --network host \
        --entrypoint node \
        -v "$script:/loader.js:ro" \
        -v "$dump_dir:/dumps:ro" \
        "$VIDEO_ANALYTICS_API_IMAGE" \
        /loader.js "$url" /dumps
}

va_api_wait_for_api() {
    local url="${1:-${VA_API_URL}}"
    local attempts="${2:-90}"
    local sleep_s="${3:-1}"
    local i
    echo "Waiting for video-analytics-api at $url..."
    for i in $(seq 1 "$attempts"); do
        if curl -sf "$url/livez" >/dev/null 2>&1; then
            echo "✓ video-analytics-api is up"
            return 0
        fi
        if [[ "$i" -eq "$attempts" ]]; then
            echo "✗ video-analytics-api did not become ready"
            va_api_dump_service_logs
            return 1
        fi
        sleep "$sleep_s"
    done
}
