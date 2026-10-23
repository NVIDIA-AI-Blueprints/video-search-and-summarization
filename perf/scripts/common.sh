#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PERF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PERF_DIR/.." && pwd)"
VSS_APPS_DIR="${VSS_APPS_DIR:-$REPO_ROOT/deploy/docker}"
WAREHOUSE_DIR="$VSS_APPS_DIR/industry-profiles/warehouse-operations"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-warehouse-bp-perf}"
MONITOR_NAME="${MONITOR_NAME:-warehouse-bp-latency-monitor}"
PERF_SCENARIO="${PERF_SCENARIO:-quick_test}"
PERF_CONFIG_ID="${PERF_CONFIG_ID:-warehouse-2d-kafka}"
PERF_CONFIG_PATH="${PERF_CONFIG_PATH:-$PERF_DIR/configs/config.yaml}"
if [[ -f "$PERF_DIR/benchmark/scenario_config.py" && -f "$PERF_CONFIG_PATH" ]]; then
    eval "$(python3 "$PERF_DIR/benchmark/scenario_config.py" \
        --config "$PERF_CONFIG_PATH" \
        --scenario "$PERF_SCENARIO")"
fi
PERF_DURATION_SECONDS="${PERF_DURATION_SECONDS:-${CONFIG_DURATION_SECONDS:-60}}"
PERF_WARMUP_SECONDS="${PERF_WARMUP_SECONDS:-${CONFIG_WARMUP_SECONDS:-15}}"
PERF_TOPICS="${PERF_TOPICS:-${CONFIG_TOPICS:-raw frames behavior events incidents}}"
PERF_PERCENTILES="${PERF_PERCENTILES:-${CONFIG_PERCENTILES:-50 95 99}}"
PERF_REQUEST_COUNT="${PERF_REQUEST_COUNT:-${CONFIG_REQUEST_COUNT:-20}}"
PERF_WARMUP_REQUESTS="${PERF_WARMUP_REQUESTS:-${CONFIG_WARMUP_REQUESTS:-2}}"
PERF_SENSOR_ID="${PERF_SENSOR_ID:-${CONFIG_SENSOR_ID:-Camera}}"
PERF_PLACE="${PERF_PLACE:-${CONFIG_PLACE:-building=Warehouse/room=Room-1}}"
PERF_ENDPOINTS="${PERF_ENDPOINTS:-${CONFIG_ENDPOINTS:-livez frames behavior events incidents}}"
OUTPUT_DIR="${OUTPUT_DIR:-${PERF_OUTPUT_DIR:-$PERF_DIR/results/${PERF_SERVICE:-warehouse-bp}_${PERF_SCENARIO}_${PERF_CONFIG_ID}}}"
export WH_PERF_DATA_DIR="${WH_PERF_DATA_DIR:-/tmp/warehouse-bp-perf-${COMPOSE_PROJECT_NAME}-$(id -u)}"
export VSS_DATA_DIR="${VSS_DATA_DIR:-$WH_PERF_DATA_DIR/app-data}"
GENERATED_ENV="${GENERATED_ENV:-$WH_PERF_DATA_DIR/generated.env}"
WAREHOUSE_APP_DATA_REF="${WAREHOUSE_APP_DATA_REF:-nvstaging/vss-warehouse/vss-warehouse-app-data:v3.3.0-09152026}"
HARDWARE_PROFILE="${HARDWARE_PROFILE:-RTXPRO6000BW-SE}"
VIDEO_ANALYTICS_API_HOST_PORT="${VIDEO_ANALYTICS_API_HOST_PORT:-28081}"
ELASTICSEARCH_HOST_PORT="${ELASTICSEARCH_HOST_PORT:-29200}"
KAFKA_HOST_PORT="${KAFKA_HOST_PORT:-19092}"
HAPROXY_HOST_PORT="${HAPROXY_HOST_PORT:-17777}"
export COMPOSE_PROJECT_NAME VSS_APPS_DIR VSS_DATA_DIR OUTPUT_DIR
# Product compose defaults to GHCR. nvstaging/vss-core:develop-latest 404s for
# vss-behavior-analytics and vss-rt-cv. Jenkins ARTIFACT_REF is the warehouse
# compose NGC path, not a container tag — ignore it if the wrapper exported it.
if [[ "${VSS_CONTAINER_TAG:-}" == *vss-blueprints-compose* ]]; then
    echo "Ignoring compose ARTIFACT_REF as VSS_CONTAINER_TAG: $VSS_CONTAINER_TAG"
    unset VSS_CONTAINER_TAG
