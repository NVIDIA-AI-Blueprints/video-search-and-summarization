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

"""Coverage supplement for ``visualization.camera_placement.calibration_parser``
— pins ``_normalize_w2c_matrix`` 3x4 / invalid-shape branches,
``_parse_image_size`` defensive branches, and the
``invalid calibration matrices`` raise in
``load_camera_poses_from_calibration``."""

import json

import numpy as np
import pytest

from spatialai_data_utils.visualization.camera_placement.calibration_parser import (
    _normalize_w2c_matrix,
    _parse_image_size,
    load_camera_poses_from_calibration,
)


# ---------------------------------------------------------------------------
# _normalize_w2c_matrix — 3x4 input + invalid shape raise
# ---------------------------------------------------------------------------


def test_normalize_w2c_matrix_pads_3x4_to_4x4():
    m = np.array([
        [1, 0, 0, 1],
        [0, 1, 0, 2],
        [0, 0, 1, 3],
    ], dtype=np.float64)
    out = _normalize_w2c_matrix(m, sensor_id="Camera_01")
    assert out.shape == (4, 4)
    # Top-3 rows preserved; last row is the identity bottom.
    np.testing.assert_array_equal(out[:3, :], m)
    np.testing.assert_array_equal(out[3, :], [0, 0, 0, 1])


def test_normalize_w2c_matrix_passes_4x4_through_unchanged():
    m = np.eye(4, dtype=np.float64)
    out = _normalize_w2c_matrix(m, sensor_id="X")
    np.testing.assert_array_equal(out, m)


def test_normalize_w2c_matrix_raises_for_unsupported_shape():
    m = np.zeros((2, 3))
    with pytest.raises(ValueError, match="unsupported w2c matrix shape"):
        _normalize_w2c_matrix(m, sensor_id="X")


# ---------------------------------------------------------------------------
# _parse_image_size — defensive branches
# ---------------------------------------------------------------------------


def test_parse_image_size_none_returns_none():
    assert _parse_image_size(None) is None


def test_parse_image_size_non_sequence_returns_none():
    """A scalar, dict, or string isn't a list/tuple — returns None."""
    assert _parse_image_size("1920x1080") is None
    assert _parse_image_size(1920) is None
    assert _parse_image_size({"w": 1920, "h": 1080}) is None


def test_parse_image_size_too_short_returns_none():
    assert _parse_image_size([1920]) is None


def test_parse_image_size_non_numeric_returns_none():
    """When the entries can't be coerced to int (e.g. dict), the
    helper swallows the TypeError/ValueError and returns None."""
    assert _parse_image_size([{}, []]) is None
    assert _parse_image_size(["abc", "def"]) is None


def test_parse_image_size_valid_returns_int_tuple():
    out = _parse_image_size([1920.0, 1080.5])
    # Float input gets truncated to int.
    assert out == (1920, 1080)


# ---------------------------------------------------------------------------
# load_camera_poses_from_calibration — invalid calibration raise
# ---------------------------------------------------------------------------


def test_load_camera_poses_raises_on_invalid_matrices(tmp_path, monkeypatch):
    """A calibration whose ``extract_camera_matrices`` returns
    ``(None, None)`` triggers the 'invalid calibration matrices'
    raise. Provide a valid-on-disk calibration (so the upstream
    ``load_calib_into_dict`` succeeds), then patch
    ``extract_camera_matrices`` at the consumer-module level to
    return ``(None, None)``."""
    calib = tmp_path / "calib.json"
    calib.write_text(json.dumps({
        "sensors": [{
            "id": "Camera_01",
            "type": "camera",
            "attributes": [{"name": "fps", "value": "10"}],
            "intrinsicMatrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "extrinsicMatrix": [
                [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0],
            ],
        }],
    }))

    from spatialai_data_utils.visualization.camera_placement import (
        calibration_parser as mod,
    )
    monkeypatch.setattr(
        mod, "extract_camera_matrices", lambda calib_info: (None, None),
    )
    with pytest.raises(ValueError, match="invalid calibration matrices"):
        load_camera_poses_from_calibration(str(calib))
