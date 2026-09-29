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
# Bug 6558487. an invalid RTSP URL must not be reported as an active stream,
# and a stream that never activates must be named rather than retried forever
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

RTSP="rtsp://cam.invalid:8554/stream"

t_inactive_stream_is_reported() {
  local out status
  # adds-inactive: registered in get-stream-info, absent from metrics stream-stats.
  #
  # A healthy stream is seeded so stream-stats is non-empty. That is what makes
  # the added stream's absence meaningful: an endpoint that names no stream at
  # all cannot tell a dead source from stats not being collected (nvdslogger
  # off), and is reported as unverifiable instead. See bug 6821633 and
  # test_activation_unverifiable_when_metrics_blind.sh.
  stub_start --mode adds-inactive --seed-stream Healthy=rtsp://h.invalid/ok || return 1
  out="$(DS_PORT="$STUB_PORT" bash "$ADD_STREAMS" --no-url-check --delay 0 --ready-timeout 5 \
        --activation-timeout 4 "Camera=${RTSP}" 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 2 "exit status for a stream that never activates" || return 1
  assert_contains "$out" "not producing frames" "inactive stream is reported" || return 1
  assert_contains "$out" "Camera" "the offending camera is named"
}

t_healthy_stream_is_not_flagged() {
  local out status
  stub_start || return 1
  out="$(DS_PORT="$STUB_PORT" bash "$ADD_STREAMS" --no-url-check --delay 0 --ready-timeout 5 \
        --activation-timeout 4 "Camera=${RTSP}" 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 0 "healthy stream must not be flagged" || return 1
  assert_not_contains "$out" "not producing frames" "no false positive"
}

t_check_is_skippable() {
  local out status
  stub_start --mode adds-inactive || return 1
  out="$(DS_PORT="$STUB_PORT" bash "$ADD_STREAMS" --no-url-check --delay 0 --ready-timeout 5 \
        --activation-timeout 0 "Camera=${RTSP}" 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 0 "--activation-timeout 0 disables the check"
}

run_test "inactive stream is detected and named"      t_inactive_stream_is_reported
run_test "healthy stream is not flagged"              t_healthy_stream_is_not_flagged
run_test "activation check can be disabled"           t_check_is_skippable
tests_summary "inactive-stream"

# Added after live testing: adding cameras one at a time is a normal partial
# batch, and MV3DT activates no source until the batch is complete. The check
# must stay silent then, or every incremental add stalls and reports a failure
# that is not one (found on a real 4-camera deployment).
t_partial_batch_is_not_flagged() {
  local out status tmp
  tmp="$(mktemp -d)"
  mkdir -p "$tmp" "$tmp/generated/camInfo"
  stage_add_streams "$tmp" >/dev/null
  local cam
  for cam in Camera Camera_01 Camera_02 Camera_03; do
    printf 'name: %s\n' "$cam" > "$tmp/generated/camInfo/${cam}.yml"
  done
  stub_start --mode adds-inactive || return 1
  # 1 of 4 registered: partial batch, must not be flagged and must not stall.
  local t0=$SECONDS
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$tmp/scripts/add-streams.sh" --no-url-check \
        --delay 0 --ready-timeout 5 --activation-timeout 20 "Camera=rtsp://h.invalid/1" 2>&1)"; status=$?
  local elapsed=$(( SECONDS - t0 ))
  stub_stop; rm -rf "$tmp"
  assert_status "$status" 0 "partial batch must not be flagged" || return 1
  assert_not_contains "$out" "not producing frames" "no false positive on a partial batch" || return 1
  (( elapsed < 15 )) || { fail "partial batch stalled ${elapsed}s and must not wait out the timeout"; return 1; }
}

run_test "partial batch is not flagged and does not stall" t_partial_batch_is_not_flagged
tests_summary "partial-batch"