fi
if [[ "${VSS_CONTAINER_REGISTRY:-}" == *vss-developer* ]]; then
    echo "Ignoring compose ARTIFACT_REF as VSS_CONTAINER_REGISTRY: $VSS_CONTAINER_REGISTRY"
    unset VSS_CONTAINER_REGISTRY
fi
export VSS_CONTAINER_REGISTRY="${VSS_CONTAINER_REGISTRY:-ghcr.io/nvidia-ai-blueprints/vss}"
export VSS_CONTAINER_TAG="${VSS_CONTAINER_TAG:-develop-latest}"

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

detect_host_ip() {
    if [[ -n "${HOST_IP:-}" && "$HOST_IP" != "<HOST_IP>" ]]; then
        printf '%s\n' "$HOST_IP"
        return 0
    fi
    hostname -I 2>/dev/null | awk '{print $1}'
}

compose() {
    (
        cd "$VSS_APPS_DIR"
        docker compose -p "$COMPOSE_PROJECT_NAME" \
            --env-file "$VSS_APPS_DIR/containers.env" \
            --env-file "$WAREHOUSE_DIR/.env" \
            --env-file "$GENERATED_ENV" \
            -f "$VSS_APPS_DIR/compose.yml" \
            "$@"
    )
}

load_generated_env() {
    [[ -f "$GENERATED_ENV" ]] || return 0
    set -a
    # shellcheck disable=SC1090
    source "$GENERATED_ENV"
    set +a
}

resolve_ba_image() {
    local image tag
    image="${VSS_BEHAVIOR_ANALYTICS_IMAGE:-$VSS_CONTAINER_REGISTRY/vss-behavior-analytics}"
    tag="${VSS_BEHAVIOR_ANALYTICS_TAG:-$VSS_CONTAINER_TAG}"
    printf '%s:%s\n' "$image" "$tag"
}

warehouse_data_ready_at() {
    local root="$1"
    [[ -d "$root/videos" || -d "$root/playback" || -d "$root/models" ]]
}

warehouse_data_ready() {
    warehouse_data_ready_at "$VSS_DATA_DIR"
}

extract_one_archive() {
    local archive="$1"
    local dest="$2"
    echo "Extracting $(basename "$archive") into $dest"
    case "$archive" in
        *.zip)
            if command -v unzip >/dev/null; then
                unzip -o -q "$archive" -d "$dest"
            else
                python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
                    "$archive" "$dest"
            fi
            ;;
        *)
            tar -xf "$archive" -C "$dest"
            ;;
    esac
}

