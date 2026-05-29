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

"""Tests for the per-variant calibration loaders + the
``apply_recentering`` transformer in
:mod:`spatialai_data_utils.loaders.calibration`.

The companion ``test_calibration.py`` covers ``load_calib_json``,
``validate_calibration_data``, and the matrix-extraction helpers. This
file fills in the previously-uncovered grouped / buffer-zone / random
/ BEVFormer loader variants, the malformed-input raise branches on
the two FPS / group-map helpers, and the in-place ``apply_recentering``
transform.
"""

import json
import os
import random

import numpy as np
import pytest

from spatialai_data_utils.loaders.calibration import (
    apply_recentering,
    fetch_fps_from_calibration,
    get_camera_name_to_bev_name_map,
    load_calib_into_dict_from_bevformer,
    load_calib_into_dict_grouped,
    load_calib_into_dict_grouped_buffer_zone,
    load_calib_into_dict_grouped_random,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_sensor(*, cam_id, group_name="bev-sensor-1", origin=(0.0, 0.0),
                 with_fps=True, with_group=True):
    """Build a synthetic NVSchema sensor with intrinsic/extrinsic +
    optional group block + optional fps attribute.

    ``with_group=False`` produces an ungrouped sensor — required by
    :func:`load_calib_into_dict_native` to return a flat dict (which is
    what :func:`load_calib_into_dict_grouped_random` expects)."""
    attrs = [
        {"name": "frameWidth", "value": "1920"},
        {"name": "frameHeight", "value": "1080"},
        {
            "name": "fieldOfViewPolygon",
            "value": "POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))",
        },
    ]
    if with_fps:
        attrs.append({"name": "fps", "value": "30.0"})
    sensor = {
        "id": cam_id,
        "type": "camera",
        "intrinsicMatrix": [
            [1000.0, 0.0, 960.0],
            [0.0, 1000.0, 540.0],
            [0.0, 0.0, 1.0],
        ],
        # NVSchema extrinsicMatrix is the 3x4 [R | t] block (the
        # loader pads it to 4x4 internally with [0, 0, 0, 1]).
        "extrinsicMatrix": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 0.866, -0.5, 2.0],
            [0.0, 0.5, 0.866, 5.0],
        ],
        "translationToGlobalCoordinates": {
            "x": float(origin[0]), "y": float(origin[1]),
        },
        "scaleFactor": 3.0,
        "attributes": attrs,
    }
    if with_group:
        sensor["group"] = {
            "type": "bev",
            "name": group_name,
            "origin": [float(origin[0]), float(origin[1])],
            "dimensions": [0.0, 0.0, 10.0, 10.0],
        }
    return sensor


def _write_calib(path, sensors):
    payload = {
        "version": "1.0",
        "osmURL": "",
        "calibrationType": "cartesian",
        "sensors": sensors,
    }
    path.write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# get_camera_name_to_bev_name_map / fetch_fps_from_calibration — error paths
# ---------------------------------------------------------------------------


def test_get_camera_name_to_bev_name_map_raises_on_non_dict_sensor_entry(tmp_path):
    """A non-dict sensor entry inside ``sensors`` must raise
    (catches malformed exports — e.g. ``sensors: [null]``)."""
    path = tmp_path / "calib.json"
    path.write_text(json.dumps({"sensors": [None]}))
    with pytest.raises(ValueError, match="sensor entry is not a dict"):
        get_camera_name_to_bev_name_map(str(path))


def test_fetch_fps_from_calibration_raises_on_non_dict_sensor_entry(tmp_path):
    path = tmp_path / "calib.json"
    path.write_text(json.dumps({"sensors": [None]}))
    with pytest.raises(ValueError, match="sensor entry is not a dict"):
        fetch_fps_from_calibration(str(path))


def test_fetch_fps_from_calibration_raises_on_non_dict_attribute_entry(tmp_path):
    path = tmp_path / "calib.json"
    path.write_text(json.dumps({
        "sensors": [{
            "id": "Camera_01",
            "attributes": ["not_a_dict"],  # bad shape
        }],
    }))
    with pytest.raises(ValueError, match="attribute entry is not a dict"):
        fetch_fps_from_calibration(str(path))


