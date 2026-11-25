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

start_time_full=$(date +%s%3N)

# Error handling function
handle_error() {
    local step_name="$1"
    local error_code="$2"
    echo ""
    echo "================================================================"
    echo "ERROR: $step_name failed with exit code $error_code"
    echo "================================================================"
    echo "The calibration pipeline has encountered an error and cannot continue."
    echo "Please check the error messages above for more details."
    echo ""
    echo "Suggestions:"
    echo "- Verify all input files and directories exist and are accessible"
    echo "- Check that you have sufficient disk space and permissions"
    echo "- Ensure all dependencies are properly installed"
    echo "- Try running the command again"
    echo ""
    echo "If the problem persists, please review the error messages and"
    echo "ensure your input data and configuration are correct."
    echo "================================================================"
    exit $error_code
}

# Sanity check functions
verify_file_exists() {
    local file_path="$1"
    local description="$2"
    if [[ ! -f "$file_path" ]]; then
        echo "ERROR: Missing $description: $file_path"
        return 1
    fi
    echo "✓ Found $description: $file_path"
    return 0
}

verify_directory_exists() {
    local dir_path="$1"
    local description="$2"
    if [[ ! -d "$dir_path" ]]; then
        echo "ERROR: Missing $description: $dir_path"
        return 1
    fi
    echo "✓ Found $description: $dir_path"
    return 0
}

verify_file_not_empty() {
    local file_path="$1"
    local description="$2"
    if [[ ! -s "$file_path" ]]; then
        echo "ERROR: $description is empty or doesn't exist: $file_path"
        return 1
    fi
    echo "✓ Verified $description is not empty: $file_path"
    return 0
}

verify_files_in_directory() {
    local dir_path="$1"
    local pattern="$2"
    local description="$3"
    local min_count="${4:-1}"
    
    if [[ ! -d "$dir_path" ]]; then
        echo "ERROR: Directory doesn't exist: $dir_path"
        return 1
    fi
    
    local count=$(find "$dir_path" -name "$pattern" -type f | wc -l)
    if [[ $count -lt $min_count ]]; then
        echo "ERROR: Expected at least $min_count $description in $dir_path, found $count"
        return 1
    fi
    echo "✓ Found $count $description in $dir_path"
    return 0
}

# Pre-process sanity checks
verify_inputs_before_process() {
    local process_name="$1"
    shift
    local files_to_check=("$@")
    
    echo ""
    echo "================================================================"
    echo "PRE-CHECK: Verifying inputs for $process_name"
    echo "================================================================"
    
    local check_failed=0
    for file_desc in "${files_to_check[@]}"; do
        # Split file_desc into type:path:description
        IFS=':' read -r check_type check_path check_desc <<< "$file_desc"
        case "$check_type" in
            "file")
                if ! verify_file_exists "$check_path" "$check_desc"; then
                    check_failed=1
                fi
                ;;
            "dir")
                if ! verify_directory_exists "$check_path" "$check_desc"; then
                    check_failed=1
                fi
                ;;
            "file_nonempty")
                if ! verify_file_not_empty "$check_path" "$check_desc"; then
                    check_failed=1
                fi
                ;;
            "files_in_dir")
                # Format: files_in_dir:dir_path:pattern:description:min_count
                IFS=':' read -r _ dir_path pattern desc min_count <<< "$file_desc"
                if ! verify_files_in_directory "$dir_path" "$pattern" "$desc" "${min_count:-1}"; then
                    check_failed=1
                fi
                ;;
        esac
    done
    
    if [[ $check_failed -eq 1 ]]; then
        echo ""
        echo "❌ PRE-CHECK FAILED: Missing required inputs for $process_name"
        exit 1
    fi
    echo ""
    echo "✅ PRE-CHECK PASSED: All required inputs found for $process_name"
}

