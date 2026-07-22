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
# bev-visualizer.sh — launch a Kafka BEV (bird's-eye-view) visualizer: consumes
# per-object 3D world coordinates and plots a top-down trajectory map (live
# window, or offline mp4 when headless). Two sources, selected via BEV_SOURCE:
#
#   BEV_SOURCE=raw   (default) utils/kafka_bev_visualizer.py on mdx-raw — the
#                    per-camera measurements the MV3DT tracker emits (one point
#                    per camera view of an object; buffered/grouped client-side)
#   BEV_SOURCE=fused utils/kafka_fused_bev_visualizer.py on mdx-bev — the BEV
#                    Fusion output (one merged point per object; no client-side
#                    grouping needed, so no msgconv / timestamp-buffer knobs)
#
# Required dataset assets (the visualizer cannot render without them):
#   $BEV_DATASET_PATH/map.png         BEV background image
#   $BEV_DATASET_PATH/transforms.yml  contains T_ov2px (3x3 overlay→pixel matrix)
#
# Env:
#   BEV_DATASET_PATH  dir holding map.png + transforms.yml (REQUIRED)
#   BEV_SOURCE        raw (default) or fused — see above
#   BEV_KAFKA_BROKER  default localhost:${KAFKA_PORT:-9092} (or $KAFKA_BOOTSTRAP)
#   BEV_KAFKA_TOPIC   default mdx-raw (raw) / mdx-bev (fused)
#   BEV_SAVE_VIDEO    1 = save an mp4 instead of a live window (auto when no DISPLAY)
#   BEV_OUTPUT        output dir for the mp4 + generated msgconv (default ./bev-output)
#   BEV_SHOW_IDS      1 (default) = draw object IDs near trajectory heads; 0 = hide
# Raw-source only:
#   BEV_TIMESTAMP_BUFFER  1 (default) = group messages by timestamp bucket;
#                     0 = group by frame id
#   BEV_BUCKET_MS     bucket width in ms (default 17 ≈ half a 30 FPS frame)
#   BEV_BUFFER_DELAY_MS  flush watermark in ms (default 100)
#   BEV_MSGCONV       msgconv config path (default: generated from generated/camInfo)
# Extra args are passed through to the selected visualizer.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UTILS="$ROOT/utils"

SOURCE="${BEV_SOURCE:-raw}"
case "$SOURCE" in
  raw)   default_topic="${RAW_TOPIC:-mdx-raw}" ;;
  fused) default_topic="${FUSED_TOPIC:-mdx-bev}" ;;
  *) echo "ERROR: BEV_SOURCE must be 'raw' or 'fused' (got '$SOURCE')" >&2; exit 1 ;;
esac

export BEV_KAFKA_BROKER="${BEV_KAFKA_BROKER:-${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT:-9092}}}"
export BEV_KAFKA_TOPIC="${BEV_KAFKA_TOPIC:-$default_topic}"
DATASET_PATH="${BEV_DATASET_PATH:-}"
OUTPUT="${BEV_OUTPUT:-$ROOT/bev-output}"

echo "── BEV visualizer: source=$SOURCE broker=$BEV_KAFKA_BROKER topic=$BEV_KAFKA_TOPIC dataset=${DATASET_PATH:-<unset>}"

# 1. venv + Python deps (utils/requirements.txt)
# shellcheck disable=SC1091
source "$ROOT/scripts/ensure-venv.sh"
ensure_venv || { echo "ERROR: could not set up utils/venv" >&2; exit 1; }

# 2. required dataset assets
if [[ -z "$DATASET_PATH" || ! -f "$DATASET_PATH/map.png" || ! -f "$DATASET_PATH/transforms.yml" ]]; then
  echo "ERROR: BEV_DATASET_PATH must point at a directory containing:" >&2
  echo "         map.png         (BEV background image)" >&2
  echo "         transforms.yml  (with a 3x3 T_ov2px overlay→pixel matrix)" >&2
  echo "       e.g.  BEV_DATASET_PATH=~/my-dataset $0" >&2
  exit 1
fi

mkdir -p "$OUTPUT"

