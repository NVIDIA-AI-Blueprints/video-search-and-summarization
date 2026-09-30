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
# Bug 6558302. --list must report a REST API outage without a Python traceback
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

t_list_api_down_no_traceback() {
  local out port status
  port="$(closed_port)" || return 1

  out="$(DS_PORT="$port" bash "$ADD_STREAMS" --list 2>&1)"
  status=$?

  assert_status "$status" 1 "exit status" || return 1
  assert_contains "$out" "ERROR: Cannot connect to MV3DT perception REST API at http://localhost:${port}." "clear connection error" || return 1
  assert_contains "$out" "docker ps -a --filter name=vss-rtvi-cv-mv3dt" "diagnostic hint" || return 1
  assert_not_contains "$out" "Traceback" "no Python traceback" || return 1
  assert_not_contains "$out" "JSONDecodeError" "no JSON parser traceback" || return 1
}

run_test "add-streams --list reports API outage without Python traceback" t_list_api_down_no_traceback
tests_summary "list-api-down"
