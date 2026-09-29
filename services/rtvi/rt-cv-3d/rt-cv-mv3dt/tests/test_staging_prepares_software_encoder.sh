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
# Run:  STAGE_CONFIGS=<repo>/scripts/stage-configs.sh bash "$0"
#
# Bug 6646812. SAVE_VIDEO=1 on an NVENC-less GPU staged enc-type=1 and the
# pipeline then failed for lack of a software encoder, because no shipping image
# carries x264enc. Staging must now settle that before writing anything.
#
# nvidia-smi and prepare-sw-encoder.sh are both stubbed, so this is host
# independent: it never needs a real H100 and never runs docker.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"
: "${STAGE_CONFIGS:=$MV3DT/scripts/stage-configs.sh}"
: "${STAGE_CONFIGS}"
pass=0; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1 (want $3, got $2)"; fail=$((fail+1)); fi; }

setup() {  # $1=tmp  $2=gpu name  $3=prep exit code  $4=probe rc (default 10 = no HW encoder)
  local t="$1" gpu="$2" prep_rc="$3" probe_rc="${4:-10}" c
  rm -rf "$t"; mkdir -p "$t"
  cp -r "$(dirname "$STAGE_CONFIGS")/.." "$t/src" 2>/dev/null
  cp -r "$t/src/." "$t/" 2>/dev/null; rm -rf "$t/src" "$t/generated"
  mkdir -p "$t/generated/camInfo" "$t/bin"
  for c in Camera Camera_01 Camera_02 Camera_03; do
    printf 'name: %s\n' "$c" > "$t/generated/camInfo/$c.yml"
  done
  python3 - "$t" <<'PY'
import json, sys
t = sys.argv[1]
cams = ["Camera", "Camera_01", "Camera_02", "Camera_03"]
json.dump({"sensors": [{"id": c} for c in cams]}, open(t + "/generated/calibration.json", "w"))
with open(t + "/generated/pub_sub_info_config.yml", "w") as f:
    f.write("pubBrokerTopicStr:\n")
    for c in cams:
        f.write("  %s: localhost:1883;/trck/%s\n" % (c, c))
PY
  # nvidia-smi stub: any --query-gpu=name asks report $gpu
  cat > "$t/bin/nvidia-smi" <<EOF
#!/usr/bin/env bash
case "\$*" in *query-gpu=name*) echo "$gpu" ;; *) exit 0 ;; esac
EOF
  chmod +x "$t/bin/nvidia-smi"
  # ffmpeg stub for the third tier: $5 chooses what an nvenc encode does
  #   hw   -> encodes (hardware present)   none -> "unsupported device" (absent)
  #   gone -> no ffmpeg at all (tier cannot answer)
  if [ "${5:-gone}" != gone ]; then
    cat > "$t/bin/ffmpeg" <<EOF
#!/usr/bin/env bash
case "\$*" in
  *-encoders*) echo " V....D h264_nvenc  NVIDIA NVENC H.264 encoder"; exit 0 ;;
esac
$([ "${5:-}" = hw ] && echo "exit 0" || echo 'echo "[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: unsupported device (2)" >&2; exit 1')
EOF
    chmod +x "$t/bin/ffmpeg"
  fi
  # prepare-sw-encoder stub. --probe-hw answers the capability question (10 =
  # no hardware encoder, matching a real NVENC-less part). A plain call is the
  # prepare step and exits with the code the case wants.
  cat > "$t/scripts/prepare-sw-encoder.sh" <<EOF
#!/usr/bin/env bash
if [ "\${1:-}" = "--probe-hw" ]; then exit ${probe_rc}; fi
echo ran >> "$t/prep-invocations"
exit $prep_rc
EOF
  chmod +x "$t/scripts/prepare-sw-encoder.sh"
}

invocations() { [ -f "$1/prep-invocations" ] && wc -l < "$1/prep-invocations" | tr -d ' ' || echo 0; }

B=$(mktemp -d)

# 1. NVENC-less GPU + SAVE_VIDEO=1 -> prep runs, staging succeeds
T="$B/t1"; setup "$T" "NVIDIA H100 80GB HBM3" 0
( cd "$T" && PATH="$T/bin:$PATH" TEGRA_ENCODER_NODE="$T/no-such-node" OSD=0 INPUT_MODE=file NUM_CAMS=4 SAVE_VIDEO=1 VIDEO_DIR=/v \
    ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "H100 + SAVE_VIDEO=1 stages" "$?" 0
chk "H100 + SAVE_VIDEO=1 runs the prep" "$(invocations "$T")" 1
grep -qE '^enc-type=1' "$T/generated/configs/ds-main-config-mv3dt.txt" 2>/dev/null
chk "software encoder selected in the staged config" "$?" 0

# 2. prep failure is fatal, and nothing is staged
T="$B/t2"; setup "$T" "NVIDIA H100 80GB HBM3" 1
( cd "$T" && PATH="$T/bin:$PATH" TEGRA_ENCODER_NODE="$T/no-such-node" OSD=0 INPUT_MODE=file NUM_CAMS=4 SAVE_VIDEO=1 VIDEO_DIR=/v \
    ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "prep failure refuses staging" "$?" 1
[ -f "$T/generated/configs/ds-main-config-mv3dt.txt" ]
chk "nothing was staged on refusal" "$?" 1

out=$( cd "$T" && PATH="$T/bin:$PATH" TEGRA_ENCODER_NODE="$T/no-such-node" OSD=0 INPUT_MODE=file NUM_CAMS=4 SAVE_VIDEO=1 VIDEO_DIR=/v \
    ./scripts/stage-configs.sh 2>&1 )
case "$out" in *"needs the software encoder"*) chk "the refusal names the cause" ok ok ;;
               *) chk "the refusal names the cause" "missing" ok ;; esac

