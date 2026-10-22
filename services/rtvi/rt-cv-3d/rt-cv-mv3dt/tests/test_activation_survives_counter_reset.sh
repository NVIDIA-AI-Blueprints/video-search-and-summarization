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
#
# Regression test for the MV3DT standalone scripts. Self-contained: paths
# default to this checkout, env vars still override.
# Run:  bash tests/run.sh          (or: bash "$0" for just this one)
#
# Bug 6886570. A camera that is removed and re-added restarts its frame counter
# from zero, while the metrics endpoint can still carry the value from its
# previous life. The checker used to keep the first value it saw and wait for the
# counter to climb past it, so a healthy re-added stream was reported STATIC and
# add-streams.sh exited 2. A counter that goes backwards has to be measured again
# from the lower value. It must still need to advance afterwards: a reset alone
# is not proof of life, and nothing else covers the STATIC verdict at all.
#
# These drive wait_for_activation.py directly against a scripted metrics server,
# because the shared stub serves one counter for every stream and cannot reset a
# single camera. The first value served is consumed by the checker's own probe.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${WAIT_FOR_ACTIVATION:=$MV3DT/scripts/lib/wait_for_activation.py}"

source "${MV3DT_TESTS_DIR}/lib.sh"

# $1 = JSON list with one object per poll, each {sensor_id: frame_number}. The
# last entry repeats once the list runs out. Sets METRICS_PORT and METRICS_PID.
metrics_start() {
  METRICS_PY="$(mktemp)"; METRICS_OUT="$(mktemp)"
  cat > "$METRICS_PY" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

steps, lock, state = json.loads(sys.argv[1]), threading.Lock(), {"i": 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if not self.path.startswith("/api/v1/metrics"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        with lock:
            step = steps[min(state["i"], len(steps) - 1)]
            state["i"] += 1
        stats = [{"sensor_id": s, "sensor_name": s, "fps": 0.0, "frame_number": f}
                 for s, f in step.items()]
        body = json.dumps({"metrics-info": {"stream-count": len(stats),
                                            "stream-stats": stats}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print("PORT=%d" % server.server_address[1], flush=True)
server.serve_forever()
PY
  python3 "$METRICS_PY" "$1" > "$METRICS_OUT" &
  METRICS_PID=$!
  local i
  for i in $(seq 1 50); do
    METRICS_PORT="$(sed -n 's/^PORT=//p' "$METRICS_OUT")"
    [[ -n "$METRICS_PORT" ]] && return 0
    sleep 0.1
  done
  return 1
}

metrics_stop() {
  kill "$METRICS_PID" 2>/dev/null; wait "$METRICS_PID" 2>/dev/null
  rm -f "$METRICS_PY" "$METRICS_OUT"
}

# $1 = JSON poll list, $2 = activation timeout, then the cameras to wait for.
# Sets CHECK_OUT and CHECK_STATUS.
run_checker() {
  local steps="$1" timeout="$2"; shift 2
  metrics_start "$steps" || return 1
  CHECK_OUT="$(BASE="http://127.0.0.1:${METRICS_PORT}" ACTIVATION_TIMEOUT="$timeout" \
              python3 "$WAIT_FOR_ACTIVATION" "$@" 2>&1)"
  CHECK_STATUS=$?
  metrics_stop
}

t_reset_then_advance_is_producing() {
  # The reported case. The stale value is far above anything the new source will
  # reach for a long time, so only re-baselining on the decrease can pass this.
  run_checker '[{"Camera_01":237000},{"Camera_01":237000},{"Camera_01":10},{"Camera_01":40},{"Camera_01":70}]' 20 Camera_01 || return 1
  assert_status "$CHECK_STATUS" 0 "a re-added stream that advances is producing" || {
    printf '%s\n' "$CHECK_OUT"; return 1; }
  assert_not_contains "$CHECK_OUT" "STATIC" "no false STATIC verdict" || return 1
}

t_reset_then_frozen_is_static() {
  # Guards the other direction. Treating any change as life would pass this, and a
  # source that restarted and then stalled must still be called out.
  run_checker '[{"Camera_01":5000},{"Camera_01":5000},{"Camera_01":12},{"Camera_01":12}]' 7 Camera_01 || return 1
  assert_status "$CHECK_STATUS" 1 "a reset with no advance afterwards is not producing" || {
    printf '%s\n' "$CHECK_OUT"; return 1; }
  assert_contains "$CHECK_OUT" "STATIC Camera_01" "named as STATIC, not UNSEEN" || {
    printf '%s\n' "$CHECK_OUT"; return 1; }
}

t_advance_before_the_reset_does_not_count() {
  # Camera_01 advances, then restarts and freezes. The checker returns the moment
  # every camera has advanced, so this only matters while another camera is still
  # being waited on, which is why Camera_02 never moves. The earlier advance
  # belongs to the old source and says nothing about the one running now.
  run_checker '[{"Camera_01":100,"Camera_02":50},{"Camera_01":100,"Camera_02":50},{"Camera_01":130,"Camera_02":50},{"Camera_01":5,"Camera_02":50},{"Camera_01":5,"Camera_02":50}]' 8 Camera_01 Camera_02 || return 1
  assert_status "$CHECK_STATUS" 1 "the run fails while Camera_02 is frozen" || {
    printf '%s\n' "$CHECK_OUT"; return 1; }
  assert_contains "$CHECK_OUT" "STATIC Camera_01" "the advance before the reset is discarded" || {
    printf '%s\n' "$CHECK_OUT"; return 1; }
  assert_contains "$CHECK_OUT" "STATIC Camera_02" "the frozen camera is named too" || {
    printf '%s\n' "$CHECK_OUT"; return 1; }
}

t_frozen_counter_is_static() {
  # The original STATIC case, observed live at 296 while DeepStream retried its
  # RTSP connect. No test exercised this verdict before this one.
  run_checker '[{"Camera_01":296},{"Camera_01":296}]' 7 Camera_01 || return 1
  assert_status "$CHECK_STATUS" 1 "a frozen counter is not producing" || {
    printf '%s\n' "$CHECK_OUT"; return 1; }
  assert_contains "$CHECK_OUT" "STATIC Camera_01" "named as STATIC" || {
    printf '%s\n' "$CHECK_OUT"; return 1; }
  assert_contains "$CHECK_OUT" "OBS Camera_01 frame_number=296" "reports what it observed" || return 1
}

t_steady_advance_is_producing() {
  # The ordinary case must be unchanged by the reset handling.
  run_checker '[{"Camera_01":100},{"Camera_01":100},{"Camera_01":130},{"Camera_01":160}]' 20 Camera_01 || return 1
  assert_status "$CHECK_STATUS" 0 "a steadily advancing stream is producing" || {
    printf '%s\n' "$CHECK_OUT"; return 1; }
}

run_test "reset then advance is producing, not STATIC"      t_reset_then_advance_is_producing
run_test "reset then frozen is still STATIC"                 t_reset_then_frozen_is_static
run_test "an advance before the reset does not count"        t_advance_before_the_reset_does_not_count
run_test "a frozen counter is STATIC"                        t_frozen_counter_is_static
run_test "a steadily advancing stream is producing"          t_steady_advance_is_producing
tests_summary "activation counter reset"
