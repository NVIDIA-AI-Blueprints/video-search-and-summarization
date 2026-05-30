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

"""
Sparse4D Results Loader Module

This module provides utilities for loading and processing 3D detection and tracking results
from Sparse4D models. Sparse4D is a sparse 3D object detector for autonomous driving that
processes multi-camera inputs to produce 3D bounding boxes in world coordinates.

Key Features:
- Load Sparse4D results from JSON files
- Parse 3D detections with location, size, and orientation
- Support for both detection and tracking modes
- Convert quaternion rotations to Euler angles
- Organize results by scene and frame
- Handle dimension swapping for different coordinate conventions
- Extract confidence scores and object classes

Main Functions:
- load_sparse4d_raw_json: Load raw Sparse4D detection/tracking results

Data Format:
- Input: Standard Sparse4D JSON output format
- Each result contains: translation (x, y, z), size (w, l, h), rotation (quaternion)
- Additional fields: scores, labels, tracking IDs (if tracking mode)
- Output: Organized by scene and frame in AICity GT-compatible format

Object Representation:
- 3D location: World coordinates [x, y, z]
- 3D bounding box dimensions: [width, length, height]
- 3D rotation: Converted from quaternion to Euler angles [pitch, roll, yaw]
- Confidence: Detection or tracking score
- Type: Object class (e.g., 'car', 'pedestrian', 'cyclist')

Detection vs Tracking Mode:
- Detection: Uses detection_score, detection_name, assigns sequential IDs
- Tracking: Uses tracking_id, tracking_score, tracking_name for temporal consistency

Coordinate Systems:
- Translation: World coordinates in meters
- Rotation: Quaternion [w, x, y, z] converted to Euler angles [pitch, roll, yaw]
- Size: Object dimensions [width, length, height]

Use Cases:
- Evaluate Sparse4D 3D detection performance
- Load tracking results for temporal analysis
- Convert Sparse4D outputs to standard evaluation formats
- Visualize 3D bounding boxes
- Compare with ground truth annotations

Typical Workflow:
1. Run Sparse4D inference on multi-camera data
2. Load results using load_sparse4d_raw_json
3. Access detections/tracks by scene and frame
4. Extract 3D boxes and metadata
5. Use for evaluation or visualization
"""

import os
import json
import numpy as np
import tqdm

from spatialai_data_utils.datasets.scenes import get_scene_info_from_token
from spatialai_data_utils.core.geometry.rotation import euler_from_quaternion


def load_sparse4d_raw_json(json_path, tracking=False):
    """
    Load raw Sparse4D detection/tracking results from a JSON file.

    Parses the standard JSON output format from Sparse4D, extracts relevant fields
    (translation, size, rotation, score, name, tracking_id), handles potential
    dimension swapping (size[1], size[0], size[2]), converts rotation to Euler angles,
    and organizes results by scene and frame ID in a format similar to AICity GT.
    Distinguishes between detection and tracking results based on the `tracking` flag.

    :param json_path: Path to the input raw Sparse4D JSON file.
    :type json_path: str
    :param tracking: If True, extracts tracking_id, tracking_score, tracking_name.
                     If False, uses detection_score, detection_name, and assigns sequential IDs.
                     Defaults to False.
    :type tracking: bool, optional
    :return: A dictionary mapping scene names to dictionaries, which map frame IDs
             to lists of detection/track dictionaries in AICity GT format.
    :rtype: dict
    """
    print(f"loading Sparse4DRAW results from {json_path} ...")
    with open(json_path) as f:
        results_dict = json.load(f)

    results_dict_new = {}
    for sample_token in tqdm.tqdm(results_dict["results"].keys()):
        scene_name, frame_id = get_scene_info_from_token(sample_token)
        if scene_name not in results_dict_new:
            results_dict_new[scene_name] = {}

        det_dict_new_list = []
        for det_id, det_dict in enumerate(results_dict["results"][sample_token]):
            # convert to aic24 gt json format
            translation = det_dict["translation"]
            size = det_dict["size"]
            size = [size[1], size[0], size[2]]
            euler = euler_from_quaternion(*det_dict["rotation"])
            if tracking:
                obj_id = int(det_dict["tracking_id"])
                obj_score = det_dict["tracking_score"]
                obj_type = det_dict["tracking_name"]
            else:
                obj_id = det_id
                obj_score = det_dict["detection_score"]
                obj_type = det_dict["detection_name"]
            det_dict_new = {
                "person id": obj_id,
                "3d location": translation,
                "3d bounding box scale": size,
                "3d bounding box rotation": euler,
                "confidence": obj_score,
                "type": obj_type,
            }
            det_dict_new_list.append(det_dict_new)
        results_dict_new[scene_name][frame_id] = det_dict_new_list

    return results_dict_new


