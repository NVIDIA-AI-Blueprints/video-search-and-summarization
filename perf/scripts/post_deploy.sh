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

http_any_status() {
    local url="$1"
    local code
    code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 "$url" 2>/dev/null || true)"
    [[ "$code" =~ ^[1-5][0-9][0-9]$ ]]
}

reload_vst_nginx() {
    docker exec vss-vios-ingress sh -lc \
        'nginx -t -c /etc/nginx/nginx.conf && nginx -s reload -c /etc/nginx/nginx.conf' \
        >/dev/null 2>&1 || true
}

wait_vst_ingress() {
    # ci-vss-oss ensure_vst_ingress.py: any HTTP status means the port is up.
    local timeout="${1:-180}"
    local port="${VST_INGRESS_HOST_PORT:-30898}"
    local path="/api/v2/live/streams"
    local host_ip="${HOST_IP:-127.0.0.1}"
    local deadline=$((SECONDS + timeout))
    local reloaded=0 restarts=0
    local url
    local urls=(
        "http://127.0.0.1:${port}${path}"
        "http://${host_ip}:${port}${path}"
    )
    echo "Waiting up to ${timeout}s for VST ingress on port $port"
    while (( SECONDS < deadline )); do
        for url in "${urls[@]}"; do
            if http_any_status "$url"; then
                echo "VST ingress reachable: $url"
                return 0
            fi
        done
        if (( reloaded == 0 )); then
            reloaded=1
            echo "VST ingress not reachable yet; reloading nginx in vss-vios-ingress"
            docker logs vss-vios-ingress --tail 40 >&2 || true
            reload_vst_nginx
        elif (( restarts < 2 )); then
            restarts=$((restarts + 1))
            echo "Restarting vss-vios-ingress (attempt $restarts)"
            docker restart vss-vios-ingress >/dev/null 2>&1 || true
            reload_vst_nginx
        fi
        sleep 15
    done
    echo "Timed out waiting for VST ingress on port $port" >&2
    docker logs vss-vios-ingress --tail 80 >&2 || true
    return 1
}

wait_compose_gates() {
    python3 "$PERF_DIR/scripts/wait_compose_gates.py" \
        --project "$COMPOSE_PROJECT_NAME" \
        --budget-sec "${PERF_GATE_WAIT_SECONDS:-180}" \
        --poll-sec 15 \
        --gate-only
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

wait_compose_gates
wait_container vss-behavior-analytics "${PERF_BA_READY_TIMEOUT:-900}"
wait_container vss-rtvi-cv "${PERF_PERCEPTION_READY_TIMEOUT:-1200}"
wait_container vss-video-analytics-api "${PERF_API_READY_TIMEOUT:-300}"
wait_http "http://127.0.0.1:${VIDEO_ANALYTICS_API_HOST_PORT}/livez" "${PERF_API_HTTP_TIMEOUT:-300}"
wait_vst_ingress "${PERF_VST_INGRESS_TIMEOUT:-180}"
wait_kafka_raw "${PERF_TRAFFIC_TIMEOUT:-900}"

echo "Warehouse industry-example post-deploy ready."
docker inspect -f '{{.Name}} {{.Config.Image}} {{.State.Status}}' \
    vss-behavior-analytics vss-video-analytics-api vss-rtvi-cv 2>/dev/null || true
