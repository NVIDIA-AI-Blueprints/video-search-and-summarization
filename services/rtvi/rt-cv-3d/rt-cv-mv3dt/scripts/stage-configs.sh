#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
# stage-configs.sh — assemble the per-run DeepStream config dir the perception
# container mounts (generated/configs). Run this before `docker compose up`.
#
# The staged dir is configs/* (the sample set, including the single
# ds-main-config-mv3dt.txt) overlaid with the generated sparse
# pub_sub_info_config.yml when scripts/generate-configs.sh has been run.
# The tracker config (TRACKER_CONFIG, default the bundled sample) is staged
# with its ObjectModelProjection.cameraModelFilepath map rewritten to the
# cameras in generated/camInfo. The staged main config is then edited in place:
#   - batch-size / max-batch-size = NUM_CAMS
#   - tiled-display grid sized to NUM_CAMS
#   - REST http-port = DS_HTTP_PORT
#   - kafka sink conn-str = KAFKA_BOOTSTRAP host;port;RAW_TOPIC
#   - [sink0] on-screen display: enabled with OSD=1 (needs an X display), else off
#   - RT-DETR model-engine-file batch suffix = NUM_CAMS
#   - INPUT_MODE=file: static [source-list] of file:///videos/<cam>.mp4 + SEI/sync
#     off (plays local clips once; no add-streams.sh registration)
#   - SAVE_VIDEO=1: enable the [sink2] tiled grid file sink -> video-output/grid-view.mkv
#     (the whole annotated camera grid; see README). File input is finite; stream input
#     requires ALLOW_UNBOUNDED_RECORDING=1 because the DeepStream file sink does not
#     rotate or expire live recordings.
#
# Usage:  [OSD=0|1] [INPUT_MODE=stream|file] [SAVE_VIDEO=0|1]
#         [ALLOW_UNBOUNDED_RECORDING=0|1]
#         [TRACKER_CONFIG=/path/to/tracker.yml] ./scripts/stage-configs.sh
# Reads NUM_CAMS / DS_HTTP_PORT / KAFKA_BOOTSTRAP / RAW_TOPIC / INPUT_MODE /
# VIDEO_DIR / SAVE_VIDEO / ALLOW_UNBOUNDED_RECORDING from docker/.env
# (already-exported env values win).
#   TRACKER_CONFIG  base tracker config to stage (default:
#                   configs/ds-mv3dt-tracker-config.yml). Point this at your
#                   own tracker config to use it instead of the sample.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# docker/.env provides defaults; anything already in the environment wins.
if [ -f "$ROOT/docker/.env" ]; then
  while IFS='=' read -r k v; do
    [[ "$k" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    [ -n "${!k:-}" ] && continue          # already-exported value wins
    v="${v%$'\r'}"                         # tolerate CRLF line endings
    # Strip an unquoted trailing comment: "NUM_CAMS=4  # four cams" is a normal
    # .env line, and without this the value carries the comment into the staged
    # config. Quoted values keep their # (paths and connection strings use it).
    if [[ $v != \"* && $v != \'* ]]; then v="${v%%[[:space:]]#*}"; fi
    v="${v%"${v##*[![:space:]]}"}"           # drop trailing whitespace
    if [[ $v == \"*\" ]]; then v="${v#\"}"; v="${v%\"}"; fi   # strip paired "…"
    if [[ $v == \'*\' ]]; then v="${v#\'}"; v="${v%\'}"; fi   # strip paired '…'
    printf -v "$k" '%s' "$v" && export "$k"
  done < <(grep -E '^[A-Z_]+=' "$ROOT/docker/.env")
fi

NUM_CAMS="${NUM_CAMS:?NUM_CAMS not set (see docker/.env)}"
DS_HTTP_PORT="${DS_HTTP_PORT:-9000}"
KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:9092}"
RAW_TOPIC="${RAW_TOPIC:-mdx-raw}"
OSD="${OSD:-0}"
INPUT_MODE="${INPUT_MODE:-stream}"
SAVE_VIDEO="${SAVE_VIDEO:-0}"
ALLOW_UNBOUNDED_RECORDING="${ALLOW_UNBOUNDED_RECORDING:-0}"
GPU_DEVICE="${GPU_DEVICE:-0}"

if [ "$INPUT_MODE" = "stream" ] && [ "$SAVE_VIDEO" = "1" ] && [ "$ALLOW_UNBOUNDED_RECORDING" != "1" ]; then
  echo "ERROR: INPUT_MODE=stream SAVE_VIDEO=1 would write an unbounded live recording to video-output/grid-view.mkv." >&2
  echo "       The current DeepStream file sink does not configure segment rotation, size limits, or retention cleanup." >&2
  echo "       Use INPUT_MODE=file for finite clips, or set ALLOW_UNBOUNDED_RECORDING=1 to explicitly accept this risk." >&2
  exit 1
fi

TRACKER_CONFIG="${TRACKER_CONFIG:-$ROOT/configs/ds-mv3dt-tracker-config.yml}"
[ -f "$TRACKER_CONFIG" ] || { echo "ERROR: tracker config not found: $TRACKER_CONFIG" >&2; exit 1; }

KAFKA_HOST="${KAFKA_BOOTSTRAP%%:*}"
KAFKA_PORT_ONLY="${KAFKA_BOOTSTRAP##*:}"
NVENC_LESS_GPU_NAME=""

# /dev/v4l2-nvenc is a Tegra signal, not a dGPU signal. For dGPU hosts, only
# switch when the selected GPU is a known encoder-less compute SKU.
is_nvenc_less_gpu_name() {
  local name="$1"
  [[ "$name" =~ (^|[^[:alnum:]])(A100|H100|H200|GB200|GB300)([^[:alnum:]]|$) ]]
}

detect_nvenc_less_gpu() {
  local name

  command -v nvidia-smi >/dev/null 2>&1 || return 1

  while IFS= read -r name; do
    [ -n "$name" ] || continue
    if is_nvenc_less_gpu_name "$name"; then
      NVENC_LESS_GPU_NAME="$name"
      return 0
    fi
  done < <(nvidia-smi --id="$GPU_DEVICE" --query-gpu=name --format=csv,noheader 2>/dev/null || true)

  return 1
}

STAGE="$ROOT/generated/configs"
GEN="$ROOT/generated"

# Camera names (sorted, C locale to match the tracker rewrite's Python sorted()).
# Used to build the file-mode source-list (uri/id per camera, in list order).
CAMS=()
if ls "$GEN/camInfo"/*.yml >/dev/null 2>&1; then
  mapfile -t CAMS < <(cd "$GEN/camInfo" && LC_ALL=C ls -1 *.yml | sed 's/\.yml$//')
fi

# NUM_CAMS drives batch-size, the grid and the engine suffix; camInfo drives the
# tracker map and the file-mode source list. When they disagree nothing notices
# until sources fail to activate, so compare every source we have.
check_camera_consistency() {
  local problems=() calib_ids pubsub_ids
  (( ${#CAMS[@]} )) || return 0          # no camInfo: handled separately per mode

  [ "${#CAMS[@]}" = "$NUM_CAMS" ] || problems+=(
    "NUM_CAMS=$NUM_CAMS but generated/camInfo has ${#CAMS[@]} camera(s): $(printf '%s ' "${CAMS[@]}")")

  if [ -f "$GEN/calibration.json" ]; then
    calib_ids="$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
s = d.get("sensors", d)
ids = [x.get("id") or x.get("sensorId") for x in s] if isinstance(s, list) else list(s)
print(" ".join(sorted(str(i) for i in ids if i)))
' "$GEN/calibration.json" 2>/dev/null)"
    if [ -n "$calib_ids" ] && [ "$calib_ids" != "$(printf '%s\n' "${CAMS[@]}" | sort | tr '\n' ' ' | sed 's/ $//')" ]; then
      problems+=("calibration.json sensors [$calib_ids] do not match camInfo [$(printf '%s ' "${CAMS[@]}" | sed 's/ $//')]")
    fi
  fi

  if [ -f "$GEN/pub_sub_info_config.yml" ]; then
    pubsub_ids="$(awk '/^pubBrokerTopicStr:/ { p = 1; next }
                       /^[^[:space:]]/       { p = 0 }
                       p && /^[[:space:]]+[A-Za-z0-9_.-]+:/ {
                         sub(/^[[:space:]]+/, ""); sub(/:.*$/, ""); print }' \
                  "$GEN/pub_sub_info_config.yml" | sort | tr '\n' ' ' | sed 's/ $//')"
    if [ -n "$pubsub_ids" ] && [ "$pubsub_ids" != "$(printf '%s\n' "${CAMS[@]}" | sort | tr '\n' ' ' | sed 's/ $//')" ]; then
      problems+=("pub_sub_info_config.yml sensors [$pubsub_ids] do not match camInfo")
    fi
  fi

  (( ${#problems[@]} )) || return 0
  { echo "ERROR: camera configuration is inconsistent, nothing was staged."
    printf '       %s\n' "${problems[@]}"
    echo "       Re-run scripts/generate-configs.sh <calibration.json> for this camera set,"
    echo "       and set NUM_CAMS in docker/.env to match it."; } >&2
  exit 1
}
check_camera_consistency

# file-mode source URIs / sensor ids (file:///videos/<cam>.mp4), built from CAMS.
URIS=""; IDS=""
if [ "$INPUT_MODE" = "file" ]; then
  if [ ${#CAMS[@]} -eq 0 ]; then
    echo "ERROR: INPUT_MODE=file needs camInfo — run scripts/generate-configs.sh first" >&2; exit 1
  fi
  [ -n "${VIDEO_DIR:-}" ] || echo "   ⚠ INPUT_MODE=file but VIDEO_DIR is unset — set it in docker/.env to your <sensor_id>.mp4 dir" >&2
  for cam in "${CAMS[@]}"; do URIS+="file:///videos/${cam}.mp4;"; IDS+="${cam};"; done
fi

# Adaptive tiled-display grid: grow the shorter side until every source has a slot.
# Produces the squarest landscape grid that fits (2->1x2, 6->2x3, 8->3x3, 12->3x4).
ROWS=1; COLS=1; SLOTS=1; TILE_WIDTH=1920; TILE_HEIGHT=1080
while [ "$SLOTS" -lt "$NUM_CAMS" ]; do
  if [ "$COLS" -gt "$ROWS" ]; then ROWS=$((ROWS + 1)); else COLS=$((COLS + 1)); fi
  SLOTS=$((ROWS * COLS))
done
# 2-cam is special-cased to 720p to avoid excessive letterboxing.
if [ "$NUM_CAMS" = 2 ]; then TILE_HEIGHT=720; fi

echo "── Staging configs → $STAGE  (NUM_CAMS=$NUM_CAMS grid=${ROWS}x${COLS} port=$DS_HTTP_PORT kafka=$KAFKA_HOST:$KAFKA_PORT_ONLY OSD=$OSD INPUT_MODE=$INPUT_MODE SAVE_VIDEO=$SAVE_VIDEO)"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp "$ROOT"/configs/* "$STAGE/"
rm -f "$STAGE/mosquitto.conf"   # broker conf, not a DeepStream config

# Overlay generated files (scripts/generate-configs.sh outputs), when present.
if [ -d "$GEN" ]; then
  for f in pub_sub_info_config.yml; do
    [ -f "$GEN/$f" ] && { cp "$GEN/$f" "$STAGE/$f"; echo "   using generated $f"; }
  done
fi

# camInfo must exist for the tracker + the ds-start pub/sub fallback.
if [ ! -d "$ROOT/generated/camInfo" ] || ! ls "$ROOT/generated/camInfo"/*.yml >/dev/null 2>&1; then
  echo "   ⚠ no camInfo at generated/camInfo — run scripts/generate-configs.sh first" >&2
else
  # Stage the tracker config (TRACKER_CONFIG or the bundled sample) with its
  # ObjectModelProjection.cameraModelFilepath map rewritten to the generated cameras
  # (the sample hardcodes the 4-cam warehouse names).
  echo "   staging tracker config: $TRACKER_CONFIG (${#CAMS[@]} cameras)"
  entries=$(for cam in "${CAMS[@]}"; do printf '    %s: /tmp/camInfo/%s.yml\n' "$cam" "$cam"; done)
  awk -v entries="$entries" '
    /^[[:space:]]+cameraModelFilepath:[[:space:]]*$/ { print; print entries; skip=1; next }
    skip==1 && /^[[:space:]][[:space:]][[:space:]][[:space:]][^[:space:]]/ { next }  # drop old entries
    { skip=0; print }
  ' "$TRACKER_CONFIG" > "$STAGE/ds-mv3dt-tracker-config.yml"
fi

# RT-DETR engine file name follows the batch size (TensorRT builds it on first
# run if missing — a cold build takes minutes).
ONNX=$(sed -nE 's/^[[:space:]]*onnx-file:[[:space:]]*([^[:space:]]+).*/\1/p' "$STAGE/ds-pgie-config.yml" | head -1)
[ -n "$ONNX" ] && sed -i -E "s#(model-engine-file:[[:space:]]*).*#\1${ONNX}_b${NUM_CAMS}_gpu0_fp16.engine#" "$STAGE/ds-pgie-config.yml"

# Edit the staged main config in place, one key at a time.
MAIN="$STAGE/ds-main-config-mv3dt.txt"

# set_ini SECTION KEY VALUE — rewrite an existing "KEY=..." line inside [SECTION] of $MAIN,
# in place, leaving line order, comments and every other section untouched.
set_ini() {
  awk -v sec="[$1]" -v key="$2" -v val="$3" '
    /^\[/ { in_sec = ($0 == sec) }
    in_sec && index($0, key "=") == 1 { print key "=" val; next }
    { print }
  ' "$MAIN" > "$MAIN.tmp" && mv "$MAIN.tmp" "$MAIN"
}

set_ini tiled-display rows "$ROWS"
set_ini tiled-display columns "$COLS"
set_ini tiled-display width "$TILE_WIDTH"
set_ini tiled-display height "$TILE_HEIGHT"
set_ini tiled-display enable 1                  # tiler on: on-screen grid + SAVE_VIDEO grid sink
# OSD needs the GPU driving the display to be visible to the container. gpu-id is
# a CUDA ordinal within that visible set, and the runtime orders it by PCI
# address, so it is not the nvidia-smi index. Resolve both here, where every GPU
# is visible. Nothing is changed when the display GPU cannot be determined, to
# avoid moving the workload to a GPU nobody chose.
resolve_gpu_selection() {   # echoes "<visible_csv> <gpu_id>", or nothing
  GPU_DEVICE="$GPU_DEVICE" OSD="$OSD" python3 - <<'PY'
import os, re, subprocess, sys

def smi(q):
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=" + q, "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10)
        return [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return []

rows = [l.split(", ") for l in smi("index,pci.bus_id")]
if not rows:
    sys.exit(0)
bus = {r[0]: r[1].upper() for r in rows if len(r) == 2}

want = (os.environ.get("GPU_DEVICE") or "0").strip()
compute = [d.strip() for d in want.split(",") if d.strip()]
if want == "all":
    compute = sorted(bus, key=lambda i: bus[i])
# UUIDs cannot be ordered here.
if any(not d.isdigit() for d in compute):
    sys.exit(0)

visible = list(compute)

# xorg.conf states the bus in decimal, nvidia-smi in hex.
if os.environ.get("OSD") == "1":
    disp = None
    try:
        conf = open("/etc/X11/xorg.conf").read()
        m = re.search(r'BusID\s+"PCI:(\d+)', conf)
        if m:
            tag = ":%02X:00" % int(m.group(1))
            disp = next((i for i, b in bus.items() if tag in b), None)
    except Exception:
        disp = None
    if disp is None:
        # Only worth reporting when there is a choice to get wrong.
        if len(bus) > 1:
            print("UNKNOWN")
        sys.exit(0)
    if disp not in visible:
        visible.append(disp)

visible = sorted(set(visible), key=lambda i: bus.get(i, ""))
try:
    gpu_id = visible.index(compute[0])    # CUDA ordinal, not nvidia-smi index
except ValueError:
    sys.exit(0)
print("%s %s" % (",".join(visible), gpu_id))
PY
}

read -r RESOLVED_VISIBLE RESOLVED_GPU_ID <<<"$(resolve_gpu_selection)"
if [ "${RESOLVED_VISIBLE:-}" = UNKNOWN ]; then
  echo "   ⚠ cannot tell which GPU drives the display (no BusID in /etc/X11/xorg.conf)." >&2
  echo "     GPU_DEVICE=$GPU_DEVICE is unchanged. If the OSD fails to start, add the GPU" >&2
  echo "     that drives the display to GPU_DEVICE and restage." >&2
elif [ -n "${RESOLVED_GPU_ID:-}" ]; then
  if [ "$RESOLVED_VISIBLE" != "$GPU_DEVICE" ]; then
    ENV_FILE="$ROOT/docker/.env"
    if [ -f "$ENV_FILE" ] && grep -qE '^[[:space:]]*GPU_DEVICE=' "$ENV_FILE"; then
      sed -i -E "s|^[[:space:]]*GPU_DEVICE=.*|GPU_DEVICE=$RESOLVED_VISIBLE|" "$ENV_FILE"
      echo "   GPU_DEVICE: $GPU_DEVICE -> $RESOLVED_VISIBLE (added the GPU driving the display)"
    else
      echo "   ⚠ OSD needs GPU_DEVICE=$RESOLVED_VISIBLE; docker/.env has no GPU_DEVICE line" >&2
    fi
    GPU_DEVICE="$RESOLVED_VISIBLE"
  fi
  sed -i -E "s|^gpu-id=.*|gpu-id=$RESOLVED_GPU_ID|" "$STAGE/ds-main-config-mv3dt.txt"
  sed -i -E "s|^([[:space:]]*)gpu-id:.*|\1gpu-id: $RESOLVED_GPU_ID|" "$STAGE/ds-pgie-config.yml" 2>/dev/null || true
  echo "   gpu-id=$RESOLVED_GPU_ID within GPU_DEVICE=$GPU_DEVICE"
fi

set_ini source-list  max-batch-size "$NUM_CAMS"
set_ini source-list  http-port "$DS_HTTP_PORT"
set_ini streammux    batch-size "$NUM_CAMS"
set_ini primary-gie  batch-size "$NUM_CAMS"
set_ini sink1        msg-broker-conn-str "$KAFKA_HOST;$KAFKA_PORT_ONLY;$RAW_TOPIC"
set_ini sink0 enable "$([ "$OSD" = 1 ] && echo 1 || echo 0)"          # on-screen OSD (needs a display)
set_ini sink1 enable 1                                                # Kafka metadata sink
set_ini sink2 enable "$([ "$SAVE_VIDEO" = 1 ] && echo 1 || echo 0)"   # grid file sink (SAVE_VIDEO)
if [ "$SAVE_VIDEO" = "1" ] && detect_nvenc_less_gpu; then
  set_ini sink2 enc-type 1
  echo "   SAVE_VIDEO=1 on ${NVENC_LESS_GPU_NAME} -> using software encoder for sink2 (enc-type=1; no NVENC hardware encoder available)"
fi

# File input: static file:// [source-list], SEI extraction off, clips play once,
# live-source latency dropping disabled, and the system clock stamped as NTP so
# the Kafka/BEV output has per-frame timestamps.
if [ "$INPUT_MODE" = "file" ]; then
  set_ini source-list num-source-bins "$NUM_CAMS"
  set_ini source-list list "$URIS"
  set_ini source-list sensor-id-list "$IDS"
  set_ini source-list sensor-name-list "$IDS"
  set_ini source-list low-latency-mode 0
  set_ini source-list extract-sei-type5-data 0
  set_ini source-attr-all drop-on-latency 0
  set_ini source-attr-all latency 100000
  set_ini streammux   extract-sei-sim-time 0
  set_ini streammux   attach-sys-ts-as-ntp 1
  set_ini streammux   live-source 0
  set_ini streammux   drop-backward-sei 0
  set_ini streammux   sync-inputs-ntp 0
  set_ini streammux   align-first-buffer 0
  set_ini streammux   drop-pipeline-eos 0
fi

# SAVE_VIDEO: the grid file sink ([sink2], enabled above) writes to /video-output;
# make sure the host dir exists and is writable by the container (possibly
# non-root). The sink template itself (container/codec/enc-type/output-file) is
# baked into configs/ds-main-config-mv3dt.txt.
if [ "$SAVE_VIDEO" = "1" ]; then
  mkdir -p "$ROOT/video-output"
  chmod 777 "$ROOT/video-output"   # the container (possibly non-root) writes here
  if [ "$INPUT_MODE" = "stream" ]; then
    echo "   SAVE_VIDEO=1 with ALLOW_UNBOUNDED_RECORDING=1 -> unbounded live grid view into video-output/grid-view.mkv"
  else
    echo "   SAVE_VIDEO=1 -> finite grid view into video-output/grid-view.mkv"
  fi
fi

# The container's runtime user must be able to read the bind-mounted configs
# regardless of the host umask (e.g. 027 leaves them group-only).
chmod -R o+rX "$ROOT/generated"

echo "── Staged. Launch from the docker/ directory:"
echo "   cd docker && COMPOSE_PROFILES=mosquitto,kafka docker compose up -d   # bundled brokers"
echo "   cd docker && docker compose up -d                                    # own brokers"
if [ "$INPUT_MODE" = "file" ]; then
  echo "   INPUT_MODE=file: sources are static (VIDEO_DIR/*.mp4) — no add-streams.sh needed; clips play once."
fi
