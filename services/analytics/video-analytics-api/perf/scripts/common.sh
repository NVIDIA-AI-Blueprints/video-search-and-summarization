#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PERF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$(cd "$PERF_DIR/.." && pwd)"
INTEGRATION_DIR="$SERVICE_DIR/test/integration-test"
INTEGRATION_TEST_DIR="$INTEGRATION_DIR"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-va-api-perf}"
APP_CONTAINER="${APP_CONTAINER:-va-api-perf-app}"
ES_CONTAINER="${ES_CONTAINER:-va-api-perf-elasticsearch}"
PERF_SCENARIO="${PERF_SCENARIO:-quick_test}"
PERF_CONFIG_ID="${PERF_CONFIG_ID:-warehouse-es-dump}"
PERF_CONFIG_PATH="${PERF_CONFIG_PATH:-$PERF_DIR/configs/config.yaml}"
if [[ -f "$PERF_DIR/benchmark/scenario_config.py" && -f "$PERF_CONFIG_PATH" ]]; then
    eval "$(python3 "$PERF_DIR/benchmark/scenario_config.py" \
        --config "$PERF_CONFIG_PATH" \
        --scenario "$PERF_SCENARIO")"
fi
PERF_REQUEST_COUNT="${PERF_REQUEST_COUNT:-${CONFIG_REQUEST_COUNT:-20}}"
PERF_WARMUP_REQUESTS="${PERF_WARMUP_REQUESTS:-${CONFIG_WARMUP_REQUESTS:-2}}"
PERF_SENSOR_ID="${PERF_SENSOR_ID:-${CONFIG_SENSOR_ID:-Camera}}"
PERF_PLACE="${PERF_PLACE:-${CONFIG_PLACE:-building=Warehouse/room=Room-1}}"
PERF_FROM_TS="${PERF_FROM_TS:-${CONFIG_FROM_TS:-2026-02-14T10:16:00.000Z}}"
PERF_TO_TS="${PERF_TO_TS:-${CONFIG_TO_TS:-2026-02-14T10:17:00.000Z}}"
PERF_LOOKUP_X="${PERF_LOOKUP_X:-${CONFIG_LOOKUP_X:-0}}"
PERF_LOOKUP_Y="${PERF_LOOKUP_Y:-${CONFIG_LOOKUP_Y:-0}}"
PERF_ENDPOINTS="${PERF_ENDPOINTS:-${CONFIG_ENDPOINTS:-spec_gets}}"
OUTPUT_DIR="${OUTPUT_DIR:-${PERF_OUTPUT_DIR:-$PERF_DIR/results/${PERF_SERVICE:-video-analytics-api}_${PERF_SCENARIO}_${PERF_CONFIG_ID}}}"
ELASTICSEARCH_HOST_PORT="${ELASTICSEARCH_HOST_PORT:-19200}"
VIDEO_ANALYTICS_API_HOST_PORT="${VIDEO_ANALYTICS_API_HOST_PORT:-18081}"
export VA_API_PERF_DATA_DIR="${VA_API_PERF_DATA_DIR:-/tmp/va-api-perf-${COMPOSE_PROJECT_NAME}-$(id -u)}"
DUMP_DIR="$INTEGRATION_DIR/elasticsearch_data_dump"
ENV_FILE="$INTEGRATION_DIR/docker_compose/infra/.env"
COMPOSE_ENV_FILE="$ENV_FILE"
COMPOSE_EXTRA_FILES="$PERF_DIR/setup/compose-names.yml $PERF_DIR/setup/compose-es.yml"
# shellcheck disable=SC1091
source "$INTEGRATION_DIR/lifecycle.sh"
# shellcheck disable=SC1091
source "$INTEGRATION_DIR/cleanup.sh"
export INTEGRATION_TEST_DIR COMPOSE_ENV_FILE COMPOSE_EXTRA_FILES COMPOSE_PROJECT_NAME

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

