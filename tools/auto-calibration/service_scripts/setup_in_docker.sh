#!/bin/bash
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

set -e  # Exit on error

start_time=$(date +%s)

# Container paths (fixed locations inside container)
CONTAINER_SUBMODULES_DIR="/home/auto-calibration-ms/submodules"
CONTAINER_MODELS_DIR="/home/auto-calibration-ms/models"
CONTAINER_NGC_DOWNLOAD_DIR="/home/auto-calibration-ms/ngc_download"

# Parse command line arguments
ENABLE_VGGT=true
ENABLE_MOGE=true
VGGT_MODEL_PATH=""
MOGE_MODEL_PATH=""
SKIP_NGC_DOWNLOAD=false
SKIP_CUSTOM_PARSER=false

print_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "This script must be run from INSIDE a Docker container."
    echo "All models are downloaded to /home/auto-calibration-ms/models"
    echo ""
    echo "Options:"
    echo "  --vggt-model-path <path>   Path to VGGT model file (mounted into container)"
    echo "  --moge-model-path <path>   Path to MoGe model file (mounted into container)"
    echo "  --disable-vggt             Skip VGGT setup"
    echo "  --disable-moge             Skip MoGe setup"
    echo "  --skip-ngc-download        Skip downloading NGC models"
    echo "  --skip-custom-parser       Skip building custom parser"
    echo "  -h, --help                 Show this help message"
    echo ""
    echo "Example (inside container):"
    echo "  $0"
    echo "  $0 --vggt-model-path /tmp/vggt_model/vggt_1B_commercial.pt"
    echo "  $0 --disable-vggt"
    echo ""
    echo "After running, exit and commit the container:"
    echo "  docker commit <container_name> vss-auto-calibration:latest"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --vggt-model-path)
            VGGT_MODEL_PATH="$2"
            shift 2
            ;;
        --moge-model-path)
            MOGE_MODEL_PATH="$2"
            shift 2
            ;;
        --disable-vggt)
            ENABLE_VGGT=false
            shift
            ;;
        --disable-moge)
            ENABLE_MOGE=false
            shift
            ;;
        --skip-ngc-download)
            SKIP_NGC_DOWNLOAD=true
            shift
            ;;
        --skip-custom-parser)
            SKIP_CUSTOM_PARSER=true
            shift
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            print_usage
            exit 1
            ;;
    esac
done

# Validate we're inside a container (basic check)
if [ ! -f /.dockerenv ] && [ ! -f /run/.containerenv ]; then
    echo "⚠ Warning: This script appears to be running outside a container."
    echo "This script is designed to run INSIDE a Docker container."
    read -p "Continue anyway? (y/N): " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Aborted."
        exit 1
    fi
fi

echo "=============================================="
echo "VSS Auto Calibration In-Container Setup"
echo "=============================================="
echo ""

# Install required packages
# echo "Installing required packages..."
# apt update -y && apt install -y git wget tar unzip curl libgl1 libglib2.0-0 || true

# Create directories
echo "Creating directories..."
mkdir -p "$CONTAINER_SUBMODULES_DIR"
mkdir -p "$CONTAINER_MODELS_DIR"