def load_sparse4d_det_3d_scene(json_path, scene_name=None):
    """
    Load and process 3D detections for a specific scene from a raw Sparse4D JSON file.

    Loads the raw JSON using `load_sparse4d_raw_json` (in detection mode),
    optionally filters by `scene_name`, and converts the detections for each frame
    into a list format:
    [class_type, [x, y, z, w, l, h, yaw_rad], confidence, object_id].

    :param json_path: Path to the input raw Sparse4D JSON file.
    :type json_path: str
    :param scene_name: Optional name of the scene to load. If None, assumes the JSON
                       contains only one scene. Defaults to None.
    :type scene_name: str, optional
    :return: A dictionary mapping frame IDs (int) to lists of processed 3D detections
             for the specified scene.
    :rtype: dict
    :raises AssertionError: If `scene_name` is None and the JSON contains more than one scene.
    """
    det_3d_dict = load_sparse4d_raw_json(json_path)
    if scene_name is not None:
        det_3d_dict = det_3d_dict[scene_name]
    else:
        assert len(det_3d_dict) == 1  # not contains one scene
        det_3d_dict = det_3d_dict[list(det_3d_dict.keys())[0]]

    det_3d_dict_new = {}
    for frame_id_str in det_3d_dict.keys():  # no annotations in the first two frames
        frame_id = int(frame_id_str)
        det3d_list = []
        for anno in det_3d_dict[frame_id_str]:
            gt_box = anno["3d location"] + anno["3d bounding box scale"]
            gt_box.append(np.radians(anno["3d bounding box rotation"][2]))
            det3d_list.append(
                [anno["type"], gt_box, anno["confidence"], anno["person id"]]
            )
        det_3d_dict_new[frame_id] = det3d_list

    return det_3d_dict_new


def load_sparse4d_json(json_path):
    """
    Load post-processed Sparse4D results from a per-scene JSON file.

    Assumes the JSON file contains results for a single scene, structured as a
    dictionary mapping frame ID (string) to a list of detection/track dictionaries.
    Converts frame ID keys from strings to integers.

    :param json_path: Path to the post-processed JSON file for a single scene.
    :type json_path: str
    :return: A dictionary mapping frame IDs (int) to lists of detection/track dictionaries.
             Returns None if the file does not exist.
    :rtype: dict or None
    """
    print(f"loading Sparse4DPP result from {json_path} ...")
    if not os.path.exists(json_path):
        print("json file is not exist!")
        return

    with open(json_path) as f:
        results_json_dict = json.load(f)

    results_dict_new = {}
    for frame_id_str in results_json_dict.keys():
        frame_id = int(frame_id_str)
        results_dict_new[frame_id] = results_json_dict[frame_id_str]

    return results_dict_new


def load_sparse4d_jsons(json_dir, scene_names):
    """
    Load post-processed Sparse4D results for multiple scenes from a directory.

    Iterates through `scene_names`, constructs the expected JSON file path for each scene
    within `json_dir`, loads each scene's data using `load_sparse4d_json`, and
    combines them into a single dictionary.

    :param json_dir: Path to the directory containing per-scene post-processed JSON files.
    :type json_dir: str
    :param scene_names: A list of scene names to load.
    :type scene_names: list[str]
    :return: A dictionary mapping scene names to the loaded results dictionaries
             (output of `load_sparse4d_json` for each scene).
    :rtype: dict
    """
    print(f"loading Sparse4DPP results from {json_dir} ...")

    results_dict_new = {}
    for scene_name in scene_names:
        json_path = os.path.join(json_dir, scene_name + ".json")
        results_dict_scene = load_sparse4d_json(json_path)
        if results_dict_scene:
            results_dict_new[scene_name] = results_dict_scene

    return results_dict_new
