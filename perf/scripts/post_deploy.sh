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

kafka_raw_messages() {
    local offsets
    offsets="$(docker exec kafka kafka-get-offsets --bootstrap-server localhost:29092 \
        --topic mdx-raw --time -1 2>/dev/null || true)"
    if [[ -z "$offsets" ]]; then
        offsets="$(docker exec kafka kafka-run-class kafka.tools.GetOffsetShell \
            --broker-list localhost:29092 --topic mdx-raw --time -1 2>/dev/null || true)"
    fi
    awk -F: '{s+=$NF} END {print s+0}' <<<"$offsets"
}

dump_traffic_debug() {
    echo "No Kafka raw traffic yet; dumping perception / configurator / API." >&2
    docker logs vss-rtvi-cv --tail 80 >&2 || true
    docker logs vss-configurator --tail 80 >&2 || true
    docker exec vss-configurator printenv HOST_IP >&2 || true
    docker logs vss-video-analytics-api --tail 40 >&2 || true
    compose ps -a >&2 || true
}

wait_kafka_raw() {
    local timeout="${1:-900}"
    local deadline=$((SECONDS + timeout))
    local url="http://127.0.0.1:${VIDEO_ANALYTICS_API_HOST_PORT}/metrics/last-processed-timestamp?sensorId=${PERF_SENSOR_ID}"
    local count body
    echo "Waiting up to ${timeout}s for mdx-raw traffic (RT-DETR engine compile on first GPU run)"
    while (( SECONDS < deadline )); do
        count="$(kafka_raw_messages)"
        body="$(curl -fsS "$url" 2>/dev/null || true)"
        echo "  mdx-raw offset=${count:-0} last-processed=${body:-none}"
        if (( ${count:-0} > 0 )); then
            echo "mdx-raw has ${count} messages"
            return 0
        fi
        if grep -Eq '[0-9]{4}-[0-9]{2}-[0-9]{2}T' <<<"$body"; then
            echo "Video Analytics API last-processed-timestamp is set"
            return 0
        fi
        sleep 10
    done
    dump_traffic_debug
    echo "Timed out waiting for warehouse Kafka traffic on mdx-raw" >&2
    return 1
}

wait_container vss-behavior-analytics "${PERF_BA_READY_TIMEOUT:-900}"
wait_container vss-rtvi-cv "${PERF_PERCEPTION_READY_TIMEOUT:-1200}"
wait_container vss-video-analytics-api "${PERF_API_READY_TIMEOUT:-300}"
wait_http "http://127.0.0.1:${VIDEO_ANALYTICS_API_HOST_PORT}/livez" "${PERF_API_HTTP_TIMEOUT:-300}"
wait_kafka_raw "${PERF_TRAFFIC_TIMEOUT:-900}"

echo "Warehouse industry-example post-deploy ready."
docker inspect -f '{{.Name}} {{.Config.Image}} {{.State.Status}}' \
    vss-behavior-analytics vss-video-analytics-api vss-rtvi-cv 2>/dev/null || true
