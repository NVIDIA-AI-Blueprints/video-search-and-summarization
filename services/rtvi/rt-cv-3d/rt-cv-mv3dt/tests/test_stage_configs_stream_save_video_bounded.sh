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
# Bug 6558740. stream-mode SAVE_VIDEO must not record unbounded by default
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

copy_stage_root() {
  local root="$1"
  local source_root

  if [ -n "${ADD_STREAMS:-}" ]; then
    source_root="$(cd "$(dirname "$ADD_STREAMS")/.." && pwd)" || return 1
  else
    source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || return 1
  fi
  mkdir -p "$root/docker" || return 1
  cp -a "$source_root/scripts" "$root/scripts" || return 1
  cp -a "$source_root/configs" "$root/configs" || return 1
  [ ! -d "$source_root/utils" ] || cp -a "$source_root/utils" "$root/utils" || return 1
  printf 'NUM_CAMS=4\nDS_HTTP_PORT=9000\nKAFKA_BOOTSTRAP=localhost:9092\n' > "$root/docker/.env"
}

ini_value() {
  local file="$1"
  local section="$2"
  local key="$3"

  awk -F= -v sec="[$section]" -v key="$key" '
    /^\[/ { in_sec = ($0 == sec) }
    in_sec && $1 == key { print $2; exit }
  ' "$file"
}

t_stream_save_video_requires_bounded_recording() {
  local out status tmp main sink_enable sink_output

  tmp="$(mktemp -d)" || return 1
  copy_stage_root "$tmp" || {
    rm -rf "$tmp"
    return 1
  }

  # This test is about unbounded live recording, not GPUs. Pin the reported GPU
  # to an NVENC-capable one so it does not depend on the host: on an NVENC-less
  # machine SAVE_VIDEO=1 legitimately triggers the software-encoder image prep,
  # which pulls and commits tens of GB and is not what is under test here.
  mkdir -p "$tmp/bin"
  cat > "$tmp/bin/nvidia-smi" <<'STUB'
#!/usr/bin/env bash
case "$*" in *query-gpu=name*) echo "NVIDIA RTX 4090" ;; *) exit 0 ;; esac
STUB
  chmod +x "$tmp/bin/nvidia-smi"

  out="$(cd "$tmp" && PATH="$tmp/bin:$PATH" INPUT_MODE=stream OSD=1 SAVE_VIDEO=1 bash scripts/stage-configs.sh 2>&1)"
  status=$?

  if [ "$status" -ne 0 ]; then
    printf '%s\n' "$out" | grep -Eiq 'unbounded|bounded|segment|retention|ALLOW_UNBOUNDED_RECORDING|SAVE_VIDEO_MAX' || {
      printf '%s\n' "$out"
      rm -rf "$tmp"
      return 1
    }
    rm -rf "$tmp"
    return 0
  fi

  main="$tmp/generated/configs/ds-main-config-mv3dt.txt"
  [ -f "$main" ] || {
    printf '%s\n' "$out"
    echo "missing staged DeepStream main config: $main"
    rm -rf "$tmp"
    return 1
  }

  sink_enable="$(ini_value "$main" sink2 enable)"
  sink_output="$(ini_value "$main" sink2 output-file)"
  rm -rf "$tmp"

  if [ "$sink_enable" = "1" ] && [ "$sink_output" = "/video-output/grid-view.mkv" ]; then
    printf '%s\n' "$out"
    echo "stream SAVE_VIDEO stages enabled sink2 with unbounded fixed output-file=$sink_output"
    return 1
  fi
}

run_test "stage-configs avoids unbounded stream SAVE_VIDEO output by default" t_stream_save_video_requires_bounded_recording
tests_summary "save-video-bounded"
