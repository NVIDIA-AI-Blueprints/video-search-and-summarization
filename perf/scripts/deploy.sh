#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
export VSS_PERF_STAGE=deploy
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
if [[ "${VSS_PERF_BOUNDED_STAGE:-}" != "$VSS_PERF_STAGE" ]]; then
    export VSS_PERF_BOUNDED_STAGE="$VSS_PERF_STAGE"
    stage_status=0
    "$PYTHON" "$VSS_PERF_ROOT/scripts/run_stage.py" "$PERF_STAGE_TIMEOUT_SECONDS" "${BASH_SOURCE[0]}" '' || stage_status=$?
    exit "$stage_status"
fi
# --wait provides a bounded Docker readiness wait. RT-CV starts after VIOS assigns a sensor ID.
# Compose --wait treats explicitly selected exited one-off services as failed.
# Run topic creation separately and check its actual exit status; other init
# services are attached by service_completed_successfully dependencies.
"${COMPOSE_CMD[@]}" up -d --build kafka-topic-init-container
"${COMPOSE_CMD[@]}" wait kafka-topic-init-container
services=(elasticsearch kafka redis centralizedb sensor-ms streamprocessing-ms vst-ingress logstash vss-video-analytics-api vss-haproxy-ingress)
if [[ -n "${VSS_PERF_VIDEO:-}" ]]; then services+=(perf-media perf-video); fi
"${COMPOSE_CMD[@]}" up -d --build --wait --wait-timeout "$DEPLOY_TIMEOUT_SECONDS" "${services[@]}"
