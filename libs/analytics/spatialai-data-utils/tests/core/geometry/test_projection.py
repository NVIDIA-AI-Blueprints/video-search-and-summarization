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

"""Tests for the pure-numpy 3D geometry projection module.

Covers:
- ``spatialai_data_utils.core.boxes.box_3d.box3d_to_corners`` (with origin param)
- ``spatialai_data_utils.core.geometry.projection.vertices_to_aabb``
- ``spatialai_data_utils.core.geometry.projection.project_points_3d_to_image``
- ``spatialai_data_utils.core.geometry.projection.unproject_points_2d_to_3d``
- ``spatialai_data_utils.core.geometry.projection.unproject_points_2d_to_ground``
- ``spatialai_data_utils.core.geometry.projection.unproject_bbox3d_via_ground``
- ``spatialai_data_utils.core.geometry.projection.project_boxes_3d_to_2d``

All tests are self-contained — no external data files required.
"""

import numpy as np
import pytest

from spatialai_data_utils.core.boxes.box_3d import (
    box3d_to_corners,
    check_nvschema_coords_len,
)
from spatialai_data_utils.core.geometry.projection import (
    BBOX3D_BOTTOM_INDICES,
    BBOX3D_TOP_INDICES,
    project_boxes_3d_to_2d,
    project_points_3d_to_image,
    unproject_bbox3d_via_ground,
    unproject_points_2d_to_3d,
    unproject_points_2d_to_ground,
    vertices_to_aabb,
)


class TestCheckNvschemaCoordsLen:
    """Unit tests for the NVSchema bbox3d.coordinates length validator."""

    def test_9_values_pass(self):
        """9-element input is accepted silently (no raise, no return value)."""
        assert check_nvschema_coords_len(
            [1, 2, 3, 4, 5, 6, 0.1, 0.2, 0.7]
        ) is None

    def test_7_value_rejected(self):
        """Legacy 7-value input is no longer accepted (NVSchema >= 9)."""
        with pytest.raises(ValueError, match="at least 9 values"):
            check_nvschema_coords_len([1, 2, 3, 4, 5, 6, 0.7])

    def test_short_lengths_rejected(self):
        """Anything shorter than 9 raises ValueError with a clear message."""
        with pytest.raises(ValueError, match="at least 9 values"):
            check_nvschema_coords_len([1, 2, 3, 4, 5, 6, 0, 0])  # 8
        with pytest.raises(ValueError, match="at least 9 values"):
            check_nvschema_coords_len([1, 2, 3, 4, 5])           # 5
        with pytest.raises(ValueError, match="at least 9 values"):
            check_nvschema_coords_len([])                        # 0

    def test_extra_trailing_values_accepted(self):
        """Inputs with length >= 9 pass (trailing extras ignored downstream)."""
        # Common case: velocity components appended after yaw.
        assert check_nvschema_coords_len([0.0] * 10) is None
        assert check_nvschema_coords_len([0.0] * 12) is None
        assert check_nvschema_coords_len(
            [1, 2, 3, 4, 5, 6, 0.1, 0.2, 0.7, 99.0, -99.0]
        ) is None

    def test_accepts_numpy_array(self):
        """9-element numpy inputs (e.g. decoded JSON arrays) work."""
        arr = np.array(
            [1.0, 2.0, 3.0, 0.5, 0.5, 1.8, 0.0, 0.0, 0.7], dtype=np.float32,
        )
        check_nvschema_coords_len(arr)  # must not raise


class TestVerticesToAabb:
    """Unit tests for ``vertices_to_aabb``."""

    def test_basic_box(self):
        """Flat (N, 2) input yields [left, top, right, bottom]."""
        verts = np.array(
            [[10.0, 20.0], [30.0, 40.0], [25.0, 15.0], [5.0, 35.0]]
        )
        left, top, right, bottom = vertices_to_aabb(verts)
        assert (left, top, right, bottom) == (5.0, 15.0, 30.0, 40.0)

    def test_returns_python_floats(self):
        """Output is a 4-element list of built-in floats (JSON-safe)."""
        verts = np.array([[1.0, 2.0], [3.0, 4.0]])
        out = vertices_to_aabb(verts)
        assert isinstance(out, list) and len(out) == 4
        assert all(isinstance(v, float) for v in out)

    def test_accepts_nested_list(self):
        """Plain Python lists are accepted (converted via np.asarray)."""
        out = vertices_to_aabb([[0, 0], [10, 5]])
        assert out == [0.0, 0.0, 10.0, 5.0]

    def test_single_point(self):
        """A single vertex degenerates to a zero-area box."""
        out = vertices_to_aabb([[7.5, 3.25]])
        assert out == [7.5, 3.25, 7.5, 3.25]


