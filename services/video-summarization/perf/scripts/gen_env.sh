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
MONITORING_DIR="$(cd "$SCRIPT_DIR/../setup/monitoring" && pwd)"
ENV_FILE="$MONITORING_DIR/.env"

echo "DOCKER_UID=$(id -u)" > "$ENV_FILE"
echo "DOCKER_GID=$(id -g)" >> "$ENV_FILE"
echo "VIA_HOST=$(hostname -I | tr ' ' '\n' | grep -E '^10\.|^172\.(1[6-9]|2[0-9]|3[0-1])\.|^192\.168\.' | head -1 || hostname -I | awk '{print $1}')" >> "$ENV_FILE"
echo "LOCAL_NIM_CACHE=${LOCAL_NIM_CACHE:-~/.cache/nim}" >> "$ENV_FILE"
