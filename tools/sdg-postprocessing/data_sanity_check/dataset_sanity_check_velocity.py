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
import json
import math
import argparse
import numpy as np
from typing import Any, Dict, List

def calculate_speed(frame1: List[Dict[str, Any]], frame2: List[Dict[str, Any]], speeds: Dict[str, List[float]], step: int, fps: int = 30) -> None:
    """
    Calculate the speed of objects between two frames.

    Args:
        frame1 (list): First frame data containing object locations
        frame2 (list): Second frame data containing object locations
        speeds (dict): Dictionary to store calculated speeds (modified in-place)
        step (int): Number of frames between frame1 and frame2
        fps (int, optional): Frames per second of the dataset. Defaults to 30.

    The function calculates:
    1. Horizontal speed in the XY-plane (Euclidean distance)
    2. Vertical speed along the Z-axis (absolute difference)

    The speeds dictionary is updated with entries in the format:
    speeds[object_id] = [horizontal_speed, vertical_speed] (in meters/second)

    Example:
        >>> frame1 = [{'object name': 'obj1', '3d location': [0, 0, 0]}]
        >>> frame2 = [{'object name': 'obj1', '3d location': [1, 1, 1]}]
        >>> speeds = {}
        >>> calculate_speed(frame1, frame2, speeds, 1, 30)
        >>> print(speeds)  # {'obj1': [1.414, 1.0]}  # m/s
    """
    id_to_location1 = {
        data_list['object name']: np.array(data_list["3d location"])
        for data_list in frame1 if "3d location" in data_list
    }

    id_to_location2 = {
        data_list['object name']: np.array(data_list["3d location"])
        for data_list in frame2 if "3d location" in data_list
    }

    for char_id, loc1 in id_to_location1.items():
        if char_id in id_to_location2:
            loc2 = id_to_location2[char_id]
            distance = math.sqrt((loc2[0] - loc1[0]) ** 2 + (loc2[1] - loc1[1]) ** 2)
            z_distance = abs(loc2[2] - loc1[2])
            id = char_id
            speeds[id] = [distance / (step/fps), z_distance / (step/fps)]

def find_frames_with_large_velocity(json_path: str, threshold: float = 5.0) -> None:
    """
    Analyze a JSON file to find frames where objects exceed a velocity threshold.

    Args:
        json_path (str): Path to the JSON file containing velocity data
        threshold (float, optional): Maximum allowed velocity in m/s. Defaults to 5.0.

    The function prints:
    - 🟥 Frame number for frames containing high velocities
    - 🔹 Object IDs and their velocities that exceed the threshold
    - ✅ Confirmation message if no high velocities are found

    Example output:
        🟥 Frame: 42
          🔹 object_1: [4.5, 0.2]
        
        ✅ No large velocity found in any frame.
    """
    with open(json_path, 'r') as f:
        data = json.load(f)

    any_match_found = False

    for frame_key, obj_dict in data.items():
        match_found = False
        results = []

        for obj_path, values in obj_dict.items():
            for v in values:
                if isinstance(v, float) and 'e' not in str(v).lower() and v > threshold:
                    results.append((obj_path, values))
                    match_found = True
                    any_match_found = True
                    break

        if match_found:
            print(f"\n🟥 Frame: {frame_key}")
            for path, val in results:
                print(f"  🔹 {path}: {val}")

    if not any_match_found:
        print("✅ No large velocity found in any frame.")

def process_frames(frame_files: Dict[str, List[Dict[str, Any]]], step: int = 30) -> Dict[str, Dict[str, List[float]]]:
    """
    Process multiple frames to calculate object speeds between consecutive frames.

    Args:
        frame_files (dict): Dictionary containing frame data
        step (int, optional): Number of frames to skip between speed calculations. 
                            Defaults to 30.

    Returns:
        dict: Dictionary containing speeds for each frame interval with format:
            {
                "Frame X -> Frame Y": {
                    "object_id": [horizontal_speed, vertical_speed],
                    ...
                },
                ...
            }

    The function:
    1. Iterates through frames with the specified step size
    2. Calculates speeds between consecutive frame pairs
    3. Stores results in a structured dictionary

    Example:
        >>> frames = {
        ...     '0': [{'object name': 'obj1', '3d location': [0, 0, 0]}],
        ...     '1': [{'object name': 'obj1', '3d location': [1, 1, 1]}]
        ... }
        >>> speeds = process_frames(frames, step=1)
        >>> print(speeds)
        {'Frame 0 -> Frame 1': {'obj1': [1.414, 1.0]}}
    """
    speeds_per_frame = {}
    for i in range(0, len(frame_files) - step, step):
        frame1 = gt_data[str(i)]
        frame2 = gt_data[str(i+1)]

        speeds = {}
        calculate_speed(frame1, frame2, speeds, step)
        
        speeds_per_frame[f"Frame {i} -> Frame {i+step}"] = speeds

    return speeds_per_frame


if __name__ == "__main__":
    """
    Command-line interface for the velocity sanity check tool.

    This script analyzes object velocities in a dataset to identify potentially
    unrealistic movements.

    Usage:
        python dataset_sanity_check_velocity.py --gt_dir /path/to/ground_truth.json [--step 1]

    The script will:
    1. Load ground truth data containing object positions
    2. Calculate velocities between frames
    3. Save the velocity data to a JSON file
    4. Check for and report any velocities exceeding the threshold (5.0 m/s)

    Arguments:
        --gt_dir: Path to the ground truth JSON file
        --step: Frame step size for velocity calculations (default: 1)
    """
    parser = argparse.ArgumentParser(description="Perform a sanity check on the dataset.")
    parser.add_argument("--gt_dir", type=str, required=True, help="Path to the ground truth JSON file.")
    parser.add_argument("--step", type=int, default=1, help="Step size for calculating speeds.")
    args = parser.parse_args()

    base_dir = os.path.dirname(args.gt_dir)
    gt_file = args.gt_dir
    step = args.step

    with open(gt_file, 'r') as f:
        gt_data = json.load(f)

    speeds = process_frames(gt_data, step=step)

    output_path = os.path.join(base_dir, f"character_speeds_{step}.json")
    with open(output_path, "w") as f:
        json.dump(speeds, f, indent=4)

    print(f"Speeds calculated and saved to {output_path}")

    # Check velocity for each object in each frame
    find_frames_with_large_velocity(output_path, threshold=5.0) # default threshold is less than 5.0 m/s
