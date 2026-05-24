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

BASE_DIR=$1
SCRIPT_PATH="./convert_single_camera_rgb_depth_to_h5.py"

if [ -z "$BASE_DIR" ]; then
    echo "❗ Base directory not specified."
    exit 1
fi

for cam_dir in "$BASE_DIR"/Camera*; do
    if [ -d "$cam_dir" ]; then

        if [ -f "$cam_dir/convert_done.txt" ]; then
            echo "✅ Already processed: $cam_dir"
            continue
        fi

        if [ -d "$cam_dir/rgb" ] && [ -d "$cam_dir/distance_to_image_plane_png" ]; then
            echo "🚀 Processing Camera folder: $cam_dir"

            python "$SCRIPT_PATH" --input "$cam_dir"

            if [ $? -eq 0 ]; then
                touch "$cam_dir/convert_done.txt"
                echo "✅ Finished processing: $cam_dir"
            else
                echo "❌ Error processing: $cam_dir, stopping script."
                exit 1
            fi

            echo "----------------------"
        else
            echo "⚠️ Skipping $cam_dir (missing rgb or distance_to_image_plane_png)"
        fi
    fi
done

echo "🎉 All available cameras processed."