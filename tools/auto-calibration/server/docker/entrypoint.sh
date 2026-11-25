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

set -e

echo "=============================================="
echo "VSS Auto Calibration Container Starting"
echo "=============================================="

# Run setup script if it exists
SETUP_SCRIPT="/home/auto-calibration-ms/service_scripts/setup_in_docker.sh"

if [[ -f "$SETUP_SCRIPT" ]]; then
    echo "Running setup script..."
    
    # Build setup command with optional VGGT model path
    SETUP_CMD="bash $SETUP_SCRIPT"
    
    if [[ -n "$VGGT_MODEL_PATH" && -f "$VGGT_MODEL_PATH" ]]; then
        echo "VGGT model found at: $VGGT_MODEL_PATH"
        SETUP_CMD="$SETUP_CMD --vggt-model-path $VGGT_MODEL_PATH"
    else
        echo "VGGT model not found at: ${VGGT_MODEL_PATH:-'(not set)'}"
        echo "VGGT setup will be skipped"
    fi
    
    if [[ -n "$MOGE_MODEL_PATH" && -f "$MOGE_MODEL_PATH" ]]; then
        echo "MoGe model found at: $MOGE_MODEL_PATH"
        SETUP_CMD="$SETUP_CMD --moge-model-path $MOGE_MODEL_PATH"
    fi

    if [[ "$MOGE_ENABLED" = "false" ]]; then
        echo "MoGe setup disabled via MOGE_ENABLED=false"
        SETUP_CMD="$SETUP_CMD --disable-moge"
    fi
    
    # Run setup
    $SETUP_CMD
    
    echo "Setup completed!"
else
    echo "Warning: Setup script not found at $SETUP_SCRIPT"
fi

echo ""
echo "=============================================="
echo "Starting application..."
echo "=============================================="

# Execute the CMD passed to the container
exec "$@"

