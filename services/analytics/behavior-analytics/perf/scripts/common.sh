#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PERF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$(cd "$PERF_DIR/.." && pwd)"
INTEGRATION_DIR="$SERVICE_DIR/tests/integration"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mdx}"
MONITOR_NAME="${MONITOR_NAME:-ba-perf-latency-monitor}"
PERF_DURATION_SECONDS="${PERF_DURATION_SECONDS:-60}"
PERF_WARMUP_SECONDS="${PERF_WARMUP_SECONDS:-10}"
PERF_SCENARIO="${PERF_SCENARIO:-quick_test}"
PERF_CONFIG_ID="${PERF_CONFIG_ID:-warehouse_2d-kafka-playback}"
OUTPUT_DIR="${OUTPUT_DIR:-${PERF_OUTPUT_DIR:-$PERF_DIR/results/${PERF_SERVICE:-behavior-analytics}_${PERF_SCENARIO}_${PERF_CONFIG_ID}}}"
COMPOSE_DIR="$INTEGRATION_DIR/docker_compose"
BA_PERF_DATA_DIR="${BA_PERF_DATA_DIR:-/tmp/ba-perf-${COMPOSE_PROJECT_NAME}}"
CHECKOUT_DATA_LOG="$INTEGRATION_DIR/docker_compose/apps_data/data_log"

remove_root_tree() {
    local path="$1"
    [[ -e "$path" ]] || return 0
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
    docker compose --env-file infra/.env -f infra/compose.yml -f apps/mdx-apps.yml "$@"
}

load_compose_env() {
    set -a
    # shellcheck disable=SC1091
    source "$INTEGRATION_DIR/docker_compose/infra/.env"
    set +a
    export BEHAVIOR_ANALYTICS_IMAGE="$(resolve_image)"
    export COMPOSE_PROJECT_NAME
}
