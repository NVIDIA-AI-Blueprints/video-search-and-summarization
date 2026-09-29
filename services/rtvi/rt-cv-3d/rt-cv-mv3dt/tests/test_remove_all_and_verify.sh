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
# Bug 6636703. there must be an explicit way to remove every registered stream
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

t_clearing_all_is_quiet_when_api_survives() {
  local out status left
  stub_start --seed-stream "Camera=rtsp://h/1" --seed-stream "Camera_01=rtsp://h/2" || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --remove --delay 0 Camera Camera_01 2>&1)"; status=$?
  left="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --list 2>&1)"
  stub_stop
  assert_status "$status" 0 "clearing every stream must succeed" || return 1
  assert_contains "$out" "stream-count: 0" "reports the verified count" || return 1
  assert_not_contains "$out" "stopped responding" "no recovery guidance while the API answers" || return 1
  assert_not_contains "$out" "force-recreate perception" "no recreate advice on a healthy run" || return 1
  assert_contains "$left" "stream-count: 0" "the removal actually happened"
}

t_remove_all_lists_and_removes() {
  local out status
  stub_start --seed-stream "Camera=rtsp://h/1" --seed-stream "Camera_01=rtsp://h/2" || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --remove-all --yes --delay 0 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 0 "--remove-all --yes succeeds" || return 1
  assert_contains "$out" "2 registered stream(s) will be removed" "lists what it will remove" || return 1
  assert_contains "$out" "Camera_01" "names the streams" || return 1
  assert_contains "$out" "stream-count: 0" "prints the verified count"
}

t_remove_all_needs_confirmation() {
  local out status
  stub_start --seed-stream "Camera=rtsp://h/1" || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --remove-all --delay 0 </dev/null 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 1 "refuses without --yes when not on a terminal" || return 1
  assert_contains "$out" "Re-run with --yes" "says how to proceed non-interactively"
}

t_remove_all_on_empty_registry() {
  local out status
  stub_start || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --remove-all --yes --delay 0 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 0 "empty registry is not an error" || return 1
  assert_contains "$out" "nothing to remove" "says there is nothing to do"
}

t_partial_removal_is_silent() {
  local out status
  stub_start --seed-stream "Camera=rtsp://h/1" --seed-stream "Camera_01=rtsp://h/2" || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --remove --delay 0 Camera 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 0 "removing one of two succeeds" || return 1
  assert_not_contains "$out" "stopped responding" "no guidance when streams remain"
}

run_test "clearing every stream stays quiet when the API survives" t_clearing_all_is_quiet_when_api_survives
run_test "--remove-all lists and removes"                          t_remove_all_lists_and_removes
run_test "--remove-all refuses without --yes off a terminal"       t_remove_all_needs_confirmation
run_test "--remove-all on an empty registry"                       t_remove_all_on_empty_registry
run_test "removing one of several stays silent"                    t_partial_removal_is_silent
tests_summary "remove-path"