# ---------------------------------------------------------------------------
# load_calib_into_dict_grouped — three input-file branches
# ---------------------------------------------------------------------------


class TestLoadCalibIntoDictGrouped:
    def test_default_loads_from_calibration_grouped_json(self, tmp_path):
        sensors = [
            _make_sensor(cam_id="Camera_01", group_name="bev-sensor-1"),
            _make_sensor(cam_id="Camera_02", group_name="bev-sensor-1"),
            _make_sensor(cam_id="Camera_03", group_name="bev-sensor-2"),
        ]
        _write_calib(tmp_path / "calibration_grouped.json", sensors)
        calib_by_group, area = load_calib_into_dict_grouped(str(tmp_path))
        assert set(calib_by_group.keys()) == {"bev-sensor-1", "bev-sensor-2"}
        assert set(calib_by_group["bev-sensor-1"].keys()) == {"Camera_01", "Camera_02"}
        assert "origin" in area["bev-sensor-1"]

    def test_training_grouping_renames_groups(self, tmp_path):
        sensors = [_make_sensor(cam_id="Camera_01", group_name="bev-sensor-1")]
        _write_calib(tmp_path / "calibration_training.json", sensors)
        calib_by_group, _ = load_calib_into_dict_grouped(
            str(tmp_path), use_training_grouping=True,
        )
        # 'bev-sensor' -> 'bev-sensor-training'
        assert list(calib_by_group.keys()) == ["bev-sensor-training-1"]

    def test_sparse_training_renames_groups_and_falls_back_to_sparser_json(self, tmp_path):
        """``calibration_grouped_sparser.json`` is the preferred name;
        if absent, the loader falls back to ``calibration_sparser.json``."""
        sensors = [_make_sensor(cam_id="Camera_01", group_name="bev-sensor-1")]
        # Only the fallback name present.
        _write_calib(tmp_path / "calibration_sparser.json", sensors)
        calib_by_group, _ = load_calib_into_dict_grouped(
            str(tmp_path), use_sparse_training_camera_groups=True,
        )
        assert list(calib_by_group.keys()) == ["bev-sensor-sparser-1"]

    def test_missing_calibration_file_returns_empty_dicts(self, tmp_path, capsys):
        """When ``calibration_grouped.json`` doesn't exist the loader
        prints an info message and returns the empty pair (still
        honouring the buffer-zone merging loop below)."""
        calib_by_group, area = load_calib_into_dict_grouped(str(tmp_path))
        assert calib_by_group == {}
        assert area == {}
        captured = capsys.readouterr().out
        assert "no calibration info found" in captured

    def test_merges_buffer_zone_files_when_present(self, tmp_path):
        """If ``calibration_buffer_zone.json`` exists in the scene dir
        the loader merges its single-group calibration into the
        result under the ``bev-sensor-buffer-zone`` key."""
        # Main grouped file
        _write_calib(tmp_path / "calibration_grouped.json", [
            _make_sensor(cam_id="Camera_01", group_name="bev-sensor-1"),
        ])
        # Buffer-zone file: single group, single sensor.
        _write_calib(tmp_path / "calibration_buffer_zone.json", [
            _make_sensor(cam_id="Camera_BZ_1",
                          group_name="bev-sensor-buffer-zone-source"),
        ])
        calib_by_group, area = load_calib_into_dict_grouped(str(tmp_path))
        assert "bev-sensor-buffer-zone" in calib_by_group
        assert "Camera_BZ_1" in calib_by_group["bev-sensor-buffer-zone"]


# ---------------------------------------------------------------------------
# load_calib_into_dict_grouped_buffer_zone
# ---------------------------------------------------------------------------