def _make_calib(fx=500.0, fy=500.0, cx=320.0, cy=240.0, w2c=None):
    """Build a minimal calib dict with a pinhole intrinsic and 4x4 w2c."""
    intrinsic = np.array(
        [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=np.float64
    )
    if w2c is None:
        w2c = np.eye(4, dtype=np.float64)
    return {
        "intrinsic_matrix": intrinsic,
        "w2c_matrix": np.asarray(w2c, dtype=np.float64),
    }


# =====================================================================
# Tests for core box3d_to_corners (with origin param)
# =====================================================================

class TestBox3dToCornersWithOrigin:
    """Tests for the pure-numpy core ``box3d_to_corners`` (9-DoF, configurable origin)."""

    def test_output_shape_single(self):
        """A single (N=1) 9-DoF box yields corners of shape (1, 8, 3)."""
        corners = box3d_to_corners(np.array([[0, 0, 0, 2, 4, 1, 0.0, 0.0, 0.0]]))
        assert corners.shape == (1, 8, 3)

    def test_output_shape_batch(self):
        """A batch of N 9-DoF boxes yields corners of shape (N, 8, 3)."""
        boxes = np.zeros((5, 9))
        boxes[:, 3:6] = 1.0  # w, l, h = 1
        corners = box3d_to_corners(boxes)
        assert corners.shape == (5, 8, 3)

    def test_1d_input_promotes(self):
        """A 1-D 9-DoF input is auto-promoted to a batch of one box."""
        corners = box3d_to_corners(np.array([0, 0, 0, 2, 4, 1, 0.0, 0.0, 0.0]))
        assert corners.shape == (1, 8, 3)

    def test_default_origin_is_geometric_center(self):
        """Default origin=(0.5, 0.5, 0.5) centres z on the given ``z`` value."""
        box = np.array([[0.0, 0.0, 5.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0]])
        corners = box3d_to_corners(box)  # no explicit origin
        assert np.isclose(corners[0, :, 2].min(), 4.5)
        assert np.isclose(corners[0, :, 2].max(), 5.5)

    def test_origin_center_bottom(self):
        """With origin=(0.5, 0.5, 0.0), box centre z is at the bottom."""
        box = np.array([[0.0, 0.0, 5.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0]])
        corners = box3d_to_corners(box, origin=(0.5, 0.5, 0.0))
        # z should span [5.0, 6.0] — bottom at z=5 (the given centre).
        assert np.isclose(corners[0, :, 2].min(), 5.0)
        assert np.isclose(corners[0, :, 2].max(), 6.0)

    def test_origin_geometric_center(self):
        """With origin=(0.5, 0.5, 0.5), box centre is the geometric centre."""
        box = np.array([[0.0, 0.0, 5.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0]])
        corners = box3d_to_corners(box, origin=(0.5, 0.5, 0.5))
        # z should span [4.5, 5.5] — centred on the given z=5.
        assert np.isclose(corners[0, :, 2].min(), 4.5)
        assert np.isclose(corners[0, :, 2].max(), 5.5)

    def test_translation(self):
        """Box centre position is preserved in the x/y corner mean."""
        box = np.array([[10.0, 20.0, 30.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0]])
        corners = box3d_to_corners(box, origin=(0.5, 0.5, 0.5))
        np.testing.assert_allclose(corners[0].mean(axis=0), [10, 20, 30], atol=1e-10)

    def test_rotation_90deg(self):
        """A 90-degree yaw swaps X and Y extents while preserving Z."""
        box_0 = np.array([[0.0, 0.0, 0.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0]])
        box_90 = np.array([[0.0, 0.0, 0.0, 2.0, 4.0, 1.0, 0.0, 0.0, np.pi / 2]])
        c0 = box3d_to_corners(box_0, origin=(0.5, 0.5, 0.5))
        c90 = box3d_to_corners(box_90, origin=(0.5, 0.5, 0.5))
        assert np.isclose(np.ptp(c90[0, :, 0]), 4.0, atol=1e-6)
        assert np.isclose(np.ptp(c90[0, :, 1]), 2.0, atol=1e-6)
        assert np.isclose(np.ptp(c0[0, :, 2]), np.ptp(c90[0, :, 2]))

    def test_rejects_7_element_input(self):
        """Legacy 7-DoF [x, y, z, w, l, h, yaw] arrays are rejected."""
        with pytest.raises(ValueError, match="requires 9-DoF"):
            box3d_to_corners(np.array([[0, 0, 0, 2, 4, 1, 0.3]]))
        with pytest.raises(ValueError, match="requires 9-DoF"):
            box3d_to_corners(np.array([[0, 0, 0, 2, 4, 1, 0.3, 99.0]]))  # 8-wide
        with pytest.raises(ValueError, match="requires 9-DoF"):
            box3d_to_corners(np.array([0, 0, 0, 2, 4, 1, 0.3]))  # 1-D, 7 values

    def test_extra_columns_ignored(self):
        """Trailing columns beyond index 8 (e.g. velocity) are ignored."""
        box_10 = np.array([[0, 0, 0, 2, 4, 1, 0.0, 0.0, 0.0, 99.0]])
        np.testing.assert_allclose(
            box3d_to_corners(box_10),
            box3d_to_corners(box_10[:, :9]),
        )

    def test_9dof_pure_roll(self):
        """A pure roll (around X) rotates Y/Z extents; X extent is preserved.

        Box: w=2, l=4, h=1 centred at origin with roll=pi/2. The rotation
        maps (x, y, z) -> (x, -z, y), so the rotated bounding box has
        X extent = w = 2, Y extent = h = 1, Z extent = l = 4.
        """
        box = np.array([[0, 0, 0, 2.0, 4.0, 1.0, np.pi / 2, 0.0, 0.0]])
        corners = box3d_to_corners(box, origin=(0.5, 0.5, 0.5))[0]
        assert np.isclose(np.ptp(corners[:, 0]), 2.0, atol=1e-10)  # X = w
        assert np.isclose(np.ptp(corners[:, 1]), 1.0, atol=1e-10)  # Y = h
        assert np.isclose(np.ptp(corners[:, 2]), 4.0, atol=1e-10)  # Z = l

    def test_9dof_pure_pitch(self):
        """A pure pitch (around Y) rotates X/Z extents; Y extent is preserved.

        Box: w=2, l=4, h=1 centred at origin with pitch=pi/2. Mapping
        (x, y, z) -> (z, y, -x) gives X extent = h = 1, Y extent = l = 4,
        Z extent = w = 2.
        """
        box = np.array([[0, 0, 0, 2.0, 4.0, 1.0, 0.0, np.pi / 2, 0.0]])
        corners = box3d_to_corners(box, origin=(0.5, 0.5, 0.5))[0]
        assert np.isclose(np.ptp(corners[:, 0]), 1.0, atol=1e-10)  # X = h
        assert np.isclose(np.ptp(corners[:, 1]), 4.0, atol=1e-10)  # Y = l
        assert np.isclose(np.ptp(corners[:, 2]), 2.0, atol=1e-10)  # Z = w

    def test_9dof_composite_rotation_matches_hand_computed(self):
        """9-DoF rotation matches R_z(yaw) @ R_y(roll) @ R_x(pitch).

        Build the reference matrix numerically from the NVSchema
        ZYX-intrinsic convention (same as
        ``rotation.euler_from_quaternion``) and compare against the
        helper's output for a non-trivial (pitch, roll, yaw) under
        the body-frame naming used by
        :mod:`spatialai_data_utils.core.boxes.box_3d`.
        """
        pitch, roll, yaw = 0.3, -0.4, 0.5
        cp, sp = np.cos(pitch), np.sin(pitch)
        cr, sr = np.cos(roll), np.sin(roll)
        cy, sy = np.cos(yaw), np.sin(yaw)
        rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
        ry = np.array([[cr, 0, sr], [0, 1, 0], [-sr, 0, cr]])
        rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
        r_expected = rz @ ry @ rx

        whl = np.array([2.0, 4.0, 1.0])
        centre = np.array([1.0, -2.0, 5.0])
        box = np.array([[*centre, *whl, pitch, roll, yaw]])
        corners = box3d_to_corners(box, origin=(0.5, 0.5, 0.5))[0]

        # Reference corners: unit-cube corners scaled by whl and rotated.
        unit_corners = np.stack(
            np.unravel_index(np.arange(8), [2] * 3), axis=1
        ).astype(np.float64)
        unit_corners = unit_corners[[0, 1, 3, 2, 4, 5, 7, 6]] - 0.5
        local = unit_corners * whl  # (8, 3)
        expected = (r_expected @ local.T).T + centre  # (8, 3)

        np.testing.assert_allclose(corners, expected, atol=1e-12)

    def test_bottom_face_indices(self):
        """Indices 0, 3, 4, 7 are the bottom face (min-z corners)."""
        box = np.array([[0.0, 0.0, 5.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0]])
        corners = box3d_to_corners(box, origin=(0.5, 0.5, 0.0))
        bottom_z = corners[0, [0, 3, 4, 7], 2]
        top_z = corners[0, [1, 2, 5, 6], 2]
        np.testing.assert_allclose(bottom_z, 5.0)
        np.testing.assert_allclose(top_z, 6.0)

    def test_visualization_wrapper_matches_centered(self):
        """visualization.box_3d.box3d_to_corners matches origin=(0.5, 0.5, 0.5)."""
        from spatialai_data_utils.visualization.box_3d import (
            box3d_to_corners as viz_box3d_to_corners,
        )
        box = np.array([[1.0, 2.0, 3.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.3]])
        np.testing.assert_allclose(
            viz_box3d_to_corners(box),
            box3d_to_corners(box, origin=(0.5, 0.5, 0.5)),
        )


# =====================================================================
# Tests for project_points_3d_to_image
# =====================================================================

class TestProjectPointsToImage:
    """Tests for the pixel-space projection helper (with perspective divide)."""

    def test_2d_input_shape_preserved(self):
        """Input (N, 3) yields pixels (N, 2) and front_mask (N,)."""
        pts = np.array([[0.0, 0.0, 20.0], [1.0, 2.0, 20.0]])
        w2i = np.eye(4)
        w2i[0, 0] = 500
        w2i[1, 1] = 500
        w2i[0, 2] = 320
        w2i[1, 2] = 240
        pixels, front_mask = project_points_3d_to_image(pts, w2i)
        assert pixels.shape == (2, 2)
        assert front_mask.shape == (2,) and front_mask.dtype == bool
        assert front_mask.all()  # both points are at z=20

    def test_3d_input_shape_preserved(self):
        """Input (N, M, 3) yields pixels (N, M, 2) and front_mask (N, M)."""
        pts = np.zeros((4, 8, 3))
        pts[..., 2] = 20.0
        pixels, front_mask = project_points_3d_to_image(pts, np.eye(4))
        assert pixels.shape == (4, 8, 2)
        assert front_mask.shape == (4, 8) and front_mask.dtype == bool
        assert front_mask.all()

    def test_principal_point_projection(self):
        """A point at (0, 0, z) projects to the principal point (cx, cy)."""
        cx, cy = 320.0, 240.0
        w2i = np.eye(4)
        w2i[0, 0] = 500
        w2i[1, 1] = 500
        w2i[0, 2] = cx
        w2i[1, 2] = cy
        pts = np.array([[0.0, 0.0, 20.0]])
        pixels, front_mask = project_points_3d_to_image(pts, w2i)
        np.testing.assert_allclose(pixels, [[cx, cy]])
        assert front_mask[0]

    def test_behind_camera_masked_out(self):
        """Points with z <= min_depth are flagged front_mask=False.

        The pixel values for masked entries are produced from a clipped
        z (no div-by-zero) but are garbage — callers must consult the
        mask.  This test just asserts the mask semantics.
        """
        w2i = np.eye(4)
        pts = np.array([
            [1.0, 1.0, 20.0],   # in front
            [1.0, 1.0, 0.0],    # on image plane (z <= min_depth)
            [1.0, 1.0, -5.0],   # behind camera
        ])
        pixels, front_mask = project_points_3d_to_image(
            pts, w2i, min_depth=1e-5,
        )
        assert pixels.shape == (3, 2)
        # Only the in-front point is flagged valid.
        np.testing.assert_array_equal(front_mask, [True, False, False])
        # Pixels are finite even for invalid entries (clipped z, no NaN / inf).
        assert np.all(np.isfinite(pixels))

    def test_does_not_filter_visibility(self):
        """Points behind the camera are still present in the output shape.

        The function never drops points — it only flags them via
        ``front_mask``.  Callers decide what to do with invalid pixels.
        """
        w2i = np.eye(4)
        pts = np.array([[1.0, 1.0, -5.0]])
        pixels, front_mask = project_points_3d_to_image(pts, w2i)
        assert pixels.shape == (1, 2)
        assert front_mask.shape == (1,)
        assert not front_mask[0]

    def test_3x4_matrix_accepted(self):
        """A 3x4 projection matrix is accepted (same math as 4x4)."""
        pts = np.array([[0.0, 0.0, 20.0]])
        # 3x4 version of a pinhole at principal point (cx, cy) with f=500.
        mat_3x4 = np.array(
            [[500.0, 0.0, 320.0, 0.0],
             [0.0, 500.0, 240.0, 0.0],
             [0.0, 0.0, 1.0, 0.0]],
            dtype=np.float64,
        )
        pixels, front_mask = project_points_3d_to_image(pts, mat_3x4)
        np.testing.assert_allclose(pixels, [[320.0, 240.0]])
        assert front_mask[0]


# =====================================================================
# Tests for unproject_points_2d_to_3d
# =====================================================================

def _pinhole_world2img(fx=500.0, fy=500.0, cx=320.0, cy=240.0, w2c=None):
    """Build a 3x4 world-to-image matrix from a pinhole intrinsic + 4x4 w2c."""
    intrinsic = np.array(
        [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=np.float64,
    )
    if w2c is None:
        w2c = np.eye(4, dtype=np.float64)
    return intrinsic @ np.asarray(w2c, dtype=np.float64)[:3]


class TestUnprojectPointsToWorld:
    """Tests for the inverse pixel -> 3D world map (with given depth)."""

    def test_round_trip_single_point(self):
        """Project then unproject recovers the original 3D point."""
        w2i = _pinhole_world2img()
        pt3d = np.array([1.5, -2.0, 7.0])
        pixels, _ = project_points_3d_to_image(pt3d, w2i)
        # Camera-space depth: w2c is identity, so depth == pt3d[2].
        depth = 7.0
        recovered = unproject_points_2d_to_3d(pixels, w2i, depth)
        np.testing.assert_allclose(recovered, pt3d, atol=1e-9)

    def test_round_trip_batch(self):
        """Round-trip works for a batch of points with per-point depths."""
        w2i = _pinhole_world2img()
        pts3d = np.array([
            [0.0,  0.0, 10.0],
            [1.0,  2.0, 15.0],
            [-3.0, 0.5,  5.0],
            [4.0, -1.0, 25.0],
        ])
        pixels, front_mask = project_points_3d_to_image(pts3d, w2i)
        assert front_mask.all()
        depths = pts3d[:, 2]  # identity w2c -> camera depth == world Z
        recovered = unproject_points_2d_to_3d(pixels, w2i, depths)
        np.testing.assert_allclose(recovered, pts3d, atol=1e-9)

    def test_round_trip_with_nontrivial_extrinsic(self):
        """Round-trip works when the camera is rotated and translated."""
        # Translate camera by (2, -1, 3) and rotate 30 deg around Y in world.
        theta = np.pi / 6
        cy_, sy_ = np.cos(theta), np.sin(theta)
        r = np.array([[cy_, 0.0, sy_], [0.0, 1.0, 0.0], [-sy_, 0.0, cy_]])
        t = np.array([2.0, -1.0, 3.0])
        w2c = np.eye(4)
        w2c[:3, :3] = r
        w2c[:3, 3] = t
        w2i = _pinhole_world2img(w2c=w2c)

        pts3d = np.array([
            [0.0,  0.0, 10.0],
            [1.0,  2.0, 15.0],
            [-2.0, 0.5,  8.0],
        ])
        pixels, front_mask = project_points_3d_to_image(pts3d, w2i)
        assert front_mask.all()

        # Camera-space depth = (R @ X + t)[2].
        cam_pts = pts3d @ r.T + t
        depths = cam_pts[:, 2]
        recovered = unproject_points_2d_to_3d(pixels, w2i, depths)
        np.testing.assert_allclose(recovered, pts3d, atol=1e-9)

    def test_output_shape_single_point(self):
        """A single ``(2,)`` pixel + scalar depth yields a ``(3,)`` 3D point."""
        w2i = _pinhole_world2img()
        out = unproject_points_2d_to_3d(np.array([320.0, 240.0]), w2i, 5.0)
        assert out.shape == (3,)
        assert out.dtype == np.float64

    def test_output_shape_batch(self):
        """``(N, 2)`` pixels + ``(N,)`` depths yield a ``(N, 3)`` array."""
        w2i = _pinhole_world2img()
        pixels = np.array([[320.0, 240.0], [100.0, 50.0], [500.0, 300.0]])
        depths = np.array([5.0, 10.0, 20.0])
        out = unproject_points_2d_to_3d(pixels, w2i, depths)
        assert out.shape == (3, 3)
        assert out.dtype == np.float64

    def test_output_shape_higher_rank(self):
        """``(B, N, 2)`` pixels + ``(B, N)`` depths yield ``(B, N, 3)``."""
        w2i = _pinhole_world2img()
        rng = np.random.default_rng(0)
        pixels = rng.uniform(0, 640, size=(4, 5, 2))
        depths = rng.uniform(1, 50, size=(4, 5))
        out = unproject_points_2d_to_3d(pixels, w2i, depths)
        assert out.shape == (4, 5, 3)

    def test_scalar_depth_broadcasts_over_batch(self):
        """A Python-float depth broadcasts to all pixels in the batch."""
        w2i = _pinhole_world2img()
        pixels = np.array([[100.0, 200.0], [300.0, 400.0]])
        out = unproject_points_2d_to_3d(pixels, w2i, 5.0)
        assert out.shape == (2, 3)
        # Re-projecting must reproduce the input pixels at depth 5.
        re_px, _ = project_points_3d_to_image(out, w2i)
        np.testing.assert_allclose(re_px, pixels, atol=1e-9)

    def test_principal_point_unprojects_to_optical_axis(self):
        """The principal point at depth z unprojects to (0, 0, z) for identity w2c."""
        cx, cy = 320.0, 240.0
        w2i = _pinhole_world2img(cx=cx, cy=cy)
        out = unproject_points_2d_to_3d(np.array([cx, cy]), w2i, 12.0)
        np.testing.assert_allclose(out, [0.0, 0.0, 12.0], atol=1e-9)

    def test_3x4_and_4x4_matrices_match(self):
        """3x4 and 4x4 (with [0,0,0,1] tail) projection matrices give the same answer."""
        w2i_3x4 = _pinhole_world2img()
        w2i_4x4 = np.eye(4, dtype=np.float64)
        w2i_4x4[:3] = w2i_3x4

        pixels = np.array([[100.0, 200.0], [300.0, 400.0]])
        depths = np.array([5.0, 10.0])
        out_3x4 = unproject_points_2d_to_3d(pixels, w2i_3x4, depths)
        out_4x4 = unproject_points_2d_to_3d(pixels, w2i_4x4, depths)
        np.testing.assert_allclose(out_3x4, out_4x4, atol=1e-12)

    def test_negative_depth_allowed(self):
        """Negative depth produces a valid 3D point on the back-extended ray.

        The function performs no visibility filtering; callers decide
        whether negative-depth points are physically meaningful.
        """
        w2i = _pinhole_world2img()
        pixels = np.array([320.0, 240.0])
        out_pos = unproject_points_2d_to_3d(pixels, w2i, 5.0)
        out_neg = unproject_points_2d_to_3d(pixels, w2i, -5.0)
        # Points lie on the same viewing ray on opposite sides of the camera.
        np.testing.assert_allclose(out_pos, [0.0, 0.0, 5.0], atol=1e-9)
        np.testing.assert_allclose(out_neg, [0.0, 0.0, -5.0], atol=1e-9)

    def test_singular_matrix_raises(self):
        """A degenerate world2img with a singular 3x3 block raises LinAlgError."""
        w2i = _pinhole_world2img()
        w2i[:, 0] = 0.0  # zero out the first column -> rank-deficient
        with pytest.raises(np.linalg.LinAlgError):
            unproject_points_2d_to_3d(np.array([100.0, 200.0]), w2i, 5.0)

    def test_wrong_last_dim_raises(self):
        """Inputs whose last axis is not 2 are rejected up-front."""
        w2i = _pinhole_world2img()
        # 3D points sneakily passed in: shape (N, 3) instead of (N, 2).
        with pytest.raises(ValueError, match="last dim must be 2"):
            unproject_points_2d_to_3d(
                np.array([[1.0, 2.0, 3.0]]), w2i, np.array([5.0]),
            )
        # 0-d / scalar input.
        with pytest.raises(ValueError, match="last dim must be 2"):
            unproject_points_2d_to_3d(np.float64(3.0), w2i, 5.0)
        # 1-D with the wrong length.
        with pytest.raises(ValueError, match="last dim must be 2"):
            unproject_points_2d_to_3d(np.array([1.0, 2.0, 3.0]), w2i, 5.0)

    def test_does_not_mutate_inputs(self):
        """The input pixel and depth arrays are not modified in place."""
        w2i = _pinhole_world2img()
        pixels = np.array([[100.0, 200.0], [300.0, 400.0]])
        depths = np.array([5.0, 10.0])
        pixels_copy = pixels.copy()
        depths_copy = depths.copy()
        unproject_points_2d_to_3d(pixels, w2i, depths)
        np.testing.assert_array_equal(pixels, pixels_copy)
        np.testing.assert_array_equal(depths, depths_copy)

    def test_accepts_python_lists(self):
        """Plain Python lists work via ``np.asarray`` coercion."""
        w2i = _pinhole_world2img()
        out = unproject_points_2d_to_3d([320.0, 240.0], w2i.tolist(), 7.0)
        np.testing.assert_allclose(out, [0.0, 0.0, 7.0], atol=1e-9)


# =====================================================================
# Tests for unproject_points_2d_to_ground
# =====================================================================

def _down_looking_camera(height=10.0, fx=500.0, fy=500.0, cx=320.0, cy=240.0):
    """Build a world2img for a camera at (0, 0, height) looking straight down.

    Camera +z (forward) maps to world -z, camera +x maps to world +x,
    camera +y maps to world +y.  Ground (Z=0) appears at depth=height.
    """
    K = np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]])
    w2c = np.array([
        [1.0, 0.0,  0.0,    0.0],
        [0.0, 1.0,  0.0,    0.0],
        [0.0, 0.0, -1.0, height],
        [0.0, 0.0,  0.0,    1.0],
    ])
    return K @ w2c[:3], w2c


