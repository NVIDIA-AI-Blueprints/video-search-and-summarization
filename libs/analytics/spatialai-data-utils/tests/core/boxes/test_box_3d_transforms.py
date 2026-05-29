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

"""Tests for the two transformation helpers in ``core.boxes.box_3d``.

Both helpers are documented as "warning: no production callers; review
carefully before reuse" — but they're still part of the public surface
so we pin their numeric contract.

* ``transform_3d_bboxes`` — applies a 4x4 transform to 7-DoF
  ``[x, y, z, w, l, h, yaw]`` boxes (centre + yaw rotated;
  ``w / l / h`` left untouched).
* ``transform_3d_bboxes_10d`` — same idea on the non-canonical
  10-DoF ``[qw, qx, qy, qz, x, y, z, w, l, h]`` layout.

Identity, pure translation, and pure yaw / quaternion rotation cover
the main code paths.
"""

import math

import numpy as np
import pytest

from spatialai_data_utils.core.boxes.box_3d import (
    transform_3d_bboxes,
    transform_3d_bboxes_10d,
)


# ---------------------------------------------------------------------------
# transform_3d_bboxes — 7-DoF [x, y, z, w, l, h, yaw]
# ---------------------------------------------------------------------------


class TestTransform3DBboxes:
    def test_identity_transform_returns_input_unchanged(self):
        bboxes = np.array([[1.0, 2.0, 3.0, 0.5, 1.0, 1.8, 0.4]])
        out = transform_3d_bboxes(bboxes, np.eye(4))
        np.testing.assert_allclose(out, bboxes, atol=1e-9)

    def test_pure_translation_only_shifts_center(self):
        bboxes = np.array([[1.0, 2.0, 3.0, 0.5, 1.0, 1.8, 0.0]])
        T = np.eye(4)
        T[:3, 3] = [10.0, 20.0, 30.0]
        out = transform_3d_bboxes(bboxes, T)
        np.testing.assert_allclose(out[0, :3], [11.0, 22.0, 33.0])
        # Dimensions and yaw unchanged.
        np.testing.assert_allclose(out[0, 3:6], [0.5, 1.0, 1.8])
        assert out[0, 6] == pytest.approx(0.0)

    def test_90deg_yaw_rotation_about_z_adds_pi_over_2_to_yaw(self):
        bboxes = np.array([[1.0, 0.0, 0.0, 0.5, 1.0, 1.8, 0.0]])
        # R_z(π/2) — sends (1, 0, 0) -> (0, 1, 0)
        T = np.array([
            [0.0, -1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ])
        out = transform_3d_bboxes(bboxes, T)
        np.testing.assert_allclose(out[0, :3], [0.0, 1.0, 0.0], atol=1e-9)
        # Output yaw is +π/2.
        assert out[0, 6] == pytest.approx(math.pi / 2)

    def test_dimensions_pass_through_unchanged_under_rigid_transform(self):
        bboxes = np.array([
            [1.0, 2.0, 3.0, 0.5, 1.0, 1.8, 0.0],
            [4.0, 5.0, 6.0, 0.7, 1.4, 2.0, 0.5],
        ])
        T = np.eye(4)
        T[:3, 3] = [10.0, 0.0, 0.0]
        out = transform_3d_bboxes(bboxes, T)
        np.testing.assert_allclose(out[:, 3:6], bboxes[:, 3:6])

    def test_handles_multiple_bboxes_in_one_call(self):
        bboxes = np.array([
            [1.0, 0.0, 0.0, 0.5, 1.0, 1.8, 0.1],
            [2.0, 0.0, 0.0, 0.5, 1.0, 1.8, 0.2],
            [3.0, 0.0, 0.0, 0.5, 1.0, 1.8, 0.3],
        ])
        out = transform_3d_bboxes(bboxes, np.eye(4))
        assert out.shape == (3, 7)
        np.testing.assert_allclose(out, bboxes, atol=1e-9)


# ---------------------------------------------------------------------------
# transform_3d_bboxes_10d — 10-DoF [qw, qx, qy, qz, x, y, z, w, l, h]
# ---------------------------------------------------------------------------


class TestTransform3DBboxes10D:
    def test_identity_transform_returns_input_essentially_unchanged(self):
        # qw=1, identity rotation; centre at (1,2,3); dims (0.5, 1.0, 1.8)
        bboxes = np.array([[1.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 0.5, 1.0, 1.8]])
        out = transform_3d_bboxes_10d(bboxes, np.eye(4))
        # Quaternion may be sign-flipped (q == -q for the same rotation);
        # compare the centre / dimensions directly.
        np.testing.assert_allclose(out[0, 4:7], [1.0, 2.0, 3.0], atol=1e-9)
        np.testing.assert_allclose(out[0, 7:10], [0.5, 1.0, 1.8], atol=1e-9)

    def test_pure_translation_only_shifts_center(self):
        bboxes = np.array([[1.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 0.5, 1.0, 1.8]])
        T = np.eye(4)
        T[:3, 3] = [10.0, 20.0, 30.0]
        out = transform_3d_bboxes_10d(bboxes, T)
        np.testing.assert_allclose(out[0, 4:7], [11.0, 22.0, 33.0], atol=1e-9)
        np.testing.assert_allclose(out[0, 7:10], [0.5, 1.0, 1.8], atol=1e-9)

    def test_output_quaternion_is_unit_norm(self):
        """The conversion goes ``q -> R -> apply T -> R' -> q'``. The
        round-trip should keep ``||q'|| = 1`` for any rigid transform."""
        bboxes = np.array([
            [1.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 0.5, 1.0, 1.8],
            [0.9659258, 0.0, 0.0, 0.258819, 4.0, 5.0, 6.0, 0.7, 1.4, 2.0],  # 30 deg yaw
        ])
        T = np.eye(4)
        out = transform_3d_bboxes_10d(bboxes, T)
        norms = np.linalg.norm(out[:, :4], axis=1)
        np.testing.assert_allclose(norms, [1.0, 1.0], atol=1e-9)

    def test_returns_array_with_canonical_10d_shape(self):
        bboxes = np.array([[1.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 0.5, 1.0, 1.8]])
        out = transform_3d_bboxes_10d(bboxes, np.eye(4))
        assert out.shape == (1, 10)