# 3. expected-sensors config (raw source only) — the raw visualizer requires a
#    config_msgconv.txt with [sensorN]/id= entries. Generate it from the
#    generated camInfo (the same camera set perception runs with); override with
#    BEV_MSGCONV. The fused source doesn't group by sensor, so it needs none.
MSGCONV="${BEV_MSGCONV:-}"
if [[ "$SOURCE" == "raw" && -z "$MSGCONV" ]]; then
  MSGCONV="$OUTPUT/config_msgconv.txt"
  : > "$MSGCONV"; n=0
  for f in "$ROOT/generated/camInfo"/*.yml; do
    [[ -e "$f" ]] || continue
    printf '[sensor%d]\nid=%s\n\n' "$n" "$(basename "$f" .yml)" >> "$MSGCONV"; n=$((n+1))
  done
  if (( n == 0 )); then
    echo "ERROR: no camInfo at generated/camInfo — run scripts/generate-configs.sh first" >&2
    echo "       (or point BEV_MSGCONV at a config_msgconv.txt listing your sensors)" >&2
    exit 1
  fi
  echo "   generated msgconv config ($n sensors): $MSGCONV"
fi

# 4. mode: live window if a display exists, else offline mp4
mode=()
if [[ "${BEV_SAVE_VIDEO:-}" == "1" || -z "${DISPLAY:-}" ]]; then
  mode=(--offline); echo "   (no display / BEV_SAVE_VIDEO → saving mp4 to $OUTPUT)"
fi
# live mode draws via cv2 HighGUI; fall back to offline if OpenCV is a headless build
if [[ ${#mode[@]} -eq 0 ]] && ! "$VENV_PY" -c "import cv2,re,sys; sys.exit(0 if re.search(r'(QT|GTK\+?):\s*YES', cv2.getBuildInformation()) else 1)" 2>/dev/null; then
  echo "   ⚠ OpenCV has no GUI support (headless build) — falling back to --offline mp4"
  mode=(--offline)
fi
# offline recording auto-finalizes when the stream ends (no message for N seconds, default 15);
# BEV_EXIT_ON_IDLE=0 disables it (stop with Ctrl+C). Ignored for the live window.
[[ "${mode[*]}" == *--offline* ]] && mode+=(--exit-on-idle "${BEV_EXIT_ON_IDLE:-15}")

# 5. object IDs near trajectory heads — on by default; BEV_SHOW_IDS=0 to disable
ids=()
[[ "${BEV_SHOW_IDS:-1}" != "0" ]] && ids=(--show-ids)

# 6. launch — PYTHONPATH=utils so `import schema_pb2` resolves
if [[ "$SOURCE" == "fused" ]]; then
  echo "   launching kafka_fused_bev_visualizer.py ..."
  exec env PYTHONPATH="$UTILS${PYTHONPATH:+:$PYTHONPATH}" "$VENV_PY" "$UTILS/kafka_fused_bev_visualizer.py" \
    --dataset-path "$DATASET_PATH" \
    --output-path "$OUTPUT" \
    "${ids[@]}" "${mode[@]}" "$@"
fi

# raw source: timestamp-bucket buffering (default on) groups messages by timestamp
# bucket (robust to divergent frame-id counters); BEV_TIMESTAMP_BUFFER=0 = frame-id grouping.
tsbuf=()
[[ "${BEV_TIMESTAMP_BUFFER:-1}" == "1" ]] && tsbuf=(--timestamp-buffer --bucket-ms "${BEV_BUCKET_MS:-17}" \
  --timestamp-buffer-delay-ms "${BEV_BUFFER_DELAY_MS:-100}")

echo "   launching kafka_bev_visualizer.py ..."
exec env PYTHONPATH="$UTILS${PYTHONPATH:+:$PYTHONPATH}" "$VENV_PY" "$UTILS/kafka_bev_visualizer.py" \
  --dataset-path "$DATASET_PATH" \
  --output-path "$OUTPUT" \
  --msgconv-config "$MSGCONV" \
  "${ids[@]}" "${tsbuf[@]}" "${mode[@]}" "$@"
