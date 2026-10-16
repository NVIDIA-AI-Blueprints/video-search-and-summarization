#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

load_compose_env
image="$BEHAVIOR_ANALYTICS_IMAGE"
report="$OUTPUT_DIR/latency.txt"
rm -f "$report"

docker rm -f "$MONITOR_NAME" >/dev/null 2>&1 || true
docker run -d \
    --name "$MONITOR_NAME" \
    --network "${COMPOSE_PROJECT_NAME}_default" \
    --user 1000:1000 \
    -v "$OUTPUT_DIR:/output" \
    "$image" \
    python3 tools/latency/latency_monitor.py \
    --source-type kafka \
    --host kafka \
    --port 29092 \
    --topics raw frames behavior events incidents \
    --percentiles 50 95 99 \
    --output-file /output/latency.txt

sleep "$PERF_WARMUP_SECONDS"

cd "$SERVICE_DIR/docker_compose"
compose up -d analytics-stream-playback
sleep "$PERF_DURATION_SECONDS"

docker stop "$MONITOR_NAME" >/dev/null
test -s "$report"

PYTHONPATH="$PERF_DIR/benchmark" python3 "$PERF_DIR/benchmark/vss_perf_benchmark.py" \
    --report "$report" \
    --output "$OUTPUT_DIR/result.json" \
    --artifact-ref "$image" \
    --scenario "$PERF_SCENARIO" \
    --config-id "$PERF_CONFIG_ID" \
    --duration-seconds "$PERF_DURATION_SECONDS" \
    --warmup-seconds "$PERF_WARMUP_SECONDS"
