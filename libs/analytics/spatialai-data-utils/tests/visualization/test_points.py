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

"""Tests for ``visualization.points`` 3D-vertex / keypoint overlays.

Both helpers are debug-only OpenCV overlays. Coverage focuses on
observable canvas effects and on the keypoint helper's two
behavioural promises:

* points behind the camera (``z <= 0`` after the world->image
  transform) are dropped before drawing, and
* points with ``z`` near zero are clipped to a safe finite division.
"""

import numpy as np

from spatialai_data_utils.visualization.points import (
    visualize_keypoints,
    visualize_vertices_3d,
)


def _blank_canvas(h=200, w=300):
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_visualize_vertices_3d_empty_list_passthrough():
    img = _blank_canvas()
    pristine = img.copy()
    out = visualize_vertices_3d(img, [])
    np.testing.assert_array_equal(out, pristine)


def test_visualize_vertices_3d_draws_for_every_supplied_vertex():
    img = _blank_canvas()
    gt_dicts = [
        {"2d vertices of 3d bounding box": np.array([
            [50, 60], [70, 60], [70, 80], [50, 80],
            [55, 65], [65, 65], [65, 75], [55, 75],
        ])},
        {"2d vertices of 3d bounding box": np.array([
            [150, 100], [180, 100], [180, 140], [150, 140],
            [155, 105], [175, 105], [175, 135], [155, 135],
        ])},
    ]
    out = visualize_vertices_3d(img, gt_dicts)
    # Each box contributes 8 markers in two distinct regions; the
    # combined-canvas sum must reflect both.
    box1_region = out[40:90, 40:80]
    box2_region = out[90:150, 140:190]
    assert box1_region.sum() > 0
    assert box2_region.sum() > 0


def test_visualize_keypoints_empty_list_passthrough():
    img = _blank_canvas()
    pristine = img.copy()
    transform = np.eye(4)
    out = visualize_keypoints(img, [], transform)
    np.testing.assert_array_equal(out, pristine)


def test_visualize_keypoints_drops_points_behind_camera():
    """A 3D point with ``z = -1`` projects with negative depth after the
    identity transform and must be filtered before drawing — the
    canvas should remain untouched."""
    img = _blank_canvas()
    pristine = img.copy()
    gt_dicts = [{"3d keypoints": np.array([[10.0, 20.0, -1.0]])}]
    transform = np.eye(4)
    out = visualize_keypoints(img, gt_dicts, transform)
    np.testing.assert_array_equal(out, pristine)


def test_visualize_keypoints_draws_points_in_front_of_camera():
    """A simple pinhole-style projection: with a transform that scales
    x, y by depth, a point at (50, 30, 1) projects to pixel (50, 30)."""
    img = _blank_canvas()
    gt_dicts = [{"3d keypoints": np.array([[50.0, 30.0, 1.0]])}]
    transform = np.eye(4)  # pts_2d = pts_3d @ I, then divide by z=1
    out = visualize_keypoints(img, gt_dicts, transform)
    # The drawn star marker should leave coloured pixels in a small
    # neighbourhood of (50, 30) — sample a 10x10 window around it.
    window = out[25:36, 45:56]
    assert window.sum() > 0, "expected the projected keypoint to be drawn"