# infra/compose.yml includes HAProxy with these env files. Compose parses the
# include even when the HAProxy profile is off. The sparse compose/docker tree
# does not ship agent/alert/vios, so create empty files if they are missing.
ensure_compose_include_env_files() {
    local apps_dir="$1"
    local rel path
    for rel in services/agent/agent.env services/alert/alert.env services/vios/vst.env; do
        path="$apps_dir/$rel"
        if [[ ! -f "$path" ]]; then
            ensure_host_dir "$(dirname "$path")"
            : > "$path"
        fi
    done
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
    local repo_root apps_dir image
    repo_root="$(resolve_repo_root)"
    apps_dir="$(resolve_apps_dir "$repo_root")"
    ensure_compose_include_env_files "$apps_dir"
    image="$(resolve_image)"
    ensure_host_dir "$VA_API_PERF_DATA_DIR/data_log/video-analytics-api-app/files"
    ensure_host_dir "$VA_API_PERF_DATA_DIR/elastic/data"
    ensure_host_dir "$VA_API_PERF_DATA_DIR/elastic/logs"
    chmod a+rwX "$VA_API_PERF_DATA_DIR" "$VA_API_PERF_DATA_DIR/elastic" \
        "$VA_API_PERF_DATA_DIR/elastic/data" "$VA_API_PERF_DATA_DIR/elastic/logs" \
        "$VA_API_PERF_DATA_DIR/data_log/video-analytics-api-app/files" 2>/dev/null || true
    ensure_host_dir "$(dirname "$ENV_FILE")"
    export DATA_DIR="$VA_API_PERF_DATA_DIR"
    export VSS_DATA_DIR="$VA_API_PERF_DATA_DIR"
    export INFRA_DIR="$apps_dir/services/infra"
    export VSS_APPS_DIR="$apps_dir"
    export COMPOSE_PROJECT_NAME
    export COMPOSE_EXTRA_FILES COMPOSE_ENV_FILE
    # Reuse integration generate_env.sh, then add perf host ports and image.
    # Docker Compose --env-file does not accept `export KEY=`. KEY=VALUE only.
    # shellcheck disable=SC1091
    source "$INTEGRATION_DIR/generate_env.sh"
    cat >> "$ENV_FILE" << EOF
ELASTICSEARCH_HOST_PORT=$ELASTICSEARCH_HOST_PORT
VIDEO_ANALYTICS_API_HOST_PORT=$VIDEO_ANALYTICS_API_HOST_PORT
VIDEO_ANALYTICS_API_IMAGE=$image
CURRENT_VIDEO_ANALYTICS_API_IMAGE=$image
BUILD_SERVICE_IMAGE=false
VSS_VERSION=
COMPOSE_EXTRA_FILES=$COMPOSE_EXTRA_FILES
COMPOSE_ENV_FILE=$ENV_FILE
EOF
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    export VIDEO_ANALYTICS_API_IMAGE="$image"
    export CURRENT_VIDEO_ANALYTICS_API_IMAGE="$image"
    export BUILD_SERVICE_IMAGE=false
    export ES_URL="http://127.0.0.1:${ELASTICSEARCH_HOST_PORT}"
    export VA_API_URL="http://127.0.0.1:${VIDEO_ANALYTICS_API_HOST_PORT}"
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
    COMPOSE_ENV_FILE="$ENV_FILE"
    export COMPOSE_ENV_FILE COMPOSE_EXTRA_FILES COMPOSE_PROJECT_NAME INFRA_DIR INTEGRATION_TEST_DIR
    va_api_compose "$@"
}

dump_es_debug() {
    echo "--- elasticsearch inspect ---" >&2
    docker inspect "$ES_CONTAINER" --format \
        'status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}}' \
        >&2 || true
    va_api_dump_service_logs
    echo "--- vm.max_map_count ---" >&2
    cat /proc/sys/vm/max_map_count >&2 || true
    echo "--- ss :9200/:19200 ---" >&2
    ss -tlnp 2>/dev/null | grep -E ':9200|:19200' >&2 || true
}

wait_for_elasticsearch() {
    ES_URL="${ES_URL:-http://127.0.0.1:${ELASTICSEARCH_HOST_PORT}}"
    va_api_wait_for_elasticsearch "$ES_URL" 36 5
}
