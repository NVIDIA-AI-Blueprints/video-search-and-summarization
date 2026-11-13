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
VST_INGRESS_HOST_PORT="${VST_INGRESS_HOST_PORT:-30898}"
NGC_DOWNLOAD_ATTEMPTS="${NGC_DOWNLOAD_ATTEMPTS:-3}"
NO_TURN_COMPOSE="$VSS_APPS_DIR/services/infra/compose-no-turn-tcp-relay.yml"
# Process-env keys that must not outrank generated.env (ci-vss-oss compose_profile.sh).
COMPOSE_UNSET=(
    VSS_APPS_DIR VSS_DATA_DIR HOST_IP EXTERNAL_IP
    MODE BP_PROFILE COMPOSE_PROFILES STREAM_TYPE
    LLM_MODE VLM_MODE LLM_NAME VLM_NAME
    LLM_NAME_SLUG VLM_NAME_SLUG LLM_BASE_URL VLM_BASE_URL
    LLM_DEVICE_ID VLM_DEVICE_ID SHARED_LLM_VLM_DEVICE_ID
    FIXED_SHARED_DEVICE_IDS RT_CV_DEVICE_ID RT_VLM_DEVICE_ID
    RT_EMBED_DEVICE_ID RTVI_VLLM_GPU_MEMORY_UTILIZATION
    RTVI_VLLM_ATTENTION_BACKEND
    VSS_RT_CV_TAG VSS_RT_VLM_TAG VSS_RT_EMBED_TAG
    VSS_VIDEO_SUMMARIZATION_TAG
)
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

compose_env_prefix() {
    local args=(env)
    local name
    for name in "${COMPOSE_UNSET[@]}"; do
        args+=(-u "$name")
    done
    printf '%s\n' "${args[@]}"
}

compose_files() {
    local files=(-f "$VSS_APPS_DIR/compose.yml")
    if [[ -f "$NO_TURN_COMPOSE" ]]; then
        files+=(-f "$NO_TURN_COMPOSE")
    fi
    printf '%s\n' "${files[@]}"
}

compose() {
    local files=()
    local env_cmd=()
    mapfile -t files < <(compose_files)
    mapfile -t env_cmd < <(compose_env_prefix)
    (
        cd "$VSS_APPS_DIR"
        "${env_cmd[@]}" docker compose -p "$COMPOSE_PROJECT_NAME" \
            --env-file "$VSS_APPS_DIR/containers.env" \
            --env-file "$WAREHOUSE_DIR/.env" \
            --env-file "$GENERATED_ENV" \
            "${files[@]}" \
            "$@"
    )
}

login_nvcr() {
    local key="${NGC_CLI_API_KEY:-${NGC_API_KEY:-}}"
    if [[ -z "$key" ]]; then
        echo "Skipping nvcr.io login: NGC_CLI_API_KEY is unset"
        return 0
    fi
    printf '%s' "$key" | docker login nvcr.io -u '$oauthtoken' --password-stdin
}

validate_compose() {
    local rendered
    rendered="$(compose config)"
    if grep -Fq '/path/to/' <<<"$rendered"; then
        echo "Rendered Compose configuration contains an unresolved /path/to/ placeholder" >&2
        return 1
    fi
}

project_resource_ids() {
    local containers networks volumes
    containers="$(docker ps -aq --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" 2>/dev/null || true)"
    networks="$(docker network ls -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" 2>/dev/null || true)"
    volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" 2>/dev/null || true)"
    printf '%s\n%s\n%s\n' "$containers" "$networks" "$volumes" | sed '/^$/d'
}

down_project_by_name() {
    (cd "$VSS_APPS_DIR" && docker compose -p "$COMPOSE_PROJECT_NAME" down -v --remove-orphans)
}

down_project() {
    if [[ -f "$GENERATED_ENV" ]]; then
        if compose down -v --remove-orphans; then
            return 0
        fi
        echo "Env-aware Compose teardown failed; retrying by project name" >&2
    fi
    down_project_by_name
}

verify_project_down() {
    local leftover
    leftover="$(project_resource_ids)"
    if [[ -n "$leftover" ]]; then
        echo "Compose teardown left resources for project $COMPOSE_PROJECT_NAME:" >&2
        echo "$leftover" >&2
        return 1
    fi
    echo "Compose teardown verified: no resources remain for project $COMPOSE_PROJECT_NAME"
}

