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
# Bug 6635198. recovery instructions must not print before a request was sent
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"
: "${STAGE_CONFIGS:=$MV3DT/scripts/stage-configs.sh}"
source "${MV3DT_TESTS_DIR}/lib.sh"

t_remove_all_reports_unreachable_api() {
  local out status
  # No stub running: the registry lookup must fail loudly rather than arrive as
  # an empty list and be reported as "nothing to remove".
  out="$(DS_PORT=59999 bash "$ADD_STREAMS" --delay 0 --remove-all --yes 2>&1)"; status=$?

  assert_status "$status" 1 "unreachable API must not exit 0" || {
    printf '%s\n' "$out"
    return 1
  }
  assert_contains "$out" "cannot reach the perception REST API" "names the unreachable API" || return 1
  assert_not_contains "$out" "No streams are registered" "must not claim an empty registry" || return 1
}

run_test "--remove-all reports an unreachable registry instead of nothing-to-remove" t_remove_all_reports_unreachable_api

tests_summary "$(basename "${BASH_SOURCE[0]}")"
