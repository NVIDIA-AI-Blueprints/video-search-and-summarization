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
Ground Truth Data Loader Module

This module provides utilities for loading and processing ground truth annotations
from multi-camera scene datasets. It supports both 2D and 3D ground truth data with
various bounding box formats and coordinate systems.

Key Features:
- Load 2D detections from ground truth JSON files
- Extract full or visible bounding boxes for each person
- Load 3D ground truth with world coordinates and dimensions
- Support for multi-camera scenes with per-camera ground truth
- Handle person ID tracking across cameras and frames
- Process both visible and occluded objects

Main Functions:
- load_det_2d_from_gt_scene: Load 2D bounding box detections from ground truth
- load_det_3d_from_gt_scene: Load 3D object annotations from ground truth

2D Ground Truth Format:
- Per-camera JSON files with frame-level annotations
- Bounding boxes: [xmin, ymin, xmax, ymax] in pixel coordinates
- Two modes: "full bounding box" or "visible bounding box"
- Includes person ID for multi-object tracking evaluation

3D Ground Truth Format:
- World coordinates [x, y, z] for object center
- 3D bounding box dimensions [width, length, height]
- Support for both full scene and per-camera annotations
- Object rotation and orientation information

Data Structure:
- 2D: {camera_name: {frame_id: [['person', bbox, confidence, person_id], ...]}}
- 3D: {frame_id: [{'person id', '3d location', '3d dimensions', ...}, ...]}

Use Cases:
- Evaluation of 2D/3D detection and tracking algorithms
- Multi-camera association validation
- Training data preparation for deep learning models
- Visualization and debugging of camera systems

