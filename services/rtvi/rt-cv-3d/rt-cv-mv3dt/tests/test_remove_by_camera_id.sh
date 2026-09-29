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
# Bug 6557680. removal must accept the bare camera id that --list prints
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

t_remove_accepts_listed_camera_id() {
  local out status

  stub_start --seed-stream Camera_02=rtsp://host.invalid:8554/live/Camera_02 || return 1
  out="$(DS_PORT="$STUB_PORT" bash "$ADD_STREAMS" --delay 0 --remove Camera_02 2>&1)"; status=$?
  stub_stop

  assert_status "$status" 0 "exit status" || {
    printf '%s\n' "$out"
    return 1
  }
  assert_not_contains "$out" "Unknown arg: Camera_02" "bare camera ID should be accepted" || return 1
  assert_contains "$out" "camera_id=Camera_02" "remove targets listed camera ID" || return 1
  assert_contains "$out" "stream-count: 0" "stream was removed" || return 1
}

run_test "remove accepts camera IDs returned by list" t_remove_accepts_listed_camera_id
tests_summary "remove-by-camera-id"

# Added after live testing: with the API down, a bare-id remove used to leak
# curl's own error and then claim the camera was "not found in stream-info",
# which points at the wrong problem.
t_remove_unreachable_api_is_named() {
  local out status port
  port="$(closed_port)"
  out="$(DS_PORT="$port" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --remove --delay 0 Camera_02 2>&1)"; status=$?
  [[ "$status" -ne 0 ]] || { fail "expected non-zero exit when the API is unreachable"; return 1; }
  assert_contains "$out" "cannot reach the perception REST API" "outage is named" || return 1
  assert_not_contains "$out" "is not registered" "must not blame the camera id" || return 1
  assert_not_contains "$out" "curl: (" "curl's own error must not leak"
}

t_remove_unregistered_camera_is_named() {
  local out status
  stub_start --seed-stream "Camera=rtsp://h/1" || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --remove --delay 0 Camera_99 2>&1)"; status=$?
  stub_stop
  [[ "$status" -ne 0 ]] || { fail "expected non-zero exit for an unregistered camera"; return 1; }
  assert_contains "$out" "is not registered" "unregistered camera is named" || return 1
  assert_not_contains "$out" "cannot reach" "must not claim an outage"
}

run_test "remove: unreachable API is reported as an outage"   t_remove_unreachable_api_is_named
run_test "remove: unregistered camera id is reported as such" t_remove_unregistered_camera_is_named
tests_summary "remove-diagnosis"
