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
# Every failure path in add-streams.sh tells the operator to read the perception
# log. A deployment running more than one perception instance suffixes the
# container (vss-rtvi-cv-mv3dt-0), so a hardcoded name prints a command that does
# not exist on the machine it was printed for. The name comes from
# PERCEPTION_CONTAINER, and this test pins that every diagnostic uses it.
# No bug filed. Found during the script history review, 2026-09-28.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

# A closed port makes every connectivity diagnostic fire at once.
t_diagnostics_use_the_configured_container() {
  local out port
  port="$(closed_port)"
  out="$(DS_PORT="$port" PERCEPTION_CONTAINER=my-perception-7 \
        bash "$ADD_STREAMS" --list 2>&1)"

  assert_contains "$out" "my-perception-7" "the configured container is named" || {
    printf '%s\n' "$out"; return 1; }
  assert_not_contains "$out" "vss-rtvi-cv-mv3dt" \
    "no hardcoded default leaks into the advice" || { printf '%s\n' "$out"; return 1; }
}

t_default_is_unchanged_when_unset() {
  local out port
  port="$(closed_port)"
  out="$(DS_PORT="$port" bash "$ADD_STREAMS" --list 2>&1)"
  assert_contains "$out" "vss-rtvi-cv-mv3dt" "the stock name is still the default" || {
    printf '%s\n' "$out"; return 1; }
}

run_test "diagnostics name the configured perception container" \
         t_diagnostics_use_the_configured_container
run_test "the default container name is unchanged"  t_default_is_unchanged_when_unset

tests_summary "$(basename "${BASH_SOURCE[0]}")"