Typical Workflow:
1. Load ground truth for a scene directory
2. Access annotations by camera name and frame ID
3. Extract bounding boxes and person IDs
4. Use for evaluation metrics or visualization
"""

import os
import json
import pickle

import numpy as np

from spatialai_data_utils.constants import (
    KEY_BBOX3D,
    KEY_CONFIDENCE,
    KEY_COORDINATES,
    KEY_FRAME_IDX,
    KEY_NVSCHEMA_ID,
    KEY_TYPE,
)
from spatialai_data_utils.datasets.scenes import get_cam_names_in_scene

# Sentinel sensor key for world-space (non-per-sensor) detections.  Used when
# the data source provides a single set of detections shared across cameras
# (e.g. ground-truth boxes in world coordinates) — consumed by the
# visualization pipeline's "single sensor key" fall-through.
GT_WORLD_SENSOR_KEY = "world"


def load_det_2d_from_gt_scene(scene_dir, mode="full bounding box"):
    """
    Load 2D detections derived from ground truth data for a scene.

    Reads `ground_truth.json` for each camera in the scene and extracts
    either the "full bounding box" or "visible bounding box" along with the
    person ID. Formats the output for use in tasks like 2D association evaluation.

    :param scene_dir: Path to the scene directory.
    :type scene_dir: str
    :param mode: Specifies which bounding box to load: "full bounding box" or
                 "visible bounding box". Defaults to "full bounding box".
    :type mode: str, optional
    :return: A dictionary mapping camera names to dictionaries, which map frame IDs
             to lists of detections. Each detection is a list:
             ['person', [xmin, ymin, xmax, ymax], confidence (1.0), person_id].
    :rtype: dict
    """
    gt_det2d_dict = {}
    cam_names = get_cam_names_in_scene(scene_dir)

    for cam_name in cam_names:
        if cam_name not in gt_det2d_dict:
            gt_det2d_dict[cam_name] = {}
        with open(os.path.join(scene_dir, cam_name, "ground_truth.json")) as f:
            gt_json_aicity_dict = json.load(f)
        for frame_id_str in gt_json_aicity_dict.keys():
            frame_id = int(frame_id_str)
            det2d_list = []
            for gt in gt_json_aicity_dict[frame_id_str]:
                det2d_list.append(["person", gt[mode], 1.0, gt["person id"]])
            gt_det2d_dict[cam_name][frame_id] = det2d_list

    return gt_det2d_dict


def load_det_3d_from_gt_scene(scene_dir, mode="aic24"):
    """
    Load 3D detections derived from ground truth data for a scene.

    Reads `ground_truth_bevformer.json` (which contains 3D annotations)
    for the scene. Extracts 3D location, scale, rotation (converted to yaw),
    confidence (1.0), and person ID for each annotation in each frame.

    :param scene_dir: Path to the scene directory.
    :type scene_dir: str
    :param mode: Specifies which bounding box to load: "aic24" or "aic25".
                 Defaults to "aic24".
    :type mode: str, optional
    :return: A dictionary mapping frame IDs (int) to lists of 3D detections.
             Each detection is a list:
             [type, [x, y, z, w, l, h, yaw_rad], confidence (1.0), person_id].
    :rtype: dict
    """
    gt_det3d_dict = {}

    if mode == "aic24":
        gt_json_aicity_path = os.path.join(scene_dir, "ground_truth_bevformer.json")
    elif mode == "aic25":
        gt_json_aicity_path = os.path.join(scene_dir, "ground_truth.json")
    else:
        raise ValueError(f"Invalid mode: {mode}")

    with open(gt_json_aicity_path) as f:
        ground_truths_dict = json.load(f)

    for (
        frame_id_str
    ) in ground_truths_dict.keys():  # no annotations in the first two frames
        frame_id = int(frame_id_str)
        det3d_list = []
        for anno in ground_truths_dict[frame_id_str]:
            gt_box = anno["3d location"] + anno["3d bounding box scale"]
            gt_box.append(np.radians(anno["3d bounding box rotation"][2]))
            if mode == "aic24":
                det3d_list.append(
                    [anno["type"], gt_box, anno["confidence"], anno["person id"]]
                )
            elif mode == "aic25":
                det3d_list.append(
                    [
                        anno["object type"],
                        gt_box,
                        anno.get("confidence", 1.0),
                        anno["object id"],
                    ]
                )
        gt_det3d_dict[frame_id] = det3d_list

    return gt_det3d_dict


def load_gt_from_txt_scene(scene_dir):
    """
    Load ground truth data from a `ground_truth.txt` file for a scene.

    Parses the text file format:
    <camera_id> <obj_id> <frame_id> <xmin> <ymin> <width> <height> <xworld> <yworld>
    Organizes the data into a nested dictionary structure.

    :param scene_dir: Path to the scene directory containing `ground_truth.txt`.
    :type scene_dir: str
    :return: A dictionary mapping frame IDs to dictionaries, which map camera IDs
             to lists of ground truth object dictionaries. Each object dict contains
             'person id', 'visible bounding box', and '3d location' (z set to 0).
    :rtype: dict
    """
    gt_txt_path = os.path.join(scene_dir, "ground_truth.txt")
    gt_dict = {}
    with open(gt_txt_path, "r") as f:
        for line in f:
            cam_id, obj_id, frame_id, xmin, ymin, width, height, xworld, yworld = (
                line.split()
            )
            obj_id = int(obj_id)
            frame_id = int(frame_id)
            xmin, ymin, width, height = int(xmin), int(ymin), int(width), int(height)
            xworld, yworld = float(xworld), float(yworld)

            obj_dict = {
                "person id": obj_id,
                "visible bounding box": [xmin, ymin, xmin + width, ymin + height],
                "3d location": [xworld, yworld, 0],
            }
            if frame_id not in gt_dict:
                gt_dict[frame_id] = {}
            if cam_id not in gt_dict[frame_id]:
                gt_dict[frame_id][cam_id] = []
            gt_dict[frame_id][cam_id].append(obj_dict)

    return gt_dict


def process_bbox3d_gt(label):
    """Convert a ground-truth label dict to the canonical 9-DoF box layout.

    Extracts location, scale, and the full ``[pitch, roll, yaw]``
    rotation triple from the ground-truth dictionary and emits the
    NVSchema ``Bbox3d.coordinates`` order
    ``[x, y, z, w, l, h, pitch, roll, yaw]``.

    :param label: Dictionary with ``"3d location"``,
        ``"3d bounding box scale"``, and ``"3d bounding box rotation"``
        (a 3-element ``[pitch, roll, yaw]`` list of Euler angles,
        representing rotations about world X / Y / Z respectively).
    :type label: dict
    :return: 9-element list ``[x, y, z, w, l, h, pitch, roll, yaw]``
        suitable for feeding into
        :func:`spatialai_data_utils.core.boxes.box_3d.box3d_to_corners` /
        :func:`spatialai_data_utils.core.geometry.projection.project_boxes_3d_to_2d`.
    :rtype: list
    """
    return [
        label["3d location"][0],
        label["3d location"][1],
        label["3d location"][2],
        label["3d bounding box scale"][0],
        label["3d bounding box scale"][1],
        label["3d bounding box scale"][2],
        label["3d bounding box rotation"][0],  # pitch (rot about X)
        label["3d bounding box rotation"][1],  # roll  (rot about Y)
        label["3d bounding box rotation"][2],  # yaw   (rot about Z)
    ]


def _gt_box_to_nvschema_obj(box7, name, track_id):
    """Wrap a 7-DoF GT box into a raw NVSchema object dict.

    Pads the 7-DoF ``[x, y, z, w, l, h, yaw]`` box to the 9-value
    NVSchema ``bbox3d.coordinates`` convention by inserting zero
    pitch/roll: ``[x, y, z, w, l, h, 0, 0, yaw]``.  Confidence is set
    to 1.0 for ground-truth annotations.

    :param box7: 7-element sequence ``[x, y, z, w, l, h, yaw]``.
    :param name: Class name string (e.g. ``"person"``).
    :param track_id: Instance/track index (int or int-like).
    :returns: NVSchema-shaped dict suitable for
        :func:`spatialai_data_utils.core.geometry.projection.project_bev_objects_bbox_in_image`.
    """
    x, y, z, w, length, h, yaw = (float(v) for v in box7)
    return {
        KEY_NVSCHEMA_ID: str(int(track_id)),
        KEY_TYPE: str(name),
        KEY_CONFIDENCE: 1.0,
        "coordinate": {"x": x, "y": y, "z": z},
        KEY_BBOX3D: {
            KEY_COORDINATES: [x, y, z, w, length, h, 0.0, 0.0, yaw],
            "embedding": [{}],
            KEY_CONFIDENCE: 1.0,
        },
    }


def load_gt_from_pkl(pkl_path):
    """Load ground-truth 3D annotations from a sparse4d-style data pkl.

    The pkl is expected to follow the ``{"infos": [...], "metadata": {...}}``
    schema used by the sparse4d / TAO_Sparse4D pipeline.  Each per-frame
    ``info`` dict carries:

    * ``frame_idx``  — integer frame id.
    * ``gt_boxes``   — ``(N, 7)`` array of ``[x, y, z, w, l, h, yaw]``.
    * ``gt_names``   — ``(N,)`` class-name strings.
    * ``instance_inds`` — ``(N,)`` stable track ids (used as object id).
    * ``valid_flag`` — ``(N,)`` bool mask (invalid entries are dropped).

    Boxes are emitted as raw **NVSchema** object dicts (9-value
    ``bbox3d.coordinates`` with ``roll = pitch = 0``) so they can be fed
    straight into the stage-1/stage-2 visualization pipeline.  Because
    GT lives in world space (not per-sensor), each frame's list is
    wrapped under a single sentinel sensor key
    (``GT_WORLD_SENSOR_KEY``) which the frame driver fans out to every
    camera via its single-sensor fall-through.

    .. warning::
       **Security**: this function calls :func:`pickle.load` on the
       user-supplied path.  Pickle deserialization can execute
       arbitrary code (`CWE-502
       <https://cwe.mitre.org/data/definitions/502.html>`_).
       **Only load ``.pkl`` files from trusted sources.**  If you're
       consuming pkl files produced by someone else's training
       pipeline, verify the file's SHA-256 / provenance before calling
       this function.  A future release will migrate this format to a
       safer on-disk container (JSON / HDF5 / protobuf) — see the
       ``TODO`` below the ``pickle.load`` call.

    :param pkl_path: Path to the ``.pkl`` file.
    :type pkl_path: str
    :returns: ``{frame_idx (int): {"world": [nvschema_obj, ...]}}`` mapping.
    :rtype: dict[int, dict[str, list[dict]]]
    :raises KeyError: If any ``info`` is missing ``frame_idx``.
    """
    # TODO(security): migrate this format off ``pickle`` to a safe
    # container (JSON / HDF5 / protobuf).  pickle.load executes
    # arbitrary code on malicious input — see the docstring warning
    # and the :func:`spatialai_data_utils.loaders.calibration.load_calib_into_dict_from_pkl`
    # counterpart (both read the same underlying file and should
    # migrate together).
    with open(pkl_path, "rb") as fh:
        data = pickle.load(fh)
    infos = data["infos"]

    scene_results = {}
    for i, info in enumerate(infos):
        if KEY_FRAME_IDX not in info:
            raise KeyError(
                f"pkl info entry at index {i} is missing required "
                f"'{KEY_FRAME_IDX}' field."
            )
        frame_idx = int(info[KEY_FRAME_IDX])

        gt_boxes = np.asarray(info.get("gt_boxes", np.empty((0, 7))))
        gt_names = info.get("gt_names", np.empty((0,), dtype=object))
        instance_inds = info.get(
            "instance_inds", np.arange(len(gt_boxes), dtype=np.int64),
        )
        valid_flag = info.get("valid_flag")

        objs = []
        for j, box in enumerate(gt_boxes):
            if valid_flag is not None and not bool(valid_flag[j]):
                continue
            objs.append(
                _gt_box_to_nvschema_obj(box, gt_names[j], instance_inds[j])
            )

        scene_results[frame_idx] = {GT_WORLD_SENSOR_KEY: objs}

    return scene_results
