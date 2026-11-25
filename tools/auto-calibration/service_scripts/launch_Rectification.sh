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

set -e

# Default values
MODE="docker"
VIDEO_DIR=""
OUTPUT_BASE_DIR=""
DIST_MODEL_OVERRIDE=""

usage() {
    echo "Usage: $0 -v <video_dir> -o <output_base_dir> [-m <mode>] [--distortion-model <model>]"
    echo ""
    echo "Estimate lens distortion from frame 0 for all camera videos."
    echo "An explicit generation step is required before layout alignment."
    echo ""
    echo "Required arguments:"
    echo "  -v <video_dir>        Directory containing input video files (*.mp4)"
    echo "  -o <output_base_dir>  Base directory for all outputs"
    echo ""
    echo "Optional arguments:"
    echo "  -m <mode>             Execution mode: 'local' or 'docker' (default: 'docker')"
    echo "  --distortion-model    Per-run override: simple_radial, radial, or simple_divisional"
    echo "  -h                    Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 -v /path/to/videos/ -o /path/to/output/"
    echo "  $0 -v /path/to/videos/ -o /path/to/output/ -m local"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -v) VIDEO_DIR="$2"; shift 2 ;;
        -o) OUTPUT_BASE_DIR="$2"; shift 2 ;;
        -m) MODE="$2"; shift 2 ;;
        --distortion-model) DIST_MODEL_OVERRIDE="$2"; shift 2 ;;
        -h) usage; exit 0 ;;
        *)  echo "Unknown argument: $1"; usage; exit 1 ;;
    esac
done

if [[ -z "$VIDEO_DIR" || -z "$OUTPUT_BASE_DIR" ]]; then
    echo "Error: Missing required arguments."
    usage
    exit 1
fi

if [[ "$MODE" != "local" && "$MODE" != "docker" ]]; then
    echo "Error: Invalid mode '$MODE'. Must be 'local' or 'docker'"
    exit 1
fi

if [[ ! -d "$VIDEO_DIR" ]]; then
    echo "Error: Video directory does not exist: $VIDEO_DIR"
    exit 1
fi

echo "Running Auto Rectification Estimate in $MODE mode..."
echo "Video directory: $VIDEO_DIR"
echo "Output base directory: $OUTPUT_BASE_DIR"

mkdir -p "$OUTPUT_BASE_DIR"

SCRIPT_DIR="$(dirname "$0")"

# Host-side config paths (always accessible from host)
HOST_CONFIG_DIR="$SCRIPT_DIR/../configs/config_AutoMagicCalib"
HOST_MV_CONFIG="$HOST_CONFIG_DIR/mv_amc_config.yaml"
HOST_RECT_CONFIG="$HOST_CONFIG_DIR/rectification_config.yaml"

if [[ ! -f "$HOST_MV_CONFIG" ]]; then
    echo "Error: Config file not found: $HOST_MV_CONFIG"
    exit 1
fi

if [[ ! -f "$HOST_RECT_CONFIG" ]]; then
    echo "Error: Rectification config not found: $HOST_RECT_CONFIG"
    exit 1
fi

# Read the configured model unless this invocation explicitly overrides it.
if [ -n "$DIST_MODEL_OVERRIDE" ]; then
    DIST_MODEL="$DIST_MODEL_OVERRIDE"
else
    DIST_MODEL=$(python3 -c "import yaml; c=yaml.safe_load(open('$HOST_RECT_CONFIG')); print(c.get('distortion',{}).get('model','simple_radial'))" 2>/dev/null || echo "simple_radial")
fi
echo "Distortion model: $DIST_MODEL"
if [ "$DIST_MODEL" = "pinhole" ]; then
    echo "Error: distortion.model is 'pinhole' — no distortion to correct. Set model to simple_radial, radial, etc."
    exit 1
fi

