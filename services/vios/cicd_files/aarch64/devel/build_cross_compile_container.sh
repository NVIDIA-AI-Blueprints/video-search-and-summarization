#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2020-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Default tag matches the Makefile's AARCH64_CC_IMAGE default, so a plain
# `./build_cross_compile_container.sh` produces the image used by `make cc=1`.
IMAGE_NAME="${1:-vios-build:aarch64-cross-compiler}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker build --network=host -t "${IMAGE_NAME}" "${SCRIPT_DIR}"

echo "Built ${IMAGE_NAME}"
