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
# Bug 6821633. The activation check reads /api/v1/metrics, which only carries
# per-stream statistics when nvdslogger is enabled in the sink config. With it
# off the endpoint answers but names no stream at all, which is not evidence
# that the sources are dead. Reporting every stream UNSEEN and failing is wrong.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

t_metrics_without_stats_is_not_a_failure() {
  local out status tmp cam
  tmp="$(mktemp -d)"
  mkdir -p "$tmp" "$tmp/generated/camInfo"
  stage_add_streams "$tmp" >/dev/null
  for cam in Camera Camera_01; do
    printf 'name: %s\n' "$cam" > "$tmp/generated/camInfo/${cam}.yml"
  done

  # Every stream inactive means metrics returns an empty stream-stats list, which
  # is the same thing the client sees when nvdslogger is off and the sources are
  # perfectly healthy. The client cannot tell those apart, so it must not claim
  # the streams are dead.
  stub_start --mode adds-inactive \
             --dead-stream Camera=rtsp://h.invalid/0 || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$tmp/scripts/add-streams.sh" \
        --no-url-check --delay 0 --ready-timeout 5 --activation-timeout 6 \
        "Camera_01=rtsp://h.invalid/1" 2>&1)"; status=$?
  stub_stop; rm -rf "$tmp"

  assert_status "$status" 0 "no stats at all must not fail the run" || {
    printf '%s\n' "$out"; return 1; }
  assert_contains "$out" "could not verify activation" \
    "says the check could not run" || { printf '%s\n' "$out"; return 1; }
  assert_not_contains "$out" "UNSEEN" \
    "must not blame the streams when the endpoint named none of them" || {
    printf '%s\n' "$out"; return 1; }
}

# A wholly dead batch produces the same empty response as a blind check, so the
# run still exits 0. That is deliberate: the two are indistinguishable from the
# endpoint, the staged config cannot settle it (the running process loaded its
# own config at startup, and nvdslogger can sit in a disabled sink), and failing
# a possibly healthy run is the regression this bug was filed for. What the note
# must not do is let the operator believe activation was confirmed.
t_unverifiable_note_names_both_causes() {
  local out status tmp cam
  tmp="$(mktemp -d)"
  mkdir -p "$tmp" "$tmp/generated/camInfo"
  stage_add_streams "$tmp" >/dev/null
  for cam in Camera Camera_01; do
    printf 'name: %s\n' "$cam" > "$tmp/generated/camInfo/${cam}.yml"
  done

  stub_start --mode adds-inactive \
             --dead-stream Camera=rtsp://h.invalid/0 || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT=0 bash "$tmp/scripts/add-streams.sh" \
        --no-url-check --delay 0 --ready-timeout 5 --activation-timeout 6 \
        "Camera_01=rtsp://h.invalid/1" 2>&1)"; status=$?
  stub_stop; rm -rf "$tmp"

  assert_status "$status" 0 "an unverifiable check must not fail the run" || {
    printf '%s\n' "$out"; return 1; }
  assert_contains "$out" "nvdslogger is off" "names the collection-off cause" || {
    printf '%s\n' "$out"; return 1; }
  assert_contains "$out" "no source decoded" "names the dead-batch cause" || {
    printf '%s\n' "$out"; return 1; }
  assert_contains "$out" "Active sources" "gives the command that settles it" || {
    printf '%s\n' "$out"; return 1; }
}

run_test "metrics with no stream-stats reports unverifiable, not UNSEEN" \
         t_metrics_without_stats_is_not_a_failure

run_test "the unverifiable note names both causes, not just one" \
         t_unverifiable_note_names_both_causes

tests_summary "$(basename "${BASH_SOURCE[0]}")"
