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

################################################################################
# Copyright (c) 2023-2025, NVIDIA CORPORATION.  All rights reserved.
# Build script for VSS Auto Calibration Docker images
# 
# Usage:
#   from root directory of the project run:
#   bash server/docker/build_docker_images.sh            # release build
#   bash server/docker/build_docker_images.sh --dev      # dev build
################################################################################

set -e

VERSION=3.3.0

# Parse --dev flag
DEV_MODE=false
POSITIONAL_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --dev) DEV_MODE=true ;;
        *) POSITIONAL_ARGS+=("$arg") ;;
    esac
done

if [[ "$DEV_MODE" = "true" ]]; then
    IMAGE_NAME=vss-auto-calibration-dev
    BUILD_TARGET=dev
else
    IMAGE_NAME=vss-auto-calibration
    BUILD_TARGET=release
fi

ARCH="${ARCH:-${POSITIONAL_ARGS[0]:-}}"

# If ARCH is provided, append it to the image name (e.g., vss-auto-calibration-arm64)
if [[ -n "${ARCH}" ]]; then
    VERSION="${VERSION}-${ARCH}"
fi

ROOT_DIR=$(realpath $(pwd))
cd "${ROOT_DIR}"

echo "ROOT_DIR: ${ROOT_DIR}"
echo "VERSION: ${VERSION}"
echo "ARCH: ${ARCH:-none}"
echo "MODE: ${BUILD_TARGET}"
echo ""

if [[ "$DEV_MODE" = "true" ]]; then
    echo "================================================================"
    echo "Building DEV version"
    echo "================================================================"

    docker build --target ${BUILD_TARGET} -t ${IMAGE_NAME}:latest --network=host \
        -f server/docker/Dockerfile . \
        || (echo "Error: Docker build failed" && exit 1)

    # Remove deleted base-layer codec payloads from the distributed artifact.
    bash server/docker/flatten_image.sh ${IMAGE_NAME}:latest

    docker tag ${IMAGE_NAME}:latest ${IMAGE_NAME}:${VERSION}

    echo "================================================================"
    echo "Dev Docker image build completed successfully!"
    echo "Images created:"
    echo "  - ${IMAGE_NAME}:latest"
    echo "  - ${IMAGE_NAME}:${VERSION}"
    echo "================================================================"
else
    echo "================================================================"
    echo "Building RELEASE version"
    echo "================================================================"

    echo "Building release Docker image..."
    docker build --target ${BUILD_TARGET} -t ${IMAGE_NAME}:latest --network=host \
        -f server/docker/Dockerfile . \
        || (echo "Error: Docker build failed" && exit 1)

    # Remove deleted base-layer codec payloads from the distributed artifact.
    bash server/docker/flatten_image.sh ${IMAGE_NAME}:latest

    # Tag version-specific image
    docker tag ${IMAGE_NAME}:latest ${IMAGE_NAME}:${VERSION}

    echo "================================================================"
    echo "Release Docker image build completed successfully!"
    echo "Images created:"
    echo "  - ${IMAGE_NAME}:latest"
    echo "  - ${IMAGE_NAME}:${VERSION}"
    echo "================================================================"
fi

echo ""
echo "Build process finished!"
echo "${BUILD_TARGET^} build completed successfully."