# Post-process sanity checks
verify_outputs_after_process() {
    local process_name="$1"
    shift
    local files_to_check=("$@")
    
    echo ""
    echo "================================================================"
    echo "POST-CHECK: Verifying outputs for $process_name"
    echo "================================================================"
    
    local check_failed=0
    for file_desc in "${files_to_check[@]}"; do
        # Split file_desc into type:path:description
        IFS=':' read -r check_type check_path check_desc <<< "$file_desc"
        case "$check_type" in
            "file")
                if ! verify_file_exists "$check_path" "$check_desc"; then
                    check_failed=1
                fi
                ;;
            "dir")
                if ! verify_directory_exists "$check_path" "$check_desc"; then
                    check_failed=1
                fi
                ;;
            "file_nonempty")
                if ! verify_file_not_empty "$check_path" "$check_desc"; then
                    check_failed=1
                fi
                ;;
            "files_in_dir")
                # Format: files_in_dir:dir_path:pattern:description:min_count
                IFS=':' read -r _ dir_path pattern desc min_count <<< "$file_desc"
                if ! verify_files_in_directory "$dir_path" "$pattern" "$desc" "${min_count:-1}"; then
                    check_failed=1
                fi
                ;;
        esac
    done
    
    if [[ $check_failed -eq 1 ]]; then
        echo ""
        echo "❌ POST-CHECK FAILED: Missing expected outputs for $process_name"
        exit 1
    fi
    echo ""
    echo "✅ POST-CHECK PASSED: All expected outputs found for $process_name"
}

# Set up error trapping for the entire script
set -e
trap 'handle_error "Calibration Pipeline" $?' ERR


# Default values
MODE="docker"
INPUT_DIR=""
ADJUSTMENT_DIR=""
VGGT_OUTPUT_DIR=""
LAYOUT_IMAGE_PATH=""
GROUNDTRUTH_DIR=""
SV_OUTPUT_DIR=""
MV_OUTPUT_DIR="" 
AMC_OUTPUT_BASE_DIR="" # AMC output directory

# Function to display usage
usage() {
    echo "Usage: $0 -a <amc_output_dir>  [-g <groundtruth_dir>] [-l <layout_image_path>] [-i <input_dir>] [-m <mode>] "
    echo ""
    echo "Required arguments:"
    echo "  -a <amc_output_dir>   Directory containing AMC calibration results"
    echo ""
    echo "Optional arguments:"
    echo "  -g <groundtruth_dir>  Ground truth directory for evaluation (optional)"
    echo "  -l <layout_image_path> Path to layout image file for manual alignment (optional)"
    echo "  -i <input_dir>        Input directory containing video data (optional)"
    echo "  -m <mode>             Execution mode: 'local' or 'docker' (default: 'docker')"
    echo "  -h                    Show this help message"
    echo ""
    echo "Config files (default):"
    echo "  Config: config_AutoMagicCalib/mv_amc_config.yaml"
    echo ""
    echo "User may modify the default config file or place their own config files in the configs directory and modify the CONFIG_FILE definition in the script."
    echo ""
    echo "Examples:"
    echo "  $0 -i /home/user/dataset/seg1 -a /home/user/amc_output -o output_vggt -l /home/user/data/layout.png"
    echo "  $0 -i /home/user/dataset/seg1 -a /home/user/amc_output -o output_vggt -m local -l /home/user/data/layout.png"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -m)
            MODE="$2"
            shift 2
            ;;
        -i)
            INPUT_DIR="$2"
            shift 2
            ;;
        -l)
            LAYOUT_IMAGE_PATH="$2"
            shift 2
            ;;
        -a)
            AMC_OUTPUT_BASE_DIR="$2"
            shift 2
            ;;
        -g)
            GROUNDTRUTH_DIR="$2"
            shift 2
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

# Validate required arguments
if [[ -z "$AMC_OUTPUT_BASE_DIR" ]]; then
    echo "Error: AMC output directory (-a) is required"
    usage
    exit 1
fi

# Layout image and ground truth are now independent
# No validation needed here - we'll handle scenarios in the main logic

