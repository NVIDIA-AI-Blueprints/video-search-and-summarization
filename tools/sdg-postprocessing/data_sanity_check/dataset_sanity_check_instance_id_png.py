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

import os
import cv2
import argparse
from typing import NoReturn


def sanity_check(dataset_path: str, total_frames: int, output_log_file: str) -> None:
    """
    Perform a dataset sanity check on instance ID segmentation PNG files.

    This function verifies the integrity of instance ID segmentation image files by:
    - Checking for missing files in the sequence
    - Verifying that each PNG file is readable
    - Logging any issues found

    :param str dataset_path: Path to the root directory containing camera folders
    :param int total_frames: Expected number of frames per camera
    :param str output_log_file: Path where the log file should be saved
    :return: None
    :raises: No explicit exceptions, but logs errors for missing/unreadable files

    Examples::
        >>> sanity_check('/data/dataset', 1000, 'sanity_check.log')
    """
    log_lines = []  # List to store log lines for writing to a file

    # List all camera directories
    camera_dirs = [os.path.join(dataset_path, d) for d in os.listdir(dataset_path) if d.startswith("_World_Cameras_Camera")]
    camera_dirs.sort()
    
    print(f"Found {len(camera_dirs)} camera directories.")
    log_lines.append(f"Found {len(camera_dirs)} camera directories.\n")

    for camera_dir in camera_dirs:
        rgb_folder = os.path.join(camera_dir, "instance_id_segmentation_fast")
        
        if not os.path.exists(rgb_folder):
            print(f"[WARN] Missing 'instance_id_segmentation_fast' folder in {camera_dir}. Skipping.")
            log_lines.append(f"[WARN] Missing 'instance_id_segmentation_fast' folder in {camera_dir}. Skipping.\n")
            continue
        
        print(f"\nChecking {camera_dir}...")
        log_lines.append(f"\nChecking {camera_dir}...\n")

        # Frame template and frame index
        frame_template = "instance_id_segmentation_{:05d}.png"
        missing_frames = []
        unreadable_frames = []

        for idx in range(total_frames):
            frame_name = frame_template.format(idx)
            frame_path = os.path.join(rgb_folder, frame_name)

            # Check if the frame exists
            if not os.path.exists(frame_path):
                missing_frames.append(frame_name)
                continue

            # Check if the frame is readable
            img = cv2.imread(frame_path, -1)
            if img is None:
                unreadable_frames.append(frame_name)

        # Report missing and unreadable frames
        if missing_frames:
            print(f"  [MISSING] {len(missing_frames)} instance id map frames: {missing_frames}")
            log_lines.append(f"  [MISSING] {len(missing_frames)} instance id map frames: {missing_frames}\n")
        else:
            print(f"  All frames are present.")
            log_lines.append(f"  All frames are present.\n")

        if unreadable_frames:
            print(f"  [UNREADABLE] {len(unreadable_frames)} instance id map frames: {unreadable_frames}")
            log_lines.append(f"  [UNREADABLE] {len(unreadable_frames)} instance id map frames: {unreadable_frames}\n")
        else:
            print(f"  All instance id map frames are readable.")
            log_lines.append(f"  All instance id map frames are readable.\n")

    print("\nSanity check completed.")
    log_lines.append("\nSanity check completed.\n")

    # Save log to a file
    with open(output_log_file, "w") as log_file:
        log_file.writelines(log_lines)
    print(f"Sanity check log saved to {output_log_file}")


if __name__ == "__main__":
    """
    Command-line interface for the instance ID segmentation PNG dataset sanity check tool.

    Usage:
        python dataset_sanity_check_instance_id_png.py --base_dir /path/to/dataset --total_frames 1000 
            [--output_log log.txt]

    The script will:
    1. Check all instance ID segmentation PNG files in the dataset for:
       - Completeness
       - Image readability
    2. Generate a detailed log of any issues found
    3. Save the results to the specified output log file
    """
    parser = argparse.ArgumentParser(description="Perform a sanity check on the dataset.")
    parser.add_argument("--base_dir", type=str, required=True, help="Path to the dataset containing camera folders.")
    parser.add_argument("--total_frames", type=int, required=True, help="Total number of expected frames per camera.")
    parser.add_argument("--output_log", type=str, required=None, help="Path to save the sanity check log file.")
    args = parser.parse_args()

    # Set default output log file if not provided
    if args.output_log is None:
        args.output_log = os.path.join(args.base_dir, "sanity_png_instance_id_check_log.txt")

    sanity_check(args.base_dir, args.total_frames, args.output_log)