def _forward_looking_camera(height=5.0, fx=500.0, fy=500.0, cx=320.0, cy=240.0):
    """Build a world2img for a camera at (0, 0, height) looking along +Y.

    Camera +z (forward) maps to world +y, camera +x = world +x, camera
    +y (down in image) = world -z.  Image-bottom pixels see the ground
    in front; image-top pixels see the sky (no ground intersection).
    """
    K = np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]])
    w2c = np.array([
        [1.0, 0.0,  0.0,    0.0],
        [0.0, 0.0, -1.0, height],
        [0.0, 1.0,  0.0,    0.0],
        [0.0, 0.0,  0.0,    1.0],
    ])
    return K @ w2c[:3], w2c


class TestUnprojectPointsToGround:
    """Tests for the ground-plane unprojection (depth-free)."""

    def test_round_trip_down_looking(self):
        """Project a ground point, then unproject — must recover original."""
        w2i, _ = _down_looking_camera(height=10.0)
        gt_xy = np.array([3.0, 2.0])
        # Forward-project the ground point (3, 2, 0) to a pixel.
        pt3d = np.array([gt_xy[0], gt_xy[1], 0.0])
        pixels, front = project_points_3d_to_image(pt3d, w2i)
        assert front
        # Unproject back to the ground plane (no depth supplied).
        recovered, mask = unproject_points_2d_to_ground(pixels, w2i)
        assert mask
        np.testing.assert_allclose(recovered, [gt_xy[0], gt_xy[1], 0.0],
                                   atol=1e-9)

    def test_round_trip_batch_random(self):
        """Round-trip works for a batch of random ground points."""
        w2i, _ = _forward_looking_camera(height=5.0)
        rng = np.random.default_rng(123)
        # Pick random ground points in front of the camera (positive Y).
        gt = np.column_stack([
            rng.uniform(-5.0, 5.0, size=64),
            rng.uniform(2.0, 50.0, size=64),
            np.zeros(64),
        ])
        pixels, front = project_points_3d_to_image(gt, w2i)
        assert front.all()

        recovered, mask = unproject_points_2d_to_ground(pixels, w2i)
        assert mask.all()
        np.testing.assert_allclose(recovered, gt, atol=1e-9)

    def test_z_is_exactly_ground_z(self):
        """Output Z is forced to ``ground_z`` exactly (no float drift)."""
        w2i, _ = _down_looking_camera(height=12.5)
        pixels = np.array([[100.0, 50.0], [400.0, 300.0], [500.0, 200.0]])
        recovered, mask = unproject_points_2d_to_ground(pixels, w2i,
                                                        ground_z=0.0)
        assert mask.all()
        # Bit-exact equality (not "close to") on the Z component.
        assert np.array_equal(recovered[:, 2], np.zeros(3))

        recovered2, _ = unproject_points_2d_to_ground(pixels, w2i,
                                                     ground_z=1.5)
        assert np.array_equal(recovered2[:, 2], np.full(3, 1.5))

    def test_ground_z_offset(self):
        """``ground_z`` shifts the target plane: a point at world Z=h round-trips."""
        w2i, _ = _forward_looking_camera(height=5.0)
        h = 1.5
        # Point on the elevated plane Z=h.
        pt3d = np.array([2.0, 12.0, h])
        pixels, front = project_points_3d_to_image(pt3d, w2i)
        assert front
        recovered, mask = unproject_points_2d_to_ground(pixels, w2i,
                                                        ground_z=h)
        assert mask
        np.testing.assert_allclose(recovered, pt3d, atol=1e-9)

    def test_above_horizon_invalid(self):
        """A pixel above the horizon is flagged invalid (no ground hit)."""
        # Forward-looking camera: (cx, cy) = (320, 240) is the horizon.
        # Pixels above the horizon (smaller v) project to the sky.
        w2i, _ = _forward_looking_camera(height=5.0)
        cy = 240.0
        sky_pixels = np.array([
            [320.0, cy - 100.0],   # well above horizon
            [100.0, cy - 50.0],
            [500.0, cy - 80.0],
        ])
        _, mask = unproject_points_2d_to_ground(sky_pixels, w2i)
        assert not mask.any()

    def test_below_horizon_valid(self):
        """Pixels below the horizon (looking at the ground) are flagged valid."""
        w2i, _ = _forward_looking_camera(height=5.0)
        cy = 240.0
        ground_pixels = np.array([
            [320.0, cy + 100.0],   # below horizon
            [100.0, cy + 50.0],
            [500.0, cy + 80.0],
        ])
        recovered, mask = unproject_points_2d_to_ground(ground_pixels, w2i)
        assert mask.all()
        np.testing.assert_array_equal(recovered[:, 2], np.zeros(3))

    def test_horizon_pixel_invalid_no_inf(self):
        """A pixel exactly on the horizon is flagged invalid and finite."""
        # For our forward-looking camera, (cx, cy) is precisely the
        # horizon — d[2] = 0, no finite intersection.
        w2i, _ = _forward_looking_camera(height=5.0)
        recovered, mask = unproject_points_2d_to_ground(
            np.array([320.0, 240.0]), w2i,
        )
        assert not mask
        # No NaN/inf even for the parallel-ray case.
        assert np.all(np.isfinite(recovered))

    def test_output_shape_single_point(self):
        """``(2,)`` pixel input yields ``(3,)`` world point and 0-d mask."""
        w2i, _ = _down_looking_camera()
        out, mask = unproject_points_2d_to_ground(np.array([320.0, 240.0]),
                                                  w2i)
        assert out.shape == (3,)
        assert mask.shape == ()
        assert mask.dtype == bool
        # Down-looking camera + principal point -> directly below camera.
        np.testing.assert_allclose(out, [0.0, 0.0, 0.0], atol=1e-9)

    def test_output_shape_batch(self):
        """``(N, 2)`` pixels yield ``(N, 3)`` world points and ``(N,)`` mask."""
        w2i, _ = _down_looking_camera()
        pixels = np.array([[320.0, 240.0], [100.0, 50.0], [500.0, 300.0]])
        out, mask = unproject_points_2d_to_ground(pixels, w2i)
        assert out.shape == (3, 3)
        assert mask.shape == (3,)
        assert mask.dtype == bool

    def test_output_shape_higher_rank(self):
        """``(B, N, 2)`` pixels yield ``(B, N, 3)`` world points and ``(B, N)`` mask."""
        w2i, _ = _down_looking_camera()
        rng = np.random.default_rng(7)
        pixels = rng.uniform(0, 640, size=(4, 5, 2))
        out, mask = unproject_points_2d_to_ground(pixels, w2i)
        assert out.shape == (4, 5, 3)
        assert mask.shape == (4, 5)

    def test_3x4_and_4x4_matrices_match(self):
        """3x4 and 4x4 (with [0,0,0,1] tail) projection matrices give the same output."""
        w2i_3x4, _ = _down_looking_camera()
        w2i_4x4 = np.eye(4, dtype=np.float64)
        w2i_4x4[:3] = w2i_3x4
        pixels = np.array([[320.0, 240.0], [100.0, 50.0]])
        out_3x4, m_3x4 = unproject_points_2d_to_ground(pixels, w2i_3x4)
        out_4x4, m_4x4 = unproject_points_2d_to_ground(pixels, w2i_4x4)
        np.testing.assert_allclose(out_3x4, out_4x4, atol=1e-12)
        np.testing.assert_array_equal(m_3x4, m_4x4)

    def test_min_depth_filters_close_intersections(self):
        """A large ``min_depth`` filters out near-camera ground intersections."""
        # Down-looking camera at height 10 -> all ground pixels are at
        # depth ~10. min_depth=20 should mask everything out.
        w2i, _ = _down_looking_camera(height=10.0)
        pixels = np.array([[320.0, 240.0], [100.0, 50.0]])
        _, mask_default = unproject_points_2d_to_ground(pixels, w2i)
        _, mask_strict = unproject_points_2d_to_ground(pixels, w2i,
                                                       min_depth=20.0)
        assert mask_default.all()
        assert not mask_strict.any()

    def test_singular_matrix_raises(self):
        """Degenerate world2img with a singular 3x3 block raises LinAlgError."""
        w2i, _ = _down_looking_camera()
        w2i[:, 0] = 0.0
        with pytest.raises(np.linalg.LinAlgError):
            unproject_points_2d_to_ground(np.array([100.0, 200.0]), w2i)

    def test_wrong_last_dim_raises(self):
        """Inputs whose last axis is not 2 are rejected up-front."""
        w2i, _ = _down_looking_camera()
        with pytest.raises(ValueError, match="last dim must be 2"):
            unproject_points_2d_to_ground(np.array([[1.0, 2.0, 3.0]]), w2i)
        with pytest.raises(ValueError, match="last dim must be 2"):
            unproject_points_2d_to_ground(np.float64(3.0), w2i)
        with pytest.raises(ValueError, match="last dim must be 2"):
            unproject_points_2d_to_ground(np.array([1.0, 2.0, 3.0]), w2i)

    def test_does_not_mutate_inputs(self):
        """The input pixel array is not modified in place."""
        w2i, _ = _down_looking_camera()
        pixels = np.array([[100.0, 200.0], [300.0, 400.0]])
        pixels_copy = pixels.copy()
        unproject_points_2d_to_ground(pixels, w2i)
        np.testing.assert_array_equal(pixels, pixels_copy)


