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
import json
import math
from typing import Any, Dict, Optional

def sanity_check(dataset_path: str, total_frames: int, output_log_file: str, camera_folder: Optional[str]) -> None:
    """
    Perform a dataset sanity check on object detection JSON files.

    This function verifies the integrity of object detection JSON files in a dataset by:
    - Checking for missing files in the sequence
    - Verifying that each file is readable as JSON
    - Checking for empty JSON data
    - Checking for overflow values in bounding boxes
    - Logging any issues found

    :param str dataset_path: Path to the root directory containing camera folders
    :param int total_frames: Expected number of frames per camera
    :param str output_log_file: Path where the log file should be saved
    :param str camera_folder: Optional specific camera folder to check (e.g., '_World_Cameras_Camera_01')
    :return: None
    :raises: No explicit exceptions, but logs errors for missing/unreadable files

    Examples::
        >>> # Check all cameras
        >>> sanity_check('/data/dataset', 1000, 'sanity_check.log', None)
        >>> # Check specific camera
        >>> sanity_check('/data/dataset', 1000, 'sanity_check.log', '_World_Cameras_Camera_01')
    """
    log_lines = []  # List to store log lines for writing to a file

    # List all camera directories
    if camera_folder:
        camera_dirs = [os.path.join(dataset_path, camera_folder)]
    else:
        camera_dirs = [os.path.join(dataset_path, d) for d in os.listdir(dataset_path) if d.startswith("_World_Cameras_Camera")]
    camera_dirs.sort()
    
    print(f"Found {len(camera_dirs)} camera directories.")
    log_lines.append(f"Found {len(camera_dirs)} camera directories.\n")

    for camera_dir in camera_dirs:
        rgb_folder = os.path.join(camera_dir, "object_detection")
        
        if not os.path.exists(rgb_folder):
            print(f"[WARN] Missing 'object_detection' folder in {camera_dir}. Skipping.")
            log_lines.append(f"[WARN] Missing 'object_detection' folder in {camera_dir}. Skipping.\n")
            continue
        
        print(f"\nChecking {camera_dir}...")
        log_lines.append(f"\nChecking {camera_dir}...\n")

        # Frame template and frame index
        frame_template = "object_detection_{:05d}.json"
        missing_frames = []
        unreadable_frames = []
        empty_frames = []
        overflow_frames = []  # Frames with overflow values

        for idx in range(total_frames):
            frame_name = frame_template.format(idx)
            frame_path = os.path.join(rgb_folder, frame_name)

            # Check if the frame exists
            if not os.path.exists(frame_path):
                missing_frames.append(frame_name)
                continue

            # Check if the frame is readable
            try:
                with open(frame_path, 'r') as f:
                    json_data = json.load(f)
                if json_data is None:
                    empty_frames.append(frame_name)
                else:
                    # Check for overflow values in bounding boxes
                    has_overflow = check_for_overflow(json_data)
                    if has_overflow:
                        overflow_frames.append(frame_name)
            except Exception as e:
                unreadable_frames.append(frame_name)

        # Report missing and unreadable frames
        if missing_frames:
            print(f"  [MISSING] {len(missing_frames)} object detection frames: {missing_frames[:5]}{'...' if len(missing_frames) > 5 else ''}")
            log_lines.append(f"  [MISSING] {len(missing_frames)} object detection frames: {missing_frames}\n")
        else:
            print(f"  All object detection frames are present.")
            log_lines.append(f"  All object detection frames are present.\n")
        
        if empty_frames:
            print(f"  [EMPTY] {len(empty_frames)} object detection frames: {empty_frames[:5]}{'...' if len(empty_frames) > 5 else ''}")
            log_lines.append(f"  [EMPTY] {len(empty_frames)} object detection frames: {empty_frames}\n")
        else:
            print(f"  All object detection frames are not empty.")
            log_lines.append(f"  All object detection frames are not empty.\n")

        if unreadable_frames:
            print(f"  [UNREADABLE] {len(unreadable_frames)} object detection frames: {unreadable_frames[:5]}{'...' if len(unreadable_frames) > 5 else ''}")
            log_lines.append(f"  [UNREADABLE] {len(unreadable_frames)} object detection frames: {unreadable_frames}\n")
        else:
            print(f"  All object detection frames are readable.")
            log_lines.append(f"  All object detection frames are readable.\n")
            
        if overflow_frames:
            print(f"  [OVERFLOW] {len(overflow_frames)} object detection frames with extreme values: {overflow_frames[:5]}{'...' if len(overflow_frames) > 5 else ''}")
            log_lines.append(f"  [OVERFLOW] {len(overflow_frames)} object detection frames with extreme values: {overflow_frames}\n")
        else:
            print(f"  No overflow values detected in object detection frames.")
            log_lines.append(f"  No overflow values detected in object detection frames.\n")

    print("\nSanity check completed.")
    log_lines.append("\nSanity check completed.\n")

    # Save log to a file
    with open(output_log_file, "w") as log_file:
        log_file.writelines(log_lines)
    print(f"Sanity check log saved to {output_log_file}")


def check_for_overflow(json_data: Dict[str, Any]) -> bool:
    """
    Check for overflow values in bounding box coordinates and transform matrices.
    
    This function examines object detection data for unreasonable values that might
    indicate numerical overflow or other data corruption issues.

    :param dict json_data: JSON data from the object detection file
    :return: True if overflow values are detected, False otherwise
    :rtype: bool

    Examples::
        >>> data = {
        ...     'obj1': {
        ...         'bbox': {
        ...             'annotators': {
        ...                 'bounding_box_3d_fast': {
        ...                     'x_min': 1e20,  # overflow value
        ...                     'transform': [[1, 0], [0, 1]]
        ...                 }
        ...             }
        ...         }
        ...     }
        ... }
        >>> has_overflow = check_for_overflow(data)
        >>> print(has_overflow)  # True
    """
    max_allowed_value = 1e10  # Set a reasonable threshold
    
    # Check each object in the JSON data
    for key_obj in json_data:
        for key, object in json_data[key_obj].items():
            if "annotators" in object and "bounding_box_3d_fast" in object["annotators"]:
                bbox_3d = object["annotators"]["bounding_box_3d_fast"]
                
                # Check bounding box coordinates
                for coord in ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"]:
                    if coord in bbox_3d and abs(bbox_3d[coord]) > max_allowed_value:
                        return True
                
                # Check transform matrix
                if "transform" in bbox_3d:
                    # Flatten the nested list
                    flat_transform = [item for sublist in bbox_3d["transform"] for item in sublist]
                    if any(math.isinf(x) for x in flat_transform) or any(abs(x) > max_allowed_value for x in flat_transform):
                        return True
    
    return False


if __name__ == "__main__":
    """
    Command-line interface for the object detection JSON dataset sanity check tool.

    Usage:
        python dataset_sanity_check_json.py --base_dir /path/to/dataset --total_frames 1000 
            [--output_log log.txt] [--camera_folder _World_Cameras_Camera_01]

    The script will:
    1. Check all object detection JSON files in the dataset for:
       - Completeness
       - JSON readability
       - Empty data
       - Overflow values in bounding boxes
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
        args.output_log = os.path.join(args.base_dir, "sanity_object_detection_check_log.txt")

    sanity_check(args.base_dir, args.total_frames, args.output_log, args.camera_folder)