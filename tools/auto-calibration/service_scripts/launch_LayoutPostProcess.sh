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

set -Eeuo pipefail

MODE="docker"
OUTPUT_BASE_DIR=""
GROUNDTRUTH_DIR=""
LAYOUT_IMAGE_PATH=""
NO_PLOT=""

usage() {
    echo "Usage: $0 -a <output_base_dir> -l <layout_image_path> [-g <groundtruth_dir>] [-m <local|docker>] [--no-plot]"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -a|--output-base-dir) OUTPUT_BASE_DIR="$2"; shift 2 ;;
        -g|--groundtruth-dir) GROUNDTRUTH_DIR="$2"; shift 2 ;;
        -l|--layout-image) LAYOUT_IMAGE_PATH="$2"; shift 2 ;;
        -m|--mode) MODE="$2"; shift 2 ;;
        --no-plot) NO_PLOT="--no-plot"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Error: Unknown argument: $1"; usage; exit 1 ;;
    esac
done

if [[ -z "$OUTPUT_BASE_DIR" || -z "$LAYOUT_IMAGE_PATH" ]]; then
    usage
    exit 1
fi
if [[ "$MODE" != "local" && "$MODE" != "docker" ]]; then
    echo "Error: mode must be 'local' or 'docker'"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTO_MAGIC_ROOT="$SCRIPT_DIR/.."
CONFIG_FILE="$AUTO_MAGIC_ROOT/configs/config_AutoMagicCalib/mv_amc_config.yaml"
EVAL_CONFIG_FILE="$AUTO_MAGIC_ROOT/configs/config_AutoMagicCalib/eval_config.yaml"
SV_OUTPUT_DIR="$OUTPUT_BASE_DIR/single_view_results"
ALIGNMENT_DATA_DIR="$OUTPUT_BASE_DIR/alignment_data"
ALIGNMENT_DATA_FILE="$ALIGNMENT_DATA_DIR/alignment_data.json"

read -r VIRTUAL_GT_PERSON_HEIGHT VIRTUAL_GT_GRID_SPACING < <(
    python3 -c "
import yaml
try:
    with open('$EVAL_CONFIG_FILE') as f:
        cfg = yaml.safe_load(f) or {}
    print(float(cfg.get('virtual_gt_person_height', 1.75)),
          float(cfg.get('virtual_gt_grid_spacing', 1.0)))
except Exception:
    print(1.75, 1.0)
" 2>/dev/null || echo "1.75 1.0"
)

for required in "$SV_OUTPUT_DIR" "$ALIGNMENT_DATA_DIR"; do
    [[ -d "$required" ]] || { echo "Error: Required directory not found: $required"; exit 1; }
done
for required in "$ALIGNMENT_DATA_FILE" "$LAYOUT_IMAGE_PATH"; do
    [[ -f "$required" ]] || { echo "Error: Required file not found: $required"; exit 1; }
done
if [[ -n "$GROUNDTRUTH_DIR" && ! -d "$GROUNDTRUTH_DIR" ]]; then
    echo "Error: Ground-truth directory not found: $GROUNDTRUTH_DIR"
    exit 1
fi

if [[ "$MODE" == "local" ]]; then
    if [[ "${LOCAL_MS_MODE:-false}" == "true" ]]; then
        PYTHON_CMD="python3"
    else
        PYTHON_CMD="python3"
    fi
fi

run_virtual_gt_visualizer() {
    local result_name="$1"
    local result_root="$2"
    local output_dir="$result_root/virtual_gt_overlays"
    mkdir -p "$output_dir"
    rm -f "$output_dir/README.txt"

    echo "[$result_name] Rendering per-camera virtual-GT grid/pole overlays (person height=${VIRTUAL_GT_PERSON_HEIGHT} m, grid spacing=${VIRTUAL_GT_GRID_SPACING} m)..."
    local result=0
    if [[ "$MODE" == "local" ]]; then
        "$PYTHON_CMD" "$AUTO_MAGIC_ROOT/core/visualization/calib_wire_overlay_viewer.py" \
            "$result_root" \
            --sv-results-dir "$SV_OUTPUT_DIR" \
            --dump-virtual-gt \
            --virtual-gt-output-dir "$output_dir" \
            --person-height "$VIRTUAL_GT_PERSON_HEIGHT" \
            --grid-spacing "$VIRTUAL_GT_GRID_SPACING" \
            --grid-opacity 0.5 || result=$?
    else
        docker run --rm --security-opt=no-new-privileges \
            -v "$SV_OUTPUT_DIR:/auto-magic-calib/sv_results:ro" \
            -v "$result_root:/auto-magic-calib/result" \
            auto-magic-calib \
            python3 /auto-magic-calib/core/visualization/calib_wire_overlay_viewer.py \
                /auto-magic-calib/result \
                --sv-results-dir /auto-magic-calib/sv_results \
                --dump-virtual-gt \
                --person-height "$VIRTUAL_GT_PERSON_HEIGHT" \
                --grid-spacing "$VIRTUAL_GT_GRID_SPACING" \
                --grid-opacity 0.5 || result=$?
    fi

    if [[ $result -ne 0 ]]; then
        echo "Error: Virtual-GT visualization failed for $result_name (exit $result)"
        return "$result"
    fi
}

