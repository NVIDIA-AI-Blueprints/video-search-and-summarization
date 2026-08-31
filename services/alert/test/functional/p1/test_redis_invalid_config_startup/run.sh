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

# Test: an incomplete Redis config must stop the service, not start it
# Description: Every case here once produced a service that came up, passed its
#              readiness probe, and quietly did less than the deployment asked
#              for — a whole event kind never consumed, verdicts published to an
#              inferred stream nobody reads, one output route disabled by an
#              unresolved variable, a port out of range reported as "Redis is
#              down", a database number coerced to 0 so the pipeline reads the
#              wrong database, a Secret that never mounted surfacing as NOAUTH
#              on the first command. Unit tests assert the validators raise.
#              What only a running process can show is that the raise reaches the
#              exit: that nothing between the validator and __main__ catches it,
#              logs it, and carries on with a degraded pipeline.
#
#              Each case is a one-field mutation of this test's own config.yaml,
#              and that config is started unmodified first as the control. That
#              is what makes a refusal attributable: without it, a process that
#              cannot start in this environment for an unrelated reason would
#              pass every case below.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
export REPO_ROOT
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
BASE_CONFIG="$SCRIPT_DIR/config.yaml"
WORK_DIR="$PID_DIR/invalid_redis_configs"
TEST_NAME="redis_invalid_config_startup"

echo "=== P1: Redis configs the service must refuse to start on ==="

mkdir -p "$PID_DIR" "$WORK_DIR"

# The control below connects for real — it reads the source's consumer group
# into existence — so this needs a broker even though every rejection is
# decided before the first connection.
require_redis "$TEST_NAME" || exit $?

# The orchestrator started an Alert Bridge on this config already. This test
# drives its own instances instead, so that one goes first — two consumers on
# one group would split the reads and make the control ambiguous.
stop_alert_bridge_local "$PID_DIR"