# Check layout image exists (only when provided for evaluation)
if [[ -n "$LAYOUT_IMAGE_PATH" && ! -f "$LAYOUT_IMAGE_PATH" ]]; then
    echo "Error: Layout image does not exist: $LAYOUT_IMAGE_PATH"
    exit 1
fi

# Validate ground truth directory exists (if provided)
if [[ -n "$GROUNDTRUTH_DIR" && ! -d "$GROUNDTRUTH_DIR" ]]; then
    echo "Error: Ground truth directory does not exist: $GROUNDTRUTH_DIR"
    exit 1
fi

# Setup script directory
SCRIPT_DIR="$(dirname "$0")"

# Read camera configuration from mv_amc_config.yaml
echo "Reading camera configuration from mv_amc_config.yaml..."
CONFIG_FILE="$SCRIPT_DIR/../configs/config_AutoMagicCalib/mv_amc_config.yaml"

SV_OUTPUT_DIR="$AMC_OUTPUT_BASE_DIR/single_view_results"
MV_OUTPUT_DIR="$AMC_OUTPUT_BASE_DIR/multi_view_results"
ADJUSTMENT_DIR="$AMC_OUTPUT_BASE_DIR/alignment_data"
VGGT_OUTPUT_BASE_DIR="$AMC_OUTPUT_BASE_DIR/vggt_results"
VGGT_OUTPUT_DIR="$AMC_OUTPUT_BASE_DIR/vggt_results/multi_view_results"

# VGGT consumes linear media staged by the decoupled rectification workflow.
if [[ ! -d "$SV_OUTPUT_DIR" ]]; then
    echo "Error: Single-view output directory does not exist: $SV_OUTPUT_DIR"
    exit 1
fi

echo "Running VGGT Calibration in $MODE mode..."
if [[ -n "$INPUT_DIR" ]]; then
    echo "Input directory: $INPUT_DIR"
fi
echo "AMC output directory: $AMC_OUTPUT_BASE_DIR"
echo "Single-view output directory: $SV_OUTPUT_DIR"
echo "Adjustment directory: $ADJUSTMENT_DIR"
echo "VGGT Output directory: $VGGT_OUTPUT_DIR"

# Configure based on mode
if [ "$MODE" = "local" ]; then
    AUTO_MAGIC_ROOT="$(dirname "$0")/.."
    ALGO_ROOT="$AUTO_MAGIC_ROOT/core"
    CONFIG_ROOT="$AUTO_MAGIC_ROOT/configs/"
    SCRIPT_ROOT="$AUTO_MAGIC_ROOT/service_scripts/"
    INPUT_ROOT="$SV_OUTPUT_DIR"
    ADJUSTMENT_ROOT="$ADJUSTMENT_DIR"
    OUTPUT_ROOT="$VGGT_OUTPUT_DIR"
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
    ADJUSTMENT_ROOT="/auto-magic-calib/adjustment"
    OUTPUT_ROOT="/auto-magic-calib/output"
        
    PYTHON_CMD="python3"
    
    # Set config files using CONFIG_ROOT
    CONFIG_FILE="${CONFIG_ROOT}/config_AutoMagicCalib/mv_amc_config.yaml"
    
    # Docker container setup
    expname=$(date +%F_%H-%M-%S)
    CALIB_IMAGE="auto-magic-calib"
    CONTAINER_NAME="vggt_${expname}"
    EXEC_PREFIX="docker exec $CONTAINER_NAME"
    
    echo "Using Docker container: $CONTAINER_NAME"
    echo "================================================================"
    echo "Step 0: Starting Docker container"
    echo "================================================================"

    # Create VGGT_OUTPUT_DIR on host before mounting to avoid permission issues
    mkdir -p "$VGGT_OUTPUT_DIR"
    mkdir -p "$VGGT_OUTPUT_DIR/BA_output/results_ba_scaled"

    docker run -itd --rm --security-opt=no-new-privileges --name $CONTAINER_NAME \
        -v $PWD/../configs:$CONFIG_ROOT \
        -v $SV_OUTPUT_DIR:$INPUT_ROOT \
        -v $ADJUSTMENT_DIR:$ADJUSTMENT_ROOT \
        -v $VGGT_OUTPUT_DIR:$OUTPUT_ROOT \
        $CALIB_IMAGE tail -f /dev/null
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

