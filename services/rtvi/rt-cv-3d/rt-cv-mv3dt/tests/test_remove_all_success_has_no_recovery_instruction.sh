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
# Bug 6822178. An empty registry is the expected outcome of a successful
# --remove-all, not a failure, so the run must not hand back recovery steps.
#
# The alignment caveat that used to be printed here is gone too. It claimed that
# streams added after the registry empties share no time origin with the set that
# left. Measured on a live four-camera stack: a pipeline that never had a stream
# removed shows the same cross-sensor spread as one cycled through remove-all and
# re-add. Removal does not cause a desync, so a clean run says nothing about it.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

t_clean_remove_all_gives_no_recovery_steps() {
  local out status tmp
  tmp="$(mktemp -d)"
  mkdir -p "$tmp"
  stage_add_streams "$tmp" >/dev/null

  stub_start --seed-stream Camera=rtsp://h.invalid/0 \
             --seed-stream Camera_01=rtsp://h.invalid/1 || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$tmp/scripts/add-streams.sh" \
        --delay 0 --remove-all --yes 2>&1)"; status=$?
  stub_stop; rm -rf "$tmp"

  assert_status "$status" 0 "a clean remove-all must succeed" || {
    printf '%s\n' "$out"; return 1; }
  assert_not_contains "$out" "force-recreate" \
    "success must not hand back recovery steps" || { printf '%s\n' "$out"; return 1; }
  # The caveat is still worth saying. Only the instruction was wrong.
  assert_not_contains "$out" "time origin" \
    "no alignment caveat: removal does not cause a desync" || { printf '%s\n' "$out"; return 1; }
}

run_test "a successful --remove-all prints no recovery instruction" \
         t_clean_remove_all_gives_no_recovery_steps

tests_summary "$(basename "${BASH_SOURCE[0]}")"
