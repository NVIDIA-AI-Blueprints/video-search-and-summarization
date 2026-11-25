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

# Function to display usage
usage() {
    echo "Usage: $0 -i <input_base_dir> -o <output_base_dir> [-m <mode>] [--no-plot]"
    echo ""
    echo "Required arguments:"
    echo "  -i <input_base_dir>   Base directory for single view calibration outputs"
    echo "  -o <output_base_dir>  Base directory for output"
    echo ""
    echo "Optional arguments:"
    echo "  -m <mode>             Execution mode: 'local' or 'docker' (default: 'docker')"
    echo "  --no-plot            Disable plotting for bundle adjustment"
    echo "  -h                    Show this help message"
    echo ""
    echo "Config files (default):"
    echo "  Config: config_AutoMagicCalib/mv_amc_config.yaml"
    echo ""
    echo "User may modify the default config file or place their own config files in the configs directory and modify the CONFIG_FILE definition in the script."
    echo ""
    echo "Examples:"
    echo "  $0 -i /home/user/input/ -o /home/user/output/"
    echo "  $0 -i /home/user/input/ -o /home/user/output/ -m local"
    echo "  $0 -i /home/user/input/ -o /home/user/output/ -m docker --no-plot"
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
        --no-plot)
            NO_PLOT="--no-plot"
            shift
            ;;
        -h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            exit 1
            ;;
    esac
done

# Validate mode
if [[ "$MODE" != "local" && "$MODE" != "docker" ]]; then
    echo "Error: Invalid mode '$MODE'. Must be 'local' or 'docker'"
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

echo "Running MultiView Calibration in $MODE mode..."
echo "No-plot mode: ${NO_PLOT:-disabled}"
if [ -n "$INPUT_BASE_DIR" ]; then
    echo "Input base directory: $INPUT_BASE_DIR"
fi
if [ -n "$OUTPUT_BASE_DIR" ]; then
    echo "Output base directory: $OUTPUT_BASE_DIR"
fi

# Configure based on mode
if [ "$MODE" = "local" ]; then
    AUTO_MAGIC_ROOT="$(dirname "$0")/.."
    ALGO_ROOT="$AUTO_MAGIC_ROOT/core"
    CONFIG_ROOT="$AUTO_MAGIC_ROOT/configs/"
    SCRIPT_ROOT="$AUTO_MAGIC_ROOT/service_scripts/"
    INPUT_ROOT="$INPUT_BASE_DIR"
    OUTPUT_ROOT="$OUTPUT_BASE_DIR"
    if [ "${LOCAL_MS_MODE}" = "true" ]; then
        PYTHON_CMD="python3"
    else
        PYTHON_CMD="python3"
    fi
    EXEC_PREFIX=""
    CONTAINER_NAME=""
    
    # Set config files using CONFIG_ROOT
    CONFIG_FILE="${CONFIG_ROOT}/config_AutoMagicCalib/mv_amc_config.yaml"
    
    echo "Using local environment..."
elif [ "$MODE" = "docker" ]; then
    AUTO_MAGIC_ROOT="/auto-magic-calib"
    ALGO_ROOT="/auto-magic-calib/core"
    CONFIG_ROOT="/auto-magic-calib/configs" # used by py scripts inside container
    SCRIPT_ROOT="$(dirname "$0")" # scripts are never containerized
    INPUT_ROOT="/auto-magic-calib/input"
    OUTPUT_ROOT="/auto-magic-calib/output"
        
    PYTHON_CMD="python3"
    
    # Set config files using CONFIG_ROOT
    CONFIG_FILE="${CONFIG_ROOT}/config_AutoMagicCalib/mv_amc_config.yaml"
    
    # Docker container setup
    expname=$(date +%F_%H-%M-%S)
    CALIB_IMAGE="auto-magic-calib"
    CONTAINER_NAME="multiview_${expname}"
    EXEC_PREFIX="docker exec $CONTAINER_NAME"
    
    echo "Using Docker container: $CONTAINER_NAME"
    echo "================================================================"
    echo "Step 0: Starting Docker container"
    echo "================================================================"

    docker run -itd --rm -u $(id -u):$(id -g) --security-opt=no-new-privileges --name $CONTAINER_NAME -v $PWD/../configs:$CONFIG_ROOT -v $INPUT_BASE_DIR:$INPUT_ROOT -v $OUTPUT_BASE_DIR:$OUTPUT_ROOT $CALIB_IMAGE tail -f /dev/null
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

# Step 1: Multi-view calibration
echo ""
echo "================================================================"
echo "Step 1: Multi-view calibration started"
echo "================================================================"
start_time=$(date +%s%3N)
run_python "${ALGO_ROOT}/camera_estimation/multi_view_calib.py" \
            -c "$CONFIG_FILE" \
            -i "$INPUT_ROOT" \
            -o "$OUTPUT_ROOT"
end_time=$(date +%s%3N)
duration=$((end_time - start_time))
echo "[INFO] [LATENCY] CalibrationPipeline: MultiViewCalibration: MultiViewCalibration: $duration ms"
if [ $? -ne 0 ]; then
    echo "Error: Multi-view calibration failed"
    exit 1
fi

# Step 2: Bundle adjustment
echo ""
echo "================================================================"
echo "Step 2: Bundle adjustment started"
echo "================================================================"
start_time=$(date +%s%3N)
run_python "${ALGO_ROOT}/camera_estimation/bundle_adjustment.py" \
            -c "$CONFIG_FILE" \
            -i "$INPUT_ROOT" \
            -o "$OUTPUT_ROOT" \
            $NO_PLOT
end_time=$(date +%s%3N)
duration=$((end_time - start_time))
echo "[INFO] [LATENCY] CalibrationPipeline: MultiViewCalibration: BundleAdjustment: $duration ms"
if [ $? -ne 0 ]; then
    echo "Error: Bundle adjustment failed"
    exit 1
fi

echo "MultiView Calibration Completed!!!"

# Cleanup happens automatically via trap 