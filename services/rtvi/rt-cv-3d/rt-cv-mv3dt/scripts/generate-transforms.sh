#!/usr/bin/env bash
#
# generate-transforms.sh — generate the BEV visualizer's transforms.yml
# (the 3x3 world→map-pixel matrix T_ov2px) from a VSS calibration.json plus
# the BEV map image, using the map scale/translation measured during
# calibration (scaleFactor + translationToGlobalCoordinates).
#
# Usage:
#   ./scripts/generate-transforms.sh /path/to/calibration.json /path/to/map.png [-o OUT] [--force]
#
# Default output: transforms.yml next to map.png — after which that directory
# is ready to be used as BEV_DATASET_PATH for scripts/bev-visualizer.sh.
#
# Note: correct only when map.png is the same image used during calibration;
# the script projects the calibration's own ground reference points
# and warns if they do not land on the map.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/utils/generate_transforms.py" "$@"
