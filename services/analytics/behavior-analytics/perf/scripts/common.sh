#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PERF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$(cd "$PERF_DIR/.." && pwd)"
INTEGRATION_DIR="$SERVICE_DIR/tests/integration"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ba-perf}"
MONITOR_NAME="${MONITOR_NAME:-ba-perf-latency-monitor}"
PERF_SCENARIO="${PERF_SCENARIO:-quick_test}"
PERF_CONFIG_ID="${PERF_CONFIG_ID:-warehouse_2d-kafka-playback}"
if [[ -f "$PERF_DIR/benchmark/scenario_config.py" && -f "$PERF_DIR/configs/config.yaml" ]]; then
    # config.yaml is the run matrix. Env vars still win when already set.
    eval "$(python3 "$PERF_DIR/benchmark/scenario_config.py" \
        --config "$PERF_DIR/configs/config.yaml" \
        --scenario "$PERF_SCENARIO")"
fi
PERF_DURATION_SECONDS="${PERF_DURATION_SECONDS:-${CONFIG_DURATION_SECONDS:-60}}"
PERF_WARMUP_SECONDS="${PERF_WARMUP_SECONDS:-${CONFIG_WARMUP_SECONDS:-10}}"
PERF_TOPICS="${PERF_TOPICS:-${CONFIG_TOPICS:-raw frames behavior events incidents}}"
PERF_PERCENTILES="${PERF_PERCENTILES:-${CONFIG_PERCENTILES:-50 95 99}}"
OUTPUT_DIR="${OUTPUT_DIR:-${PERF_OUTPUT_DIR:-$PERF_DIR/results/${PERF_SERVICE:-behavior-analytics}_${PERF_SCENARIO}_${PERF_CONFIG_ID}}}"
COMPOSE_DIR="$INTEGRATION_DIR/docker_compose"
export BA_PERF_DATA_DIR="${BA_PERF_DATA_DIR:-/tmp/ba-perf-${COMPOSE_PROJECT_NAME}-$(id -u)}"
export BA_PERF_OVERLAY_STAMP="${BA_PERF_OVERLAY_STAMP:-$BA_PERF_DATA_DIR/perf_app_overlay.json}"
CHECKOUT_DATA_LOG="$INTEGRATION_DIR/docker_compose/apps_data/data_log"

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

resolve_image() {
    if [[ -n "${ARTIFACT_REF:-}" ]]; then
        printf '%s\n' "$ARTIFACT_REF"
    elif [[ -n "${IMAGE_TAG:-}" ]]; then
        printf '%s\n' "$IMAGE_TAG"
    elif [[ -n "${BEHAVIOR_ANALYTICS_IMAGE:-}" ]]; then
        printf '%s\n' "$BEHAVIOR_ANALYTICS_IMAGE"
    elif [[ -n "${CURRENT_BEHAVIOR_ANALYTICS_IMAGE:-}" ]]; then
        printf '%s\n' "$CURRENT_BEHAVIOR_ANALYTICS_IMAGE"
    else
        echo "Set ARTIFACT_REF to the promoted Behavior Analytics image." >&2
        return 1
    fi
}

compose() {
    docker compose -p "$COMPOSE_PROJECT_NAME" --env-file infra/.env \
        -f infra/compose.yml -f apps/mdx-apps.yml "$@"
}

load_compose_env() {
    set -a
    # shellcheck disable=SC1091
    source "$INTEGRATION_DIR/docker_compose/infra/.env"
    set +a
    export BEHAVIOR_ANALYTICS_IMAGE="$(resolve_image)"
    export COMPOSE_PROJECT_NAME
}