# 2b. a GB200 node reports its GPUs as B200: that must still trigger
T="$B/t2b"; setup "$T" "NVIDIA B200" 0
( cd "$T" && PATH="$T/bin:$PATH" TEGRA_ENCODER_NODE="$T/no-such-node" OSD=0 INPUT_MODE=file NUM_CAMS=4 SAVE_VIDEO=1 VIDEO_DIR=/v \
    ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "B200 (GB200 node) stages" "$?" 0
chk "B200 runs the prep" "$(invocations "$T")" 1

# 2c. probe inconclusive, host ffmpeg says the GPU cannot encode -> prepare
T="$B/t2c"; setup "$T" "NVIDIA Unknown Future SKU" 0 2 none
( cd "$T" && PATH="$T/bin:$PATH" TEGRA_ENCODER_NODE="$T/no-such-node" OSD=0 INPUT_MODE=file NUM_CAMS=4 SAVE_VIDEO=1 VIDEO_DIR=/v \
    ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "unknown SKU, ffmpeg says no encoder -> stages" "$?" 0
chk "unknown SKU, ffmpeg says no encoder -> prepares" "$(invocations "$T")" 1

# 2d. probe inconclusive, host ffmpeg encodes fine -> hardware, no prep
T="$B/t2d"; setup "$T" "NVIDIA Unknown Future SKU" 1 2 hw
( cd "$T" && PATH="$T/bin:$PATH" TEGRA_ENCODER_NODE="$T/no-such-node" OSD=0 INPUT_MODE=file NUM_CAMS=4 SAVE_VIDEO=1 VIDEO_DIR=/v \
    ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "unknown SKU, ffmpeg encodes -> stages" "$?" 0
chk "unknown SKU, ffmpeg encodes -> skips the prep" "$(invocations "$T")" 0

# 2e. nothing can answer -> refuse rather than guess
T="$B/t2e"; setup "$T" "NVIDIA Unknown Future SKU" 0 2 gone
( cd "$T" && PATH="$T/bin:$PATH" TEGRA_ENCODER_NODE="$T/no-such-node" OSD=0 INPUT_MODE=file NUM_CAMS=4 SAVE_VIDEO=1 VIDEO_DIR=/v \
    ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "no tier can answer -> staging refuses" "$?" 1
[ -f "$T/generated/configs/ds-main-config-mv3dt.txt" ]
chk "no tier can answer -> nothing staged" "$?" 1

# 2f. Tegra: the encoder device node is present -> hardware, whatever the name
T="$B/t2f"; setup "$T" "Orin (nvgpu)" 1 2 gone
touch "$T/fake-tegra-node"
( cd "$T" && PATH="$T/bin:$PATH" TEGRA_ENCODER_NODE="$T/fake-tegra-node" OSD=0 INPUT_MODE=file \
    NUM_CAMS=4 SAVE_VIDEO=1 VIDEO_DIR=/v ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "tegra encoder node -> stages" "$?" 0
chk "tegra encoder node -> skips the prep" "$(invocations "$T")" 0

# 3. NVENC-capable GPU -> prep must not run
T="$B/t3"; setup "$T" "NVIDIA RTX PRO 6000 Blackwell Server Edition" 1 0 hw
( cd "$T" && PATH="$T/bin:$PATH" TEGRA_ENCODER_NODE="$T/no-such-node" OSD=0 INPUT_MODE=file NUM_CAMS=4 SAVE_VIDEO=1 VIDEO_DIR=/v \
    ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "NVENC-capable GPU stages" "$?" 0
chk "NVENC-capable GPU skips the prep" "$(invocations "$T")" 0

# 4. SAVE_VIDEO=0 on an NVENC-less GPU -> prep must not run
T="$B/t4"; setup "$T" "NVIDIA H100 80GB HBM3" 1
( cd "$T" && PATH="$T/bin:$PATH" TEGRA_ENCODER_NODE="$T/no-such-node" OSD=0 INPUT_MODE=stream NUM_CAMS=4 SAVE_VIDEO=0 \
    ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "SAVE_VIDEO=0 stages" "$?" 0
chk "SAVE_VIDEO=0 skips the prep" "$(invocations "$T")" 0

rm -rf "$B"
echo "  --- $pass passed, $fail failed"
[ "$fail" -eq 0 ]
