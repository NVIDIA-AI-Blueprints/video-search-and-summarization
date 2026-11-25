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

MODE="docker"
NO_PLOT=""
INPUT_BASE_DIR=""
OUTPUT_BASE_DIR=""
GT_BASE_DIR=""
LAYOUT_IMAGE_PATH=""

# Function to display usage
usage() {
    echo "Note: This script is used to evaluate the multi-camera calibration results."
    echo "      The ground truth is required for evaluation."
    echo ""
    echo "Usage: $0 -i <input_base_dir> -o <output_base_dir> -g <gt_dir> -l <layout_image_path> [-m <mode>] [--no-plot]"
    echo ""
    echo "Required arguments:"
    echo "  -i <input_base_dir>   Base directory for single view calibration outputs"
    echo "  -o <output_base_dir>  Base directory for output"
    echo "  -g <gt_dir>           Directory containing ground truth data"
    echo "  -l <layout_image_path> Path to layout image file"
    echo ""
    echo "Optional arguments:"
    echo "  -m <mode>             Execution mode: 'local' or 'docker' (default: 'docker')"
    echo "  --no-plot            Disable plotting for evaluation"
    echo "  -h                    Show this help message"
    echo ""
    echo "Config files (default):"
    echo "  Config: config_AutoMagicCalib/mv_amc_config.yaml"
    echo "  Eval Config: config_AutoMagicCalib/eval_config.yaml"
    echo ""
    echo "User may modify the default config file or place their own config files in the configs directory and modify the CONFIG_FILE definition in the script."
    echo ""
    echo "Examples:"
    echo "  $0 -i /home/user/input/ -o /home/user/output/ -g /home/user/data/ -l /home/user/data/layout.png"
    echo "  $0 -i /home/user/input/ -o /home/user/output/ -g /home/user/data/ -l /home/user/data/layout.png -m local"
    echo "  $0 -i /home/user/input/ -o /home/user/output/ -g /home/user/data/ -l /home/user/data/layout.png -m docker --no-plot"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -m)
            MODE="$2"
            shift 2
            ;;
        -i)
            INPUT_BASE_DIR="$2"
            shift 2
            ;;
        -o)
            OUTPUT_BASE_DIR="$2"
            shift 2
            ;;
        -g)
            GT_BASE_DIR="$2"
            shift 2
            ;;
        -l)
            LAYOUT_IMAGE_PATH="$2"
            shift 2
            ;;
        --no-plot)
            NO_PLOT="--no-plot"
            shift
            ;;
        -h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
done

# Validate mode
if [[ "$MODE" != "local" && "$MODE" != "docker" ]]; then
    echo "Error: Invalid mode '$MODE'. Must be 'local' or 'docker'."
    exit 1
fi

# Validate input base directory is provided
if [[ -z "$INPUT_BASE_DIR" ]]; then
    echo "Error: Input base directory (-i) is required"
    usage
    exit 1
fi

# Validate output base directory is provided
if [[ -z "$OUTPUT_BASE_DIR" ]]; then
    echo "Error: Output base directory (-o) is required"
    usage
    exit 1
fi

# Validate ground truth directory is provided
if [[ -z "$GT_BASE_DIR" ]]; then
    echo "Error: Ground truth directory (-g) is required"
    usage
    exit 1
fi

# Validate layout image path is provided
if [[ -z "$LAYOUT_IMAGE_PATH" ]]; then
    echo "Error: Layout image path (-l) is required"
    usage
    exit 1
fi

echo "Mode: $MODE"
echo "No-plot mode: ${NO_PLOT:-disabled}"
if [ -n "$INPUT_BASE_DIR" ]; then
    echo "Input base directory: $INPUT_BASE_DIR"
fi
if [ -n "$OUTPUT_BASE_DIR" ]; then
    echo "Output base directory: $OUTPUT_BASE_DIR"
fi
if [ -n "$GT_BASE_DIR" ]; then
    echo "Ground truth directory: $GT_BASE_DIR"
fi
if [ -n "$LAYOUT_IMAGE_PATH" ]; then
    echo "Layout image path: $LAYOUT_IMAGE_PATH"
fi

# Set up environment based on mode
if [ "$MODE" = "local" ]; then
    AUTO_MAGIC_ROOT="$(dirname "$0")/.."
    ALGO_ROOT="$AUTO_MAGIC_ROOT/core"
    CONFIG_ROOT="$AUTO_MAGIC_ROOT/configs/"
    SCRIPT_ROOT="$AUTO_MAGIC_ROOT/service_scripts/"
    INPUT_ROOT="$INPUT_BASE_DIR"
    OUTPUT_ROOT="$OUTPUT_BASE_DIR"
    GT_ROOT="$GT_BASE_DIR"
    LAYOUT_ROOT="$LAYOUT_IMAGE_PATH"
    if [ "${LOCAL_MS_MODE}" = "true" ]; then
        PYTHON_CMD="python3"
    else
        PYTHON_CMD="python3"
    fi
    EXEC_PREFIX=""
    CONTAINER_NAME=""
    
    # Set config files using CONFIG_ROOT
    CONFIG_FILE="${CONFIG_ROOT}/config_AutoMagicCalib/mv_amc_config.yaml"
    EVAL_CONFIG_FILE="${CONFIG_ROOT}/config_AutoMagicCalib/eval_config.yaml"
    
    echo "Using local environment..."
