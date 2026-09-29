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
# Bug 6557851. a camera with no generated camInfo/tracker/pubsub entry must be refused
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

make_component_root() {
  local root="$1"

  mkdir -p "$root/scripts" "$root/generated/camInfo" "$root/generated/configs"
  stage_add_streams "$root" >/dev/null

  local cam
  for cam in Camera Camera_01 Camera_02 Camera_03; do
    printf 'name: %s\n' "$cam" > "$root/generated/camInfo/${cam}.yml"
  done

  cat > "$root/generated/configs/ds-mv3dt-tracker-config.yml" <<'YAML'
ObjectModelProjection:
  cameraModelFilepath:
    Camera: /tmp/camInfo/Camera.yml
    Camera_01: /tmp/camInfo/Camera_01.yml
    Camera_02: /tmp/camInfo/Camera_02.yml
    Camera_03: /tmp/camInfo/Camera_03.yml
YAML

  cat > "$root/generated/configs/pub_sub_info_config.yml" <<'YAML'
pubBrokerTopicStr:
  Camera: broker;/trck/Camera
  Camera_01: broker;/trck/Camera_01
  Camera_02: broker;/trck/Camera_02
  Camera_03: broker;/trck/Camera_03
subPeerBrokerTopicStrs:
  Camera: []
  Camera_01: []
  Camera_02: []
  Camera_03: []
YAML
}

t_add_rejects_unconfigured_camera_id() {
  local out status tmp

  tmp="$(mktemp -d)" || return 1
  make_component_root "$tmp" || {
    rm -rf "$tmp"
    return 1
  }

  stub_start || {
    rm -rf "$tmp"
    return 1
  }
  out="$(cd "$tmp" && DS_PORT="$STUB_PORT" bash scripts/add-streams.sh --delay 0 --ready-timeout 5 \
        "Camera_04=rtsp://host.invalid:8554/live/Camera_04" 2>&1)"
  status=$?
  stub_stop
  rm -rf "$tmp"

  assert_status "$status" 2 "exit status" || {
    printf '%s\n' "$out"
    return 1
  }
  assert_contains "$out" "Camera_04" "error identifies the camera id" || return 1
  assert_contains "$out" "not configured" "camera id is rejected by config validation" || return 1
  assert_not_contains "$out" "HTTP 200" "invalid camera should not be POSTed successfully" || return 1
}

run_test "add-streams rejects camera IDs missing from generated MV3DT configs" t_add_rejects_unconfigured_camera_id
tests_summary "unconfigured-camera-id"
