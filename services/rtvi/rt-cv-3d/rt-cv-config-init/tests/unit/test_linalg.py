# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Unit tests for src/_linalg.py, the numpy replacement for OpenCV.

These are ground-truth tests: a projection matrix is built from a known K, R
and camera centre, then decomposed, and the inputs must come back out. They do
NOT require OpenCV, so they run anywhere and keep protecting this code now that
opencv-python-headless is gone from the image.

The OpenCV differential comparison lives in tests/test_linalg_equivalence.py.
That one needs cv2 installed and is migration evidence rather than CI cover.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

pytestmark = pytest.mark.unit

RNG_SEED = 20260812


def _rotation(rx: float, ry: float, rz: float) -> np.ndarray:
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _projection(K: np.ndarray, R: np.ndarray, centre: np.ndarray) -> np.ndarray:
    """P = K [R | -R c] for camera centre c in world coordinates."""
    t = -R @ centre.reshape(3, 1)
    return K @ np.hstack([R, t])


def _sample_camera(rng):
    fx = rng.uniform(600.0, 2000.0)
    fy = fx * rng.uniform(0.95, 1.05)
    cx = rng.uniform(300.0, 1000.0)
    cy = rng.uniform(200.0, 700.0)
    K = np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]])
    R = _rotation(
        rng.uniform(-0.4, 0.4), rng.uniform(-0.4, 0.4), rng.uniform(-math.pi, math.pi)
    )
    centre = np.array(
        [rng.uniform(-40, 40), rng.uniform(-40, 40), rng.uniform(3.0, 12.0)]
    )
    return K, R, centre


def test_decompose_recovers_known_intrinsics():
    """K, R and the camera centre survive a round trip through the decomposition."""
    from _linalg import decompose_projection_matrix

    rng = np.random.default_rng(RNG_SEED)
    for _ in range(200):
        K, R, centre = _sample_camera(rng)
        P = _projection(K, R, centre)

        K_out, R_out, t_out = decompose_projection_matrix(P)

        K_norm = K_out / K_out[2, 2]
        assert np.allclose(K_norm, K, rtol=1e-9, atol=1e-6), "intrinsics not recovered"
        assert np.allclose(R_out, R, atol=1e-9), "rotation not recovered"

        centre_out = (t_out[:3] / t_out[-1]).flatten()
        assert np.allclose(centre_out, centre, rtol=1e-8, atol=1e-8), (
            "camera centre not recovered"
        )


def test_decomposition_reconstructs_the_projection_matrix():
    """K [R | -R c] rebuilt from the decomposition matches P up to scale."""
    from _linalg import decompose_projection_matrix

    rng = np.random.default_rng(RNG_SEED + 1)
    for _ in range(100):
        K, R, centre = _sample_camera(rng)
        P = _projection(K, R, centre)

        K_out, R_out, t_out = decompose_projection_matrix(P)
        centre_out = (t_out[:3] / t_out[-1]).reshape(3, 1)
        P_rebuilt = K_out @ np.hstack([R_out, -R_out @ centre_out])

        scale = P[0, 0] / P_rebuilt[0, 0]
        assert np.allclose(P_rebuilt * scale, P, rtol=1e-8, atol=1e-6)


def test_rotation_is_a_proper_rotation():
    """R is orthonormal with det +1, which the FOV culling relies on."""
    from _linalg import decompose_projection_matrix

    rng = np.random.default_rng(RNG_SEED + 2)
    for _ in range(100):
        K, R, centre = _sample_camera(rng)
        _, R_out, _ = decompose_projection_matrix(_projection(K, R, centre))

        assert np.allclose(R_out @ R_out.T, np.eye(3), atol=1e-12)
        assert np.isclose(np.linalg.det(R_out), 1.0, atol=1e-12)


def test_perspective_transform_matches_manual_homogeneous_math():
    """The transform is a matrix multiply followed by a divide by w."""
    from _linalg import perspective_transform_3d_to_2d

    rng = np.random.default_rng(RNG_SEED + 3)
    K, R, centre = _sample_camera(rng)
    P = _projection(K, R, centre)

    pts = rng.uniform(-50, 50, size=(7, 5, 3))

    got = perspective_transform_3d_to_2d(pts, P)

    flat = pts.reshape(-1, 3)
    homogeneous = np.hstack([flat, np.ones((flat.shape[0], 1))])
    projected = (P @ homogeneous.T).T
    expected = (projected[:, :2] / projected[:, 2:3]).reshape(pts.shape[0], pts.shape[1], 2)

    assert got.shape == expected.shape
    assert np.allclose(got, expected, rtol=1e-12, atol=1e-12)


def test_points_on_the_ground_plane_project_consistently():
    """z=0 and z=h points differ only through the projection, as FOV masking assumes."""
    from _linalg import perspective_transform_3d_to_2d

    rng = np.random.default_rng(RNG_SEED + 4)
    K, R, centre = _sample_camera(rng)
    P = _projection(K, R, centre)

    xy = rng.uniform(-20, 20, size=(4, 4, 2))
    ground = np.concatenate([xy, np.zeros((*xy.shape[:2], 1))], axis=-1)
    raised = np.concatenate([xy, np.full((*xy.shape[:2], 1), 1.6)], axis=-1)

    p_ground = perspective_transform_3d_to_2d(ground, P)
    p_raised = perspective_transform_3d_to_2d(raised, P)

    assert np.all(np.isfinite(p_ground))
    assert np.all(np.isfinite(p_raised))
    # A 1.6 m object must have non-zero extent in the image.
    assert np.any(np.abs(p_raised - p_ground) > 1e-6)
