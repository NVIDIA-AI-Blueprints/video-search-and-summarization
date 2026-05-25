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
import numpy as np
from typing import Optional


def sanity_check(dataset_path: str, total_frames: int, output_log_file: str, camera_folder: Optional[str] = None) -> None:
    """
    Perform a dataset sanity check on numpy array files (distance to image plane data).

    This function verifies the integrity of .npy files in a dataset by:
    - Checking for missing files in the sequence
    - Verifying that each file is readable as a numpy array
    - Checking for empty or corrupted numpy arrays
    - Logging any issues found

    :param str dataset_path: Path to the root directory containing camera folders
    :param int total_frames: Expected number of frames per camera
    :param str output_log_file: Path where the log file should be saved
    :param str camera_folder: Optional specific camera folder to check (e.g., '_World_Cameras_Camera_01')
    :return: None
    :raises: No explicit exceptions, but logs errors for missing/unreadable files

    Examples::
        >>> # Check all cameras
        >>> sanity_check('/data/dataset', 1000, 'sanity_check.log')
        >>> # Check specific camera
        >>> sanity_check('/data/dataset', 1000, 'sanity_check.log', '_World_Cameras_Camera_01')
    """
    log_lines = []  # List to store log lines for writing to a file

    # List all camera directories or a specific one
    if camera_folder:
        camera_dirs = [os.path.join(dataset_path, camera_folder)]
    else:
        camera_dirs = [os.path.join(dataset_path, d) for d in os.listdir(dataset_path) if d.startswith("_World_Cameras_Camera")]

    camera_dirs.sort()
    
    print(f"Found {len(camera_dirs)} camera directories.")
    log_lines.append(f"Found {len(camera_dirs)} camera directories.\n")

    for camera_dir in camera_dirs:
        rgb_folder = os.path.join(camera_dir, "distance_to_image_plane")
        
        if not os.path.exists(rgb_folder):
            print(f"[WARN] Missing 'distance_to_image_plane' folder in {camera_dir}. Skipping.")
            log_lines.append(f"[WARN] Missing 'rgb' folder in {camera_dir}. Skipping.\n")
            continue
        
        print(f"\nChecking {camera_dir}...")
        log_lines.append(f"\nChecking {camera_dir}...\n")

        # Frame template and frame index
        frame_template = "distance_to_image_plane_{:05d}.npy"
        missing_frames = []
        unreadable_frames = []
        empty_frames = []

        for idx in range(total_frames):
            frame_name = frame_template.format(idx)
            frame_path = os.path.join(rgb_folder, frame_name)

            # Check if the frame exists
            if not os.path.exists(frame_path):
                missing_frames.append(frame_name)
                continue

            # Check if the frame is readable
            try:
                depth_data = np.load(frame_path)

                if depth_data is None:
                    empty_frames.append(frame_name)
            except Exception as e:
                unreadable_frames.append(frame_name)

        # Report missing and unreadable frames
        if missing_frames:
            print(f"  [MISSING] {len(missing_frames)} depth map frames: {missing_frames}")
            log_lines.append(f"  [MISSING] {len(missing_frames)} depth map frames: {missing_frames}\n")
        else:
            print(f"  All depth map frames are present.")
            log_lines.append(f"  All depth map frames are present.\n")

        if empty_frames:
            print(f"  [EMPTY] {len(empty_frames)} depth map frames: {empty_frames}")
            log_lines.append(f"  [EMPTY] {len(empty_frames)} depth map frames: {empty_frames}\n")
        else:
            print(f"  All depth map frames are not empty.")
            log_lines.append(f"  All depth map frames are not empty.\n")
            
        if unreadable_frames:
            print(f"  [UNREADABLE] {len(unreadable_frames)} depth map frames: {unreadable_frames}")
            log_lines.append(f"  [UNREADABLE] {len(unreadable_frames)} depth map frames: {unreadable_frames}\n")
        else:
            print(f"  All depth map frames are readable.")
            log_lines.append(f"  All depth map frames are readable.\n")

    print("\nSanity check completed.")
    log_lines.append("\nSanity check completed.\n")

    # Save log to a file
    with open(output_log_file, "w") as log_file:
        log_file.writelines(log_lines)
    print(f"Sanity check log saved to {output_log_file}")


if __name__ == "__main__":
    """
    Command-line interface for the numpy array dataset sanity check tool.

    Usage:
        python dataset_sanity_check_npy.py --base_dir /path/to/dataset --total_frames 1000 
            [--output_log log.txt] [--camera_folder _World_Cameras_Camera_01]

    The script will:
    1. Check all numpy array files in the dataset for completeness and readability
    2. Generate a detailed log of any issues found
    3. Save the results to the specified output log file
    4. Optionally focus on a specific camera folder
    """
    parser = argparse.ArgumentParser(description="Perform a sanity check on the dataset.")
    parser.add_argument("--base_dir", type=str, required=True, help="Path to the dataset containing camera folders.")
    parser.add_argument("--total_frames", type=int, required=True, help="Total number of expected frames per camera.")
    parser.add_argument("--output_log", type=str, required=None, help="Path to save the sanity check log file.")
    parser.add_argument("--camera_folder", type=str, required=False, help="Specific camera folder to check (e.g., '_World_Cameras_Camera_01').")
    args = parser.parse_args()

    # Set default output log file if not provided
    if args.output_log is None:
        args.output_log = os.path.join(args.base_dir, "sanity_depth_map_check_log.txt")

    sanity_check(args.base_dir, args.total_frames, args.output_log, args.camera_folder)