#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Regression test for the MV3DT standalone scripts. Self-contained: paths
# default to this checkout, env vars still override.
# Run:  bash tests/run.sh          (or: bash "$0" for just this one)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

# The check only applies when the staged config reads timestamps from the SEI,
# so a component copy with that config is what exercises it at all.
staged_copy() {  # $1=extract-sei-sim-time value; echoes the copy's add-streams.sh
  local v="$1" tmp cam
  tmp="$(mktemp -d)"
  mkdir -p "$tmp/scripts" "$tmp/generated/camInfo" "$tmp/generated/configs"
  cp "$ADD_STREAMS" "$tmp/scripts/add-streams.sh"; chmod +x "$tmp/scripts/add-streams.sh"
  for cam in Camera; do printf 'name: %s\n' "$cam" > "$tmp/generated/camInfo/${cam}.yml"; done
  printf '[streammux]\nextract-sei-sim-time=%s\n' "$v" \
    > "$tmp/generated/configs/ds-main-config-mv3dt.txt"
  echo "$tmp"
}

t_sei_disabled_is_reported() {
  local out status tmp; tmp="$(staged_copy 1)"
  stub_start --mode no-sei || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT="$STUB_PORT" bash "$tmp/scripts/add-streams.sh" --no-url-check \
        --delay 0 --ready-timeout 5 "Camera=rtsp://127.0.0.1:8554/s" 2>&1)"; status=$?
  stub_stop; rm -rf "$tmp"
  assert_status "$status" 2 "exit status when SEI frame IDs are off" || return 1
  assert_contains "$out" "not emitting SEI frame IDs" "SEI misconfiguration is named" || return 1
  assert_not_contains "$out" "STREAM_ADD_SUCCESS" "must refuse before registering anything"
}

t_sei_enabled_passes() {
  local out status tmp; tmp="$(staged_copy 1)"
  stub_start || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT="$STUB_PORT" bash "$tmp/scripts/add-streams.sh" --no-url-check \
        --delay 0 --ready-timeout 5 --activation-timeout 4 "Camera=rtsp://127.0.0.1:8554/s" 2>&1)"; status=$?
  stub_stop; rm -rf "$tmp"
  assert_status "$status" 0 "SEI enabled must not be flagged" || return 1
  assert_not_contains "$out" "SEI frame IDs" "no false positive"
}

# A deployment staged for SEI whose proxy cannot be reached is stopped, not let
# through: no timestamp configuration makes a SEI-less live source produce
# frames, so registering would only defer the failure.
t_unreachable_vst_blocks() {
  local out status port tmp; tmp="$(staged_copy 1)"
  port="$(closed_port)"
  stub_start || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT="$port" bash "$tmp/scripts/add-streams.sh" --no-url-check \
        --delay 0 --ready-timeout 5 --activation-timeout 4 "Camera=rtsp://127.0.0.1:8554/s" 2>&1)"; status=$?
  stub_stop; rm -rf "$tmp"
  assert_status "$status" 2 "unreachable VST API must stop the run" || return 1
  assert_not_contains "$out" "STREAM_ADD_SUCCESS" "must refuse before registering anything"
}

# Staged for host-clock timestamps: the SEI is not a prerequisite, so the check
# does not apply and must stay silent.
t_sei_not_required_skips() {
  local out status port tmp; tmp="$(staged_copy 0)"
  port="$(closed_port)"
  stub_start || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT="$port" bash "$tmp/scripts/add-streams.sh" --no-url-check \
        --delay 0 --ready-timeout 5 --activation-timeout 4 "Camera=rtsp://127.0.0.1:8554/s" 2>&1)"; status=$?
  stub_stop; rm -rf "$tmp"
  assert_status "$status" 0 "no SEI requirement means no check" || return 1
  assert_not_contains "$out" "SEI frame IDs" "no message when the SEI is not required"
}

t_override() {
  local out status
  stub_start --mode no-sei || return 1
  out="$(DS_PORT="$STUB_PORT" VST_HTTP_PORT="$STUB_PORT" bash "$ADD_STREAMS" --no-url-check \
        --no-sei-check --delay 0 --ready-timeout 5 --activation-timeout 4 "Camera=rtsp://127.0.0.1:8554/s" 2>&1)"; status=$?
  stub_stop
  assert_status "$status" 0 "--no-sei-check overrides"
}

run_test "SEI disabled is reported before any stream is added" t_sei_disabled_is_reported
run_test "SEI enabled is not flagged"                          t_sei_enabled_passes
run_test "unreachable VST proxy stops the run"                  t_unreachable_vst_blocks
run_test "SEI not required by the staged config -> skipped"     t_sei_not_required_skips
run_test "--no-sei-check overrides"                            t_override
tests_summary "sei-prerequisite"