pre_clean_project() {
    echo "Pre-cleaning compose project $COMPOSE_PROJECT_NAME"
    down_project || true
    if ! verify_project_down; then
        down_project_by_name || true
        verify_project_down || true
    fi
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
    # Eval uses chmod 777; a+rwX is the Jenkins equivalent without a global 777.
    chmod -R a+rX "$VSS_DATA_DIR" || true
    local extra
    for extra in videos playback models data_log; do
        if [[ -d "$VSS_DATA_DIR/$extra" ]]; then
            chmod -R a+rwX "$VSS_DATA_DIR/$extra" || true
        fi
    done
}

_stage_ngc_app_data() {
    local staging="$1"
    local extract_dir="$2"
    local archive downloaded
    ensure_host_dir "$staging"
    ensure_host_dir "$extract_dir"
    ngc registry resource download-version "$WAREHOUSE_APP_DATA_REF" --dest "$staging" || return 1
    archive="$(find "$staging" -type f \( -name '*.tar.gz' -o -name '*.tgz' -o -name '*.tar' -o -name '*.zip' \) | head -n 1 || true)"
    if [[ -n "$archive" ]]; then
        extract_one_archive "$archive" "$extract_dir" || return 1
    else
        downloaded="$(find "$staging" -mindepth 1 -maxdepth 1 -type d | head -n 1 || true)"
        if [[ -z "$downloaded" ]]; then
            echo "NGC download produced no archive under $staging" >&2
            return 1
        fi
        cp -a "$downloaded"/. "$extract_dir"/
    fi
    (
        export VSS_DATA_DIR="$extract_dir"
        normalize_warehouse_app_data
    )
    warehouse_data_ready_at "$extract_dir"
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
    local attempt staging extract_dir last_error="<none>"
    # Eval download_app_data.py retries truncated NGC transfers 3 times and
    # never keeps a partial extract as the cache.
    for attempt in $(seq 1 "$NGC_DOWNLOAD_ATTEMPTS"); do
        echo "Downloading $WAREHOUSE_APP_DATA_REF (attempt ${attempt}/${NGC_DOWNLOAD_ATTEMPTS})"
        staging="$WH_PERF_DATA_DIR/ngc-download"
        extract_dir="$WH_PERF_DATA_DIR/ngc-extract"
        remove_root_tree "$staging"
        remove_root_tree "$extract_dir"
        if _stage_ngc_app_data "$staging" "$extract_dir"; then
            ensure_host_dir "$VSS_DATA_DIR"
            cp -a "$extract_dir"/. "$VSS_DATA_DIR"/
            normalize_warehouse_app_data
            chmod_warehouse_app_data
            if warehouse_data_ready; then
                remove_root_tree "$staging"
                remove_root_tree "$extract_dir"
                return 0
            fi
            last_error="copied extract was not ready at $VSS_DATA_DIR"
        else
            last_error="ngc download or extract failed"
            echo "WARN: $last_error — NGC transfer may be truncated; purged partial extract"
        fi
    done
    echo "FATAL: NGC download of $WAREHOUSE_APP_DATA_REF failed after ${NGC_DOWNLOAD_ATTEMPTS} attempt(s); last error: $last_error" >&2
    ls -la "$VSS_DATA_DIR" >&2 || true
    return 1
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
        "LLM_NAME_SLUG=none" \
        "VLM_NAME_SLUG=none" \
        "COMPOSE_PROFILES=\${COMPOSE_PROFILES_WH_KAFKA_2D}" \
        "INSTALL_PROPRIETARY_CODECS=${INSTALL_PROPRIETARY_CODECS:-true}" \
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
    # Same warehouse bind-mount tree as ci-vss-oss compose_profile.sh prepare().
    local dir
    for dir in \
        "$VSS_DATA_DIR/data_log/analytics_cache" \
        "$VSS_DATA_DIR/data_log/calibration_toolkit" \
        "$VSS_DATA_DIR/data_log/elastic/data" \
        "$VSS_DATA_DIR/data_log/elastic/logs" \
        "$VSS_DATA_DIR/data_log/kafka" \
        "$VSS_DATA_DIR/data_log/redis/data" \
        "$VSS_DATA_DIR/data_log/redis/log" \
        "$VSS_DATA_DIR/data_log/nvstreamer/vst_data" \
        "$VSS_DATA_DIR/data_log/behavior_learning_data" \
        "$VSS_DATA_DIR/data_log/vss_video_analytics_api" \
        "$VSS_DATA_DIR/models" \
        "$VSS_DATA_DIR/playback"
    do
        ensure_host_dir "$dir"
    done
    chmod -R a+rwX "$VSS_DATA_DIR/data_log" "$VSS_DATA_DIR/models" "$VSS_DATA_DIR/playback" || true
    if [[ -d "$VSS_DATA_DIR/videos" ]]; then
        chmod -R a+rwX "$VSS_DATA_DIR/videos" || true
    fi
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
