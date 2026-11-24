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
VIDEO_NAME=""
OUT_DIR_INPUT=""
DETECTOR_TYPE="resnet"
FOCAL_LENGTH_OVERRIDE=""

# Function to display usage
usage() {
    echo "Usage: $0 -i <source_video> -o <output_folder> [-m <mode>] [-d <detector_type>] [-f <focal_length>]"
    echo ""
    echo "Required arguments:"
    echo "  -i <source_video>   Source video used to identify/log the camera"
    echo "  -o <output_folder>  Camera output directory containing rectified.mp4 and rectified.jpg"
    echo ""
    echo "Optional arguments:"
    echo "  -m <mode>          Execution mode: 'local' or 'docker' (default: 'docker')"
    echo "  -d <type>          Detector type: 'resnet' or 'transformer' (default: 'resnet')"
    echo "  -f <focal_length>  Ground truth focal length in pixels (overrides GeoCalib estimate)"
    echo "  -h                 Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 -i video.mp4 -o output_dir"
    echo "  $0 -i video.mp4 -o output_dir -m local"
    echo "  $0 -i video.mp4 -o output_dir -t distorted"
    echo "  $0 -i video.mp4 -o output_dir -m local -t distorted -d transformer"
    echo "  $0 -i video.mp4 -o output_dir -f 1200.0"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -i)
            VIDEO_NAME="$2"
            shift 2
            ;;
        -o)
            OUT_DIR_INPUT="$2"
            shift 2
            ;;
        -m)
            MODE="$2"
            shift 2
            ;;
        -d)
            DETECTOR_TYPE="$2"
            shift 2
            ;;
        -f)
            FOCAL_LENGTH_OVERRIDE="$2"
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

# Check required arguments
if [ -z "$VIDEO_NAME" ] || [ -z "$OUT_DIR_INPUT" ]; then
    echo "Error: Missing required arguments."
    usage
    exit 1
fi

# Validate mode
if [[ "$MODE" != "local" && "$MODE" != "docker" ]]; then
    echo "Error: Invalid mode '$MODE'. Must be 'local' or 'docker'"
    exit 1
fi

# Validate detector type
if [[ "$DETECTOR_TYPE" != "resnet" && "$DETECTOR_TYPE" != "transformer" ]]; then
    echo "Error: Invalid detector type '$DETECTOR_TYPE'. Must be 'resnet' or 'transformer'"
    exit 1
fi

echo "Running AutoMagicCalib in $MODE mode..."
echo "Input video: $VIDEO_NAME"
echo "Output folder: $OUT_DIR_INPUT"
echo "Detector type: $DETECTOR_TYPE"
if [[ -n "$FOCAL_LENGTH_OVERRIDE" ]]; then
    echo "Focal length override: $FOCAL_LENGTH_OVERRIDE px (will replace GeoCalib estimate)"
fi

# Normalize output directory to absolute path
OUT_DIR=$(realpath -m "$OUT_DIR_INPUT")  # -m allows non-existent paths
echo "Normalized output path: $OUT_DIR"

# Normalize and validate input video path
if [ ! -f "$VIDEO_NAME" ]; then
    echo "Error: Input video file does not exist: $VIDEO_NAME"
    exit 1
fi

# Keep the source name for logs only. Calibration always consumes the staged
# linear media in OUT_DIR.
VIDEO_NAME_REL=$(basename "$VIDEO_NAME")
echo "Relative video name: $VIDEO_NAME_REL"

# Check if output directory exists
# if [ -d "$OUT_DIR" ]; then
#     echo "Output directory already exists: $OUT_DIR"
#     exit 1
# fi

mkdir -p "$OUT_DIR"

# Rectification is an upstream stage. E2E must never recreate or overwrite
# these artifacts.
for linear_artifact in rectified.mp4 rectified.jpg; do
    if [[ ! -s "$OUT_DIR/$linear_artifact" ]]; then
        echo "Error: Missing staged linear media: $OUT_DIR/$linear_artifact"
        echo "Run launch_Rectification.sh first, or stage an already-linear video via the UI/frontend."
        exit 1
    fi