promote_nested_dir() {
    local nested="$1"
    local dest="$2"
    local item
    echo "Flattening nested app-data $(basename "$nested") into $dest"
    shopt -s dotglob nullglob
    for item in "$nested"/*; do
        mv "$item" "$dest/"
    done
    shopt -u dotglob nullglob
    rmdir "$nested" 2>/dev/null || true
}

# NGC ships vss-warehouse-app-data.tar.gz whose top directory is
# vss-warehouse-app-data/{videos,playback,models}. Jenkins #81 extracted that
# wrapper into VSS_DATA_DIR and then failed looking for $VSS_DATA_DIR/videos.
normalize_warehouse_app_data() {
    local depth=0
    local archive nested
    ensure_host_dir "$VSS_DATA_DIR"
    while (( depth < 4 )); do
        if warehouse_data_ready; then
            return 0
        fi
        archive="$(find "$VSS_DATA_DIR" -maxdepth 3 -type f \
            \( -name '*.tar.gz' -o -name '*.tgz' -o -name '*.tar' -o -name '*.zip' \) \
            | head -n 1 || true)"
        if [[ -n "$archive" ]]; then
            extract_one_archive "$archive" "$VSS_DATA_DIR"
            rm -f "$archive"
            depth=$((depth + 1))
            continue
        fi
        nested=""
        if warehouse_data_ready_at "$VSS_DATA_DIR/vss-warehouse-app-data"; then
            nested="$VSS_DATA_DIR/vss-warehouse-app-data"
        else
            nested="$(find "$VSS_DATA_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1 || true)"
            if [[ -z "$nested" ]] || ! warehouse_data_ready_at "$nested"; then
                break
            fi
        fi
        promote_nested_dir "$nested" "$VSS_DATA_DIR"
        depth=$((depth + 1))
    done
}

chmod_warehouse_app_data() {
    # NGC extract is mode 750 uid 2524; warehouse containers run as uid 1000.
    chmod -R a+rX "$VSS_DATA_DIR" || true
}

download_warehouse_app_data() {
    ensure_host_dir "$VSS_DATA_DIR"
    normalize_warehouse_app_data
    if warehouse_data_ready; then
        echo "Warehouse app data already present at $VSS_DATA_DIR"
        chmod_warehouse_app_data
        return 0
    fi
    if ! command -v ngc >/dev/null; then
        echo "ngc CLI is required to download $WAREHOUSE_APP_DATA_REF" >&2
        return 1
    fi
    if [[ -z "${NGC_CLI_API_KEY:-${NGC_API_KEY:-}}" ]]; then
        echo "Set NGC_CLI_API_KEY (or NGC_API_KEY) to download warehouse app data." >&2
        return 1
    fi
    export NGC_CLI_API_KEY="${NGC_CLI_API_KEY:-$NGC_API_KEY}"
    local staging="$WH_PERF_DATA_DIR/ngc-download"
    ensure_host_dir "$staging"
    echo "Downloading $WAREHOUSE_APP_DATA_REF"
    ngc registry resource download-version "$WAREHOUSE_APP_DATA_REF" --dest "$staging"
    local archive
    archive="$(find "$staging" -type f \( -name '*.tar.gz' -o -name '*.tgz' -o -name '*.tar' -o -name '*.zip' \) | head -n 1 || true)"
    ensure_host_dir "$VSS_DATA_DIR"
    if [[ -n "$archive" ]]; then
        extract_one_archive "$archive" "$VSS_DATA_DIR"
    else
        local downloaded
        downloaded="$(find "$staging" -mindepth 1 -maxdepth 1 -type d | head -n 1 || true)"
        if [[ -z "$downloaded" ]]; then
            echo "NGC download produced no archive under $staging" >&2
            return 1
        fi
        cp -a "$downloaded"/. "$VSS_DATA_DIR"/
    fi
    normalize_warehouse_app_data
    chmod_warehouse_app_data
    if ! warehouse_data_ready; then
        echo "Warehouse app data extract did not contain videos/ or playback/: $VSS_DATA_DIR" >&2
        ls -la "$VSS_DATA_DIR" >&2 || true
        ls -la "$VSS_DATA_DIR/vss-warehouse-app-data" >&2 || true
        return 1
    fi
}

write_generated_env() {
    local host_ip
    host_ip="$(detect_host_ip)"
    if [[ -z "$host_ip" ]]; then
        echo "Could not detect HOST_IP" >&2
        return 1
    fi
    ensure_host_dir "$WH_PERF_DATA_DIR"
    python3 "$PERF_DIR/scripts/overlay_env.py" \
        --source "$WAREHOUSE_DIR/overrides.env" \
        --dest "$GENERATED_ENV" \
        "COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME" \
        "MODE=2d" \
        "BP_PROFILE=bp_wh_kafka" \
        "STREAM_TYPE=kafka" \
        "LLM_MODE=none" \
        "VLM_MODE=none" \
        "HARDWARE_PROFILE=$HARDWARE_PROFILE" \
        "VSS_APPS_DIR=$VSS_APPS_DIR" \
        "VSS_DATA_DIR=$VSS_DATA_DIR" \
        "BP_CONFIGURATOR_ENV_FILE=$GENERATED_ENV" \
        "VSS_CONTAINER_REGISTRY=$VSS_CONTAINER_REGISTRY" \
        "VSS_CONTAINER_TAG=$VSS_CONTAINER_TAG" \
        "HOST_IP=$host_ip" \
        "EXTERNAL_IP=$host_ip" \
        "VSS_PUBLIC_HOST=$host_ip" \
        "TURN_PUBLIC_HOST=$host_ip" \
        "TURN_EXTERNAL_IP=$host_ip" \
        "HAPROXY_HOST_PORT=$HAPROXY_HOST_PORT" \
        "VSS_PUBLIC_PORT=$HAPROXY_HOST_PORT" \
        "VIDEO_ANALYTICS_API_HOST_PORT=$VIDEO_ANALYTICS_API_HOST_PORT" \
        "ELASTICSEARCH_HOST_PORT=$ELASTICSEARCH_HOST_PORT" \
        "KAFKA_HOST_PORT=$KAFKA_HOST_PORT" \
        "KIBANA_HOST_PORT=${KIBANA_HOST_PORT:-15601}" \
        "VSS_UI_HOST_PORT=${VSS_UI_HOST_PORT:-13000}" \
        "REDIS_HOST_PORT=${REDIS_HOST_PORT:-16379}" \
        "PHOENIX_HOST_PORT=${PHOENIX_HOST_PORT:-16006}" \
        "NVSTREAMER_HTTP_HOST_PORT=${NVSTREAMER_HTTP_HOST_PORT:-31010}" \
        "VST_INGRESS_HOST_PORT=${VST_INGRESS_HOST_PORT:-30898}" \
        "SENSOR_HTTP_HOST_PORT=${SENSOR_HTTP_HOST_PORT:-30010}" \
        "STREAM_PROCESSOR_HTTP_HOST_PORT=${STREAM_PROCESSOR_HTTP_HOST_PORT:-30011}" \
        "RTSP_SERVER_HOST_PORT=${RTSP_SERVER_HOST_PORT:-30574}" \
        "RTSP_SERVER_HOST_PORT_END=${RTSP_SERVER_HOST_PORT_END:-30584}" \
        "SDRC_CONTROLLER_HOST_PORT=${SDRC_CONTROLLER_HOST_PORT:-15003}" \
        "SDRC_PROXY_HOST_PORT=${SDRC_PROXY_HOST_PORT:-10010}" \
        "DCGM_EXPORTER_HOST_PORT=${DCGM_EXPORTER_HOST_PORT:-19400}" \
        "PROMETHEUS_HOST_PORT=${PROMETHEUS_HOST_PORT:-19090}" \
        "GRAFANA_HOST_PORT=${GRAFANA_HOST_PORT:-35010}" \
        "NODE_EXPORTER_HOST_PORT=${NODE_EXPORTER_HOST_PORT:-19110}" \
        "CADVISOR_HOST_PORT=${CADVISOR_HOST_PORT:-18090}" \
        "TURN_HOST_PORT=${TURN_HOST_PORT:-13478}" \
        "TURN_MIN_RELAY_HOST_PORT=${TURN_MIN_RELAY_HOST_PORT:-21000}" \
        "TURN_MAX_RELAY_HOST_PORT=${TURN_MAX_RELAY_HOST_PORT:-21040}" \
        "MQTT_HOST_PORT=${MQTT_HOST_PORT:-11883}" \
        "RTVI_CV_HOST_PORT=${RTVI_CV_HOST_PORT:-19000}" \
        "NGC_CLI_API_KEY=${NGC_CLI_API_KEY:-${NGC_API_KEY:-}}"
}

prepare_data_logs() {
    ensure_host_dir "$VSS_DATA_DIR/data_log/elastic/data"
    ensure_host_dir "$VSS_DATA_DIR/data_log/elastic/logs"
    ensure_host_dir "$VSS_DATA_DIR/data_log/kafka"
    ensure_host_dir "$VSS_DATA_DIR/data_log/redis/data"
    ensure_host_dir "$VSS_DATA_DIR/data_log/redis/log"
    ensure_host_dir "$VSS_DATA_DIR/data_log/vss_video_analytics_api"
    ensure_host_dir "$VSS_DATA_DIR/models"
    chmod -R a+rwX "$VSS_DATA_DIR/data_log" "$VSS_DATA_DIR/models" || true
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    case "${1:-}" in
        normalize-app-data)
            export VSS_DATA_DIR="${2:?normalize-app-data requires a directory}"
            normalize_warehouse_app_data
            chmod_warehouse_app_data
            if warehouse_data_ready; then
                echo "ready $VSS_DATA_DIR"
                exit 0
            fi
            echo "not ready $VSS_DATA_DIR" >&2
            ls -la "$VSS_DATA_DIR" >&2 || true
            exit 1
            ;;
    esac
fi
