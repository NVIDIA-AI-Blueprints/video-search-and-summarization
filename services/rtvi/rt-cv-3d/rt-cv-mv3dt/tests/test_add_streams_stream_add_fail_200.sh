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
# Bug 6558401. a stream add reported as failed in an HTTP 200 body must fail the run
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

t_add_reports_stream_add_fail_body_as_failure() {
  local out status

  stub_start --mode add-fails-200 || return 1
  out="$(DS_PORT="$STUB_PORT" bash "$ADD_STREAMS" --no-url-check --delay 0 --ready-timeout 5 \
        "Camera=rtsp://host.invalid:8554/live/Camera" 2>&1)"
  status=$?
  stub_stop

  assert_status "$status" 2 "exit status" || {
    printf '%s\n' "$out"
    return 1
  }
  assert_contains "$out" "failed to add stream" "server failure reason is reported" || return 1
  assert_not_contains "$out" "✓ HTTP 200" "failed stream add should not be shown as success" || return 1
}

run_test "add-streams treats HTTP 200 STREAM_ADD_FAIL body as failure" t_add_reports_stream_add_fail_body_as_failure
tests_summary "stream-add-fail-200"
