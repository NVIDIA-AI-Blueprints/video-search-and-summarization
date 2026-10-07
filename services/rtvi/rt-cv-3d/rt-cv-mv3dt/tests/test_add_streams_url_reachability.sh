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
# Bug 6558487. a URL that cannot be reached must be caught before registering
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

t_unreachable_endpoint_is_refused() {
  local out status dead left
  dead="$(closed_port)"
  stub_start || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --delay 0 --ready-timeout 5 \
        "Camera=rtsp://127.0.0.1:${dead}/nonexistent" 2>&1)"; status=$?
  left="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --list 2>&1)"
  stub_stop
  assert_status "$status" 2 "unreachable endpoint must be refused" || return 1
  assert_contains "$out" "nothing is listening" "says why" || return 1
  assert_not_contains "$out" "STREAM_ADD_SUCCESS" "must refuse before the POST" || return 1
  assert_contains "$left" "stream-count: 0" "nothing was registered"
}

t_reachable_endpoint_passes() {
  local out status
  stub_start || return 1
  # The stub's own port is listening, so it stands in for a reachable RTSP source.
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --delay 0 --ready-timeout 5 \
        --activation-timeout 0 "Camera=rtsp://127.0.0.1:${STUB_PORT}/live/Camera" 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 0 "reachable endpoint must be accepted" || return 1
  assert_not_contains "$out" "nothing is listening" "no false refusal"
}

t_override_allows_unreachable() {
  local out status dead
  dead="$(closed_port)"
  stub_start || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$ADD_STREAMS" --no-url-check --delay 0 \
        --ready-timeout 5 --activation-timeout 0 "Camera=rtsp://127.0.0.1:${dead}/x" 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 0 "--no-url-check overrides" || return 1
  assert_contains "$out" "STREAM_ADD_SUCCESS" "the add still happens"
}

run_test "unreachable RTSP endpoint is refused before the add" t_unreachable_endpoint_is_refused
run_test "reachable endpoint is not refused"                   t_reachable_endpoint_passes
run_test "--no-url-check overrides the check"                  t_override_allows_unreachable
tests_summary "url-reachability"
