#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PERF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$(cd "$PERF_DIR/.." && pwd)"
INTEGRATION_DIR="$SERVICE_DIR/test/integration-test"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-va-api-perf}"
APP_CONTAINER="${APP_CONTAINER:-va-api-perf-app}"
ES_CONTAINER="${ES_CONTAINER:-va-api-perf-elasticsearch}"
PERF_SCENARIO="${PERF_SCENARIO:-quick_test}"
PERF_CONFIG_ID="${PERF_CONFIG_ID:-warehouse-es-dump}"
if [[ -f "$PERF_DIR/benchmark/scenario_config.py" && -f "$PERF_DIR/configs/config.yaml" ]]; then
    eval "$(python3 "$PERF_DIR/benchmark/scenario_config.py" \
        --config "$PERF_DIR/configs/config.yaml" \
        --scenario "$PERF_SCENARIO")"
fi
PERF_REQUEST_COUNT="${PERF_REQUEST_COUNT:-${CONFIG_REQUEST_COUNT:-20}}"
PERF_WARMUP_REQUESTS="${PERF_WARMUP_REQUESTS:-${CONFIG_WARMUP_REQUESTS:-2}}"
PERF_SENSOR_ID="${PERF_SENSOR_ID:-${CONFIG_SENSOR_ID:-Camera}}"
PERF_FROM_TS="${PERF_FROM_TS:-${CONFIG_FROM_TS:-2026-02-14T10:16:00.000Z}}"
PERF_TO_TS="${PERF_TO_TS:-${CONFIG_TO_TS:-2026-02-14T10:17:00.000Z}}"
PERF_ENDPOINTS="${PERF_ENDPOINTS:-${CONFIG_ENDPOINTS:-livez frames behavior events incidents}}"
OUTPUT_DIR="${OUTPUT_DIR:-${PERF_OUTPUT_DIR:-$PERF_DIR/results/${PERF_SERVICE:-video-analytics-api}_${PERF_SCENARIO}_${PERF_CONFIG_ID}}}"
ELASTICSEARCH_HOST_PORT="${ELASTICSEARCH_HOST_PORT:-19200}"
VIDEO_ANALYTICS_API_HOST_PORT="${VIDEO_ANALYTICS_API_HOST_PORT:-18081}"
export VA_API_PERF_DATA_DIR="${VA_API_PERF_DATA_DIR:-/tmp/va-api-perf-${COMPOSE_PROJECT_NAME}-$(id -u)}"
DUMP_DIR="$INTEGRATION_DIR/elasticsearch_data_dump"
ENV_FILE="$INTEGRATION_DIR/docker_compose/infra/.env"

refuse_symlink() {
    local path="$1"
    if [[ -L "$path" ]]; then
        echo "Refusing symlink $path" >&2
        return 1
    fi
}

ensure_host_dir() {
    local path="$1"
    refuse_symlink "$path"
    mkdir -p "$path"
    refuse_symlink "$path"
}

remove_root_tree() {
    local path="$1"
    [[ -e "$path" || -L "$path" ]] || return 0
    if [[ -L "$path" ]]; then
        echo "Refusing to delete symlink $path" >&2
        return 0
    fi
    if [[ ! -d "$path" ]]; then
        echo "Refusing to delete non-directory $path" >&2
        return 0
    fi
    docker run --rm -u 0 -v "$path:/data" alpine:3.20 \
        sh -c 'chmod -R a+rwX /data 2>/dev/null; rm -rf /data/..?* /data/.[!.]* /data/*' \
        >/dev/null 2>&1 || true
    rm -rf "$path" >/dev/null 2>&1 || true
}

resolve_repo_root() {
    local git_root
    git_root="$(git -C "$SERVICE_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$git_root" ]]; then
        printf '%s\n' "$git_root"
        return 0
    fi
    cd "$INTEGRATION_DIR/../../../../.." && pwd
}

resolve_apps_dir() {
    local repo_root="$1"
    if [[ -f "$repo_root/deploy/docker/services/infra/compose.yml" ]]; then
        printf '%s\n' "$repo_root/deploy/docker"
    elif [[ -f "$repo_root/compose/docker/services/infra/compose.yml" ]]; then
        printf '%s\n' "$repo_root/compose/docker"
    else
        echo "Missing deploy/docker/services/infra/compose.yml (and compose/docker fallback)." >&2
        return 1
    fi
}

resolve_image() {
    if [[ -n "${ARTIFACT_REF:-}" ]]; then
        printf '%s\n' "$ARTIFACT_REF"
    elif [[ -n "${IMAGE_TAG:-}" ]]; then
        printf '%s\n' "$IMAGE_TAG"
    elif [[ -n "${VIDEO_ANALYTICS_API_IMAGE:-}" ]]; then
        printf '%s\n' "$VIDEO_ANALYTICS_API_IMAGE"
    elif [[ -n "${CURRENT_VIDEO_ANALYTICS_API_IMAGE:-}" ]]; then
        printf '%s\n' "$CURRENT_VIDEO_ANALYTICS_API_IMAGE"
    else
        echo "Set ARTIFACT_REF to the promoted Video Analytics API image." >&2
        return 1
    fi
}