# Derive a config with one Redis setting changed. The mutation is expressed as
# a path plus a value so the cases below read as the thing being tested rather
# than as YAML surgery; a literal `null` deletes the key.
# Usage: mutate_config DST_NAME PATH VALUE
mutate_config() {
    local dst="$WORK_DIR/$1" path="$2" value="$3"
    python3 - "$BASE_CONFIG" "$dst" "$path" "$value" <<'PY'
import sys
import yaml

src, dst, path, raw = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(src, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

keys = path.split(".")
node = cfg
for key in keys[:-1]:
    node = node[key]

if raw == "null":
    node.pop(keys[-1], None)
else:
    node[keys[-1]] = yaml.safe_load(raw)

with open(dst, "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)
PY
    echo "$dst"
}

# Start the service on CONFIG and require that it stops on its own, with LOG
# naming the setting at fault.
#
# Both halves are the assertion. Exiting is not enough on its own — a process
# that dies for an unrelated reason also exits — and the message is what says
# the operator was told which key to fix rather than left with a stack trace
# about a connection.
# Usage: expect_refusal LABEL CONFIG EXPECTED_LOG_TEXT
expect_refusal() {
    local label="$1" config="$2" expected="$3"
    local log="$WORK_DIR/$(basename "$config").log"
    local waited=0

    # The config directory is the variant's own, not the test's: load_config
    # refuses a --config outside ALERT_AGENT_CONFIG_DIR, so a variant written
    # elsewhere is rejected as a disallowed path before its contents are read —
    # which would look like a pass and prove nothing.
    ALERT_AGENT_CONFIG_DIR="$WORK_DIR" python3 \
        "$REPO_ROOT/enhance_alert_with_vlm.py" --config "$config" > "$log" 2>&1 &
    local pid=$!

    # Every case here is decided by validate_configuration, which runs before
    # the API child, the metrics port and the fork — so this returns in a couple
    # of seconds. The bound is generous, and being bounded is what makes a
    # service that starts anyway a failure rather than a hang.
    while [ "$waited" -lt 20 ] && kill -0 "$pid" 2>/dev/null; do
        sleep 1
        waited=$((waited + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        sleep 1
        kill -9 "$pid" 2>/dev/null || true
        print_status "fail" "FAIL: $label — the service started and kept running"
        print_status "info" "Last 20 lines of its log:"
        tail -20 "$log" 2>/dev/null || true
        return 1
    fi

    if ! grep -qF "$expected" "$log" 2>/dev/null; then
        print_status "fail" "FAIL: $label — the service stopped, but not for the reason under test"
        print_status "info" "  expected the log to name: $expected"
        print_status "info" "Last 20 lines of its log:"
        tail -20 "$log" 2>/dev/null || true
        return 1
    fi

    print_status "ok" "$label — refused, and the log names the setting"
}

# 1. The control. The base config must start, or every refusal below could be
#    this environment rather than the mutation.
if ! start_alert_bridge_local "$REPO_ROOT" "$PID_DIR" "$BASE_CONFIG" 15; then
    print_status "fail" "FAIL: the unmodified config did not start — the cases below would prove nothing"
    exit 1
fi
print_status "ok" "Control: the unmodified Redis config starts"
stop_alert_bridge_local "$PID_DIR"

# 2. A source naming one kind. Both kinds are produced upstream and verified by
#    the same pipeline, so consuming one means the other accumulates unread on a
#    stream nobody is listening to, while the service reports healthy.
CONFIG=$(mutate_config "one_kind.yaml" "event_bridge.redis_source.streams.incident" "null")
expect_refusal "A source that consumes alerts but not incidents" "$CONFIG" \
    "configures no incident stream"

# 3. A terminal route naming no stream. There is no default for where a verdict
#    goes: inferring one publishes every alert verdict to a stream the
#    deployment never named.
CONFIG=$(mutate_config "no_route_stream.yaml" "vlm_enhanced_sink.alert.redisStream.stream" "null")
expect_refusal "A VLM sink route with no stream" "$CONFIG" \
    "vlm_enhanced_sink.alert.redisStream.stream is not set"

# 4. A blank stream on the event-bridge sink. This is what a rendered config
#    produces for an unresolved variable, and reading it as "absent" disables
#    that one route while the other keeps working — so the sink looks healthy
#    and half its output goes nowhere.
CONFIG=$(mutate_config "blank_stream.yaml" "event_bridge.redis_sink.streams.enhanced_anomaly" '""')
expect_refusal "A blank stream on the event-bridge sink" "$CONFIG" \
    "event_bridge.redis_sink.streams['enhanced_anomaly'] is empty"

# 5. A port outside the TCP range. Passed through, this fails as a connection
#    error against the address, which reads as "Redis is down" and sends the
#    operator to look at a broker that is fine.
CONFIG=$(mutate_config "bad_port.yaml" "redis.port" "70000")
expect_refusal "A port outside the TCP range" "$CONFIG" \
    "redis.port is 70000"

# 6. A database number that is not a number. Coerced to 0, this connects to a
#    database that exists, accepts every command and consumes an empty stream in
#    the wrong place — which reads as "the producer published nothing".
CONFIG=$(mutate_config "bad_db.yaml" "redis.db" '"one"')
expect_refusal "A database that is not a database number" "$CONFIG" \
    "redis.db must be a database number"

# 7. A password file that was named and never mounted. Connecting without the
#    credential surfaces as NOAUTH on the first command, several layers from the
#    mount that caused it — and in a forked child, so as a crash-loop.
CONFIG=$(mutate_config "missing_secret.yaml" "redis.password_file" \
    '"/run/secrets/redis-password-that-was-never-mounted"')
expect_refusal "A password file that is not there" "$CONFIG" \
    "Redis password is unavailable"

echo ""
print_status "ok" "PASS: every incomplete Redis config stopped the service and named the setting"
exit 0
