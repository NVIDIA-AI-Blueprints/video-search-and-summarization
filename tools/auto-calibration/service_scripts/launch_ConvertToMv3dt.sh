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

start_time=$(date +%s%3N)

# Default values
MODE="docker"
INPUT_BASE_DIR=""
OUTPUT_BASE_DIR=""
G_FLAG=false

# Function to display usage
usage() {
    echo "Note: This script is used to convert the auto-magic-calibration results to the MV3DT file structure."
    echo "      The input directory should contain the auto-magic-calibration results."
    echo ""
    echo "Usage: $0 -i <input_base_dir> -o <output_base_dir> [-m <mode>]"
    echo ""
    echo "Required arguments:"
    echo "  -i <input_base_dir>   Base directory for single view calibration outputs"
    echo "  -o <output_base_dir>  Base directory for output"
    echo ""
    echo "Optional arguments:"
    echo "  -m <mode>             Execution mode: 'local' or 'docker' (default: 'docker')"
    echo "  -g                    Enable additional processing in convert_to_mv3dt.py"
    echo "  -h                    Show this help message"
    echo "Examples:"
    echo "  $0 -i /home/user/input/ -o /home/user/output/"
    echo "  $0 -i /home/user/input/ -o /home/user/output/ -m local"
    echo "  $0 -i /home/user/input/ -o /home/user/output/ -m docker -g"
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
            G_FLAG=true
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

echo "Mode: $MODE"
if [ -n "$INPUT_BASE_DIR" ]; then
    echo "Input base directory: $INPUT_BASE_DIR"
fi
if [ -n "$OUTPUT_BASE_DIR" ]; then
    echo "Output base directory: $OUTPUT_BASE_DIR"
fi
if [ "$G_FLAG" = true ]; then
    echo "G flag: enabled"
fi

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_BASE_DIR"

# Set up environment based on mode
if [ "$MODE" = "local" ]; then
    AUTO_MAGIC_ROOT="$(dirname "$0")/.."
    ALGO_ROOT="$AUTO_MAGIC_ROOT/core"
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
    
    echo "Using local environment..."
elif [ "$MODE" = "docker" ]; then
    AUTO_MAGIC_ROOT="/auto-magic-calib"
    ALGO_ROOT="/auto-magic-calib/core"
    SCRIPT_ROOT="$(dirname "$0")" # scripts are never containerized
    INPUT_ROOT="/auto-magic-calib/input"
    OUTPUT_ROOT="/auto-magic-calib/output"
    
    PYTHON_CMD="python3"
    
    # Docker container setup
    expname=$(date +%F_%H-%M-%S)
    CALIB_IMAGE="auto-magic-calib"
    CONTAINER_NAME="conversion_${expname}"
    EXEC_PREFIX="docker exec $CONTAINER_NAME"
    
    echo "Using Docker container: $CONTAINER_NAME"
    echo "================================================================"
    echo "Step 0: Starting Docker container"
    echo "================================================================"

    # Build docker run command with required directory mounts
    DOCKER_VOLUMES="-v $INPUT_BASE_DIR:$INPUT_ROOT -v $OUTPUT_BASE_DIR:$OUTPUT_ROOT"
    
    docker run -itd --rm -u $(id -u):$(id -g) --security-opt=no-new-privileges --name $CONTAINER_NAME $DOCKER_VOLUMES $CALIB_IMAGE tail -f /dev/null
    # docker run -itd --rm --security-opt=no-new-privileges --name $CONTAINER_NAME $DOCKER_VOLUMES $CALIB_IMAGE tail -f /dev/null
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

# Multi-camera evaluation
echo ""
echo "================================================================"
echo "Conversion to MV3DT file structure started"
echo "================================================================"
G_FLAG_ARG=""
if [ "$G_FLAG" = true ]; then
    G_FLAG_ARG="-g"
fi

run_python "${ALGO_ROOT}/utils/convert_to_mv3dt.py" \
            --input_dir "$INPUT_ROOT" \
            --output_dir "$OUTPUT_ROOT" \
            $G_FLAG_ARG

if [ $? -ne 0 ]; then
    echo "Error: Conversion to MV3DT file structure failed"
    exit 1
fi

echo "Conversion to MV3DT file structure Completed!!!"

# Cleanup happens automatically via trap 
end_time=$(date +%s%3N)
duration=$((end_time - start_time))
echo "[INFO] [LATENCY] MV3DTConversion: $duration ms"