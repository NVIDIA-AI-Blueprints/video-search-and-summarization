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

"""
3D Rotation Representation Conversion Module

This module provides utilities for converting between different 3D rotation representations
commonly used in computer vision and robotics. It supports conversions between Euler angles,
quaternions, and rotation matrices.

Key Features:
- Convert quaternions to Euler angles (pitch, roll, yaw)
- Convert Euler angles to quaternions
- Convert quaternions to rotation matrices
- Convert rotation matrices to quaternions
- Handle different rotation conventions and coordinate systems

Main Functions:
- euler_from_quaternion: Convert quaternion to Euler angles
- euler_to_quaternion: Convert Euler angles to quaternion
- quaternion_to_rotation_matrix: Convert quaternion to 3x3 rotation matrix
- rotation_matrix_to_quaternion: Convert rotation matrix to quaternion

Rotation Representations:

Euler Angles:
- Format: (pitch, roll, yaw) in radians
- pitch: Rotation around x-axis
- roll:  Rotation around y-axis
- yaw:   Rotation around z-axis
- Convention: ZYX intrinsic rotations.  The pitch/roll naming follows
  the body-frame convention used by
  :mod:`spatialai_data_utils.core.boxes.box_3d`, where the box's
  heading is along world ``-Y``, so the longitudinal (roll) axis is
  ``Y`` and the lateral (pitch) axis is ``X``.

Quaternions:
- Format: (w, x, y, z) where w is the real part
- Unit quaternions: ||q|| = sqrt(w² + x² + y² + z²) = 1
- Advantages: No gimbal lock, smooth interpolation, compact representation
- Commonly used in 3D graphics and physics engines

Rotation Matrices:
- Format: 3x3 orthogonal matrix with determinant = 1
- Represents rotation as linear transformation
- Easy to compose (matrix multiplication)
- Used in camera calibration and coordinate transformations

Conversion Notes:
- Gimbal lock can occur when the middle Euler angle — ``roll``
  (rotation about Y) — reaches ±π/2; this is the angle recovered
  via :func:`math.asin` in :func:`euler_from_quaternion` and is
  always the singular axis under the ZYX-intrinsic decomposition,
  regardless of the body-frame names attached to the other two.
- Quaternion to Euler conversion uses ZYX convention
- Rotation matrix to quaternion uses numerically stable algorithm
- All angles are in radians unless otherwise specified

Use Cases:
- Convert between rotation formats for different libraries/frameworks
- Process 3D object orientations from detection models
- Transform rotations between coordinate systems
- Prepare rotations for visualization or evaluation
- Handle camera pose representations

Typical Workflow:
1. Receive rotation in one format (e.g., quaternion from model output)
2. Convert to desired format (e.g., Euler angles for human interpretation)
3. Apply rotation transformations as needed
4. Convert back if required by downstream components

Coordinate System:
The conversions in this module are right-handed and use the
ZYX-intrinsic convention for Euler angles
(``R = R_z(yaw) · R_y(roll) · R_x(pitch)`` under the body-frame
naming, where ``pitch`` rotates about world X, ``roll`` rotates
about world Y, and ``yaw`` rotates about world Z), but they make no
assumption about which axis of the world frame is "forward" — they
work with whatever world / camera / box frame the caller uses.
The 3D-box frame conventions used elsewhere in this package (z-up
world; box ``w`` / ``l`` / ``h`` along X / Y / Z; heading along -Y
at ``yaw = 0``) are documented in
:mod:`spatialai_data_utils.core.boxes.box_3d`.
"""

import math
import numpy as np


def euler_from_quaternion(w, x, y, z):
    """
    Convert a quaternion into Euler angles (pitch, roll, yaw) in radians.

    Uses the standard formula for ZYX Euler angles (intrinsic rotations).
    Under this codebase's body-frame naming (heading along ``-Y``):

    - pitch is rotation around x (radians, counterclockwise) — lateral axis
    - roll  is rotation around y (radians, counterclockwise) — longitudinal axis
    - yaw   is rotation around z (radians, counterclockwise) — vertical axis

    :param w: The real component of the quaternion.
    :type w: float
    :param x: The first imaginary component of the quaternion.
    :type x: float
    :param y: The second imaginary component of the quaternion.
    :type y: float
    :param z: The third imaginary component of the quaternion.
    :type z: float
    :return: A tuple containing ``(pitch_x, roll_y, yaw_z)`` in radians
        — same column order as ``box[:, 6:9]`` in the canonical 9-DoF
        layout.
    :rtype: tuple(float, float, float)
    """
    t0 = +2.0 * (w * x + y * z)
    t1 = +1.0 - 2.0 * (x * x + y * y)
    pitch_x = math.atan2(t0, t1)

    t2 = +2.0 * (w * y - z * x)
    t2 = +1.0 if t2 > +1.0 else t2
    t2 = -1.0 if t2 < -1.0 else t2
    roll_y = math.asin(t2)

    t3 = +2.0 * (w * z + x * y)
    t4 = +1.0 - 2.0 * (y * y + z * z)
    yaw_z = math.atan2(t3, t4)

    return pitch_x, roll_y, yaw_z  # in radians


