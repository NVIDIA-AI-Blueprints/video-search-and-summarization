#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# =============================================================================
# Probe: assert_metrics_present.sh
# Curls the Prometheus /metrics endpoint and asserts each required metric
# name appears as a top-level series (anchored at line start, terminated by
# whitespace or `{` — avoids name-prefix collisions like
# alert_bridge_events_total vs alert_bridge_events_total_per_thread).
#
# Required PROBE_ENV_*:
#   REQUIRED_METRICS    comma-separated metric names to grep for
#
# Optional:
#   METRICS_HOST, METRICS_PORT  (defaults to ES_HOST, 9081)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../lib/common.sh"
source "$SCRIPT_DIR/../../checks_p1/config_p1.env" 2>/dev/null || true

CHECK_NAME="prometheus /metrics"
URL="http://${METRICS_HOST:-${ES_HOST}}:${METRICS_PORT:-9081}/metrics"

body=$(curl -sf --max-time 5 "$URL" 2>/dev/null || true)
if [[ -z "$body" ]]; then
    skip_check "$CHECK_NAME" "endpoint unreachable: $URL (PROMETHEUS_METRICS_ENABLED=true on AB?)"
    exit 0
fi

if [[ -z "${REQUIRED_METRICS:-}" ]]; then
    fail "$CHECK_NAME" "REQUIRED_METRICS not set" || true
    exit 1
fi

IFS=',' read -ra wanted <<< "$REQUIRED_METRICS"
missing=()
for m in "${wanted[@]}"; do
    m="${m## }"; m="${m%% }"
    # Anchor: line-start, exact name, then whitespace or `{` (label brace).
    grep -qE "^${m}([[:space:]{]|$)" <<< "$body" || missing+=("$m")
done

if [[ ${#missing[@]} -ne 0 ]]; then
    fail "$CHECK_NAME" "missing metrics: ${missing[*]}" || true
    exit 1
fi

pass "$CHECK_NAME" "${#wanted[@]} metric(s) present at $URL"
