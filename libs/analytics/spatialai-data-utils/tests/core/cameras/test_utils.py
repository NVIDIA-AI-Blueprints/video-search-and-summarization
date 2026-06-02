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

"""Tests for ``core.cameras.utils`` — the lightweight calibration-dict,
map-image, and persistence helpers used by the camera-grouping pipeline:

* ``get_calib_field`` — canonical-key read with legacy-key fallback.
* ``load_map_data`` — load a floor-plan map image + its dimensions.
* ``discover_map_file`` — locate ``Top.png`` (scene root or ``images/``).
* ``extract_camera_matrices`` (+ ``_validate_camera_matrices``) — pull and
  validate intrinsic / extrinsic matrices from processed or raw formats.
* ``save_calibration_data`` — persist a calibration dict to JSON.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from spatialai_data_utils.constants import (
    KEY_INTRINSIC_MATRIX,
    KEY_W2C_MATRIX,
)
from spatialai_data_utils.core.cameras.utils import (
    _validate_camera_matrices,
    discover_map_file,
    extract_camera_matrices,
    get_calib_field,
    load_map_data,
    save_calibration_data,
)


def _make_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


# ---------------------------------------------------------------------------
# discover_map_file
# ---------------------------------------------------------------------------


class TestDiscoverMapFile:
    """``discover_map_file`` finds ``Top.png`` at the scene root or under an
    ``images/`` subfolder, given either the scene directory or the
    ``calibration.json`` file inside it."""

    def test_finds_map_at_scene_root_given_dir(self, tmp_path):
        expected = _make_file(tmp_path / "Top.png")
        assert discover_map_file(tmp_path) == expected

    def test_finds_map_in_images_subfolder_given_dir(self, tmp_path):
        expected = _make_file(tmp_path / "images" / "Top.png")
        assert discover_map_file(tmp_path) == expected

    def test_finds_map_in_images_subfolder_given_calibration_file(self, tmp_path):
        # Warehouse sample-data layout: calibration.json at the scene root,
        # Top.png under images/.
        calib = tmp_path / "calibration.json"
        calib.touch()
        expected = _make_file(tmp_path / "images" / "Top.png")
        assert discover_map_file(calib) == expected

    def test_finds_map_next_to_calibration_file(self, tmp_path):
        calib = tmp_path / "calibration.json"
        calib.touch()
        expected = _make_file(tmp_path / "Top.png")
        assert discover_map_file(calib) == expected

    def test_scene_root_takes_precedence_over_images(self, tmp_path):
        root_map = _make_file(tmp_path / "Top.png")
        _make_file(tmp_path / "images" / "Top.png")
        assert discover_map_file(tmp_path) == root_map

    def test_returns_none_when_absent(self, tmp_path):
        assert discover_map_file(tmp_path) is None

    def test_respects_custom_filename(self, tmp_path):
        expected = _make_file(tmp_path / "images" / "map.png")
        assert discover_map_file(tmp_path, map_filename="map.png") == expected


# ---------------------------------------------------------------------------
# get_calib_field
# ---------------------------------------------------------------------------


class TestGetCalibField:
    """``get_calib_field`` reads canonical keys, falls back to legacy
    string keys, and honours the ``default`` / ``KeyError`` contract."""

    def test_returns_value_for_canonical_key(self):
        calib = {KEY_INTRINSIC_MATRIX: [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        assert get_calib_field(calib, KEY_INTRINSIC_MATRIX) == [
            [1, 0, 0],
            [0, 1, 0],
            [0, 0, 1],
        ]

    def test_falls_back_to_legacy_key(self):
        # Only the legacy spelling is present; asking for the canonical
        # key still resolves it.
        calib = {"intrinsic matrix": [[2, 0, 0], [0, 2, 0], [0, 0, 1]]}
        assert get_calib_field(calib, KEY_INTRINSIC_MATRIX) == [
            [2, 0, 0],
            [0, 2, 0],
            [0, 0, 1],
        ]

    def test_canonical_takes_precedence_over_legacy(self):
        calib = {KEY_W2C_MATRIX: "canonical", "projection matrix w2c": "legacy"}
        assert get_calib_field(calib, KEY_W2C_MATRIX) == "canonical"

    def test_returns_default_when_missing(self):
        assert get_calib_field({}, KEY_W2C_MATRIX, default=None) is None

    def test_raises_keyerror_when_missing_and_no_default(self):
        with pytest.raises(KeyError):
            get_calib_field({}, KEY_W2C_MATRIX)

    def test_raises_keyerror_for_unknown_key_without_legacy_mapping(self):
        # A key with no legacy alias and no default → KeyError.
        with pytest.raises(KeyError):
            get_calib_field({"other": 1}, "no_such_field")


# ---------------------------------------------------------------------------
# load_map_data
# ---------------------------------------------------------------------------


class TestLoadMapData:
    """``load_map_data`` returns ``(path, image, width, height)`` for a
    readable image, and an all-``None`` tuple for missing / unreadable
    inputs."""

    def test_returns_all_none_when_map_file_is_none(self):
        assert load_map_data(None) == (None, None, None, None)

    def test_returns_all_none_when_path_missing(self, tmp_path):
        assert load_map_data(tmp_path / "missing.png") == (None, None, None, None)

    def test_loads_dimensions_from_real_png(self, tmp_path):
        map_path = tmp_path / "Top.png"
        Image.new("RGB", (8, 4)).save(map_path)
        path, image, width, height = load_map_data(map_path)
        try:
            assert path == map_path
            assert (width, height) == (8, 4)
            assert image is not None and image.size == (8, 4)
        finally:
            if image is not None:
                image.close()

    def test_returns_path_but_none_image_for_unreadable_file(self, tmp_path):
        bad = tmp_path / "Top.png"
        bad.write_bytes(b"not a real image")
        assert load_map_data(bad) == (bad, None, None, None)


# ---------------------------------------------------------------------------
# _validate_camera_matrices
# ---------------------------------------------------------------------------


class TestValidateCameraMatrices:
    """Shape + NaN/Inf validation for intrinsic (3x3 / 3x4) and extrinsic
    (3x4 / 4x4) matrices."""

    def test_accepts_3x3_intrinsic_and_4x4_extrinsic(self):
        assert _validate_camera_matrices(np.eye(3), np.eye(4)) is True

    def test_accepts_3x4_intrinsic_and_3x4_extrinsic(self):
        assert _validate_camera_matrices(np.zeros((3, 4)), np.zeros((3, 4))) is True

    def test_rejects_bad_intrinsic_shape(self):
        assert _validate_camera_matrices(np.eye(2), np.eye(4)) is False

    def test_rejects_bad_extrinsic_shape(self):
        assert _validate_camera_matrices(np.eye(3), np.eye(2)) is False

    def test_rejects_nan_in_intrinsic(self):
        intrinsic = np.eye(3)
        intrinsic[0, 0] = np.nan
        assert _validate_camera_matrices(intrinsic, np.eye(4)) is False

    def test_rejects_inf_in_extrinsic(self):
        extrinsic = np.eye(4)
        extrinsic[1, 1] = np.inf
        assert _validate_camera_matrices(np.eye(3), extrinsic) is False


# ---------------------------------------------------------------------------
# extract_camera_matrices
# ---------------------------------------------------------------------------


class TestExtractCameraMatrices:
    """Pulls intrinsic/extrinsic matrices from processed (canonical +
    legacy keys) and raw sensor formats; returns ``(None, None)`` on
    missing or invalid matrices."""

    def test_extracts_from_processed_canonical_keys(self):
        sensor = {
            KEY_INTRINSIC_MATRIX: np.eye(3).tolist(),
            KEY_W2C_MATRIX: np.eye(4).tolist(),
        }
        intrinsic, extrinsic = extract_camera_matrices(sensor)
        assert intrinsic.shape == (3, 3)
        assert extrinsic.shape == (4, 4)

    def test_extracts_from_legacy_processed_keys(self):
        sensor = {
            "intrinsic matrix": np.eye(3).tolist(),
            "projection matrix w2c": np.eye(4).tolist(),
        }
        intrinsic, extrinsic = extract_camera_matrices(sensor)
        assert intrinsic.shape == (3, 3)
        assert extrinsic.shape == (4, 4)

    def test_extracts_from_raw_sensor_format(self):
        sensor = {
            "intrinsicMatrix": np.eye(3).tolist(),
            "extrinsicMatrix": np.eye(4).tolist(),
        }
        intrinsic, extrinsic = extract_camera_matrices(sensor)
        assert intrinsic.shape == (3, 3)
        assert extrinsic.shape == (4, 4)

    def test_returns_none_for_invalid_processed_shape(self):
        sensor = {
            KEY_INTRINSIC_MATRIX: np.eye(2).tolist(),  # bad shape
            KEY_W2C_MATRIX: np.eye(4).tolist(),
        }
        assert extract_camera_matrices(sensor) == (None, None)

    def test_returns_none_for_invalid_raw_shape(self):
        sensor = {
            "id": "cam_0",
            "intrinsicMatrix": np.eye(2).tolist(),  # bad shape
            "extrinsicMatrix": np.eye(4).tolist(),
        }
        assert extract_camera_matrices(sensor) == (None, None)

    def test_returns_none_when_no_matrices_present(self):
        assert extract_camera_matrices({}) == (None, None)

    def test_returns_none_on_malformed_matrix_data(self):
        # Ragged/inhomogeneous data makes ``np.array`` raise; the catch-all
        # logs the error and returns ``(None, None)`` instead of propagating.
        sensor = {
            KEY_INTRINSIC_MATRIX: [[1, 2], [3]],
            KEY_W2C_MATRIX: np.eye(4).tolist(),
        }
        assert extract_camera_matrices(sensor) == (None, None)


# ---------------------------------------------------------------------------
# save_calibration_data
# ---------------------------------------------------------------------------


class TestSaveCalibrationData:
    """Writes a calibration dict to JSON (creating parent dirs) and
    re-raises on unwritable paths."""

    def test_writes_json_round_trip(self, tmp_path):
        data = {"version": 1, "sensors": [{"id": "cam_0"}]}
        out = tmp_path / "calibration_out.json"
        save_calibration_data(data, str(out))
        with open(out) as f:
            assert json.load(f) == data

    def test_creates_missing_parent_dirs(self, tmp_path):
        data = {"sensors": []}
        out = tmp_path / "nested" / "deep" / "calibration.json"
        save_calibration_data(data, str(out))
        assert out.is_file()
        with open(out) as f:
            assert json.load(f) == data

    def test_raises_oserror_when_parent_is_a_file(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.touch()
        out = blocker / "sub" / "calibration.json"
        with pytest.raises(OSError):
            save_calibration_data({}, str(out))
