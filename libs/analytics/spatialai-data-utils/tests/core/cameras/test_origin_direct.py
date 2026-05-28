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

"""Direct tests for ``core.cameras.origin``'s lower-level
``calculate_group_origin*`` helpers.

The existing ``test_origin_calculation.py`` drives the high-level
``calculate_group_origins_from_calibration`` orchestrator end-to-end;
this file fills the gaps for the three direct entry points:

* ``calculate_group_origin`` — happy path with a FOV polygon, the
  ``polygon is None: continue`` branch, the ``MultiPolygon`` buffering
  branch, and the empty-union fallback that returns
  ``([0, 0], [0, 0, 0, 0])``.
* ``calculate_group_origin_from_frustum`` — happy path + the
  missing-camera-matrices warn-and-skip branch + the empty-result
  fallback.

The helpers consume the same synthetic sensor shape used by
``test_origin_calculation.py``; we reuse that fixture pattern here.
"""

import numpy as np
import pytest

from spatialai_data_utils.core.cameras.origin import (
    calculate_group_origin,
    calculate_group_origin_from_frustum,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_synthetic_sensors(n_cameras=2):
    """Build a list of synthetic sensors with both FOV polygons (used
    by ``calculate_group_origin``) and valid intrinsic/extrinsic
    matrices (used by ``calculate_group_origin_from_frustum``)."""
    sensors = []
    for idx in range(n_cameras):
        sensors.append({
            "id": f"Camera_{idx:02d}",
            "type": "camera",
            "intrinsicMatrix": [
                [1000.0, 0.0, 960.0],
                [0.0, 1000.0, 540.0],
                [0.0, 0.0, 1.0],
            ],
            "extrinsicMatrix": [
                [1.0, 0.0, 0.0, float(idx * 5)],
                [0.0, 0.866, -0.5, 2.0],
                [0.0, 0.5, 0.866, 5.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            "translationToGlobalCoordinates": {"x": 0.0, "y": 0.0},
            "scaleFactor": 3.0,
            "attributes": [
                {"name": "frameWidth", "value": "1920"},
                {"name": "frameHeight", "value": "1080"},
                {
                    "name": "fieldOfViewPolygon",
                    "value": (
                        f"POLYGON(({idx * 10} 0, {idx * 10 + 10} 0, "
                        f"{idx * 10 + 10} 10, {idx * 10} 10, {idx * 10} 0))"
                    ),
                },
            ],
        })
    return sensors


def _sensor_with_unparseable_fov():
    """Synthetic sensor whose ``fieldOfViewPolygon`` attribute string
    fails to parse (``parse_polygon`` returns None on bad WKT).
    Exercises the ``polygon is None: continue`` branch in
    ``calculate_group_origin``. (A sensor with the attribute entirely
    absent triggers ``ValueError`` upstream, not the branch we care
    about.)"""
    return {
        "id": "Camera_FOV_BAD",
        "attributes": [
            {"name": "frameWidth", "value": "1920"},
            {"name": "frameHeight", "value": "1080"},
            {"name": "fieldOfViewPolygon", "value": "NOT A VALID WKT STRING"},
        ],
    }


def _sensor_without_matrices():
    """Synthetic sensor without intrinsic/extrinsic matrices —
    exercises the ``extract_camera_matrices`` returns-None branch.
    Note: the warning log uses the **list index** (``sensor_id``) the
    helper iterates over, not the camera's ``id`` field — so the
    matching assertion targets the index, not the id string."""
    return {
        "id": "Camera_NO_MATRICES",
        "attributes": [
            {"name": "frameWidth", "value": "1920"},
            {"name": "frameHeight", "value": "1080"},
        ],
    }


# ---------------------------------------------------------------------------
# calculate_group_origin
# ---------------------------------------------------------------------------


class TestCalculateGroupOrigin:
    def test_returns_centroid_and_bounds_for_valid_polygons(self):
        sensors = _make_synthetic_sensors(n_cameras=2)
        origin, dims = calculate_group_origin(
            sensors, [0, 1], dilation_distance=1.0,
        )
        # origin is [center_x, center_y]
        assert len(origin) == 2
        assert all(isinstance(v, float) for v in origin)
        # dims is [x_min, y_min, x_max, y_max]
        assert len(dims) == 4
        x_min, y_min, x_max, y_max = dims
        assert x_max > x_min and y_max > y_min

    def test_skips_sensors_with_unparseable_polygon(self):
        """When ``parse_polygon`` returns None (unparseable WKT), that
        sensor is skipped without breaking the union of the rest."""
        sensors = [
            *_make_synthetic_sensors(n_cameras=1),
            _sensor_with_unparseable_fov(),
        ]
        _origin, dims = calculate_group_origin(sensors, [0, 1])
        # Should still produce a valid result from sensor 0 alone.
        x_min, y_min, x_max, y_max = dims
        assert x_max > x_min and y_max > y_min

    def test_empty_union_returns_zero_origin_and_zero_dimensions(self):
        """When every supplied sensor has a polygon that fails to
        parse, the function falls back to ``([0, 0], [0, 0, 0, 0])``."""
        sensors = [
            _sensor_with_unparseable_fov(),
            _sensor_with_unparseable_fov(),
        ]
        origin, dims = calculate_group_origin(sensors, [0, 1])
        assert origin == [0, 0]
        assert dims == [0, 0, 0, 0]

    def test_dilation_distance_grows_bounding_box(self):
        """A larger ``dilation_distance`` should expand the bbox."""
        sensors = _make_synthetic_sensors(n_cameras=1)
        _, dims_small = calculate_group_origin(sensors, [0], dilation_distance=0.1)
        _, dims_large = calculate_group_origin(sensors, [0], dilation_distance=5.0)
        # Larger dilation -> larger bbox extent in at least one dim.
        small_extent = (dims_small[2] - dims_small[0]) + (dims_small[3] - dims_small[1])
        large_extent = (dims_large[2] - dims_large[0]) + (dims_large[3] - dims_large[1])
        assert large_extent > small_extent


# ---------------------------------------------------------------------------
# calculate_group_origin_from_frustum
# ---------------------------------------------------------------------------


class TestCalculateGroupOriginFromFrustum:
    def test_happy_path_computes_origin_from_camera_matrices(self):
        sensors = _make_synthetic_sensors(n_cameras=2)
        origin, dims = calculate_group_origin_from_frustum(
            sensors, [0, 1],
            height_range=(1.0, 3.0),
            image_size=(1920, 1080),
            dilation_distance=1.0,
        )
        assert len(origin) == 2
        assert len(dims) == 4
        x_min, y_min, x_max, y_max = dims
        assert x_max > x_min and y_max > y_min

    def test_missing_matrices_warns_and_skips(self, caplog):
        """Sensors without intrinsic/extrinsic must be skipped (warned)
        without failing the whole call. The remaining sensor still
        contributes. The warning uses the ``sensor_id`` index passed
        to the function (not the camera's ``id`` field)."""
        import logging
        sensors = _make_synthetic_sensors(n_cameras=1) + [_sensor_without_matrices()]
        with caplog.at_level(logging.WARNING):
            origin, dims = calculate_group_origin_from_frustum(
                sensors, [0, 1],
                height_range=(1.0, 3.0),
                image_size=(1920, 1080),
            )
        x_min, y_min, x_max, y_max = dims
        # Sensor 0 still produced a valid frustum, so bounds are non-zero.
        assert x_max > x_min and y_max > y_min
        # Sensor 1 (the camera at list index 1) was skipped.
        assert "Could not extract camera matrices for sensor 1" in caplog.text

    def test_all_invalid_sensors_returns_zero_origin_and_dimensions(self, caplog):
        """When every sensor lacks usable matrices, we get the
        empty-union fallback ``([0, 0], [0, 0, 0, 0])``."""
        import logging
        sensors = [_sensor_without_matrices(), _sensor_without_matrices()]
        with caplog.at_level(logging.WARNING):
            origin, dims = calculate_group_origin_from_frustum(sensors, [0, 1])
        assert list(origin) == [0, 0]
        assert list(dims) == [0, 0, 0, 0]
        # The "No valid polygons" warning was emitted.
        assert "No valid polygons" in caplog.text
