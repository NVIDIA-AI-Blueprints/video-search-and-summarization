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
# Regression test for the MV3DT standalone scripts. Self-contained: paths
# default to this checkout, env vars still override.
# Run:  bash tests/run.sh          (or: bash "$0" for just this one)
#
# Bug 6636526. an invalid stream registered earlier stalls the whole batch, so
# the valid stream that completes the batch was reported as the one not producing.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"
: "${STAGE_CONFIGS:=$MV3DT/scripts/stage-configs.sh}"
source "${MV3DT_TESTS_DIR}/lib.sh"

t_blames_the_actually_stalled_stream() {
  local out status tmp cam
  tmp="$(mktemp -d)"
  mkdir -p "$tmp" "$tmp/generated/camInfo"
  stage_add_streams "$tmp" >/dev/null
  for cam in Camera Camera_01 Camera_02 Camera_03; do
    printf 'name: %s\n' "$cam" > "$tmp/generated/camInfo/${cam}.yml"
  done

  # Three good streams plus one dead one already registered. The fourth add
  # completes the batch. Camera_02 is the source that cannot decode.
  # adds-inactive makes the newly added camera inactive too, which is what the
  # real stalled batch does: one source that cannot decode takes the rest with it.
  stub_start --mode adds-inactive \
             --seed-stream Camera=rtsp://h.invalid/0 \
             --seed-stream Camera_01=rtsp://h.invalid/1 \
             --dead-stream Camera_02=rtsp://h.invalid/2 || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$tmp/scripts/add-streams.sh" \
        --no-url-check --delay 0 --ready-timeout 5 --activation-timeout 6 \
        "Camera_03=rtsp://h.invalid/3" 2>&1)"; status=$?
  stub_stop; rm -rf "$tmp"

  assert_status "$status" 2 "a stalled batch must fail" || { printf '%s\n' "$out"; return 1; }
  # Assert on the report itself. Plain "Camera_02" also appears in the trailing
  # stream-info listing, so matching that would pass even unfixed.
  assert_contains "$out" "UNSEEN Camera_02" "the actually stalled stream is named in the report" || {
    printf '%s\n' "$out"; return 1; }

}

run_test "6636526: the stalled stream is named, not the last one added" t_blames_the_actually_stalled_stream

tests_summary "$(basename "${BASH_SOURCE[0]}")"
