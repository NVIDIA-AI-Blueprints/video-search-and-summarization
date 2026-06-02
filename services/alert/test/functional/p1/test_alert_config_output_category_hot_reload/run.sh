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

# Test: Alert Config output_category hot-reload through the pipeline
#       (regression for fix_output_category_reload).
#
# Description: ``test_sink_output_category_hot_reload.py`` covers the
#              sink's ``_resolve_output_category`` method at the unit
#              level. ``test_alert_config_field_overwrite`` covers the
#              Redis cache write path. This test stitches them: it
#              sends a real incident through the pipeline after each
#              API edit and asserts the published ES document carries
#              the latest ``output_category`` value — proving the sink
#              actually reads from Redis at publish time, not from a
#              cached file mapping.
#
#              Four phases against the same incident category
#              (``collision``):
#
#              0. **Seed baseline**: AB loaded
#                 ``alert_type_config.json`` into Redis at startup, so
#                 ``alert_config:collision.output_category`` is
#                 ``"Vehicle Collision"`` even before this test
#                 touches the API. Sending an incident publishes with
#                 that mapping. Sanity check.
#              1. **PUT to "HOT_X"**: next incident publishes with
#                 ``"HOT_X"`` — proves PUT propagates without restart.
#              2. **PUT to "HOT_Y"**: next incident publishes with
#                 ``"HOT_Y"`` — proves the sink re-reads on every
#                 publish, not just the first one.
#              3. **PUT ``output_category: null``**: next incident
#                 publishes with the original category ``"collision"``
#                 — i.e., the explicit clear is honoured and the file
#                 mapping does NOT silently resurrect. This is the
#                 contract the unit test
#                 ``test_explicit_none_in_store_clears_static_mapping``
#                 pins; this test confirms it survives the wiring.
#
# Why no DELETE phase: ``alert_config:`` is the runtime source of
# truth for prompts as well as output_category. A DELETE removes the
# user prompt too, so the verification flow drops the incident with a
# "No user prompt found" warning before it ever reaches the sink. The
# file fallback only applies to ``output_category`` at sink time, not
# to prompts at the verification entry point — testing DELETE here
# would conflate two independent code paths.
#
# Coupling: this test relies on the existing ES + NIM simulators that
# ``run_p1.sh`` starts in Phase 1, plus the alert_type_config.json
# entry that maps ``collision`` → ``"Vehicle Collision"``. If either
# moves, update the EXPECTED_FILE_MAPPING constant below.
#
# Per-phase isolation: the ``Incident`` protobuf has no ``id`` field
# so we cannot rely on ``--id-suffix`` to disambiguate phases. We
# patch a unique ``sensorId`` into the payload per phase instead and
# filter the ES sim's ``/_all`` response by that.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"
AB_HOST="${AB_HOST:-http://localhost:9080}"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"
BASE="$AB_HOST/api/v1/verification/config"
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"

TEST_NAME="alert_config_output_category_hot_reload"
ALERT_TYPE="collision"  # matches sample_incident.json's category field
RUN_ID="$(date +%s)_$$"
EXPECTED_FILE_MAPPING="Vehicle Collision"

mkdir -p "$PID_DIR"

echo "=== P1: Alert Config output_category hot-reload ($RUN_ID) ==="

# Snapshot the seeded ``alert_config:collision`` so the cleanup trap
# can restore it. Without this, a phase 4 (DELETE) leaves the
# subsequent test starting from a different baseline.
INITIAL_REDIS_DOC="$(python3 -c "
import json, redis
r = redis.Redis(host='$REDIS_HOST', port=$REDIS_PORT, decode_responses=True)
v = r.json().get('alert_config:$ALERT_TYPE')
print(json.dumps(v) if v else '')
" 2>/dev/null || echo "")"

cleanup() {
    local rc=$?
    print_status "info" "Cleaning up — restoring initial alert_config:$ALERT_TYPE"
    if [ -n "$INITIAL_REDIS_DOC" ]; then
        python3 -c "
import json, redis
r = redis.Redis(host='$REDIS_HOST', port=$REDIS_PORT, decode_responses=True)
doc = json.loads('''$INITIAL_REDIS_DOC''')
r.json().set('alert_config:$ALERT_TYPE', '\$', doc)
" 2>/dev/null || true
    else
        python3 -c "
import redis
r = redis.Redis(host='$REDIS_HOST', port=$REDIS_PORT, decode_responses=True)
try: r.json().delete('alert_config:$ALERT_TYPE')
except Exception: r.delete('alert_config:$ALERT_TYPE')
" 2>/dev/null || true
    fi
    rm -f "$PID_DIR/payload_${RUN_ID}_"*.json 2>/dev/null || true
    exit $rc
}
trap cleanup EXIT