done

# Configure based on mode
if [ "$MODE" = "local" ]; then
    AUTO_MAGIC_ROOT="$(dirname "$0")/.."
    ALGO_ROOT="$AUTO_MAGIC_ROOT/core"
    CONFIG_ROOT="$AUTO_MAGIC_ROOT/configs/"
    SCRIPT_ROOT="$AUTO_MAGIC_ROOT/service_scripts/"
    OUTPUT_ROOT="$OUT_DIR"
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
    CONFIG_ROOT="/auto-magic-calib/configs" # used by py scripts inside container
    SCRIPT_ROOT="$(dirname "$0")" # scripts are never containerized
    OUTPUT_ROOT="/auto-magic-calib/output/"
    PYTHON_CMD="python3"
    
    # Docker container setup
    expname=$(date +%F_%H-%M-%S)
    CALIB_IMAGE="auto-magic-calib"
    CONTAINER_NAME="calib_${expname}"
    EXEC_PREFIX="docker exec $CONTAINER_NAME"
    
    echo "Using Docker container: $CONTAINER_NAME"
    echo "================================================================"
    echo "Step 0: Starting Docker container"
    echo "================================================================"

    docker run -itd --rm -u $(id -u):$(id -g) --security-opt=no-new-privileges --name $CONTAINER_NAME -v $PWD/../configs:$CONFIG_ROOT -v $OUT_DIR:$OUTPUT_ROOT $CALIB_IMAGE tail -f /dev/null
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
    
    # Clean up detector config copies
    cleanup_detector_configs
}

# Function to setup detector config copies
setup_detector_configs() {
    local config_dir="../configs/config_DeepStream"
    local source_file="$config_dir/config_deepstream_2d_${DETECTOR_TYPE}.txt"
    local target_file="$config_dir/conf_2d.txt"
    
    echo "Setting up detector configs for $DETECTOR_TYPE..."
    
    # Remove existing file if it exists
    [ -f "$target_file" ] && rm "$target_file"
    
    # Copy the config file
    cp "$source_file" "$target_file"
    
    echo "Created copy:"
    echo "  conf_2d.txt (from config_deepstream_2d_${DETECTOR_TYPE}.txt)"
}

# Function to cleanup detector config copies
cleanup_detector_configs() {
    local config_dir="../configs/config_DeepStream"
    local target_file="$config_dir/conf_2d.txt"
    
    # Remove copy if it exists
    [ -f "$target_file" ] && rm "$target_file"
}

# Set up trap for cleanup on exit
trap cleanup EXIT

# Copy config files (local mode only, docker has them in the image)
if [ "$MODE" = "local" ]; then
    cp -r ${CONFIG_ROOT}/config_AutoMagicCalib "${OUT_DIR}/"
fi

# Step 1: Model Runner for f/pitch/roll on upstream linear media
echo ""
echo "================================================================"
echo "Step 1: Model Runner started (linear media)"
echo "================================================================"

start_time=$(date +%s%3N)
run_python "${ALGO_ROOT}/model_runner/run.py" run-amc-compatible \
            -v "$OUTPUT_ROOT/rectified.mp4" \
            -o "$OUTPUT_ROOT" \
            -c "${CONFIG_ROOT}/config_AutoMagicCalib/sv_amc_config.yaml"

if [ $? -ne 0 ]; then
    echo "Error: Model Runner failed"
    exit 1
fi
end_time=$(date +%s%3N)
duration=$((end_time - start_time))
echo "[INFO] [LATENCY] CalibrationPipeline: SingleViewCalibration: $VIDEO_NAME_REL: ModelRunner: $duration ms"

