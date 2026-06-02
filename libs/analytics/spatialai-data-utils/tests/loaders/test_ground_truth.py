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

"""Tests for ``loaders.ground_truth``.

Covers all six public loaders / helpers:

* ``load_det_2d_from_gt_scene`` (full + visible bbox modes)
* ``load_det_3d_from_gt_scene`` (aic24 + aic25 modes + bad-mode raise)
* ``load_gt_from_txt_scene`` (txt parser, frame/cam grouping)
* ``process_bbox3d_gt`` (pure 9-DoF unpacker)
* ``_gt_box_to_nvschema_obj`` (private wrapper, used by load_gt_from_pkl)
* ``load_gt_from_pkl`` (pickle path with valid_flag mask)
"""

import json
import math
import os
import pickle

import numpy as np
import pytest

from spatialai_data_utils.loaders.ground_truth import (
    GT_WORLD_SENSOR_KEY,
    _gt_box_to_nvschema_obj,
    load_det_2d_from_gt_scene,
    load_det_3d_from_gt_scene,
    load_gt_from_pkl,
    load_gt_from_txt_scene,
    process_bbox3d_gt,
)


# ---------------------------------------------------------------------------
# load_det_2d_from_gt_scene
# ---------------------------------------------------------------------------


def _write_per_cam_gt2d(scene_dir, cam_to_frames):
    """``cam_to_frames`` = ``{"Camera_01": {"0": [annotation_dict, ...]}}``"""
    for cam, frames in cam_to_frames.items():
        cam_dir = scene_dir / cam
        cam_dir.mkdir(parents=True, exist_ok=True)
        (cam_dir / "ground_truth.json").write_text(json.dumps(frames))


def test_load_det_2d_full_bounding_box_mode(tmp_path):
    scene_dir = tmp_path / "scene"
    _write_per_cam_gt2d(scene_dir, {
        "Camera_01": {
            "0": [{
                "full bounding box": [10, 20, 110, 220],
                "visible bounding box": [15, 25, 105, 215],
                "person id": 42,
            }],
            "1": [{
                "full bounding box": [11, 21, 111, 221],
                "visible bounding box": [16, 26, 106, 216],
                "person id": 42,
            }],
        },
    })
    out = load_det_2d_from_gt_scene(str(scene_dir))
    assert set(out.keys()) == {"Camera_01"}
    # Frame ids cast to int.
    assert set(out["Camera_01"].keys()) == {0, 1}
    det = out["Camera_01"][0][0]
    assert det == ["person", [10, 20, 110, 220], 1.0, 42]


def test_load_det_2d_visible_bounding_box_mode(tmp_path):
    scene_dir = tmp_path / "scene"
    _write_per_cam_gt2d(scene_dir, {
        "Camera_01": {
            "0": [{
                "full bounding box": [10, 20, 110, 220],
                "visible bounding box": [15, 25, 105, 215],
                "person id": 42,
            }],
        },
    })
    out = load_det_2d_from_gt_scene(str(scene_dir), mode="visible bounding box")
    assert out["Camera_01"][0][0][1] == [15, 25, 105, 215]


def test_load_det_2d_handles_multiple_cameras(tmp_path):
    scene_dir = tmp_path / "scene"
    _write_per_cam_gt2d(scene_dir, {
        "Camera_01": {"0": [{"full bounding box": [0, 0, 1, 1],
                              "visible bounding box": [0, 0, 1, 1],
                              "person id": 1}]},
        "Camera_02": {"0": [{"full bounding box": [2, 2, 3, 3],
                              "visible bounding box": [2, 2, 3, 3],
                              "person id": 2}]},
    })
    out = load_det_2d_from_gt_scene(str(scene_dir))
    assert set(out.keys()) == {"Camera_01", "Camera_02"}


# ---------------------------------------------------------------------------
# load_det_3d_from_gt_scene
# ---------------------------------------------------------------------------


