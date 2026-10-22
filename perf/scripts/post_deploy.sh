#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

load_generated_env

wait_container() {
    local name="$1"
    local timeout="${2:-900}"
    local deadline=$((SECONDS + timeout))
    local state
    while (( SECONDS < deadline )); do
        state="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo missing)"
        if [[ "$state" == "running" ]]; then
            echo "$name is running"
            return 0
        fi
        sleep 5
    done
    echo "Timed out waiting for $name (state=${state:-missing})" >&2
    docker logs "$name" >&2 || true
    return 1
}

wait_http() {
    local url="$1"
    local timeout="${2:-300}"
    local deadline=$((SECONDS + timeout))
    while (( SECONDS < deadline )); do
        if curl -fsS "$url" >/dev/null 2>&1; then
            echo "$url is ready"
            return 0
        fi
        sleep 5
    done
    echo "Timed out waiting for $url" >&2
    return 1
}

wait_container vss-behavior-analytics "${PERF_BA_READY_TIMEOUT:-900}"
wait_container vss-rtvi-cv "${PERF_PERCEPTION_READY_TIMEOUT:-1200}"
wait_container vss-video-analytics-api "${PERF_API_READY_TIMEOUT:-300}"
wait_http "http://127.0.0.1:${VIDEO_ANALYTICS_API_HOST_PORT}/livez" "${PERF_API_HTTP_TIMEOUT:-300}"

echo "Warehouse industry-example post-deploy ready."
docker inspect -f '{{.Name}} {{.Config.Image}} {{.State.Status}}' \
    vss-behavior-analytics vss-video-analytics-api vss-rtvi-cv 2>/dev/null || true
