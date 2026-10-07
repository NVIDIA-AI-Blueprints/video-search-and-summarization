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
# Invariants: help, list, add and remove-by-NAME=URL against a stub DeepStream
# REST API. These hold before and after any fix, so a regression here means the
# basic contract of add-streams.sh moved.
# Invariants: help, list, add, remove by NAME=URL
#
# No bug. Invariants that must hold before and after any fix, so a regression
# here means the basic contract of add-streams.sh moved.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

RTSP_A="rtsp://cam-a.invalid:8554/stream"
RTSP_B="rtsp://cam-b.invalid:8554/stream"

t_help_exits_zero() {
  local out status
  out="$(bash "$ADD_STREAMS" --help 2>&1)"; status=$?
  assert_status "$status" 0 "--help exit status" || return 1
  assert_contains "$out" "add-streams.sh" "--help output"
}

t_list_reports_seeded_streams() {
  local out status
  stub_start --seed-stream "Camera=${RTSP_A}" || return 1
  out="$(DS_PORT="$STUB_PORT" bash "$ADD_STREAMS" --list 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 0 "--list exit status" || return 1
  assert_contains "$out" "stream-count: 1" "--list output" || return 1
  assert_contains "$out" "camera_id=Camera" "--list output"
}

t_add_registers_stream() {
  local out status
  stub_start || return 1
  out="$(DS_PORT="$STUB_PORT" bash "$ADD_STREAMS" --no-url-check \
        --delay 0 --ready-timeout 5 "Camera_01=${RTSP_B}" 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 0 "add exit status" || return 1
  assert_contains "$out" "ds-ready: YES" "add output" || return 1
  assert_contains "$out" "camera_id=Camera_01" "add output"
}

t_remove_by_name_url() {
  local out status
  stub_start --seed-stream "Camera_01=${RTSP_B}" || return 1
  out="$(DS_PORT="$STUB_PORT" bash "$ADD_STREAMS" \
        --remove --delay 0 "Camera_01=${RTSP_B}" 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 0 "remove exit status" || return 1
  assert_contains "$out" "stream-count: 0" "remove output"
}

run_test "--help exits 0"                       t_help_exits_zero
run_test "--list reports seeded streams"        t_list_reports_seeded_streams
run_test "add registers a stream"               t_add_registers_stream
run_test "remove by NAME=URL clears the stream" t_remove_by_name_url

tests_summary "basic operations"
