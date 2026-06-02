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

"""Tests for ``spatialai_data_utils.loaders.ground_truth``.

Focused coverage of the sparse4d-style pkl ground-truth loader
(:func:`load_gt_from_pkl`) that powers the pkl viz mode.
"""

import pickle

import numpy as np
import pytest

from spatialai_data_utils.loaders.ground_truth import (
    GT_WORLD_SENSOR_KEY,
    load_gt_from_pkl,
)


def _make_pkl(tmp_path, infos):
    """Write a minimal sparse4d-style pkl to *tmp_path* and return its path."""
    path = tmp_path / "scene_infos.pkl"
    with open(path, "wb") as f:
        pickle.dump({"infos": infos, "metadata": {"version": "test"}}, f)
    return str(path)


def _info_entry(
    frame_idx,
    boxes=((0.0, 0.0, 1.0, 0.5, 0.5, 1.8, 0.7),),
    names=("person",),
    track_ids=(100,),
    valid=None,
):
    """Build one pkl info dict with the expected numpy shapes and dtypes."""
    boxes_arr = np.asarray(boxes, dtype=np.float32)
    names_arr = np.asarray(names, dtype=object)
    track_arr = np.asarray(track_ids, dtype=np.int64)
    if valid is None:
        valid_arr = np.ones(len(boxes_arr), dtype=bool)
    else:
        valid_arr = np.asarray(valid, dtype=bool)
    return {
        "frame_idx": frame_idx,
        "cams": {},
        "gt_boxes": boxes_arr,
        "gt_names": names_arr,
        "instance_inds": track_arr,
        "valid_flag": valid_arr,
    }


class TestLoadGtFromPkl:
    """Tests for converting sparse4d-style pkl GT to NVSchema-shaped dicts."""

    def test_basic_schema(self, tmp_path):
        """Each frame's GT is wrapped under the sentinel sensor key."""
        pkl = _make_pkl(tmp_path, [_info_entry(0), _info_entry(1)])
        result = load_gt_from_pkl(pkl)
        assert set(result.keys()) == {0, 1}
        for frame_data in result.values():
            assert list(frame_data.keys()) == [GT_WORLD_SENSOR_KEY]

    def test_object_fields_are_nvschema_shaped(self, tmp_path):
        """Each object carries id / type / confidence / bbox3d.coordinates (9-DoF)."""
        box = (1.0, 2.0, 3.0, 0.4, 0.5, 1.7, 1.2)
        pkl = _make_pkl(
            tmp_path,
            [_info_entry(0, boxes=(box,), names=("person",), track_ids=(42,))],
        )
        objs = load_gt_from_pkl(pkl)[0][GT_WORLD_SENSOR_KEY]
        assert len(objs) == 1
        obj = objs[0]
        # Top-level NVSchema fields.
        assert obj["id"] == "42"
        assert obj["type"] == "person"
        assert obj["confidence"] == 1.0
        # float32 -> float converts with ~1e-7 drift, use pytest.approx.
        coord = obj["coordinate"]
        assert coord["x"] == pytest.approx(1.0)
        assert coord["y"] == pytest.approx(2.0)
        assert coord["z"] == pytest.approx(3.0)
        # bbox3d.coordinates padded from 7 -> 9 with zero roll/pitch.
        assert obj["bbox3d"]["coordinates"] == pytest.approx(
            [1.0, 2.0, 3.0, 0.4, 0.5, 1.7, 0.0, 0.0, 1.2]
        )

    def test_valid_flag_filters_invalid_entries(self, tmp_path):
        """Entries with valid_flag=False are dropped from the output list."""
        pkl = _make_pkl(
            tmp_path,
            [_info_entry(
                0,
                boxes=[(0, 0, 0, 1, 1, 1, 0), (1, 0, 0, 1, 1, 1, 0)],
                names=["a", "b"],
                track_ids=[1, 2],
                valid=[True, False],
            )],
        )
        objs = load_gt_from_pkl(pkl)[0][GT_WORLD_SENSOR_KEY]
        assert len(objs) == 1
        assert objs[0]["id"] == "1"

    def test_empty_frame(self, tmp_path):
        """A frame with zero GT boxes yields an empty list (not missing)."""
        pkl = _make_pkl(
            tmp_path,
            [_info_entry(
                0, boxes=np.empty((0, 7), dtype=np.float32),
                names=np.empty((0,), dtype=object),
                track_ids=np.empty((0,), dtype=np.int64),
            )],
        )
        result = load_gt_from_pkl(pkl)
        assert result[0][GT_WORLD_SENSOR_KEY] == []

    def test_missing_frame_idx_raises(self, tmp_path):
        """An info entry without frame_idx raises KeyError mentioning the index."""
        bad = _info_entry(0)
        del bad["frame_idx"]
        pkl = _make_pkl(tmp_path, [bad])
        with pytest.raises(KeyError, match="index 0"):
            load_gt_from_pkl(pkl)

    def test_frame_idx_coerced_to_int(self, tmp_path):
        """frame_idx values are normalized to int keys in the output dict."""
        pkl = _make_pkl(tmp_path, [_info_entry(np.int64(7))])
        result = load_gt_from_pkl(pkl)
        assert list(result.keys()) == [7]
        assert isinstance(next(iter(result.keys())), int)
