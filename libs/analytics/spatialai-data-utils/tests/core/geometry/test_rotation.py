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

"""Tests for ``core.geometry.rotation``.

Pins all four conversion helpers + their gimbal-lock clamps and
round-trip stability:

* ``euler_from_quaternion`` — identity / single-axis rotations + the
  ``asin`` clamp at ±π/2 (gimbal lock).
* ``euler_to_quaternion`` — identity + single-axis + the positional-
  only signature (kwargs raise to prevent the old/new axis-name
  swap from silently miscomputing).
* ``quaternion_to_rotation_matrix`` — identity + a known axis-angle
  case.
* ``rotation_matrix_to_quaternion`` — exercises all four
  branches of the numerically-stable Shepperd algorithm by feeding
  matrices whose largest diagonal element lives in each position;
  plus the round-trip ``q → R → q``.
"""

import math

import numpy as np
import pytest

from spatialai_data_utils.core.geometry.rotation import (
    euler_from_quaternion,
    euler_to_quaternion,
    quaternion_to_rotation_matrix,
    rotation_matrix_to_quaternion,
)


# ---------------------------------------------------------------------------
# euler_from_quaternion
# ---------------------------------------------------------------------------


class TestEulerFromQuaternion:
    def test_identity_quaternion_maps_to_zero_euler(self):
        pitch, roll, yaw = euler_from_quaternion(1.0, 0.0, 0.0, 0.0)
        assert (pitch, roll, yaw) == (0.0, 0.0, 0.0)

    def test_pure_yaw_quaternion_extracts_yaw_only(self):
        """90 deg yaw rotation: q = (cos(π/4), 0, 0, sin(π/4))."""
        c = math.cos(math.pi / 4)
        s = math.sin(math.pi / 4)
        pitch, roll, yaw = euler_from_quaternion(c, 0.0, 0.0, s)
        assert pitch == pytest.approx(0.0, abs=1e-9)
        assert roll == pytest.approx(0.0, abs=1e-9)
        assert yaw == pytest.approx(math.pi / 2)

    def test_gimbal_lock_upper_clamp_for_roll_at_pi_over_2(self):
        """Setting roll (asin-derived Y rotation) to +π/2 puts us at
        the singularity. The implementation clamps the asin argument
        to +1.0 so we should get exactly ``π/2`` back rather than NaN."""
        # q corresponding to pure roll = +π/2: q = (cos(π/4), 0, sin(π/4), 0)
        c = math.cos(math.pi / 4)
        s = math.sin(math.pi / 4)
        _, roll, _ = euler_from_quaternion(c, 0.0, s, 0.0)
        assert roll == pytest.approx(math.pi / 2)

    def test_gimbal_lock_lower_clamp_for_roll_at_minus_pi_over_2(self):
        """Same as above but for the -1 clamp side."""
        c = math.cos(-math.pi / 4)
        s = math.sin(-math.pi / 4)
        _, roll, _ = euler_from_quaternion(c, 0.0, s, 0.0)
        assert roll == pytest.approx(-math.pi / 2)


# ---------------------------------------------------------------------------
# euler_to_quaternion
# ---------------------------------------------------------------------------


class TestEulerToQuaternion:
    def test_zero_euler_gives_identity_quaternion(self):
        w, x, y, z = euler_to_quaternion(0.0, 0.0, 0.0)
        assert (w, x, y, z) == (1.0, 0.0, 0.0, 0.0)

    def test_round_trip_through_quaternion_recovers_euler(self):
        """``euler -> q -> euler`` should be a no-op for angles
        comfortably away from the gimbal-lock singularity."""
        pitch_in, roll_in, yaw_in = 0.1, 0.2, 0.3
        w, x, y, z = euler_to_quaternion(pitch_in, roll_in, yaw_in)
        pitch_out, roll_out, yaw_out = euler_from_quaternion(w, x, y, z)
        assert pitch_out == pytest.approx(pitch_in)
        assert roll_out == pytest.approx(roll_in)
        assert yaw_out == pytest.approx(yaw_in)

    def test_signature_is_positional_only_kwarg_use_raises(self):
        """The trailing ``/`` makes the args positional-only so callers
        cannot silently swap pitch/roll naming after the rename. (This
        contract is also pinned by test_box_3d_rotation_contracts.py;
        re-asserted here for module-local coverage.)"""
        with pytest.raises(TypeError):
            euler_to_quaternion(pitch=0.1, roll=0.2, yaw=0.3)