# =============================================================================
# Setup FL Models (GeoCalib, VGGT, MoGe) via setup_models.py
# =============================================================================
setup_inference_models() {
    echo ""
    echo "----------------------------------------------"
    echo "Setting up FL models (manifest-driven)..."
    echo "----------------------------------------------"

    CONTAINER_HOME="/home/auto-calibration-ms"

    # Pre-copy mounted weight files so setup_models.py sees them
    # and skips redundant downloads via _is_fully_set_up.
    if [ -n "$VGGT_MODEL_PATH" ] && [ -f "$VGGT_MODEL_PATH" ]; then
        mkdir -p "$CONTAINER_MODELS_DIR/vggt"
        if [ ! -f "$CONTAINER_MODELS_DIR/vggt/model.pt" ]; then
            cp "$VGGT_MODEL_PATH" "$CONTAINER_MODELS_DIR/vggt/model.pt"
            echo "Copied VGGT model to $CONTAINER_MODELS_DIR/vggt/model.pt"
        else
            echo "VGGT model already exists at $CONTAINER_MODELS_DIR/vggt/model.pt"
        fi
    fi
    if [ -n "$MOGE_MODEL_PATH" ] && [ -f "$MOGE_MODEL_PATH" ]; then
        mkdir -p "$CONTAINER_MODELS_DIR/moge"
        if [ ! -f "$CONTAINER_MODELS_DIR/moge/model.pt" ]; then
            cp "$MOGE_MODEL_PATH" "$CONTAINER_MODELS_DIR/moge/model.pt"
            echo "Copied MoGe model to $CONTAINER_MODELS_DIR/moge/model.pt"
        else
            echo "MoGe model already exists at $CONTAINER_MODELS_DIR/moge/model.pt"
        fi
    fi

    # Build skip list from disable flags
    SKIP_MODELS=""
    if [ "$ENABLE_VGGT" = false ]; then SKIP_MODELS="$SKIP_MODELS vggt"; fi
    if [ "$ENABLE_MOGE" = false ]; then SKIP_MODELS="$SKIP_MODELS moge"; fi

    python3 -m core.model_runner.setup.setup_models \
        --manifest "$CONTAINER_HOME/configs/config_AutoMagicCalib/models_manifest.yaml" \
        --project-root "$CONTAINER_HOME" \
        --models geocalib vggt moge \
        --skip-deps \
        ${SKIP_MODELS:+--skip-models $SKIP_MODELS}

    echo "FL model setup completed"
}

# =============================================================================
# Download NGC Models
# =============================================================================
download_ngc_models() {
    echo ""
    echo "----------------------------------------------"
    echo "Downloading NGC models..."
    echo "----------------------------------------------"
    
    local ngc_download="$CONTAINER_NGC_DOWNLOAD_DIR"
    mkdir -p $ngc_download
    
    echo "Downloading to: $CONTAINER_NGC_DOWNLOAD_DIR"
    
    # Download PeopleNet ResNet34 Detector
    echo "Downloading PeopleNet ResNet34 detector..."
    local det_zip="$ngc_download/peoplenet_deployable_quantized.zip"
    if [ ! -f "$CONTAINER_MODELS_DIR/resnet34_peoplenet_int8.onnx" ]; then
        wget --content-disposition https://api.ngc.nvidia.com/v2/models/nvidia/tao/peoplenet/versions/deployable_quantized_onnx_v2.6.3/zip -O "$det_zip"
        unzip -o "$det_zip" -d "$CONTAINER_MODELS_DIR"
        mv "$CONTAINER_MODELS_DIR/labels.txt" "$CONTAINER_MODELS_DIR/labels_peoplenet_resnet34.txt" 2>/dev/null || true
        echo "✓ PeopleNet ResNet34 detector downloaded"
    else
        echo "✓ PeopleNet ResNet34 detector already exists"
    fi
    
    # Download Re-ID model
    echo "Downloading Re-ID model..."
    if [ ! -f "$CONTAINER_MODELS_DIR/resnet50_market1501_aicity156.onnx" ]; then
        wget 'https://api.ngc.nvidia.com/v2/models/nvidia/tao/reidentificationnet/versions/deployable_v1.2/files/resnet50_market1501_aicity156.onnx' -P "$CONTAINER_MODELS_DIR"
        echo "✓ Re-ID model downloaded"
    else
        echo "✓ Re-ID model already exists"
    fi
    
    # Download PeopleNet Transformer Detector
    echo "Downloading PeopleNet Transformer detector..."
    if [ ! -f "$CONTAINER_MODELS_DIR/peoplenet_transformer_v2.onnx" ]; then
        curl -L 'https://api.ngc.nvidia.com/v2/models/org/nvidia/team/tao/peoplenet_transformer_v2/deployable_v1.0/files?redirect=true&path=dino_fan_small_astro_delta.onnx' -o "$CONTAINER_MODELS_DIR/peoplenet_transformer_v2.onnx"
        wget https://api.ngc.nvidia.com/v2/models/nvidia/tao/peoplenet_transformer/versions/deployable_v1.0/files/labels.txt -O "$CONTAINER_MODELS_DIR/labels_peoplenet_transformer.txt"
        echo "✓ PeopleNet Transformer detector downloaded"
        
        # Filter labels
        echo "Filtering PeopleNet Transformer labels..."
        cat "$CONTAINER_MODELS_DIR/labels_peoplenet_transformer.txt" | tr -d '\r' | sed '/^$/d' | grep -E "^(BG|Person)$" > "$CONTAINER_MODELS_DIR/labels_peoplenet_transformer_filtered.txt"
        mv "$CONTAINER_MODELS_DIR/labels_peoplenet_transformer_filtered.txt" "$CONTAINER_MODELS_DIR/labels_peoplenet_transformer.txt"
        echo "✓ Labels filtered - kept only BG and Person"
    else
        echo "✓ PeopleNet Transformer detector already exists"
    fi
    
    echo "✓ NGC models downloaded to $CONTAINER_MODELS_DIR"
}