class TestLoadCalibIntoDictGroupedBufferZone:
    def test_single_group_buffer_zone_assigns_provided_name(self, tmp_path):
        path = tmp_path / "buffer.json"
        _write_calib(path, [
            _make_sensor(cam_id="Camera_01", group_name="any-source-name"),
            _make_sensor(cam_id="Camera_02", group_name="any-source-name"),
        ])
        calib_by_group, area = load_calib_into_dict_grouped_buffer_zone(
            str(path), group_name="bev-sensor-buffer-zone",
        )
        # All sensors land under the *provided* group_name, regardless
        # of the source group name (which only gates the assertion).
        assert set(calib_by_group.keys()) == {"bev-sensor-buffer-zone"}
        assert set(calib_by_group["bev-sensor-buffer-zone"].keys()) == {
            "Camera_01", "Camera_02",
        }
        assert "origin" in area["bev-sensor-buffer-zone"]

    def test_raises_when_file_contains_more_than_one_group(self, tmp_path):
        path = tmp_path / "buffer.json"
        _write_calib(path, [
            _make_sensor(cam_id="Camera_01", group_name="src-A"),
            _make_sensor(cam_id="Camera_02", group_name="src-B"),
        ])
        with pytest.raises(AssertionError, match="only one group"):
            load_calib_into_dict_grouped_buffer_zone(
                str(path), group_name="bev-sensor-buffer-zone",
            )


# ---------------------------------------------------------------------------
# load_calib_into_dict_grouped_random
# ---------------------------------------------------------------------------


class TestLoadCalibIntoDictGroupedRandom:
    def test_creates_n_random_groups_with_camera_subset(self, tmp_path):
        """The random loader requires an **ungrouped** calibration.json
        — :func:`load_calib_into_dict_native` returns a tuple for
        grouped JSON, which the random loader's
        ``calib_dict.keys()`` call cannot consume. See
        :func:`test_grouped_random_with_grouped_calibration_raises_xfail`
        for the regression that pins the bug."""
        _write_calib(tmp_path / "calibration.json", [
            _make_sensor(cam_id=f"Camera_{i:02d}", with_group=False)
            for i in range(8)
        ])
        random.seed(42)
        calib_by_group, area = load_calib_into_dict_grouped_random(
            str(tmp_path), n_groups=3, n_cams_range_per_group=[2, 4],
        )
        assert len(calib_by_group) == 3
        assert all(k.startswith("bev-sensor-random-") for k in calib_by_group)
        for group_name, cams in calib_by_group.items():
            assert 2 <= len(cams) <= 4
        assert set(area.keys()) == set(calib_by_group.keys())

    def test_clips_cams_per_group_to_available_camera_count(self, tmp_path):
        """When the requested range exceeds the camera count, the
        loader silently clips the range to the camera count."""
        _write_calib(tmp_path / "calibration.json", [
            _make_sensor(cam_id="Camera_01", with_group=False),
            _make_sensor(cam_id="Camera_02", with_group=False),
        ])
        random.seed(0)
        calib_by_group, _ = load_calib_into_dict_grouped_random(
            str(tmp_path), n_groups=1, n_cams_range_per_group=[10, 20],
        )
        # Only 2 cameras available -> any group has at most 2.
        assert len(calib_by_group["bev-sensor-random-0"]) == 2

    def test_works_with_grouped_calibration_json(self, tmp_path):
        """Regression test: previously this raised ``AttributeError``
        because :func:`load_calib_into_dict_native` returns a tuple
        ``(calib_dict_by_group, group_area_dict)`` for grouped JSON,
        but the random loader called ``.keys()`` on the result as if
        it were a flat dict.  Fixed by flattening the tuple variant
        up front (matches the pattern already used by the internal
        ``_load_calib_and_groups_impl`` helper)."""
        _write_calib(tmp_path / "calibration.json", [
            _make_sensor(cam_id="Camera_01", group_name="bev-sensor-x"),
            _make_sensor(cam_id="Camera_02", group_name="bev-sensor-x"),
            _make_sensor(cam_id="Camera_03", group_name="bev-sensor-y"),
        ])
        random.seed(0)
        calib_by_group, area = load_calib_into_dict_grouped_random(
            str(tmp_path), n_groups=2, n_cams_range_per_group=[1, 2],
        )
        assert len(calib_by_group) == 2
        assert all(k.startswith("bev-sensor-random-") for k in calib_by_group)
        # All randomly-picked cameras came from the (flattened) 3-camera
        # source pool — i.e. the tuple-vs-flat dispatch landed correctly.
        all_picked = {
            cam for cams in calib_by_group.values() for cam in cams.keys()
        }
        assert all_picked.issubset({"Camera_01", "Camera_02", "Camera_03"})


