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
# generate-configs.sh — generate the per-dataset MV3DT configs from a
# calibration.json, using this repo's tools/rtvi-cv-mv3dt-utils generators
# (referenced read-only; deps go into the local utils/venv).
#
# Outputs (all under generated/, consumed by stage-configs.sh / the compose mounts):
#   generated/camInfo/<sensor>.yml     per-camera projection matrices + model priors
#   generated/pub_sub_info_config.yml  sparse MQTT pub/sub neighbour graph
#
# (The tracker config is handled by stage-configs.sh, which rewrites its
# cameraModelFilepath map to the camInfo generated here.)
#
# Usage:
#   ./scripts/generate-configs.sh /path/to/calibration.json
#
# Env overrides:
#   NEIGHBOR_CRITERIA  pub/sub neighbour graph (default: overlap_threshold:1e-6).
#                      Forms: top_N:<K>  |  overlap_threshold:<T>. A sparse
#                      vision-neighbour graph beats the dense all-to-all fallback
#                      once camera counts grow.
#   MQTT_BROKERS       broker host:port for the /trck topics (default:
#                      ${MQTT_HOST:-localhost}:${MQTT_PORT:-1883}; ds-start also
#                      rewrites host:port at container start).
#   CLASS_SPECS        object-model priors, comma-separated "classID height radius"
#                      triples in metres (default: warehouse RT-DETR classes 0-5).
set -euo pipefail

CALIB="${1:-}"
[ -n "$CALIB" ] && [ -f "$CALIB" ] || { echo "Usage: $0 /path/to/calibration.json" >&2; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/../../../.." && pwd)"
TOOLS="$REPO_ROOT/tools/rtvi-cv-mv3dt-utils"
[ -f "$TOOLS/generate_cam_info_configs.py" ] || { echo "ERROR: generators not found at $TOOLS" >&2; exit 1; }

NEIGHBOR_CRITERIA="${NEIGHBOR_CRITERIA:-overlap_threshold:1e-6}"
MQTT_BROKERS="${MQTT_BROKERS:-${MQTT_HOST:-localhost}:${MQTT_PORT:-1883}}"
# Warehouse RT-DETR classes (person, humanoid x2, cart, box, forklift) — height/radius in m.
CLASS_SPECS="${CLASS_SPECS:-0 1.60 0.3,1 1.60 0.3,2 1.60 0.3,3 0.48 0.3,4 0.2 0.52,5 2.2 0.9}"

CAMINFO="$ROOT/generated/camInfo"
GEN="$ROOT/generated"
mkdir -p "$CAMINFO" "$GEN"

# venv with the generators' deps (numpy/PyYAML/tqdm, from utils/requirements.txt)
# shellcheck disable=SC1091
source "$ROOT/scripts/ensure-venv.sh"
ensure_venv || { echo "ERROR: could not set up utils/venv" >&2; exit 1; }

# ── 1. Per-camera camInfo ────────────────────────────────────────────────────
echo "── Generating camInfo → $CAMINFO"
CLASS_ARGS=()
IFS=',' read -ra TRIPLES <<< "$CLASS_SPECS"
for t in "${TRIPLES[@]}"; do
  read -ra F <<< "$t"
  CLASS_ARGS+=(--class "${F[0]}" "${F[1]}" "${F[2]}")
done
rm -f "$CAMINFO"/*.yml
"$VENV_PY" "$TOOLS/generate_cam_info_configs.py" \
  --calibration-json "$CALIB" \
  --output-dir "$CAMINFO" \
  "${CLASS_ARGS[@]}"

# ── 2. Sparse MQTT pub/sub neighbour graph ───────────────────────────────────
echo "── Generating pub_sub_info_config.yml → $GEN  (neighbor_criteria=$NEIGHBOR_CRITERIA)"
"$VENV_PY" "$TOOLS/generate_pub_sub_configs.py" \
  --cam_info_path "$CAMINFO" \
  --mqtt_brokers "$MQTT_BROKERS" \
  --neighbor_criteria "$NEIGHBOR_CRITERIA" \
  --output_path "$GEN"

# The container's runtime user must be able to read the bind-mounted camInfo
# regardless of the host umask (e.g. 027 leaves it group-only).
chmod -R o+rX "$ROOT/generated"

echo
echo "DONE. Generated:"
echo "  $CAMINFO/  ($(ls -1 "$CAMINFO"/*.yml 2>/dev/null | wc -l | tr -d ' ') files)"
echo "  $GEN/pub_sub_info_config.yml"
echo
echo "Next: set NUM_CAMS=$(ls -1 "$CAMINFO"/*.yml 2>/dev/null | wc -l | tr -d ' ') in docker/.env, then ./scripts/stage-configs.sh"