def _aic24_frame(person_id=1):
    return [{
        "3d location": [1.0, 2.0, 0.5],
        "3d bounding box scale": [0.5, 1.0, 1.8],
        "3d bounding box rotation": [0.0, 0.0, 45.0],  # yaw in degrees
        "confidence": 1.0,
        "type": "person",
        "person id": person_id,
    }]


def _aic25_frame(object_id=1):
    return [{
        "3d location": [1.0, 2.0, 0.5],
        "3d bounding box scale": [0.5, 1.0, 1.8],
        "3d bounding box rotation": [0.0, 0.0, 90.0],
        "object type": "Person",
        "object id": object_id,
        # confidence intentionally omitted to exercise the .get(...,1.0) default
    }]


def test_load_det_3d_aic24_mode_emits_yaw_radians(tmp_path):
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    (scene_dir / "ground_truth_bevformer.json").write_text(
        json.dumps({"0": _aic24_frame(), "1": _aic24_frame(person_id=2)})
    )
    out = load_det_3d_from_gt_scene(str(scene_dir), mode="aic24")
    assert set(out.keys()) == {0, 1}
    type_name, gt_box, conf, pid = out[0][0]
    assert type_name == "person"
    assert conf == 1.0
    assert pid == 1
    # Box: [x, y, z, w, l, h, yaw_rad]
    assert gt_box[:6] == [1.0, 2.0, 0.5, 0.5, 1.0, 1.8]
    assert gt_box[6] == pytest.approx(math.radians(45.0))


def test_load_det_3d_aic25_mode_uses_object_keys_and_default_confidence(tmp_path):
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    (scene_dir / "ground_truth.json").write_text(
        json.dumps({"0": _aic25_frame(object_id=7)})
    )
    out = load_det_3d_from_gt_scene(str(scene_dir), mode="aic25")
    type_name, gt_box, conf, oid = out[0][0]
    assert type_name == "Person"
    assert oid == 7
    assert conf == 1.0  # default when "confidence" key absent


def test_load_det_3d_invalid_mode_raises(tmp_path):
    with pytest.raises(ValueError, match="Invalid mode"):
        load_det_3d_from_gt_scene(str(tmp_path), mode="aic26")


# ---------------------------------------------------------------------------
# load_gt_from_txt_scene
# ---------------------------------------------------------------------------


def test_load_gt_from_txt_scene_parses_per_frame_per_cam_layout(tmp_path):
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    # Format: cam_id obj_id frame_id xmin ymin w h xworld yworld
    (scene_dir / "ground_truth.txt").write_text(
        "1 42 0 100 200 30 40 5.5 6.5\n"
        "1 43 0 110 210 30 40 7.5 8.5\n"
        "2 42 0 120 220 30 40 9.5 10.5\n"
        "1 42 1 105 205 30 40 5.6 6.6\n"
    )
    out = load_gt_from_txt_scene(str(scene_dir))
    assert set(out.keys()) == {0, 1}
    # Two cameras at frame 0
    assert set(out[0].keys()) == {"1", "2"}
    # cam 1 frame 0 has two objects
    assert len(out[0]["1"]) == 2
    obj = out[0]["1"][0]
    assert obj["person id"] == 42
    # visible bbox is [xmin, ymin, xmin+w, ymin+h]
    assert obj["visible bounding box"] == [100, 200, 130, 240]
    # 3D location is [xworld, yworld, 0]
    assert obj["3d location"] == [5.5, 6.5, 0]


# ---------------------------------------------------------------------------
# process_bbox3d_gt + _gt_box_to_nvschema_obj
# ---------------------------------------------------------------------------


def test_process_bbox3d_gt_returns_canonical_9dof_layout():
    label = {
        "3d location": [1.0, 2.0, 3.0],
        "3d bounding box scale": [0.5, 1.0, 1.8],
        "3d bounding box rotation": [0.1, 0.2, 0.3],  # pitch, roll, yaw
    }
    out = process_bbox3d_gt(label)
    assert out == [1.0, 2.0, 3.0, 0.5, 1.0, 1.8, 0.1, 0.2, 0.3]