write_compose_env() {
    local repo_root apps_dir infra_dir image
    repo_root="$(resolve_repo_root)"
    apps_dir="$(resolve_apps_dir "$repo_root")"
    infra_dir="$apps_dir/services/infra"
    image="$(resolve_image)"
    ensure_host_dir "$VA_API_PERF_DATA_DIR/data_log/video-analytics-api-app/files"
    ensure_host_dir "$VA_API_PERF_DATA_DIR/elastic/data"
    ensure_host_dir "$VA_API_PERF_DATA_DIR/elastic/logs"
    chmod a+rwX "$VA_API_PERF_DATA_DIR" "$VA_API_PERF_DATA_DIR/elastic" \
        "$VA_API_PERF_DATA_DIR/elastic/data" "$VA_API_PERF_DATA_DIR/elastic/logs" \
        "$VA_API_PERF_DATA_DIR/data_log/video-analytics-api-app/files" 2>/dev/null || true
    ensure_host_dir "$(dirname "$ENV_FILE")"
    # Docker Compose --env-file does not accept `export KEY=`. KEY=VALUE only.
    cat > "$ENV_FILE" << EOF
INTEGRATION_TEST_DIR=$INTEGRATION_DIR
VIDEO_ANALYTICS_API_ROOT=$SERVICE_DIR
DATA_DIR=$VA_API_PERF_DATA_DIR
HOST_IP=localhost
INFRA_DIR=$infra_dir
VSS_APPS_DIR=$apps_dir
VSS_DATA_DIR=$VA_API_PERF_DATA_DIR
VSS_VERSION=
COMPOSE_PROFILES=elasticsearch,elasticsearch-init-container
COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME
STREAM_TYPE=
VSS_PUBLIC_HOST=
VSS_PUBLIC_PORT=
EXTERNAL_IP=
NUM_STREAMS=
NUM_SENSORS=
ELASTICSEARCH_HOST_PORT=$ELASTICSEARCH_HOST_PORT
VIDEO_ANALYTICS_API_HOST_PORT=$VIDEO_ANALYTICS_API_HOST_PORT
VIDEO_ANALYTICS_API_IMAGE=$image
CURRENT_VIDEO_ANALYTICS_API_IMAGE=$image
BUILD_SERVICE_IMAGE=false
EOF
    export INTEGRATION_TEST_DIR SERVICE_DIR
    export DATA_DIR="$VA_API_PERF_DATA_DIR"
    export INFRA_DIR="$infra_dir"
    export VSS_APPS_DIR="$apps_dir"
    export VSS_DATA_DIR="$VA_API_PERF_DATA_DIR"
    export COMPOSE_PROFILES="elasticsearch,elasticsearch-init-container"
    export COMPOSE_PROJECT_NAME
    export ELASTICSEARCH_HOST_PORT VIDEO_ANALYTICS_API_HOST_PORT VSS_VERSION
    export VIDEO_ANALYTICS_API_IMAGE="$image"
    export CURRENT_VIDEO_ANALYTICS_API_IMAGE="$image"
    export BUILD_SERVICE_IMAGE=false
}

load_compose_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        write_compose_env
    fi
    set -a
    # shellcheck disable=SC1091
    source "$ENV_FILE"
    set +a
    export VIDEO_ANALYTICS_API_IMAGE="$(resolve_image)"
    export CURRENT_VIDEO_ANALYTICS_API_IMAGE="$VIDEO_ANALYTICS_API_IMAGE"
    export COMPOSE_PROJECT_NAME
}

compose() {
    docker compose --project-directory "$INFRA_DIR" \
        --env-file "$ENV_FILE" \
        -p "$COMPOSE_PROJECT_NAME" \
        -f "$INFRA_DIR/compose.yml" \
        -f "$INTEGRATION_DIR/docker_compose/infra/video-analytics-api-infra.yml" \
        -f "$INTEGRATION_DIR/docker_compose/apps/video-analytics-api-app.yml" \
        -f "$PERF_DIR/setup/compose-names.yml" \
        -f "$PERF_DIR/setup/compose-es.yml" \
        "$@"
}

dump_es_debug() {
    echo "--- elasticsearch inspect ---" >&2
    docker inspect "$ES_CONTAINER" --format \
        'status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}}' \
        >&2 || true
    echo "--- elasticsearch logs ---" >&2
    docker logs "$ES_CONTAINER" >&2 || true
    echo "--- vm.max_map_count ---" >&2
    cat /proc/sys/vm/max_map_count >&2 || true
    echo "--- ss :9200/:19200 ---" >&2
    ss -tlnp 2>/dev/null | grep -E ':9200|:19200' >&2 || true
}

wait_for_elasticsearch() {
    local deadline=$((SECONDS + 180))
    local health status
    while (( SECONDS < deadline )); do
        status="$(docker inspect -f '{{.State.Status}}' "$ES_CONTAINER" 2>/dev/null || echo missing)"
        health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$ES_CONTAINER" 2>/dev/null || echo none)"
        if [[ "$status" == "exited" || "$status" == "dead" || "$status" == "missing" ]]; then
            echo "Elasticsearch container $status" >&2
            dump_es_debug
            return 1
        fi
        if curl -sf "http://127.0.0.1:${ELASTICSEARCH_HOST_PORT}/_cluster/health" >/dev/null 2>&1; then
            echo "Elasticsearch is reachable on :${ELASTICSEARCH_HOST_PORT} (docker health=${health})"
            return 0
        fi
        sleep 5
    done
    echo "Timed out waiting for Elasticsearch on :${ELASTICSEARCH_HOST_PORT}" >&2
    dump_es_debug
    return 1
}
