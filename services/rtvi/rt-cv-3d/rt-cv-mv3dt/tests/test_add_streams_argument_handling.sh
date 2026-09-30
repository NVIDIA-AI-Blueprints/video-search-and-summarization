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
# Bug 6630744. -h must print usage, not the SPDX licence header.
# Bug 6633350. --file must tolerate surrounding whitespace, blanks and comments.
#
# Both behaviours are argument parsing only: nothing here contacts the REST API,
# so no stub is needed. The existing basic-operations test asserts that --help
# exits 0, which 6630744 also did while printing the licence, so the exit status
# alone cannot defend it. These assert the output.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

t_help_prints_usage_not_licence() {
  local out status flag
  for flag in -h --help; do
    out="$(bash "$ADD_STREAMS" "$flag" 2>&1)"
    status=$?
    assert_status "$status" 0 "$flag exit status" || return 1
    assert_contains "$out" "Usage:" "$flag prints usage" || return 1
    assert_contains "$out" "--remove-all" "$flag documents the options" || return 1
    assert_not_contains "$out" "SPDX-License-Identifier" "$flag hides the licence header" || return 1
    assert_not_contains "$out" "Copyright (c)" "$flag hides the copyright line" || return 1
  done
}

RTSP_B="rtsp://cam-b.invalid:8554/stream"

# A --file whose lines are padded, blank or comments. The trim has to survive as
# far as registration to matter, so this drives the stub and asserts on the
# camera_id the server actually received. Asserting only that the run failed
# against a closed port does not work: add-streams.sh gives up at the readiness
# check before it ever uses a stream name, so trimmed and untrimmed behave
# identically there.
t_file_tolerates_whitespace_and_comments() {
  local dir out status
  dir="$(mktemp -d)"
  printf '  Camera_01=%s  \n\n\t# a comment\n   \n' "$RTSP_B" > "$dir/streams.txt"

  stub_start || { rm -rf "$dir"; return 1; }
  out="$(DS_PORT="$STUB_PORT" bash "$ADD_STREAMS" --no-url-check --no-sei-check \
           --delay 0 --ready-timeout 5 --file "$dir/streams.txt" 2>&1)"
  status=$?
  stub_stop
  rm -rf "$dir"

  assert_status "$status" 0 "--file exit status" || return 1
  assert_contains "$out" "camera_id=Camera_01" "the trimmed name reached the server" || return 1
  assert_not_contains "$out" "camera_id= Camera_01" "no leading space survived" || return 1
  assert_not_contains "$out" "a comment" "comment lines are dropped" || return 1
}

run_test "-h and --help print usage, not the licence header" t_help_prints_usage_not_licence
run_test "--file tolerates whitespace, blanks and comments"  t_file_tolerates_whitespace_and_comments
tests_summary "argument handling"
