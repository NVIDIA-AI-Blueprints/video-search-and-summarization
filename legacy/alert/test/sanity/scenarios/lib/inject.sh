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
# scenarios/lib/inject.sh
#
# Input-injection helpers used by .case files. Two paths:
#   inject_kafka_incident   — produces to mdx-incidents Kafka topic
#                             (reuses test/functional/p1/shared/helpers.sh)
#   inject_rest_incident    — POSTs to AB REST /api/v1/incidents
#
# Both:
#   - patch the payload to the current timestamp
#   - replace sensorId with a SYNTHETIC sensor-id from runner_helpers.synth_sensor_id
#   - return the synthetic sensor-id on stdout for the probe to scope its query
#
# Required env (cases may set defaults via the runner):
#   BOOTSTRAP            Kafka bootstrap (default 127.0.0.1:9092)
#   TOPIC                Kafka topic     (default mdx-incidents)
#   AB_HOST, AB_PORT     AB REST host/port (default localhost:9080)
#   PAYLOAD              path to base JSON payload (default sample_incident.json)
#   ALERT_AGENT_REPO     repo root containing test/functional/p1/shared/helpers.sh
#                        (default: derive from this script's location upward)
# =============================================================================

# Resolve repo root once (containing test/sanity/scenarios/lib)
_default_repo_root() {
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$lib_dir/../../../.." && pwd
}

# Patch a JSON payload: timestamp -> now, sensorId -> synthetic, optional
# category override via $CATEGORY env (matches alert_type_config.json on the
# target deployment — must be set to a category the deployment recognizes,
# else AB has no prompt and drops the incident silently).
# Echoes the patched-file path.
_patch_payload() {
    local src="$1" sensor_id="$2"
    local out="${SANITY_CASE_DIR}/payload-$(date +%s%N).json"
    python3 -c "
import json, os
from datetime import datetime, timezone
with open('$src') as f:
    data = json.load(f)
now = datetime.now(timezone.utc)
data['timestamp'] = now.strftime('%Y-%m-%dT%H:%M:%S.000Z')
data['end'] = data['timestamp']
data['sensorId'] = '$sensor_id'
cat = os.environ.get('CATEGORY', '').strip()
if cat:
    data['category'] = cat
with open('$out', 'w') as f:
    json.dump(data, f)
" || { log_warn "[inject] _patch_payload failed for $src"; return 1; }
    echo "$out"
}

# inject_kafka_incident <test-tag> [idx]
# Produces one incident to Kafka with a synthetic sensor-id.
# Returns the synthetic sensor-id on stdout.
inject_kafka_incident() {
    local tag="$1" idx="${2:-1}"
    local repo="${ALERT_AGENT_REPO:-$(_default_repo_root)}"
    local payload="${PAYLOAD:-$repo/test/protobuf/test_data/sample_incident.json}"
    local bootstrap="${BOOTSTRAP:-127.0.0.1:9092}"
    local topic="${TOPIC:-mdx-incidents}"

    local sensor_id
    sensor_id="$(synth_sensor_id "$tag" "$idx")"

    local patched
    patched="$(_patch_payload "$payload" "$sensor_id")" || return 1

    local id_suffix="${tag}_${idx}_${SANITY_RUN_ID}"
    # Save the patched payload path for replay_last_kafka_inject (used by dedup tests).
    echo "$patched" > "${SANITY_CASE_DIR}/last_kafka_payload"
    echo "$id_suffix" > "${SANITY_CASE_DIR}/last_kafka_id_suffix"

    # Reuse the FT helper.
    if ! ( source "$repo/test/functional/p1/shared/helpers.sh" \
           && produce_incident "$repo" "$bootstrap" "$topic" "$patched" "$id_suffix" --no-patch ) >&2; then
        log_warn "[inject] kafka produce failed (sensor=$sensor_id)"
        return 1
    fi
    echo "$sensor_id"
}

# Replay the last Kafka inject — produces the SAME patched payload again
# (same timestamp, same fingerprint). For dedup tests where we need two
# producers of the identical incident.
replay_last_kafka_inject() {
    local repo="${ALERT_AGENT_REPO:-$(_default_repo_root)}"
    local bootstrap="${BOOTSTRAP:-127.0.0.1:9092}"
    local topic="${TOPIC:-mdx-incidents}"
    local patched id_suffix
    patched=$(cat "${SANITY_CASE_DIR}/last_kafka_payload" 2>/dev/null) || return 1
    id_suffix=$(cat "${SANITY_CASE_DIR}/last_kafka_id_suffix" 2>/dev/null) || return 1
    if ! ( source "$repo/test/functional/p1/shared/helpers.sh" \
           && produce_incident "$repo" "$bootstrap" "$topic" "$patched" "$id_suffix" --no-patch ) >&2; then
        log_warn "[inject] kafka replay failed"
        return 1
    fi
}

# inject_rest_incident <test-tag> [idx]
# POSTs one incident to AB REST API. Returns the synthetic sensor-id on stdout.
# Side effect: writes the HTTP status to $SANITY_CASE_DIR/last_http_status —
# the case reads it via last_http_status (subshell-safe; export wouldn't
# survive command substitution).
inject_rest_incident() {
    local tag="$1" idx="${2:-1}"
    local repo="${ALERT_AGENT_REPO:-$(_default_repo_root)}"
    local payload="${PAYLOAD:-$repo/test/protobuf/test_data/sample_incident.json}"
    local ab_host="${AB_HOST:-localhost}" ab_port="${AB_PORT:-9080}"

    local sensor_id
    sensor_id="$(synth_sensor_id "$tag" "$idx")"

    local patched
    patched="$(_patch_payload "$payload" "$sensor_id")" || return 1

    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
               -X POST "http://${ab_host}:${ab_port}/api/v1/incidents" \
               -H "Content-Type: application/json" \
               -d @"$patched" 2>/dev/null) || status="000"
    echo "$status" > "${SANITY_CASE_DIR}/last_http_status"
    echo "$sensor_id"
}

# Read the HTTP status from the most recent inject_rest_incident in this case.
last_http_status() {
    cat "${SANITY_CASE_DIR}/last_http_status" 2>/dev/null || echo "000"
}