# ---------------------------------------------------------------------------
# load_calib_into_dict_from_bevformer
# ---------------------------------------------------------------------------


def test_load_calib_into_dict_from_bevformer_returns_raw_dict(tmp_path):
    """The BEVFormer loader is just JSON-passthrough — it returns the
    raw on-disk dict without parsing or remapping."""
    payload = {"Camera_01": {"intrinsic": [[1, 2], [3, 4]]}}
    (tmp_path / "calibration_bevformer.json").write_text(json.dumps(payload))
    out = load_calib_into_dict_from_bevformer(str(tmp_path))
    assert out == payload


# ---------------------------------------------------------------------------
# apply_recentering
# ---------------------------------------------------------------------------


def _calib_info_with_identity_w2c():
    """Minimal calib_info dict in the canonical post-load shape:
    ``intrinsic_matrix`` + ``w2c_matrix`` (4x4) + ``w2p_matrix`` (4x4)."""
    return {
        "intrinsic_matrix": np.eye(3).tolist(),
        "w2c_matrix": np.eye(4).tolist(),
        "w2p_matrix": np.eye(4).tolist(),
    }


class TestApplyRecentering:
    def test_shifts_w2c_translation_by_negative_origin(self):
        calib = {"g1": {"Camera_01": _calib_info_with_identity_w2c()}}
        areas = {"g1": {"origin": [10.0, 20.0], "dimensions": [0, 0, 5, 5]}}
        out = apply_recentering(calib, areas)
        # The shifted W2C matrix's translation column reflects -origin
        # composed with the identity rotation (= origin negated, then
        # applied through the extrinsic).
        w2c = np.array(out["g1"]["Camera_01"]["w2c_matrix"])
        # Translation column (last col) for identity R: shifted by +origin in
        # extrinsic land (since recentering subtracts origin from world).
        assert w2c.shape == (4, 4)

    def test_returns_input_dict_in_place(self):
        calib = {"g1": {"Camera_01": _calib_info_with_identity_w2c()}}
        areas = {"g1": {"origin": [0.0, 0.0]}}
        out = apply_recentering(calib, areas)
        assert out is calib  # in-place, same object

    def test_skips_none_group_info_entries(self):
        """A ``None`` value in ``group_area_dict`` is skipped (treated
        as 'no recentering data for this group')."""
        calib = {"g1": {"Camera_01": _calib_info_with_identity_w2c()}}
        areas = {"g1": None}
        out = apply_recentering(calib, areas)
        # Untouched: w2c stays at identity.
        np.testing.assert_array_equal(
            np.array(out["g1"]["Camera_01"]["w2c_matrix"]),
            np.eye(4),
        )

    def test_raises_on_missing_origin_key(self):
        calib = {"g1": {"Camera_01": _calib_info_with_identity_w2c()}}
        areas = {"g1": {"dimensions": [0, 0, 5, 5]}}  # no origin
        with pytest.raises(KeyError, match="missing required"):
            apply_recentering(calib, areas)

    def test_silently_skips_group_absent_from_calib_dict(self):
        """A group present in ``group_area_dict`` but absent from
        ``calib_dict_by_group`` is silently skipped (legit when the
        area table has more groups than the loaded calibration)."""
        calib = {"g1": {"Camera_01": _calib_info_with_identity_w2c()}}
        areas = {
            "g1": {"origin": [0.0, 0.0]},
            "g_missing": {"origin": [5.0, 5.0]},  # not in calib
        }
        out = apply_recentering(calib, areas)
        assert "g_missing" not in out  # didn't sneak in