# ---------------------------------------------------------------------------
# quaternion_to_rotation_matrix
# ---------------------------------------------------------------------------


class TestQuaternionToRotationMatrix:
    def test_identity_quaternion_gives_identity_matrix(self):
        R = quaternion_to_rotation_matrix(1.0, 0.0, 0.0, 0.0)
        np.testing.assert_allclose(R, np.eye(3), atol=1e-9)

    def test_90deg_yaw_about_z_swaps_x_and_y_with_sign(self):
        """yaw=+π/2 should rotate +X → +Y, +Y → -X. q = (cos π/4, 0, 0, sin π/4)."""
        c = math.cos(math.pi / 4)
        s = math.sin(math.pi / 4)
        R = quaternion_to_rotation_matrix(c, 0.0, 0.0, s)
        # +X (col 0) should land on +Y → (0, 1, 0)
        np.testing.assert_allclose(R[:, 0], [0.0, 1.0, 0.0], atol=1e-9)
        # +Y (col 1) should land on -X → (-1, 0, 0)
        np.testing.assert_allclose(R[:, 1], [-1.0, 0.0, 0.0], atol=1e-9)

    def test_returned_matrix_is_orthogonal_with_det_one(self):
        """Sanity check: the output is a proper rotation matrix."""
        w, x, y, z = euler_to_quaternion(0.1, 0.2, 0.3)
        R = quaternion_to_rotation_matrix(w, x, y, z)
        # R @ R.T should equal identity
        np.testing.assert_allclose(R @ R.T, np.eye(3), atol=1e-9)
        # det(R) should be +1 (proper rotation, not reflection)
        assert np.linalg.det(R) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# rotation_matrix_to_quaternion — exercise all four branches of the
# numerically-stable Shepperd algorithm.
# ---------------------------------------------------------------------------


class TestRotationMatrixToQuaternion:
    def test_identity_matrix_maps_to_identity_quaternion(self):
        """Trace branch (``tr > 0``): identity has trace=3."""
        q = rotation_matrix_to_quaternion(np.eye(3))
        np.testing.assert_allclose(q, [1.0, 0.0, 0.0, 0.0], atol=1e-9)

    def test_round_trip_via_a_known_rotation(self):
        """``q -> R -> q`` should round-trip (within sign — quaternions
        are double-cover, so q and -q both map to R)."""
        q_in = np.array(euler_to_quaternion(0.1, 0.2, 0.3))
        R = quaternion_to_rotation_matrix(*q_in)
        q_out = rotation_matrix_to_quaternion(R)
        # Allow for quaternion sign ambiguity.
        assert (np.allclose(q_in, q_out, atol=1e-9) or
                np.allclose(q_in, -q_out, atol=1e-9))

    def test_largest_x_diagonal_branch(self):
        """When ``R[0,0]`` is the largest diagonal (e.g. 180 deg about X
        sends ``R = diag(1, -1, -1)``), the algorithm takes the
        ``elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]`` branch."""
        R = np.diag([1.0, -1.0, -1.0])
        q = rotation_matrix_to_quaternion(R)
        # Expect q = (0, 1, 0, 0) (pure 180 deg about X, w=0)
        np.testing.assert_allclose(q, [0.0, 1.0, 0.0, 0.0], atol=1e-9)

    def test_largest_y_diagonal_branch(self):
        """``R = diag(-1, 1, -1)`` is 180 deg about Y → q = (0, 0, 1, 0)."""
        R = np.diag([-1.0, 1.0, -1.0])
        q = rotation_matrix_to_quaternion(R)
        np.testing.assert_allclose(q, [0.0, 0.0, 1.0, 0.0], atol=1e-9)

    def test_largest_z_diagonal_branch(self):
        """``R = diag(-1, -1, 1)`` is 180 deg about Z → q = (0, 0, 0, 1)."""
        R = np.diag([-1.0, -1.0, 1.0])
        q = rotation_matrix_to_quaternion(R)
        np.testing.assert_allclose(q, [0.0, 0.0, 0.0, 1.0], atol=1e-9)