elif [ "$MODE" = "docker" ]; then
    AUTO_MAGIC_ROOT="/auto-magic-calib"
    ALGO_ROOT="/auto-magic-calib/core"
    CONFIG_ROOT="/auto-magic-calib/configs" # used by py scripts inside container
    SCRIPT_ROOT="$(dirname "$0")" # scripts are never containerized
    INPUT_ROOT="/auto-magic-calib/input"
    OUTPUT_ROOT="/auto-magic-calib/output"
    GT_ROOT="/auto-magic-calib/gt"
    
    # Extract layout directory and filename for docker mounting
    LAYOUT_DIR=$(dirname "$LAYOUT_IMAGE_PATH")
    LAYOUT_FILENAME=$(basename "$LAYOUT_IMAGE_PATH")
    LAYOUT_ROOT="/auto-magic-calib/layout/$LAYOUT_FILENAME"
    
    PYTHON_CMD="python3"
    
    # Set config files using CONFIG_ROOT
    CONFIG_FILE="${CONFIG_ROOT}/config_AutoMagicCalib/mv_amc_config.yaml"
    EVAL_CONFIG_FILE="${CONFIG_ROOT}/config_AutoMagicCalib/eval_config.yaml"
    
    # Docker container setup
    expname=$(date +%F_%H-%M-%S)
    CALIB_IMAGE="auto-magic-calib"
    CONTAINER_NAME="evaluation_${expname}"
    EXEC_PREFIX="docker exec $CONTAINER_NAME"
    
    echo "Using Docker container: $CONTAINER_NAME"
    echo "================================================================"
    echo "Step 0: Starting Docker container"
    echo "================================================================"

    # Build docker run command with required directory mounts
    DOCKER_VOLUMES="-v $PWD/../configs:$CONFIG_ROOT -v $INPUT_BASE_DIR:$INPUT_ROOT -v $OUTPUT_BASE_DIR:$OUTPUT_ROOT -v $GT_BASE_DIR:$GT_ROOT -v $LAYOUT_DIR:/auto-magic-calib/layout"
    
    docker run -itd --rm -u $(id -u):$(id -g) --security-opt=no-new-privileges --name $CONTAINER_NAME $DOCKER_VOLUMES $CALIB_IMAGE tail -f /dev/null
    docker ps -a
    
    if [ $? -ne 0 ]; then
        echo "Error: Failed to start Docker container"
        exit 1
    fi
fi

# Function to run Python commands
run_python() {
    local script_path="$1"
    shift
    local args="$@"
    
    if [ "$MODE" = "local" ]; then
        echo "Executing: $PYTHON_CMD \"$script_path\" $args"
        $PYTHON_CMD "$script_path" $args
    else
        echo "Executing: $EXEC_PREFIX python3 \"$script_path\" $args"
        $EXEC_PREFIX python3 "$script_path" $args
    fi
}

# Function to cleanup (for Docker mode)
cleanup() {
    if [ "$MODE" = "docker" ] && [ -n "$CONTAINER_NAME" ]; then
        echo "Cleaning up containers..."
        docker stop $CONTAINER_NAME >/dev/null 2>&1
        docker rm $CONTAINER_NAME >/dev/null 2>&1
        echo "Containers stopped successfully"
    fi
}

# Set up trap for cleanup on exit
trap cleanup EXIT

# Detect single-camera vs multi-camera dataset (same as scripts/launch_Evaluation.sh)
echo ""
echo "================================================================"
echo "Detecting camera configuration ..."
echo "================================================================"

NUM_CAMS=$($EXEC_PREFIX find "$INPUT_ROOT" -maxdepth 1 -type d -name "cam_*" 2>/dev/null \
           | sort -u | wc -l)
echo "Found $NUM_CAMS camera directory(ies) under $INPUT_ROOT"

if [ "$NUM_CAMS" -eq 1 ]; then
    # ---- Single-camera evaluation ----
    CAM_DIR=$($EXEC_PREFIX find "$INPUT_ROOT" -maxdepth 1 -type d -name "cam_*" 2>/dev/null \
              | sort -u | head -1)
    AMC_CALIB="${CAM_DIR}/camInfo_world.yaml"
    GT_CALIB="${GT_ROOT}/calibration.json"
    GT_TRAJ="${GT_ROOT}/ground_truth.json"

    echo ""
    echo "================================================================"
    echo "Single-camera evaluation started"
    echo "================================================================"
    echo "  AMC calib : $AMC_CALIB"
    echo "  GT calib  : $GT_CALIB"
    echo "  GT traj   : $GT_TRAJ"

    run_python "${ALGO_ROOT}/evaluation/evaluate_single_cam.py" \
                --amc_calib "$AMC_CALIB" \
                --gt_calib "$GT_CALIB" \
                --gt_traj "$GT_TRAJ" \
                --eval_config "$EVAL_CONFIG_FILE" \
                --output_dir "$OUTPUT_ROOT" \
                --layout_image_path "$LAYOUT_ROOT" \
                --object_types "person"

    if [ $? -ne 0 ]; then
        echo "Error: Single-camera evaluation failed"
        exit 1
    fi

    echo "Single-camera Evaluation Completed!!!"
else
    # ---- Multi-camera evaluation ----
    echo ""
    echo "================================================================"
    echo "Multi-camera evaluation started"
    echo "================================================================"
    run_python "${ALGO_ROOT}/evaluation/evaluate_multi_cam.py" \
                -c "$CONFIG_FILE" \
                -e "$EVAL_CONFIG_FILE" \
                -i "$INPUT_ROOT" \
                -o "$OUTPUT_ROOT" \
                -g "$GT_ROOT" \
                -l "$LAYOUT_ROOT" \
                $NO_PLOT

    if [ $? -ne 0 ]; then
        echo "Error: Multi-camera evaluation failed"
        exit 1
    fi

    echo "Multi-camera Evaluation Completed!!!"
fi

# Cleanup happens automatically via trap 