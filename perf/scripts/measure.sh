#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

load_generated_env
ensure_host_dir "$OUTPUT_DIR"
report="$OUTPUT_DIR/latency.txt"
rm -f "$report"

dump_measure_debug() {
    echo "Measure failed; dumping compose and container logs." >&2
    compose ps -a >&2 || true
    docker logs "$MONITOR_NAME" >&2 || true
    docker logs vss-behavior-analytics >&2 || true
    docker logs vss-video-analytics-api >&2 || true
    docker logs vss-rtvi-cv >&2 || true
    if [[ -f "$report" ]]; then
        echo "--- latency.txt (head) ---" >&2
        head -n 80 "$report" >&2 || true
    fi
}

fail_measure() {
    echo "$*" >&2
    dump_measure_debug
    exit 1
}

analytics_state="$(docker inspect -f '{{.State.Status}}' vss-behavior-analytics 2>/dev/null || true)"
if [[ "$analytics_state" != "running" ]]; then
    fail_measure "vss-behavior-analytics is not running (state=${analytics_state:-missing})"
fi

network="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' vss-behavior-analytics)"
if [[ -z "$network" ]]; then
    fail_measure "vss-behavior-analytics has no docker network"
fi

image="$(resolve_ba_image)"
# post_deploy already waited for mdx-raw; keep a short extra settle for BA.
sleep "${PERF_SETTLE_SECONDS:-30}"

docker rm -f "$MONITOR_NAME" >/dev/null 2>&1 || true
read -r -a monitor_topics <<< "$PERF_TOPICS"
read -r -a monitor_percentiles <<< "$PERF_PERCENTILES"
monitor_help="$(docker run --rm --entrypoint python3 --user "$(id -u):$(id -g)" \
    "$image" tools/latency/latency_monitor.py --help 2>&1 || true)"
monitor_cmd=(
    docker run -d
    --name "$MONITOR_NAME"
    --network "$network"
    --user "$(id -u):$(id -g)"
    -v "$OUTPUT_DIR:/output"
    --entrypoint python3
    "$image"
    tools/latency/latency_monitor.py
    --source-type kafka
    --host kafka
    --port 29092
    --topics "${monitor_topics[@]}"
    --percentiles "${monitor_percentiles[@]}"
)
if grep -q -- '--skip-completed-behaviors' <<<"$monitor_help"; then
    monitor_cmd+=(--skip-completed-behaviors)
fi
monitor_cmd+=(--output-file /output/latency.txt)
if ! "${monitor_cmd[@]}"; then
    fail_measure "latency monitor failed to start"
fi
if [[ "$(docker inspect -f '{{.State.Status}}' "$MONITOR_NAME" 2>/dev/null || true)" != "running" ]]; then
    fail_measure "latency monitor exited immediately"
fi

echo "Measuring live warehouse BA Kafka topics for ${PERF_DURATION_SECONDS}s"
sleep "$PERF_WARMUP_SECONDS"
sleep "$PERF_DURATION_SECONDS"

docker stop "$MONITOR_NAME" >/dev/null || true
if [[ ! -s "$report" ]]; then
    fail_measure "latency report is missing or empty: $report"
fi

PYTHONPATH="$PERF_DIR/benchmark" python3 "$PERF_DIR/benchmark/vss_perf_benchmark.py" \
    --report "$report" \
    --output "$OUTPUT_DIR/result.json" \
    --artifact-ref "${ARTIFACT_REF:-$image}" \
    --scenario "$PERF_SCENARIO" \
    --config-id "$PERF_CONFIG_ID" \
    --duration-seconds "$PERF_DURATION_SECONDS" \
    --warmup-seconds "$PERF_WARMUP_SECONDS" \
    --api-base-url "http://127.0.0.1:${VIDEO_ANALYTICS_API_HOST_PORT}" \
    --endpoints "$PERF_ENDPOINTS" \
    --sensor-id "$PERF_SENSOR_ID" \
    --place "$PERF_PLACE" \
    --request-count "$PERF_REQUEST_COUNT" \
    --warmup-requests "$PERF_WARMUP_REQUESTS" \
    --compose-profiles "${COMPOSE_PROFILES:-COMPOSE_PROFILES_WH_KAFKA_2D}" \
    --hardware-profile "$HARDWARE_PROFILE" \
    || fail_measure "warehouse benchmark did not produce a passing result.json"