# Configure based on mode
if [ "$MODE" = "local" ]; then
    AUTO_MAGIC_ROOT="$SCRIPT_DIR/.."
    ALGO_ROOT="$AUTO_MAGIC_ROOT/core"
    CONFIG_ROOT="$AUTO_MAGIC_ROOT/configs"
    if [ "${LOCAL_MS_MODE}" = "true" ]; then
        PYTHON_CMD="python3"
    else
        PYTHON_CMD="python3"
    fi
    echo "Using local environment..."
elif [ "$MODE" = "docker" ]; then
    AUTO_MAGIC_ROOT="/auto-magic-calib"
    ALGO_ROOT="/auto-magic-calib/core"
    CONFIG_ROOT="/auto-magic-calib/configs"
    PYTHON_CMD="python3"
    echo "Using docker environment..."
fi

# Read camera configuration from mv_amc_config.yaml (host-side)
echo ""
echo "Reading camera configuration..."
CAMERA_CONFIG=$(python3 << EOF
import yaml, sys
try:
    with open("$HOST_MV_CONFIG", 'r') as f:
        cfg = yaml.safe_load(f)
    cam_dirs = cfg.get('cam_dir', [])
    cam_dirs_str = " ".join([f"'{d}'" for d in cam_dirs])
    print(f"CAM_DIRS_STR='{cam_dirs_str}'")
    print(f"NUM_CAMERAS={len(cam_dirs)}")
except Exception as e:
    print(f"Error reading config: {e}", file=sys.stderr)
    sys.exit(1)
EOF
)

if [ $? -ne 0 ]; then
    echo "Error: Failed to read camera configuration"
    exit 1
fi

eval "$CAMERA_CONFIG"
CAM_DIRS=($CAM_DIRS_STR)

echo "  Camera directories: ${CAM_DIRS[*]}"
echo "  Number of cameras: $NUM_CAMERAS"

# Find video files for configured cameras
VIDEO_FILES=()
MISSING_CAMERAS=()

for cam_dir in "${CAM_DIRS[@]}"; do
    if [[ $cam_dir =~ cam_([0-9]+) ]]; then
        cam_num="${BASH_REMATCH[1]}"
        video_file=$(find "$VIDEO_DIR" -maxdepth 1 -name "*${cam_num}*.mp4" -type f | head -1)
        if [[ -n "$video_file" ]]; then
            VIDEO_FILES+=("$video_file")
            echo "  Found video for $cam_dir: $(basename "$video_file")"
        else
            MISSING_CAMERAS+=("$cam_dir")
        fi
    else
        echo "Error: Invalid camera directory format: $cam_dir"
        exit 1
    fi
done

