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

"""Tests for ``core.boxes.box_2d`` helpers.

Pins (a) the half-open ``[0, W)`` × ``[0, H)`` semantics shared by
``is_inside_image_any`` / ``is_inside_image_all``, and (b) the
clamp-then-AABB behaviour of ``get_box_2d_from_projected_vertices``
that the bbox-3d-projection CLI relies on to keep image-AABB outputs
inside the canvas.
"""

import numpy as np

from spatialai_data_utils.core.boxes.box_2d import (
    get_box_2d_from_projected_vertices,
    is_inside_image_all,
    is_inside_image_any,
)

IMAGE_SIZE = (1920, 1080)  # (W, H)


def test_is_inside_image_any_finds_at_least_one_point():
    pts = np.array([
        [-10, -10],
        [50, 50],     # inside
        [3000, 3000],
    ])
    assert is_inside_image_any(pts, IMAGE_SIZE) is np.True_ or \
           bool(is_inside_image_any(pts, IMAGE_SIZE)) is True


def test_is_inside_image_any_returns_false_when_all_out():
    pts = np.array([
        [-1, -1],
        [3000, 3000],
        [-50, 2000],
    ])
    assert bool(is_inside_image_any(pts, IMAGE_SIZE)) is False


def test_is_inside_image_all_requires_every_point_inside():
    inside_pts = np.array([[10, 10], [100, 200], [1919, 1079]])
    assert bool(is_inside_image_all(inside_pts, IMAGE_SIZE)) is True

    mixed = np.array([[10, 10], [3000, 100]])
    assert bool(is_inside_image_all(mixed, IMAGE_SIZE)) is False


def test_image_bounds_are_half_open():
    """Both helpers use ``< W`` / ``< H`` so a pixel at coordinate ``W``
    or ``H`` is outside. Pin that to match OpenCV array indexing."""
    on_edge_w = np.array([[1920, 100]])
    on_edge_h = np.array([[100, 1080]])
    assert bool(is_inside_image_any(on_edge_w, IMAGE_SIZE)) is False
    assert bool(is_inside_image_any(on_edge_h, IMAGE_SIZE)) is False


def test_get_box_2d_from_projected_vertices_returns_clamped_aabb():
    """A single box (8 vertices) should collapse to one row of
    ``[xmin, ymin, xmax, ymax]`` clamped to the image canvas."""
    vertices = np.array([[[
        (-100, -50),
        (2000, -50),
        (2000, 1500),
        (-100, 1500),
        (-100, -50),
        (2000, -50),
        (2000, 1500),
        (-100, 1500),
    ]]], dtype=float).squeeze(0)  # shape (1, 8, 2)

    out = get_box_2d_from_projected_vertices(vertices, IMAGE_SIZE)

    assert out.shape == (1, 4)
    xmin, ymin, xmax, ymax = out[0]
    assert xmin == 0
    assert ymin == 0
    assert xmax == 1920
    assert ymax == 1080


def test_get_box_2d_from_projected_vertices_preserves_in_canvas_boxes():
    vertices = np.array([[[
        (100, 200), (300, 200), (300, 400), (100, 400),
        (100, 200), (300, 200), (300, 400), (100, 400),
    ]]], dtype=float).squeeze(0)  # (1, 8, 2)

    out = get_box_2d_from_projected_vertices(vertices, IMAGE_SIZE)
    np.testing.assert_array_equal(out, [[100, 200, 300, 400]])


def test_get_box_2d_from_projected_vertices_empty_passthrough():
    """Empty input returns the input array unchanged (no shape change)."""
    empty = np.empty((0, 8, 2), dtype=float)
    out = get_box_2d_from_projected_vertices(empty, IMAGE_SIZE)
    assert out.shape == (0, 8, 2)