# =====================================================================
# Tests for unproject_bbox3d_via_ground
# =====================================================================

def _project_bbox3d_corners(box_9dof, world2img):
    """Project a single 9-DoF box's 8 corners to 2D pixels.

    Returns the (8, 2) pixel array; asserts every corner is in front
    of the camera so the round-trip tests are well-defined.
    """
    corners_3d = box3d_to_corners(np.asarray(box_9dof, dtype=np.float64),
                                  origin=(0.5, 0.5, 0.0))[0]    # (8, 3)
    pixels, front = project_points_3d_to_image(corners_3d, world2img)
    assert front.all(), "test fixture has corners behind camera"
    return pixels, corners_3d


class TestUnprojectBbox3dViaGround:
    """Tests for the ground-anchored, yaw-only 3D bbox reconstruction."""

    def test_index_constants(self):
        """Default index constants match the NVSchema bottom/top layout."""
        assert BBOX3D_BOTTOM_INDICES == (0, 3, 4, 7)
        assert BBOX3D_TOP_INDICES == (1, 2, 5, 6)
        # And they form a partition of 0..7.
        assert (set(BBOX3D_BOTTOM_INDICES) | set(BBOX3D_TOP_INDICES)
                == set(range(8)))
        assert not (set(BBOX3D_BOTTOM_INDICES) & set(BBOX3D_TOP_INDICES))

    def test_round_trip_yaw_zero_down_looking(self):
        """Yaw=0 box round-trips exactly under a downward-looking camera."""
        w2i, _ = _down_looking_camera(height=20.0)
        # Box centred at world (3, 2, 0), size (1.5, 2.5, 1.8), yaw=0.
        box = [3.0, 2.0, 0.0, 1.5, 2.5, 1.8, 0.0, 0.0, 0.0]
        pixels, corners_3d = _project_bbox3d_corners(box, w2i)

        recovered, foot, mask = unproject_bbox3d_via_ground(pixels, w2i)
        assert mask
        np.testing.assert_allclose(recovered, corners_3d, atol=1e-9)
        # Foot is the centre of the bottom face -> (3, 2, 0).
        np.testing.assert_allclose(foot, [3.0, 2.0, 0.0], atol=1e-9)

    def test_round_trip_with_yaw(self):
        """Non-zero yaw is preserved exactly (encoded in the bottom (X, Y))."""
        w2i, _ = _down_looking_camera(height=20.0)
        box = [3.0, 2.0, 0.0, 1.5, 2.5, 1.8, 0.0, 0.0, 0.7]   # yaw ~ 40 deg
        pixels, corners_3d = _project_bbox3d_corners(box, w2i)

        recovered, foot, mask = unproject_bbox3d_via_ground(pixels, w2i)
        assert mask
        np.testing.assert_allclose(recovered, corners_3d, atol=1e-9)
        np.testing.assert_allclose(foot, [3.0, 2.0, 0.0], atol=1e-9)

    def test_round_trip_forward_camera(self):
        """Round-trip works for a forward-looking (non-trivial extrinsic) camera."""
        w2i, _ = _forward_looking_camera(height=5.0)
        # Place box well in front of the camera.
        box = [1.0, 12.0, 0.0, 1.2, 2.0, 1.7, 0.0, 0.0, 0.3]
        pixels, corners_3d = _project_bbox3d_corners(box, w2i)

        recovered, foot, mask = unproject_bbox3d_via_ground(pixels, w2i)
        assert mask
        np.testing.assert_allclose(recovered, corners_3d, atol=1e-9)
        np.testing.assert_allclose(foot, [1.0, 12.0, 0.0], atol=1e-9)

    def test_height_recovery_exact(self):
        """The recovered top corners' Z exactly equals the input bbox height."""
        w2i, _ = _down_looking_camera(height=15.0)
        h = 1.7
        box = [2.0, -1.0, 0.0, 1.0, 1.0, h, 0.0, 0.0, 0.4]
        pixels, _ = _project_bbox3d_corners(box, w2i)

        recovered, _, mask = unproject_bbox3d_via_ground(pixels, w2i)
        assert mask
        # All 4 top corners should land exactly on Z = h.
        top_z = recovered[list(BBOX3D_TOP_INDICES), 2]
        np.testing.assert_allclose(top_z, h, atol=1e-9)
        # And all 4 bottom corners on Z = 0.
        bot_z = recovered[list(BBOX3D_BOTTOM_INDICES), 2]
        assert np.array_equal(bot_z, np.zeros(4))

    def test_ground_z_offset(self):
        """Non-zero ``ground_z`` shifts both faces by the same amount."""
        w2i, _ = _down_looking_camera(height=20.0)
        h, gz = 1.4, 2.5
        # Box sitting on Z=gz: centre Z=gz, origin=(0.5, 0.5, 0.0) -> bottom at gz, top at gz+h.
        box = [3.0, 2.0, gz, 1.0, 1.0, h, 0.0, 0.0, 0.0]
        pixels, _ = _project_bbox3d_corners(box, w2i)

        recovered, foot, mask = unproject_bbox3d_via_ground(pixels, w2i,
                                                            ground_z=gz)
        assert mask
        bot_z = recovered[list(BBOX3D_BOTTOM_INDICES), 2]
        top_z = recovered[list(BBOX3D_TOP_INDICES), 2]
        assert np.array_equal(bot_z, np.full(4, gz))
        np.testing.assert_allclose(top_z, gz + h, atol=1e-9)
        np.testing.assert_allclose(foot, [3.0, 2.0, gz], atol=1e-9)

    def test_batch_shape_preserved(self):
        """A batch of N bboxes lifts to ``(N, 8, 3)`` / ``(N, 3)`` / ``(N,)``."""
        w2i, _ = _down_looking_camera(height=20.0)
        boxes = [
            [3.0,  2.0, 0.0, 1.5, 2.5, 1.8, 0.0, 0.0, 0.0],
            [-2.0, 1.0, 0.0, 1.0, 1.0, 1.5, 0.0, 0.0, 0.5],
            [4.0, -1.5, 0.0, 2.0, 1.5, 1.2, 0.0, 0.0, -0.3],
        ]
        pixel_batch = np.stack(
            [_project_bbox3d_corners(b, w2i)[0] for b in boxes], axis=0
        )                                     # (3, 8, 2)
        corners_batch = np.stack(
            [_project_bbox3d_corners(b, w2i)[1] for b in boxes], axis=0
        )                                     # (3, 8, 3)

        recovered, feet, mask = unproject_bbox3d_via_ground(pixel_batch, w2i)
        assert recovered.shape == (3, 8, 3)
        assert feet.shape == (3, 3)
        assert mask.shape == (3,) and mask.dtype == bool
        assert mask.all()
        np.testing.assert_allclose(recovered, corners_batch, atol=1e-9)
        np.testing.assert_allclose(
            feet, [[3.0, 2.0, 0.0], [-2.0, 1.0, 0.0], [4.0, -1.5, 0.0]],
            atol=1e-9,
        )

    def test_higher_rank_shape_preserved(self):
        """Leading axes are preserved: (B, N, 8, 2) -> (B, N, 8, 3)."""
        w2i, _ = _down_looking_camera(height=20.0)
        # Build a (2, 3, 8, 2) batch from 6 boxes.
        boxes = [
            [3.0,  2.0, 0.0, 1.5, 2.5, 1.8, 0.0, 0.0, 0.0],
            [-2.0, 1.0, 0.0, 1.0, 1.0, 1.5, 0.0, 0.0, 0.5],
            [4.0, -1.5, 0.0, 2.0, 1.5, 1.2, 0.0, 0.0, -0.3],
            [1.0,  3.0, 0.0, 1.5, 1.5, 1.0, 0.0, 0.0, 0.1],
            [-3.0, -2.0, 0.0, 1.0, 2.0, 1.6, 0.0, 0.0, 0.8],
            [0.0,  0.0, 0.0, 1.5, 1.5, 1.4, 0.0, 0.0, 0.0],
        ]
        flat = np.stack(
            [_project_bbox3d_corners(b, w2i)[0] for b in boxes], axis=0
        )                                     # (6, 8, 2)
        pixels = flat.reshape(2, 3, 8, 2)

        recovered, feet, mask = unproject_bbox3d_via_ground(pixels, w2i)
        assert recovered.shape == (2, 3, 8, 3)
        assert feet.shape == (2, 3, 3)
        assert mask.shape == (2, 3)
        assert mask.all()

    def test_above_horizon_invalid(self):
        """A bbox whose foot is above the horizon is flagged invalid."""
        # Forward-looking camera + sky pixels -> foot can't hit the ground.
        w2i, _ = _forward_looking_camera(height=5.0)
        cy = 240.0
        # Pretend 8 vertices are all above the horizon.
        sky_pixels = np.array([
            [320.0, cy - 80.0], [320.0, cy - 100.0],
            [340.0, cy - 100.0], [340.0, cy - 80.0],
            [310.0, cy - 80.0], [310.0, cy - 100.0],
            [330.0, cy - 100.0], [330.0, cy - 80.0],
        ])
        _, _, mask = unproject_bbox3d_via_ground(sky_pixels, w2i)
        assert not mask

    def test_inverted_top_bottom_invalid(self):
        """Mislabeling top corners as bottom yields height <= 0 and is rejected.

        With the bottom/top partition swapped, ``valid_bottom`` and
        ``well_posed`` still pass (the LSQ is numerically fine), but
        the recovered height comes out negative — i.e. the top face
        unprojects below the bottom face.  ``valid_mask`` must catch
        this physically degenerate case.
        """
        w2i, _ = _down_looking_camera(height=20.0)
        box = [3.0, 2.0, 0.0, 1.5, 2.5, 1.8, 0.0, 0.0, 0.0]
        pixels, _ = _project_bbox3d_corners(box, w2i)
        _, _, mask = unproject_bbox3d_via_ground(
            pixels, w2i,
            bottom_indices=(1, 2, 5, 6),
            top_indices=(0, 3, 4, 7),
        )
        assert not mask

    def test_custom_index_partition(self):
        """Caller can swap to a non-default partition (consistent re-pairing)."""
        w2i, _ = _down_looking_camera(height=20.0)
        box = [3.0, 2.0, 0.0, 1.5, 2.5, 1.8, 0.0, 0.0, 0.0]
        pixels, corners_3d = _project_bbox3d_corners(box, w2i)

        # Swap the (bottom, top) pairing while preserving the per-pair
        # correspondence: re-index both arrays consistently.
        bottom_perm = (4, 7, 0, 3)            # any reordering of bottom set
        top_perm    = (5, 6, 1, 2)            # corresponding tops
        recovered, _, mask = unproject_bbox3d_via_ground(
            pixels, w2i,
            bottom_indices=bottom_perm,
            top_indices=top_perm,
        )
        assert mask
        np.testing.assert_allclose(recovered, corners_3d, atol=1e-9)

    def test_bottom_z_is_exactly_ground_z(self):
        """Bottom-corner Z values are bit-exactly ``ground_z`` (no float drift)."""
        w2i, _ = _down_looking_camera(height=20.0)
        box = [3.0, 2.0, 0.0, 1.5, 2.5, 1.8, 0.0, 0.0, 0.0]
        pixels, _ = _project_bbox3d_corners(box, w2i)
        recovered, _, _ = unproject_bbox3d_via_ground(pixels, w2i,
                                                      ground_z=0.0)
        bot_z = recovered[list(BBOX3D_BOTTOM_INDICES), 2]
        assert np.array_equal(bot_z, np.zeros(4))

    def test_wrong_shape_raises(self):
        """Inputs without trailing ``(8, 2)`` are rejected up-front."""
        w2i, _ = _down_looking_camera()
        with pytest.raises(ValueError, match=r"shape \(\.\.\., 8, 2\)"):
            unproject_bbox3d_via_ground(np.zeros((10, 2)), w2i)
        with pytest.raises(ValueError, match=r"shape \(\.\.\., 8, 2\)"):
            unproject_bbox3d_via_ground(np.zeros((8, 3)), w2i)
        with pytest.raises(ValueError, match=r"shape \(\.\.\., 8, 2\)"):
            unproject_bbox3d_via_ground(np.zeros((8,)), w2i)

    def test_invalid_index_partition_raises(self):
        """Bottom/top indices must form a disjoint partition of 0..7."""
        w2i, _ = _down_looking_camera()
        pixels = np.zeros((8, 2))
        # Wrong arity.
        with pytest.raises(ValueError, match="exactly 4 elements"):
            unproject_bbox3d_via_ground(pixels, w2i,
                                        bottom_indices=(0, 3, 4),
                                        top_indices=(1, 2, 5, 6))
        # Overlap between bottom and top.
        with pytest.raises(ValueError, match="disjoint partition"):
            unproject_bbox3d_via_ground(pixels, w2i,
                                        bottom_indices=(0, 3, 4, 7),
                                        top_indices=(1, 2, 5, 7))
        # Doesn't cover all 8 (uses index 8).
        with pytest.raises(ValueError, match="disjoint partition"):
            unproject_bbox3d_via_ground(pixels, w2i,
                                        bottom_indices=(0, 3, 4, 7),
                                        top_indices=(1, 2, 5, 8))

    def test_singular_matrix_raises(self):
        """Degenerate world2img with a singular 3x3 block raises LinAlgError."""
        w2i, _ = _down_looking_camera()
        w2i[:, 0] = 0.0
        with pytest.raises(np.linalg.LinAlgError):
            unproject_bbox3d_via_ground(np.zeros((8, 2)), w2i)

    def test_does_not_mutate_input(self):
        """The input pixel array is not modified in place."""
        w2i, _ = _down_looking_camera(height=20.0)
        box = [3.0, 2.0, 0.0, 1.5, 2.5, 1.8, 0.0, 0.0, 0.0]
        pixels, _ = _project_bbox3d_corners(box, w2i)
        pixels_copy = pixels.copy()
        unproject_bbox3d_via_ground(pixels, w2i)
        np.testing.assert_array_equal(pixels, pixels_copy)


