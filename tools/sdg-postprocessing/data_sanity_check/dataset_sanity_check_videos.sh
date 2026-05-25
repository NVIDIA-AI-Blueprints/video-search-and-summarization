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

# Check if a directory argument was provided
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 /path/to/folder"
    exit 1
fi

# Assign the first argument to base_dir
base_dir=$1

# Check if the specified directory exists
if [ ! -d "$base_dir" ]; then
    echo "Error: Directory '$base_dir' does not exist."
    exit 1
fi

# Output file to store results
output_file="$base_dir/bframes_check_results.txt"
echo "B-frames Sanity Check Results" > "$output_file"
echo "=============================" >> "$output_file"

# Function to check B-frames in a video
check_bframes() {
    local video_file=$1
    echo "Checking B-frames in: $video_file"

    # Run ffprobe command to check for B-frames
    if ffprobe -loglevel error -show_frames "$video_file" | grep -qE 'pict_type=B'; then
        echo "❌ B-frames detected in: $video_file"
        echo "❌ B-frames detected in: $video_file" >> "$output_file"
    else
        echo "✅ No B-frames found in: $video_file"
        echo "✅ No B-frames found in: $video_file" >> "$output_file"
    fi
}

# Loop through all Camera* folders and check for video.mp4
find "$base_dir" -type d -name "Camera*" | while read -r camera_folder; do
    video_path="$camera_folder/video.mp4"
    if [ -f "$video_path" ]; then
        check_bframes "$video_path"
    else
        echo "⚠️ No video.mp4 found in: $camera_folder"
        echo "⚠️ No video.mp4 found in: $camera_folder" >> "$output_file"
    fi
done

echo "B-frame sanity check completed. Results saved in $output_file."