def test_gt_box_to_nvschema_obj_pads_pitch_roll_to_zero():
    obj = _gt_box_to_nvschema_obj([1.0, 2.0, 0.5, 0.5, 1.0, 1.8, 1.57],
                                  name="person", track_id=42)
    coords = obj["bbox3d"]["coordinates"]
    assert coords[:6] == [1.0, 2.0, 0.5, 0.5, 1.0, 1.8]
    assert coords[6] == 0.0 and coords[7] == 0.0  # padded pitch/roll
    assert coords[8] == pytest.approx(1.57)
    assert obj["type"] == "person"
    assert obj["confidence"] == 1.0
    assert obj["coordinate"] == {"x": 1.0, "y": 2.0, "z": 0.5}


# ---------------------------------------------------------------------------
# load_gt_from_pkl
# ---------------------------------------------------------------------------


def _write_pkl(path, infos, metadata=None):
    with open(path, "wb") as f:
        pickle.dump({"infos": infos, "metadata": metadata or {}}, f)


def test_load_gt_from_pkl_emits_per_frame_world_sensor_dict(tmp_path):
    pkl = tmp_path / "info.pkl"
    _write_pkl(pkl, [
        {
            "frame_idx": 0,
            "gt_boxes": np.array([
                [1.0, 2.0, 0.5, 0.5, 1.0, 1.8, 1.57],
                [3.0, 4.0, 0.5, 0.5, 1.0, 1.8, 0.0],
            ]),
            "gt_names": np.array(["person", "person"]),
            "instance_inds": np.array([1, 2]),
            "valid_flag": np.array([True, True]),
        },
        {
            "frame_idx": 1,
            "gt_boxes": np.array([[5.0, 6.0, 0.5, 0.5, 1.0, 1.8, 0.5]]),
            "gt_names": np.array(["person"]),
            "instance_inds": np.array([1]),
        },
    ])
    out = load_gt_from_pkl(str(pkl))
    assert set(out.keys()) == {0, 1}
    assert set(out[0].keys()) == {GT_WORLD_SENSOR_KEY}
    assert len(out[0][GT_WORLD_SENSOR_KEY]) == 2
    assert len(out[1][GT_WORLD_SENSOR_KEY]) == 1
    # ID stringification matches NVSchema spec.
    assert out[0][GT_WORLD_SENSOR_KEY][0]["id"] == "1"


def test_load_gt_from_pkl_honors_valid_flag_mask(tmp_path):
    pkl = tmp_path / "info.pkl"
    _write_pkl(pkl, [{
        "frame_idx": 0,
        "gt_boxes": np.array([
            [1.0, 2.0, 0.5, 0.5, 1.0, 1.8, 0.0],
            [3.0, 4.0, 0.5, 0.5, 1.0, 1.8, 0.0],
        ]),
        "gt_names": np.array(["person", "person"]),
        "instance_inds": np.array([1, 2]),
        "valid_flag": np.array([True, False]),  # second box dropped
    }])
    out = load_gt_from_pkl(str(pkl))
    # Only the valid box survives.
    assert len(out[0][GT_WORLD_SENSOR_KEY]) == 1
    assert out[0][GT_WORLD_SENSOR_KEY][0]["id"] == "1"


def test_load_gt_from_pkl_raises_on_missing_frame_idx(tmp_path):
    pkl = tmp_path / "info.pkl"
    _write_pkl(pkl, [{"gt_boxes": np.empty((0, 7))}])  # no frame_idx
    with pytest.raises(KeyError, match="frame_idx"):
        load_gt_from_pkl(str(pkl))


def test_load_gt_from_pkl_handles_empty_gt_boxes(tmp_path):
    """Empty ``gt_boxes`` and missing ``gt_names`` (defaulted by
    ``info.get``) should yield an empty per-frame object list."""
    pkl = tmp_path / "info.pkl"
    _write_pkl(pkl, [{"frame_idx": 0}])  # nothing else
    out = load_gt_from_pkl(str(pkl))
    assert out[0][GT_WORLD_SENSOR_KEY] == []