# =============================================================================
# Build Custom Parser
# =============================================================================
build_custom_parser() {
    echo ""
    echo "----------------------------------------------"
    echo "Building custom parser..."
    echo "----------------------------------------------"
    
    local parser_dir="$CONTAINER_MODELS_DIR/custom_parser"
    
    if [ ! -d "$parser_dir" ]; then
        echo "⚠ Custom parser directory not found at $parser_dir"
        echo "  Skipping custom parser build."
        return 0
    fi
    
    # Get CUDA version
    if [ -n "$CUDA_VERSION" ]; then
        export CUDA_VER=${CUDA_VERSION%%.*}
        echo "Using CUDA version: $CUDA_VER (from CUDA_VERSION=$CUDA_VERSION)"
    else
        echo "⚠ CUDA_VERSION not set, trying to detect..."
        if command -v nvcc &> /dev/null; then
            CUDA_VER=$(nvcc --version | grep release | sed 's/.*release //' | sed 's/,.*//' | cut -d. -f1)
            export CUDA_VER
            echo "Detected CUDA version: $CUDA_VER"
        else
            echo "✗ Could not detect CUDA version, skipping custom parser build"
            return 1
        fi
    fi
    
    cd "$parser_dir"
    echo "Building in directory: $(pwd)"
    
    echo "Running make clean..."
    make clean || true
    
    echo "Running make..."
    if make; then
        echo "✓ Custom parser build completed"
        rm -rf ~/.cache/gstreamer-1.0/
        ls -la *.so 2>/dev/null || echo "No .so files found"
    else
        echo "✗ Custom parser build failed"
        return 1
    fi
    
    cd - > /dev/null
}

# =============================================================================
# Main Execution
# =============================================================================

echo "Configuration:"
echo "  Models directory: $CONTAINER_MODELS_DIR"
echo "  Submodules directory: $CONTAINER_SUBMODULES_DIR"
echo "  VGGT model path: ${VGGT_MODEL_PATH:-'(not set)'}"
echo "  VGGT enabled: $ENABLE_VGGT"
echo "  MoGe model path: ${MOGE_MODEL_PATH:-'(not set)'}"
echo "  MoGe enabled: $ENABLE_MOGE"
echo "  Skip NGC download: $SKIP_NGC_DOWNLOAD"
echo "  Skip custom parser: $SKIP_CUSTOM_PARSER"
echo ""

# Setup FL models (GeoCalib, VGGT, MoGe) via manifest-driven setup_models.py
setup_inference_models

# Download NGC models (if not skipped)
if [ "$SKIP_NGC_DOWNLOAD" = false ]; then
    download_ngc_models
else
    echo ""
    echo "NGC model download skipped (--skip-ngc-download flag provided)"
fi

# Build custom parser (if not skipped)
if [ "$SKIP_CUSTOM_PARSER" = false ]; then
    build_custom_parser
else
    echo ""
    echo "Custom parser build skipped (--skip-custom-parser flag provided)"
fi

echo ""
echo "=============================================="
echo "Setup completed!"
echo "=============================================="
echo ""
echo "Next steps:"
echo "  1. Exit the container: exit"
echo "  2. Commit the container to save changes:"
echo "     docker commit <container_name_or_id> vss-auto-calibration:latest"
echo ""
echo "Example:"
echo "  docker commit \$(docker ps -lq) vss-auto-calibration:latest"
echo ""

end_time=$(date +%s)
duration=$((end_time - start_time))
echo "\n\n[INFO] [LATENCY] Setup completed in $duration seconds"
