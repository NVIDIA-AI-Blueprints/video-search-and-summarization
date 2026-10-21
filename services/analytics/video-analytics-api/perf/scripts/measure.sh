#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

load_compose_env
ensure_host_dir "$OUTPUT_DIR"
export VA_API_URL="http://127.0.0.1:${VIDEO_ANALYTICS_API_HOST_PORT}"
export PERF_REQUEST_COUNT PERF_WARMUP_REQUESTS PERF_SENSOR_ID PERF_FROM_TS PERF_TO_TS PERF_ENDPOINTS
export ARTIFACT_REF="${ARTIFACT_REF:-$VIDEO_ANALYTICS_API_IMAGE}"

python3 "$PERF_DIR/benchmark/http_latency.py" \
    --base-url "$VA_API_URL" \
    --output "$OUTPUT_DIR/result.json" \
    --artifact-ref "$ARTIFACT_REF" \
    --scenario "$PERF_SCENARIO" \
    --config-id "$PERF_CONFIG_ID" \
    --request-count "$PERF_REQUEST_COUNT" \
    --warmup-requests "$PERF_WARMUP_REQUESTS" \
    --sensor-id "$PERF_SENSOR_ID" \
    --from-ts "$PERF_FROM_TS" \
    --to-ts "$PERF_TO_TS" \
    --endpoints "$PERF_ENDPOINTS"
