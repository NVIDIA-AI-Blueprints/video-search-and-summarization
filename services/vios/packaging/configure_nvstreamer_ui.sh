#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

base_path="${NVSTREAMER_UI_BASE_PATH:-}"
release_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$base_path" == "/" ]]; then
    base_path=""
elif [[ -n "$base_path" ]]; then
    if [[ ! "$base_path" =~ ^(/[A-Za-z0-9._~-]+)+/?$ ]]; then
        echo "Invalid NVSTREAMER_UI_BASE_PATH: use an absolute URL path such as /nvstreamer" >&2
        exit 1
    fi

    base_path="${base_path%/}"
    IFS='/' read -ra path_segments <<< "$base_path"
    for segment in "${path_segments[@]}"; do
        if [[ "$segment" == "." || "$segment" == ".." ]]; then
            echo "Invalid NVSTREAMER_UI_BASE_PATH: path traversal segments are not allowed" >&2
            exit 1
        fi
    done
fi

printf 'window.__VIOS_RUNTIME_CONFIG__ = {"basePath":"%s"};\n' "$base_path" \
    > "$release_dir/webroot/runtime-config.js"
