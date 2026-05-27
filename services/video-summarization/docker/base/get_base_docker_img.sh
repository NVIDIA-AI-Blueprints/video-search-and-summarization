#!/usr/bin/env sh
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

git config core.abbrev 7

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
GIT_ROOT="$(git rev-parse --show-toplevel)"
REL_SERVICE_ROOT="$(realpath --relative-to="$GIT_ROOT" "$SERVICE_ROOT")"

cd "$GIT_ROOT"

BASE_DIRS="$REL_SERVICE_ROOT/docker/base $REL_SERVICE_ROOT/LICENSE.3rdparty"

ls ${BASE_DIRS} >/dev/null

IMG="nvcr.io/nv-metropolis-dev/vss-core/via-engine-base:$(git log -n 1 --oneline ${BASE_DIRS} | awk '{print $1}')"
BUILD_PLATFORM=${BUILD_PLATFORM:-$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')}
IMG="$IMG-$BUILD_PLATFORM"

if [ "$BUILD_PLATFORM" = "arm64" ]; then
    GPU_NAME="Unknown"
    if command -v nvidia-smi >/dev/null 2>&1; then
        GPU_NAME=$(nvidia-smi --query-gpu=gpu_name --format=csv,noheader -i 0 2>/dev/null || echo "Unknown")
    fi
    if echo "$GPU_NAME" | grep -q "Thor"; then
        ARM_PLATFORM=${ARM_PLATFORM:-"igpu"}
    else
        ARM_PLATFORM=${ARM_PLATFORM:-"sbsa"}
    fi
    IMG="$IMG-$ARM_PLATFORM"
fi
UNCOMITTED_CHANGES=$(git status --porcelain=v1 2>/dev/null ${BASE_DIRS})

if [ -n "$UNCOMITTED_CHANGES" ]; then
IMG="$IMG-uncommitted-${USER}"
fi

echo "$IMG"
