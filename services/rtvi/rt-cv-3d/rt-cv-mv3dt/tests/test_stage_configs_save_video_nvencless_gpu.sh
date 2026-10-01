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
# Bug 6577979. SAVE_VIDEO on an NVENC-less GPU must stage the software encoder
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
  mkdir -p "$root/docker" "$root/generated/camInfo" "$root/bin" || return 1
  cp -a "$source_root/scripts" "$root/scripts" || return 1
  cp -a "$source_root/configs" "$root/configs" || return 1
  [ ! -d "$source_root/utils" ] || cp -a "$source_root/utils" "$root/utils" || return 1
  printf 'NUM_CAMS=4\nDS_HTTP_PORT=9000\nKAFKA_BOOTSTRAP=localhost:9092\n' > "$root/docker/.env"

  touch \
    "$root/generated/camInfo/Camera_00.yml" \
    "$root/generated/camInfo/Camera_01.yml" \
    "$root/generated/camInfo/Camera_02.yml" \
    "$root/generated/camInfo/Camera_03.yml"
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

write_fake_nvidia_smi() {
  local bin_dir="$1"
  local gpu_name="$2"

  cat > "$bin_dir/nvidia-smi" <<EOF
#!/usr/bin/env bash
case " \$* " in
  *"--query-gpu=name"*|*" -L "*)
    printf '%s\n' '$gpu_name'
    ;;
  *)
    printf '%s\n' '$gpu_name'
    ;;
esac
EOF
  chmod +x "$bin_dir/nvidia-smi"
}

assert_save_video_avoids_hardware_encoder() {
  local gpu_name="$1"
  local out status tmp main sink_enable enc_type rc

  tmp="$(mktemp -d)" || return 1
  copy_stage_root "$tmp" || {
    rm -rf "$tmp"
    return 1
  }
  write_fake_nvidia_smi "$tmp/bin" "$gpu_name" || {
    rm -rf "$tmp"
    return 1
  }

  # Staging now prepares the software encoder for these GPUs, which needs the
  # perception image. Stub docker so that step fails immediately: this asserts
  # the refusal path without the harness depending on a registry, a multi-GB
  # pull, or network reachability. The success path is covered on real hardware.
  cat > "$tmp/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "docker: stubbed out for this test" >&2
exit 1
STUB
  chmod +x "$tmp/bin/docker"
  # Detection asks the hardware, so every source of an answer must be stubbed or
  # this depends on the host: ffmpeg reports no encoder, and the Tegra node is
  # pointed at a path that does not exist (a real Jetson has one).
  cat > "$tmp/bin/ffmpeg" <<'STUB'
#!/usr/bin/env bash
case "$*" in *-encoders*) echo " V....D h264_nvenc  NVIDIA NVENC H.264 encoder"; exit 0 ;; esac
echo "[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: unsupported device (2)" >&2
exit 1
STUB
  chmod +x "$tmp/bin/ffmpeg"

  out="$(cd "$tmp" && PATH="$tmp/bin:$PATH" INPUT_MODE=file OSD=1 SAVE_VIDEO=1 \
        TEGRA_ENCODER_NODE="$tmp/no-such-node" bash scripts/stage-configs.sh 2>&1)"
  status=$?

  if [ "$status" -ne 0 ]; then
    printf '%s\n' "$out" | grep -Eiq 'save_video|save video|nvenc|encoder|h100|gb300|a100|gb200|h200'
    rc=$?
    if [ "$rc" -ne 0 ]; then
      printf '%s\n' "$out"
      echo "SAVE_VIDEO=1 was refused for $gpu_name without an actionable encoder message"
    fi
    rm -rf "$tmp"
    return "$rc"
  fi

  main="$tmp/generated/configs/ds-main-config-mv3dt.txt"
  if [ ! -f "$main" ]; then
    printf '%s\n' "$out"
    echo "missing staged DeepStream main config: $main"
    rm -rf "$tmp"
    return 1
  fi

  sink_enable="$(ini_value "$main" sink2 enable)"
  enc_type="$(ini_value "$main" sink2 enc-type)"
  rm -rf "$tmp"

  if [ "$sink_enable" = "1" ] && [ "$enc_type" = "0" ]; then
    printf '%s\n' "$out"
    echo "SAVE_VIDEO=1 on $gpu_name stages sink2 with hardware NVENC enc-type=0"
    return 1
  fi

  if [ "$sink_enable" != "1" ]; then
    printf '%s\n' "$out"
    echo "SAVE_VIDEO=1 on $gpu_name completed but did not keep sink2 enabled"
    return 1
  fi
}

t_save_video_h100_requires_non_nvenc_path() {
  assert_save_video_avoids_hardware_encoder "NVIDIA H100 80GB HBM3"
}

t_save_video_gb300_requires_non_nvenc_path() {
  assert_save_video_avoids_hardware_encoder "NVIDIA GB300"
}

run_test "stage-configs avoids hardware NVENC for SAVE_VIDEO on H100" t_save_video_h100_requires_non_nvenc_path
run_test "stage-configs avoids hardware NVENC for SAVE_VIDEO on GB300" t_save_video_gb300_requires_non_nvenc_path
tests_summary "nvencless-gpu"