run_layout_scaling() {
    local result_name="$1"
    local mv_dir="$2"
    local result_root="$3"
    local result=0

    echo ""
    echo "================================================================"
    echo "Layout scaling and visualization: $result_name"
    echo "================================================================"

    if [[ "$MODE" == "local" ]]; then
        "$PYTHON_CMD" "$AUTO_MAGIC_ROOT/core/camera_estimation/layout_alignment_post_process.py" \
            -c "$CONFIG_FILE" -i "$SV_OUTPUT_DIR" -o "$mv_dir" \
            -a "$result_root" -l "$LAYOUT_IMAGE_PATH" || result=$?
    else
        local layout_dir
        layout_dir="$(dirname "$LAYOUT_IMAGE_PATH")"
        docker run --rm --security-opt=no-new-privileges \
            -v "$AUTO_MAGIC_ROOT/configs:/auto-magic-calib/configs:ro" \
            -v "$SV_OUTPUT_DIR:/auto-magic-calib/sv_results:ro" \
            -v "$mv_dir:/auto-magic-calib/mv_results" \
            -v "$result_root:/auto-magic-calib/output" \
            -v "$layout_dir:/auto-magic-calib/layout:ro" \
            auto-magic-calib \
            python3 /auto-magic-calib/core/camera_estimation/layout_alignment_post_process.py \
                -c /auto-magic-calib/configs/config_AutoMagicCalib/mv_amc_config.yaml \
                -i /auto-magic-calib/sv_results -o /auto-magic-calib/mv_results \
                -a /auto-magic-calib/output \
                -l "/auto-magic-calib/layout/$(basename "$LAYOUT_IMAGE_PATH")" || result=$?
    fi

    [[ $result -eq 0 ]] || {
        echo "Error: Layout post-processing failed for $result_name (exit $result)"
        return "$result"
    }

    local scaled_world="$mv_dir/BA_output/results_ba_scaled_world"
    if compgen -G "$mv_dir/cam*_cam*/calib_results.json" >/dev/null; then
        echo "[$result_name] Pairwise calibration artifacts found; map overlay generated."
    else
        rm -f "$scaled_world"/overlay_img_*.png
    fi

    run_virtual_gt_visualizer "$result_name" "$result_root" || {
        result=$?
        echo "Error: Post-processing failed during Virtual-GT visualization for $result_name"
        return "$result"
    }
}

run_evaluation() {
    local result_name="$1"
    local result_root="$2"
    if [[ -z "$GROUNDTRUTH_DIR" ]]; then
        echo "[$result_name] Ground truth not provided; evaluation skipped."
        return 0
    fi
    bash "$SCRIPT_DIR/launch_Evaluation.sh" \
        -i "$SV_OUTPUT_DIR" -o "$result_root" -g "$GROUNDTRUTH_DIR" \
        -l "$LAYOUT_IMAGE_PATH" -m "$MODE" $NO_PLOT
}

run_mv3dt_conversion() {
    local result_root="$1"
    local conversion_output="$result_root/mv3dt_output"
    rm -rf "$conversion_output"
    bash "$SCRIPT_DIR/launch_ConvertToMv3dt.sh" \
        -i "$result_root" -o "$conversion_output" -m "$MODE"
}

RESULT_NAMES=()
RESULT_MV_DIRS=()
RESULT_ROOTS=()

AMC_MV_DIR="$OUTPUT_BASE_DIR/multi_view_results"
if [[ -d "$AMC_MV_DIR/BA_output/results_ba/refined" ]]; then
    RESULT_NAMES+=("AMC")
    RESULT_MV_DIRS+=("$AMC_MV_DIR")
    RESULT_ROOTS+=("$OUTPUT_BASE_DIR")
fi

VGGT_ROOT="$OUTPUT_BASE_DIR/vggt_results"
VGGT_MV_DIR="$VGGT_ROOT/multi_view_results"
if [[ -d "$VGGT_MV_DIR/BA_output/results_ba/refined" ]]; then
    # AMC/E2E owns pairwise trajectories. Reuse them with VGGT camera matrices
    # whenever both trees exist, regardless of calibration execution order.
    if compgen -G "$AMC_MV_DIR/cam*_cam*/calib_results.json" >/dev/null; then
        echo "[VGGT] Refreshing pairwise trajectory artifacts from AMC."
        for source_pair_dir in "$AMC_MV_DIR"/cam*_cam*; do
            [[ -f "$source_pair_dir/calib_results.json" ]] || continue
            pair_name="$(basename "$source_pair_dir")"
            rm -rf "$VGGT_MV_DIR/$pair_name"
            cp -r "$source_pair_dir" "$VGGT_MV_DIR/$pair_name"
        done
    fi
    rm -rf "$VGGT_ROOT/alignment_data"
    cp -r "$ALIGNMENT_DATA_DIR" "$VGGT_ROOT/alignment_data"
    RESULT_NAMES+=("VGGT")
    RESULT_MV_DIRS+=("$VGGT_MV_DIR")
    RESULT_ROOTS+=("$VGGT_ROOT")
fi

if [[ ${#RESULT_NAMES[@]} -eq 0 ]]; then
    echo "Error: No supported multi-camera calibration results found."
    exit 1
fi

failures=0
for i in "${!RESULT_NAMES[@]}"; do
    name="${RESULT_NAMES[$i]}"
    mv_dir="${RESULT_MV_DIRS[$i]}"
    root="${RESULT_ROOTS[$i]}"

    if ! run_layout_scaling "$name" "$mv_dir" "$root"; then
        failures=$((failures + 1))
        continue
    fi
    run_evaluation "$name" "$root" || failures=$((failures + 1))
    run_mv3dt_conversion "$root" || failures=$((failures + 1))
done

if [[ $failures -ne 0 ]]; then
    echo "Shared post-processing completed with $failures failed operation(s)."
    exit 1
fi

echo "Shared layout post-processing completed: ${RESULT_NAMES[*]}"