# Step 1: VGGT calibration
start_time=$(date +%s%3N)

echo ""
echo "================================================================"
echo "Step 1: VGGT calibration started"
echo "================================================================"
run_python "${ALGO_ROOT}/VGGT/run_vggt_calib.py" \
            -c "$CONFIG_FILE" \
            -s "$INPUT_ROOT" \
            -a "$ADJUSTMENT_ROOT" \
            -o "$OUTPUT_ROOT" 

if [ $? -ne 0 ]; then
    echo "Error: VGGT calibration failed"
    exit 1
fi

echo ""
echo "================================================================"
echo "VGGT Calibration Completed!!!"
echo "================================================================"
echo "Results saved to: $VGGT_OUTPUT_DIR"

end_time=$(date +%s%3N)
duration=$((end_time - start_time))
echo "[INFO] [LATENCY] CalibrationPipeline: VGGTCalibration: $duration ms"

# Post-check for VGGT calibration outputs
verify_outputs_after_process "VGGT calibration" \
    "dir:$VGGT_OUTPUT_DIR:VGGT output directory" \
    "dir:$VGGT_OUTPUT_DIR/BA_output/results_ba/refined:camInfo directory"

echo ""
echo "================================================================"
echo "VGGT Calibration Complete (raw artifacts)"
echo "================================================================"
echo "VGGT-based calibration results: $VGGT_OUTPUT_DIR"

# revert the config file to the original one
CONFIG_FILE="$SCRIPT_DIR/../configs/config_AutoMagicCalib/mv_amc_config.yaml"

# get auto_select from config file
AUTO_SELECT=$(grep -oP 'auto_select: \K[^ ]+' "$CONFIG_FILE")
echo "Auto-select: $AUTO_SELECT"

# Discover top-k result directories (if available)
TOP_K_RESULTS_DIR="$VGGT_OUTPUT_DIR/auto_selection/top_k_results"
TOP_K_TARGET_DIRS=()
if [[ -d "$TOP_K_RESULTS_DIR" ]]; then
    while IFS= read -r rank_dir; do
        TOP_K_TARGET_DIRS+=("$rank_dir")
    done < <(ls -d "$TOP_K_RESULTS_DIR"/rank_* 2>/dev/null | sort)
fi

# Layout scaling, visualization, GT evaluation, and MV3DT conversion are
# intentionally deferred to launch_LayoutPostProcess.sh.

# Copy top-1 raw BA output to the stable VGGT result path used by the UI and
# shared post-processing workflow.
if [[ ${#TOP_K_TARGET_DIRS[@]} -gt 0 ]]; then
    echo "Detected ${#TOP_K_TARGET_DIRS[@]} top-k result directories for downstream processing."
    top1_dir="${TOP_K_TARGET_DIRS[0]}"
    # Copy BA_output
    if [[ -d "$top1_dir/BA_output" ]]; then
        rm -rf "$VGGT_OUTPUT_DIR/BA_output"
        cp -r "$top1_dir/BA_output" "$VGGT_OUTPUT_DIR/"
        echo "Copied top-1 BA_output to $VGGT_OUTPUT_DIR for backward compatibility."
    else
        echo "Warning: top-1 BA_output not found at $top1_dir/BA_output"
    fi
else
    echo "No top-k result directories detected; using default VGGT output for downstream processing."
fi

echo "Run launch_LayoutPostProcess.sh to generate layout-scaled artifacts,"
echo "overlays, optional GT evaluation, and MV3DT output."
