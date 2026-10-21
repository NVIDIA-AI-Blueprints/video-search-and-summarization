#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

load_compose_env
cd "$COMPOSE_DIR"
image="$BEHAVIOR_ANALYTICS_IMAGE"
ensure_host_dir "$OUTPUT_DIR"
report="$OUTPUT_DIR/latency.txt"
rm -f "$report"

dump_measure_debug() {
    echo "Measure failed; dumping compose and container logs." >&2
    compose ps -a >&2 || true
    docker logs "$MONITOR_NAME" >&2 || true
    docker logs mdx-analytics >&2 || true
    docker logs mdx-analytics-playback >&2 || true
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

analytics_state="$(docker inspect -f '{{.State.Status}}' mdx-analytics 2>/dev/null || true)"
if [[ "$analytics_state" != "running" ]]; then
    fail_measure "mdx-analytics is not running (state=${analytics_state:-missing})"
fi

network="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' mdx-analytics)"
if [[ -z "$network" ]]; then
    fail_measure "mdx-analytics has no docker network"
fi

# post_deploy only checks that mdx-analytics has started. Give the app time to
# subscribe to kafka before the monitor and playback begin.
sleep "${PERF_SETTLE_SECONDS:-20}"

docker rm -f "$MONITOR_NAME" >/dev/null 2>&1 || true
read -r -a monitor_topics <<< "$PERF_TOPICS"
read -r -a monitor_percentiles <<< "$PERF_PERCENTILES"
# Distroless image documents `python3 <script>` as the container command.
# Run as the host user so OUTPUT_DIR does not need world-writable mode.
if ! docker run -d \
    --name "$MONITOR_NAME" \
    --network "$network" \
    --user "$(id -u):$(id -g)" \
    -v "$OUTPUT_DIR:/output" \
    --entrypoint python3 \
    "$image" \
    tools/latency/latency_monitor.py \
    --source-type kafka \
    --host kafka \
    --port 29092 \
    --topics "${monitor_topics[@]}" \
    --percentiles "${monitor_percentiles[@]}" \
    --output-file /output/latency.txt
then
    fail_measure "latency monitor failed to start"
fi
if [[ "$(docker inspect -f '{{.State.Status}}' "$MONITOR_NAME" 2>/dev/null || true)" != "running" ]]; then
    fail_measure "latency monitor exited immediately"
fi

sleep "$PERF_WARMUP_SECONDS"

if ! compose up -d analytics-stream-playback; then
    fail_measure "analytics-stream-playback failed to start"
fi

# warehouse_2d kafka config sets playbackStartUpDelaySec=90. A fixed
# PERF_DURATION_SECONDS sleep after `compose up` stops the monitor before
# playback publishes any mdx-raw messages.
playback_start_timeout="${PERF_PLAYBACK_START_TIMEOUT:-180}"
deadline=$((SECONDS + playback_start_timeout))
until docker logs mdx-analytics-playback 2>&1 | grep -q "starting playback"; do
    if (( SECONDS >= deadline )); then
        fail_measure "playback did not start sending within ${playback_start_timeout}s"
    fi
    playback_state="$(docker inspect -f '{{.State.Status}}' mdx-analytics-playback 2>/dev/null || true)"
    if [[ "$playback_state" != "running" ]]; then
        fail_measure "playback container exited before sending (state=${playback_state:-missing})"
    fi
    sleep 2
done
echo "Playback is sending frames."
sleep "$PERF_DURATION_SECONDS"

docker stop "$MONITOR_NAME" >/dev/null || true
if [[ ! -s "$report" ]]; then
    fail_measure "latency report is missing or empty: $report"
fi

PYTHONPATH="$PERF_DIR/benchmark" python3 "$PERF_DIR/benchmark/vss_perf_benchmark.py" \
    --report "$report" \
    --output "$OUTPUT_DIR/result.json" \
    --artifact-ref "$image" \
    --scenario "$PERF_SCENARIO" \
    --config-id "$PERF_CONFIG_ID" \
    --duration-seconds "$PERF_DURATION_SECONDS" \
    --warmup-seconds "$PERF_WARMUP_SECONDS" \
    || fail_measure "latency report did not produce a passing result.json"
