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
    echo "Usage: $0 /path/to/your/folder"
    exit 1
fi

# Assign the first argument to base_dir
base_dir=$1

# Check if the specified directory exists
if [ ! -d "$base_dir" ]; then
    echo "Error: Directory '$base_dir' does not exist."
    exit 1
fi

# Function to process each subdirectory
process_subdir() {
    local subdir=$1
    echo "Processing $subdir"

    if [[ -d "$subdir/rgb" ]]; then
        cd "$subdir/rgb" || return

        # Sort images naturally and store the list in a file
        ls *.jpg | sort -V > temp_filelist.txt

        # Use FFMPEG to combine images into an MP4 video at 30 FPS, excluding B-frames
        ffmpeg -f concat -safe 0 -r 30 -i <(for f in $(cat temp_filelist.txt); do echo "file '$PWD/$f'"; done) \
            -c:v libx264 -crf 25 -pix_fmt yuv420p -x264opts "bframes=0:keyint=30" "../video.mp4"

        # Remove the temporary file list
        rm temp_filelist.txt

        cd "$base_dir" || return
    else
        echo "'rgb' folder not found in $subdir"
    fi
}

# Navigate to the base directory
cd "$base_dir" || exit

# Set the maximum number of concurrent jobs
MAX_JOBS=16

# Initialize a counter for running jobs
RUNNING_JOBS=0

# Loop through each subfolder and process it
for dir in */ ; do
    # Process the subdirectory in the background
    process_subdir "$dir" &

    # Increment the running jobs counter
    ((RUNNING_JOBS++))

    # Check if we have reached the maximum number of concurrent jobs
    if [ "$RUNNING_JOBS" -ge "$MAX_JOBS" ]; then
        # Wait for any job to finish before continuing
        wait -n
        ((RUNNING_JOBS--))
    fi
done

# Wait for all background processes to complete
wait

echo "Processing complete."
