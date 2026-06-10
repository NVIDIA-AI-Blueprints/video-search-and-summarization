#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DEFAULT_IMAGE="vios/vst-streamprocessing:latest"
MOUNT_PATH="/home/vst/vst_release"

IMAGE=""
GTEST_ARGS=()

usage() {
    echo "Usage: $0 [OPTIONS] [-- GTEST_ARGS...]"
    echo ""
    echo "Run pre-built vst_test inside a runtime container."
    echo "Build first with: ./build.sh tests"
    echo ""
    echo "The workspace is mounted at ${MOUNT_PATH} so that the"
    echo "binary's relative rpath (prebuilts/x86_64/) resolves correctly."
    echo ""
    echo "Options:"
    echo "  --image=IMAGE        Runtime container image (default: ${DEFAULT_IMAGE})"
    echo "  --video-dir=PATH     Host path to video files (mounted at ${MOUNT_PATH}/videos)"
    echo "  --help               Show this help"
    echo ""
    echo "Any other arguments are forwarded to vst_test."
    echo ""
    echo "Examples:"
    echo "  $0                                            # Run all tests"
    echo "  $0 --gtest_filter='RtspServerManagerTest.*'   # Filter tests"
    echo "  $0 --gtest_list_tests                         # List tests"
    echo "  $0 --video-dir=/data/videos                   # Custom video path"
}

for arg in "$@"; do
    case "$arg" in
        --image=*)
            IMAGE="${arg#--image=}"
            ;;
        --video-dir=*)
            GTEST_ARGS+=("--video-dir=${MOUNT_PATH}/videos")
            VIDEO_HOST_DIR="${arg#--video-dir=}"
            ;;
        --help)
            usage
            exit 0
            ;;
        --)
            ;;
        *)
            GTEST_ARGS+=("$arg")
            ;;
    esac
done

IMAGE="${IMAGE:-$DEFAULT_IMAGE}"

if [[ ! -f "${WORKSPACE_DIR}/vst_test" ]]; then
    echo "ERROR: vst_test not found. Build first with: ./build.sh tests"
    exit 1
fi

echo "============================================"
echo "GTest Runner"
echo "  Image:     ${IMAGE}"
echo "  Workspace: ${WORKSPACE_DIR}"
echo "  Args:      ${GTEST_ARGS[*]:-"(none)"}"
echo "============================================"

DOCKER_ARGS=(
    --rm
    --user root
    --gpus all
    --network host
    --entrypoint ""
    -v "${WORKSPACE_DIR}:${MOUNT_PATH}"
)

if [[ -n "${VIDEO_HOST_DIR:-}" ]]; then
    DOCKER_ARGS+=(-v "${VIDEO_HOST_DIR}:${MOUNT_PATH}/videos:ro")
fi

ADDITIONAL_INSTALL="${MOUNT_PATH}/packaging/user_additional_install.sh"

EXIT_CODE=0
docker run "${DOCKER_ARGS[@]}" "$IMAGE" \
    bash -c "if [[ -f ${ADDITIONAL_INSTALL} ]]; then bash ${ADDITIONAL_INSTALL}; fi && apt-get update -qq && apt-get install -y -qq libgtest-dev > /dev/null 2>&1 && ldconfig && cd ${MOUNT_PATH} && ./vst_test ${GTEST_ARGS[*]:-}" \
    || EXIT_CODE=$?

echo "============================================"
if [[ $EXIT_CODE -eq 0 ]]; then
    echo "GTests PASSED"
else
    echo "GTests FAILED (exit code: ${EXIT_CODE})"
fi
echo "============================================"

exit $EXIT_CODE
