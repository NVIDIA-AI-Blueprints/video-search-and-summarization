#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Stage 3: wait until the verification path is ready and metrics are flowing.
#
# Warm-up traffic is not sent here: the benchmark registers the first stream
# and waits `warmup_seconds` before it measures, which covers RT-VLM and
# RTVI-CV cold starts with real incidents.
set -euo pipefail
# shellcheck source=_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

TIMEOUT_S="${PERF_READY_TIMEOUT_S:-2400}"   # model download + TensorRT engine build
PROM_PORT="$(grep -E '^PERF_PROMETHEUS_HOST_PORT=' "${PERF_WORK_DIR}/perf-runtime.env" | cut -d= -f2)"
PROM_PORT="${PROM_PORT:-9091}"

log "post_deploy: waiting for containers (timeout ${TIMEOUT_S}s)"
for _c in kafka elasticsearch vss-rtvi-vlm vss-rtvi-cv vss-behavior-analytics \
          vss-vios-nvstreamer vss-alert-bridge vss-perf-prometheus; do
  if docker inspect "${_c}" >/dev/null 2>&1; then
    wait_container "${_c}" "${TIMEOUT_S}"
  else
    log "WARNING: container ${_c} not found (name changed?); skipping"
  fi
done

wait_http "http://localhost:${ALERT_BRIDGE_HOST_PORT:-9080}/ready" 600
wait_http "http://localhost:${PROM_PORT}/-/ready" 300

# Prometheus must actually scrape Alert Bridge, or every metric reads N/A.
_start="$(date +%s)"
until curl -sf "http://localhost:${PROM_PORT}/api/v1/query" --data-urlencode 'query=up{job="alert-bridge"}' \
      | grep -q '"value":\[[^]]*,"1"\]'; do
  (( $(date +%s) - _start > 300 )) && die "Prometheus is not scraping alert-bridge:9081 (up != 1)"
  sleep 5
done
log "Prometheus scraping alert-bridge"

docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | sort > "${PERF_WORK_DIR}/containers.txt"
log "post_deploy done"