def euler_to_quaternion(pitch, roll, yaw, /):
    """
    Convert Euler angles to quaternion.

    Argument order matches the canonical 9-DoF ``box[:, 6:9]`` layout
    used elsewhere in this codebase (``pitch`` = rotation about X,
    ``roll`` = rotation about Y, ``yaw`` = rotation about Z, under
    body-frame heading-along-``-Y`` naming).

    .. note::
       Parameters are **positional-only** (the trailing ``/`` in the
       signature) to prevent silent miscomputation across the
       ``[roll, pitch, yaw]`` → ``[pitch, roll, yaw]`` rename: callers
       still using the old keyword names (e.g.
       ``euler_to_quaternion(roll=θ, pitch=φ, yaw=ψ)``) now raise
       ``TypeError`` instead of silently swapping which axis each
       value rotates about.

    :param pitch: Rotation around the x-axis in radians.
    :param roll:  Rotation around the y-axis in radians.
    :param yaw:   Rotation around the z-axis in radians.
    :return: Quaternion as a tuple (w, x, y, z).
    """
    # Calculate the half angles
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)

    # Calculate the quaternion components for R = R_z(yaw) · R_y(roll)
    # · R_x(pitch) (cp/sp are X-axis half-angle trig, cr/sr are Y-axis
    # half-angle trig).
    w = cp * cr * cy + sp * sr * sy
    x = sp * cr * cy - cp * sr * sy
    y = cp * sr * cy + sp * cr * sy
    z = cp * cr * sy - sp * sr * cy

    return (w, x, y, z)


def quaternion_to_rotation_matrix(qw, qx, qy, qz):
    """
    Convert a quaternion (w, x, y, z) to a 3x3 rotation matrix.

    :param qw: The real component of the quaternion.
    :type qw: float
    :param qx: The first imaginary component of the quaternion.
    :type qx: float
    :param qy: The second imaginary component of the quaternion.
    :type qy: float
    :param qz: The third imaginary component of the quaternion.
    :type qz: float
    :return: A 3x3 NumPy array representing the rotation matrix.
    :rtype: numpy.ndarray
    """
    return np.array(
        [
            [
                1 - 2 * qy**2 - 2 * qz**2,
                2 * qx * qy - 2 * qz * qw,
                2 * qx * qz + 2 * qy * qw,
            ],
            [
                2 * qx * qy + 2 * qz * qw,
                1 - 2 * qx**2 - 2 * qz**2,
                2 * qy * qz - 2 * qx * qw,
            ],
            [
                2 * qx * qz - 2 * qy * qw,
                2 * qy * qz + 2 * qx * qw,
                1 - 2 * qx**2 - 2 * qy**2,
            ],
        ]
    )


def rotation_matrix_to_quaternion(R):
    """
    Convert a 3x3 rotation matrix to a quaternion (w, x, y, z).

    :param R: A 3x3 NumPy array representing the rotation matrix.
    :type R: numpy.ndarray
    :return: A NumPy array representing the quaternion [qw, qx, qy, qz].
    :rtype: numpy.ndarray
    """
    tr = np.trace(R)
    if tr > 0:
        S = np.sqrt(tr + 1.0) * 2
        qw = 0.25 * S
        qx = (R[2, 1] - R[1, 2]) / S
        qy = (R[0, 2] - R[2, 0]) / S
        qz = (R[1, 0] - R[0, 1]) / S
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        S = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        qw = (R[2, 1] - R[1, 2]) / S
        qx = 0.25 * S
        qy = (R[0, 1] + R[1, 0]) / S
        qz = (R[0, 2] + R[2, 0]) / S
    elif R[1, 1] > R[2, 2]:
        S = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        qw = (R[0, 2] - R[2, 0]) / S
        qx = (R[0, 1] + R[1, 0]) / S
        qy = 0.25 * S
        qz = (R[1, 2] + R[2, 1]) / S
    else:
        S = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        qw = (R[1, 0] - R[0, 1]) / S
        qx = (R[0, 2] + R[2, 0]) / S
        qy = (R[1, 2] + R[2, 1]) / S
        qz = 0.25 * S
    return np.array([qw, qx, qy, qz])