# Step 1.5: Replace focal length with ground truth if provided
if [[ -n "$FOCAL_LENGTH_OVERRIDE" ]]; then
    echo ""
    echo "================================================================"
    echo "Step 1.5: Replacing focal length with ground truth value"
    echo "================================================================"
    
    start_time=$(date +%s%3N)
    run_python "${ALGO_ROOT}/utils/replace_focal_length_in_sv_config.py" \
        -c "${OUTPUT_ROOT}/config_sv_amc.yaml" \
        -f "$FOCAL_LENGTH_OVERRIDE" \
        -r "0.5"

    if [ $? -ne 0 ]; then
        echo "Error: Failed to replace focal length in SV config"
        exit 1
    fi
    echo "Ground truth focal length successfully applied to SV configuration"
    end_time=$(date +%s%3N)
    duration=$((end_time - start_time))
    echo "[INFO] [LATENCY] CalibrationPipeline: SingleViewCalibration: $VIDEO_NAME_REL: FocalLengthOverride: $duration ms"
fi

# Step 3: Bounding box detection
echo ""
echo "================================================================"
echo "Step 3: Running deepstream app to detect people..."
echo "================================================================"

start_time=$(date +%s%3N)
# Setup detector config symlinks
setup_detector_configs

echo "....."

# Use absolute path for the rectified video
rectified_video=$(realpath "${OUT_DIR}/rectified.mp4")

# edit video file name - escape forward slashes for sed
rectified_video_escaped=$(echo "${rectified_video}" | sed 's/\//\\\//g')
sed -i "s/uri=file:\/\/video.mp4/uri=file:\/\/${rectified_video_escaped}/g" ../configs/config_DeepStream/conf_2d.txt

# run detection
echo "run peoplenet detector..."
echo "bash ${SCRIPT_ROOT}/run_2d.sh \"${OUT_DIR}\""
bash ${SCRIPT_ROOT}/run_2d.sh "${OUT_DIR}"
echo "done peoplenet detector"

echo "convert format"
echo "bash ${SCRIPT_ROOT}/convert_det2kitti.sh \"${OUT_DIR}\""
bash ${SCRIPT_ROOT}/convert_det2kitti.sh "${OUT_DIR}"
echo "done converting format"

# copy detection
cp "${OUT_DIR}/Det-Stream_0.log" "${OUT_DIR}/Det-bboxes.log"
end_time=$(date +%s%3N)
duration=$((end_time - start_time))
echo "[INFO] [LATENCY] CalibrationPipeline: SingleViewCalibration: $VIDEO_NAME_REL: BoundingBoxDetection: $duration ms"

# Step 4: Bounding box sampling
echo ""
echo "================================================================"
echo "Step 4: Bounding box sampling"
echo "================================================================"
start_time=$(date +%s%3N)
run_python "${ALGO_ROOT}/camera_estimation/box_sampling.py" \
            -c "${CONFIG_ROOT}/config_AutoMagicCalib/preprocess_config.yaml" \
            -i "${OUTPUT_ROOT}/Det-bboxes.log" \
            -b "${OUTPUT_ROOT}/rectified.jpg" \
            -o "${OUTPUT_ROOT}"

if [ $? -ne 0 ]; then
    echo "Error: Bounding box sampling failed"
    exit 1
fi
end_time=$(date +%s%3N)
duration=$((end_time - start_time))
echo "[INFO] [LATENCY] CalibrationPipeline: SingleViewCalibration: $VIDEO_NAME_REL: BoundingBoxSampling: $duration ms"

# Step 5: Camera projection matrix estimation
echo ""
echo "================================================================"
echo "Step 5: Camera projection matrix estimation"
echo "================================================================"
start_time=$(date +%s%3N)
run_python "${ALGO_ROOT}/camera_estimation/camera_proj_matrix_estimation.py" \
           -c "${OUTPUT_ROOT}/config_sv_amc.yaml" \
           -b "${OUTPUT_ROOT}/Det_bbox_sampling_v2.txt" \
           -i "${OUTPUT_ROOT}/rectified.jpg" \
           -o "${OUTPUT_ROOT}"

if [ $? -ne 0 ]; then
    echo "Error: Camera projection matrix estimation failed"
    exit 1
fi
end_time=$(date +%s%3N)
duration=$((end_time - start_time))
echo "[INFO] [LATENCY] CalibrationPipeline: SingleViewCalibration: $VIDEO_NAME_REL: CameraProjectionMatrixEstimation: $duration ms"
echo "AutoMagicCalib Completed!!!"

# Cleanup happens automatically via trap 