# =====================================================================
# Tests for project_boxes_3d_to_2d
# =====================================================================

class TestProjectBoxes3dTo2d:
    """Tests for projecting 3D boxes to 2D image corners."""

    def test_visible_box_projected(self):
        """A box in front of the camera returns shape (1, 8, 2)."""
        box = np.array([[0.0, 0.0, 20.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0]])
        calib = _make_calib()
        verts, visible = project_boxes_3d_to_2d(box, calib)
        assert verts.shape == (1, 8, 2)
        assert visible == [0]

    def test_box_behind_camera_filtered(self):
        """A box behind the camera (negative z) is filtered out."""
        box = np.array([[0.0, 0.0, -10.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0]])
        calib = _make_calib()
        verts, visible = project_boxes_3d_to_2d(box, calib)
        assert visible == []
        assert verts.shape == (0, 8, 2)

    def test_box_far_off_screen_filtered(self):
        """A box fully outside image bounds is filtered out."""
        # Place box very far to the side so it projects outside the image.
        box = np.array([[1e5, 0.0, 20.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0]])
        calib = _make_calib()
        verts, visible = project_boxes_3d_to_2d(box, calib)
        assert visible == []

    def test_image_size_override(self):
        """Custom image_size narrows the visibility window."""
        # Box projects near the principal point (320, 240). The default
        # IMAGE_SIZE (1920, 1080) keeps it; a tiny 10x10 window drops it.
        box = np.array([[0.0, 0.0, 20.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0]])
        calib = _make_calib()
        _, visible_default = project_boxes_3d_to_2d(box, calib)
        _, visible_tiny = project_boxes_3d_to_2d(box, calib, image_size=(10, 10))
        assert visible_default == [0]
        assert visible_tiny == []

    def test_mixed_boxes(self):
        """Mix visible + hidden boxes — only the visible one is kept."""
        boxes = np.array([
            [0.0, 0.0, 20.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0],     # visible
            [0.0, 0.0, -10.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0],    # behind camera
            [1e5, 0.0, 20.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0],     # off-screen
        ])
        calib = _make_calib()
        verts, visible = project_boxes_3d_to_2d(boxes, calib)
        assert visible == [0]
        assert verts.shape == (1, 8, 2)

    def test_empty_input(self):
        """Empty box array returns vertices with shape (0, 8, 2) and empty id list."""
        verts, visible = project_boxes_3d_to_2d(np.empty((0, 9)), _make_calib())
        assert visible == []
        assert verts.shape == (0, 8, 2)
        assert verts.dtype == np.float64

    def test_no_visible_boxes_returns_consistent_shape(self):
        """Non-empty input where every box fails visibility still yields (0, 8, 2)."""
        # Both boxes are either behind the camera or fully off-screen.
        boxes = np.array([
            [0.0, 0.0, -10.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0],  # behind camera
            [1e5, 0.0, 20.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0],   # off-screen
        ])
        verts, visible = project_boxes_3d_to_2d(boxes, _make_calib())
        assert visible == []
        assert verts.shape == (0, 8, 2)
        assert verts.dtype == np.float64

    def test_image_center_projection(self):
        """A box at (0, 0, 20) with identity extrinsic projects to image centre."""
        box = np.array([[0.0, 0.0, 20.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0]])
        cx, cy = 320.0, 240.0
        calib = _make_calib(cx=cx, cy=cy)
        verts, _ = project_boxes_3d_to_2d(box, calib)
        # The mean of the 8 projected corners should be near the principal point.
        np.testing.assert_allclose(verts[0].mean(axis=0), [cx, cy], atol=1.0)

    def test_origin_parameter_changes_projection(self):
        """Different origin conventions produce different projected corners."""
        box = np.array([[0.0, 0.0, 20.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0]])
        calib = _make_calib()
        verts_bottom, _ = project_boxes_3d_to_2d(box, calib, origin=(0.5, 0.5, 0.0))
        verts_center, _ = project_boxes_3d_to_2d(box, calib, origin=(0.5, 0.5, 0.5))
        assert not np.allclose(verts_bottom, verts_center)

    def test_does_not_mutate_input(self):
        """The input array is not modified in place."""
        box = np.array([[0.0, 0.0, 20.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0]])
        box_copy = box.copy()
        project_boxes_3d_to_2d(box, _make_calib())
        np.testing.assert_array_equal(box, box_copy)

    def test_no_mmdet3d_import(self):
        """Module does not import mmdet3d (pure-numpy implementation)."""
        import spatialai_data_utils.core.geometry.projection as proj_mod
        assert "mmdet3d" not in proj_mod.__dict__

    def test_1d_input_reshaped(self):
        """A single ``(9,)`` box is auto-reshaped to ``(1, 9)``."""
        box = np.array([0.0, 0.0, 20.0, 2.0, 4.0, 1.0, 0.0, 0.0, 0.0])
        verts, visible = project_boxes_3d_to_2d(box, _make_calib())
        assert verts.shape == (1, 8, 2)
        assert visible == [0]

    def test_visible_ids_in_ascending_order(self):
        """``visible_ids`` is sorted ascending regardless of input order."""
        # Alternating visible/invisible boxes to guarantee the output is
        # a non-contiguous subset of indices.
        boxes = np.array([
            [0.0, 0.0, 20.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0],   # 0: visible
            [0.0, 0.0, -5.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0],   # 1: behind camera
            [1.0, 0.0, 20.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0],   # 2: visible
            [1e5, 0.0, 20.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0],   # 3: off-screen
            [2.0, 0.0, 20.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0],   # 4: visible
        ])
        verts, visible = project_boxes_3d_to_2d(boxes, _make_calib())
        assert visible == [0, 2, 4]
        assert verts.shape == (3, 8, 2)
        # verts order matches visible_ids order.
        assert visible == sorted(visible)

    def test_partial_depth_visibility_filtered(self):
        """A box straddling the camera plane is filtered (requires ALL > 0)."""
        # Very large box centred exactly on the camera so roughly half its
        # corners have negative depth.  The visibility rule requires ALL 8
        # corners in front, so this box must be dropped.
        box = np.array([[0.0, 0.0, 0.0, 50.0, 50.0, 50.0, 0.0, 0.0, 0.0]])
        _, visible = project_boxes_3d_to_2d(box, _make_calib())
        assert visible == []