# ── Helpers ─────────────────────────────────────────────────────────

# Build a payload variant with a unique sensorId and current
# timestamps. Echoes the path of the produced file.
make_payload() {
    local sensor_marker="$1"
    local out="$PID_DIR/payload_${RUN_ID}_${sensor_marker}.json"
    python3 - <<EOF
import json
from datetime import datetime, timezone
with open("$PAYLOAD") as f:
    data = json.load(f)
data["sensorId"] = "$sensor_marker"
now = datetime.now(timezone.utc)
data["timestamp"] = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
data["end"] = data["timestamp"]
with open("$out", "w") as f:
    json.dump(data, f)
EOF
    echo "$out"
}

# Wait until an ES sim doc with the given sensorId shows up, then
# echo its ``category`` field. Returns 1 on timeout.
fetch_category_by_sensor() {
    local sensor_marker="$1"
    local timeout="${2:-90}"
    local interval=2
    local today
    today=$(date -u +%Y-%m-%d)
    local index="mdx-vlm-incidents-$today"
    local elapsed=0
    while [ $elapsed -lt $timeout ]; do
        local response
        response=$(curl -sf "$ES_HOST/$index/_all" 2>/dev/null || echo "")
        if [ -n "$response" ]; then
            local cat
            cat=$(echo "$response" | python3 -c "
import json, sys
data = json.load(sys.stdin)
docs = data.get('documents', [])
for d in docs:
    src = d.get('_source', d)
    if src.get('sensorId') == '$sensor_marker':
        print(src.get('category', ''))
        sys.exit(0)
sys.exit(1)
" 2>/dev/null || true)
            if [ -n "$cat" ]; then
                echo "$cat"
                return 0
            fi
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    return 1
}

# Send one incident with a unique sensorId and assert the published
# doc's ``category`` field equals the expected value.
phase_check() {
    local label="$1"
    local phase_tag="$2"
    local expected="$3"
    local sensor_marker="hotreload_${phase_tag}_${RUN_ID}"
    local payload_path
    print_status "wait" "$label — sending incident, expecting category='$expected'"
    payload_path=$(make_payload "$sensor_marker")
    python3 "$REPO_ROOT/test/protobuf/produce_incident.py" \
        --bootstrap "$BOOTSTRAP" --topic "$TOPIC" \
        --payload "$payload_path" --id-suffix "" >/dev/null
    local got
    if ! got=$(fetch_category_by_sensor "$sensor_marker" 90); then
        print_status "fail" "$label: no published doc with sensorId=$sensor_marker (timed out)"
        return 1
    fi
    if [ "$got" != "$expected" ]; then
        print_status "fail" "$label: published category='$got', expected='$expected'"
        return 1
    fi
    print_status "ok" "$label: published category='$got'"
}

# ── 0. Prerequisites ────────────────────────────────────────────────
print_status "wait" "Checking prerequisites"
curl -fsS "$AB_HOST/health" >/dev/null \
    || { print_status "fail" "AB unreachable at $AB_HOST"; exit 2; }
python3 -c "
import redis
redis.Redis(host='$REDIS_HOST', port=$REDIS_PORT).ping()
" >/dev/null 2>&1 \
    || { print_status "fail" "Redis unreachable at $REDIS_HOST:$REDIS_PORT"; exit 2; }

# ── 1. Phase 0 — seeded baseline ────────────────────────────────────
phase_check "Phase 0 (seeded baseline)"             "p0" "$EXPECTED_FILE_MAPPING" || exit 1

# ── 2. Phase 1 — PUT output_category=HOT_X ──────────────────────────
print_status "wait" "PUT output_category='HOT_X'"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"output_category": "HOT_X"}' >/dev/null
phase_check "Phase 1 (PUT HOT_X)"                  "p1" "HOT_X" || exit 1

# ── 3. Phase 2 — PUT output_category=HOT_Y ──────────────────────────
print_status "wait" "PUT output_category='HOT_Y'"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"output_category": "HOT_Y"}' >/dev/null
phase_check "Phase 2 (PUT HOT_Y)"                  "p2" "HOT_Y" || exit 1

# ── 4. Phase 3 — PUT output_category=null ───────────────────────────
# Explicit clear: the sink must NOT silently fall back to the file
# mapping. With no override, the published doc carries the original
# incident category ("collision").
print_status "wait" "PUT output_category=null (explicit clear)"
curl -fsS -X PUT "$BASE/$ALERT_TYPE" \
    -H "Content-Type: application/json" \
    -d '{"output_category": null}' >/dev/null
phase_check "Phase 3 (PUT null - explicit clear)"  "p3" "$ALERT_TYPE" || exit 1

print_status "ok" "PASS: output_category propagates from API → Redis → published doc on every change"
exit 0