if [[ ${#MISSING_CAMERAS[@]} -gt 0 ]]; then
    echo "Error: Missing video files for cameras: ${MISSING_CAMERAS[*]}"
    exit 1
fi

SV_OUTPUT_DIR="$OUTPUT_BASE_DIR/single_view_results"
mkdir -p "$SV_OUTPUT_DIR"

echo ""
echo "================================================================"
echo "Auto-estimating distortion for $NUM_CAMERAS cameras (frame 0)"
echo "================================================================"

for ((i=0; i<$NUM_CAMERAS; i++)); do
    video_file="${VIDEO_FILES[$i]}"
    cam_dir="${CAM_DIRS[$i]}"
    cam_output_dir="$SV_OUTPUT_DIR/$cam_dir"
    INPUT_DIR=$(dirname "$(realpath "$video_file")")
    VIDEO_NAME_REL=$(basename "$video_file")

    mkdir -p "$cam_output_dir"

    echo ""
    echo "----------------------------------------------------------------"
    echo "Processing $cam_dir: $VIDEO_NAME_REL"
    echo "----------------------------------------------------------------"

    # Docker container management (per-camera to match launch_AutoMagicCalib.sh pattern)
    CONTAINER_NAME=""
    EXEC_PREFIX=""
    if [ "$MODE" = "docker" ]; then
        OUTPUT_ROOT="/auto-magic-calib/output/"
        INPUT_ROOT="/auto-magic-calib/input/"
        VIDEO_NAME_CONTAINER="/auto-magic-calib/input/$VIDEO_NAME_REL"
        RECT_CONFIG="${CONFIG_ROOT}/config_AutoMagicCalib/rectification_config.yaml"

        expname=$(date +%F_%H-%M-%S)_${cam_dir}
        CALIB_IMAGE="auto-magic-calib"
        CONTAINER_NAME="rect_${expname}"
        EXEC_PREFIX="docker exec $CONTAINER_NAME"

        echo "  Starting Docker container: $CONTAINER_NAME"
        docker run -itd --rm -u $(id -u):$(id -g) --security-opt=no-new-privileges \
            --name $CONTAINER_NAME \
            -v $PWD/../configs:$CONFIG_ROOT \
            -v $cam_output_dir:$OUTPUT_ROOT \
            -v $INPUT_DIR:$INPUT_ROOT \
            $CALIB_IMAGE tail -f /dev/null

        if [ $? -ne 0 ]; then
            echo "Error: Failed to start Docker container for $cam_dir"
            exit 1
        fi
    else
        OUTPUT_ROOT="$cam_output_dir"
        VIDEO_NAME_CONTAINER="$video_file"
        RECT_CONFIG="${CONFIG_ROOT}/config_AutoMagicCalib/rectification_config.yaml"
    fi

    run_python() {
        local script_path="$1"
        shift
        local args="$@"
        if [ "$MODE" = "local" ]; then
            echo "  Executing: $PYTHON_CMD \"$script_path\" $args"
            $PYTHON_CMD "$script_path" $args
        else
            echo "  Executing: $EXEC_PREFIX python3 \"$script_path\" $args"
            $EXEC_PREFIX python3 "$script_path" $args
        fi
    }

    # Step 1: K1 estimation via model runner (GeoCalib with distorted model)
    echo ""
    echo "  K1 estimation..."
    start_time=$(date +%s%3N)

    run_python "${ALGO_ROOT}/model_runner/run.py" run-amc-compatible \
        -v "$VIDEO_NAME_CONTAINER" \
        --video-frame-index 0 \
        -o "$OUTPUT_ROOT/distortion_estimation" \
        -c "${CONFIG_ROOT}/config_AutoMagicCalib/sv_amc_config.yaml" \
        --rectification_config "${RECT_CONFIG}" \
        --camera-model "$DIST_MODEL" \
        --models geocalib

    if [ $? -ne 0 ]; then
        echo "Error: K1 estimation failed for $cam_dir"
        [ -n "$CONTAINER_NAME" ] && docker stop $CONTAINER_NAME >/dev/null 2>&1
        exit 1
    fi

    # Check output (host-side path)
    if [ ! -f "$cam_output_dir/distortion_estimation/geocalib_distortion.yaml" ]; then
        echo "Error: geocalib_distortion.yaml not produced for $cam_dir"
        [ -n "$CONTAINER_NAME" ] && docker stop $CONTAINER_NAME >/dev/null 2>&1
        exit 1
    fi

    end_time=$(date +%s%3N)
    duration=$((end_time - start_time))
    echo "  [INFO] [LATENCY] Rectification: $cam_dir: K1Estimation: $duration ms"

    # Cleanup docker container
    if [ -n "$CONTAINER_NAME" ]; then
        docker stop $CONTAINER_NAME >/dev/null 2>&1
        docker rm $CONTAINER_NAME >/dev/null 2>&1
    fi

    echo "  $cam_dir distortion estimate complete."
done

echo ""
echo "================================================================"
echo "Auto Rectification Estimate Complete!"
echo "================================================================"
echo "Outputs:"
for cam_dir in "${CAM_DIRS[@]}"; do
    echo "  $SV_OUTPUT_DIR/$cam_dir/distortion_estimation/"
done
echo ""
echo "Next steps:"
echo "  1. Review/tune frame-0 previews in the UI"
echo "  2. Generate rectified videos explicitly"
echo "================================================================"
