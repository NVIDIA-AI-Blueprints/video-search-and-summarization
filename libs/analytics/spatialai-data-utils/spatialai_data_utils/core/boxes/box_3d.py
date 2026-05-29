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
3D Bounding Box Processing Module

This module provides utilities for processing and converting 3D bounding boxes between
different formats and coordinate systems. It handles boxes from various sources including
ground truth, BEVFormer, Sparse4D, and other 3D detection/tracking models.

Key Features:
- Convert between different 3D box representations
- Process boxes from various model outputs (BEVFormer, Sparse4D, etc.)
- Handle coordinate system transformations
- Support multiple rotation representations (Euler, quaternion, rotation matrix)
- Extract 8-corner vertices from oriented 3D boxes
- Convert between different box parameterizations

Main Functions:
- check_nvschema_coords_len: Validate that an NVSchema
  ``bbox3d.coordinates`` list has **at least** 9 values
  ``[x, y, z, w, l, h, p, r, y, ...]`` (trailing extras permitted)
- box3d_to_corners: Get 8 corner vertices of a 3D box (requires 9-DoF
  input)
- recenter_boxes / unrecenter_boxes: Shift / unshift 3D box centres
  by a 2D origin (data-prep helper for camera-group recentering)
- transform_3d_bboxes / transform_3d_bboxes_10d: Apply a 4x4 rigid
  transform to a batch of yaw-only / quaternion-form boxes
  (legacy; see warnings in their docstrings)

The format-specific ``process_bbox3d_gt`` helper now lives alongside
the loader that produces its input dict
(:mod:`spatialai_data_utils.loaders.ground_truth`) and is re-exported
here for backward compatibility.  The companion
``process_bbox3d_bevformer`` (along with the BEVFormer model-output
loaders that fed it) has been removed in this version; it will be
restored in a later version once the nuScenes → NVSchema
normalization (size swap + heading-axis offset) is implemented and
covered by tests.

3D Box Representation:

Canonical 9-DoF Format (NVSchema ``Bbox3d.coordinates`` layout):
- [x, y, z, width, length, height, pitch, roll, yaw]
- Location: Center point in world coordinates
- Dimensions: Box size in meters
- Orientation: Full Euler-angle triple in radians; rotation applied as
  ``R = R_z(yaw) · R_y(roll) · R_x(pitch)`` (ZYX-intrinsic), matching
  :func:`spatialai_data_utils.core.geometry.rotation.euler_from_quaternion`.
  The names ``pitch`` / ``roll`` / ``yaw`` follow body-frame convention
  for this codebase's heading-along-``-Y`` boxes — ``pitch`` rotates
  about world X (the body's lateral axis), ``roll`` rotates about
  world Y (the body's longitudinal / heading axis), and ``yaw``
  rotates about world Z (the vertical axis).  See the per-function
  docstrings and ``BOX3D_HEADING_FACE`` for details.

Coordinate Systems:
- World: Right-handed, z-up.  Boxes use ``w`` / ``l`` / ``h`` as the
  X / Y / Z extents respectively; at ``yaw = 0`` the heading face
  points along **-Y** (the negative end of the local length axis;
  see :data:`BOX3D_HEADING_FACE`).
- Camera: Right-handed, z-forward (along the optical axis).
- BEV: Top-down view of the world frame; the BEV renderer maps world
  +X to image columns (right) and world +Y to image rows (up, after
  row-flipping on the canvas).

Rotation Representations:
- Euler angles: [pitch, roll, yaw] in radians (see body-frame note
  above for axis mapping)
- Quaternion: [w, x, y, z] unit quaternion
- Rotation matrix: 3x3 orthogonal matrix

Use Cases:
- Convert model outputs to standard evaluation format
- Transform boxes between coordinate systems
- Extract box geometry for visualization
- Prepare boxes for 3D IoU computation
- Process multi-model detection results

Typical Workflow:
1. Load detection/tracking results from model
2. Convert to the canonical 9-DoF layout
3. Apply coordinate transformations if needed
4. Extract corners for rendering or IoU calculation
5. Use for evaluation or downstream processing
"""

import numpy as np

from spatialai_data_utils.core.geometry.rotation import (
    quaternion_to_rotation_matrix,
    rotation_matrix_to_quaternion,
)

# ---------------------------------------------------------------------------
# Backward-compatibility re-export
# ---------------------------------------------------------------------------
# ``process_bbox3d_gt`` used to live in this module but now lives alongside
# the loader that produces its input dict
# (:mod:`spatialai_data_utils.loaders.ground_truth`).  Importing it from
# here keeps existing call sites working unchanged; new code should
# import from the canonical module.
from spatialai_data_utils.loaders.ground_truth import process_bbox3d_gt  # noqa: E402,F401

# ---------------------------------------------------------------------------
# 9-DoF box-vector layout
# ---------------------------------------------------------------------------
# Column indices into the canonical
# ``[x, y, z, w, l, h, pitch, roll, yaw, ...]`` box array (NVSchema
# ``Bbox3d.coordinates`` order).  Use these instead of hard-coded
# literals in callers to keep the layout consistent across the codebase.
#
# Body-frame naming (heading along -Y, see :data:`BOX3D_HEADING_FACE`):
# - column 6 (``pitch``) is the angle that rotates points about world X
#   (the box's lateral axis);
# - column 7 (``roll``) rotates about world Y (the longitudinal /
#   heading axis);
# - column 8 (``yaw``) rotates about world Z (vertical).

X, Y, Z = 0, 1, 2
W, L, H = 3, 4, 5
PITCH, ROLL, YAW = 6, 7, 8


# ---------------------------------------------------------------------------
# 3D bounding box corner topology
# ---------------------------------------------------------------------------
# These constants describe the 8-corner convention returned by
# :func:`box3d_to_corners`.  They are the source of truth for cuboid
# topology and are reused by visualisation helpers (wireframe drawing,
# heading-face shading, BEV rendering).

#: 12 edges of the 3D cuboid — pairs of corner indices to connect when
#: drawing a wireframe box.
BOX3D_EDGES = (
    (0, 1), (0, 3), (0, 4),
    (1, 2), (1, 5),
    (3, 2), (3, 7),
    (4, 5), (4, 7),
    (2, 6), (5, 6), (6, 7),
)

#: Corner indices of the bottom face (``z = centre - h / 2``).
BOX3D_BOTTOM_FACE = (0, 3, 4, 7)

#: Corner indices of the top face (``z = centre + h / 2``).
BOX3D_TOP_FACE = (1, 2, 5, 6)

#: Corner indices of the box's "heading" face (front in -Y at ``yaw = 0``),
#: used for direction indication in visualisations.
BOX3D_HEADING_FACE = (1, 5, 4, 0)


def check_nvschema_coords_len(coords):
    """Raise if NVSchema ``bbox3d.coordinates`` has fewer than 9 values.

    The NVSchema wire format reserves the first 9 values as
    ``[x, y, z, w, l, h, pitch, roll, yaw]`` (the ``Bbox3d`` proto's
    ``repeated double coordinates`` field).  Additional trailing
    values beyond index 8 (e.g. velocity components appended by some
    converters) are permitted — downstream consumers such as
    :func:`box3d_to_corners` only read the first nine columns and
    ignore the rest.

    Legacy 7-value NVSchema inputs (some real-world datasets still
    emit ``[x, y, z, w, l, h, yaw]`` only) are **rejected** — those
    datasets must be re-exported to the 9-value form before
    consumption.

    Callers that want the 7-DoF ``[x, y, z, w, l, h, yaw]`` internal
    form can do the slice inline after this check passes:
    ``[c[0], c[1], c[2], c[3], c[4], c[5], c[8]]``.

    :param coords: Sequence of coordinates whose first nine elements
        are ``[x, y, z, w, l, h, pitch, roll, yaw]``.  Length must be
        ``>= 9``; trailing extras are ignored here but preserved by
        any caller that forwards the full sequence.
    :type coords: Sequence[float]
    :raises ValueError: If *coords* has fewer than 9 values.
    """
    n = len(coords)
    if n < 9:
        raise ValueError(
            f"NVSchema bbox3d.coordinates must have at least 9 values "
            f"[x,y,z,w,l,h,pitch,roll,yaw]; got {n}.  Legacy 7-value "
            f"inputs are no longer accepted — re-export the dataset "
            f"to 9-value form.  Trailing extras beyond index 8 are "
            f"permitted."
        )


def transform_3d_bboxes(bboxes, transformation_matrix):
    """
    Apply a 4x4 transformation matrix to an array of 7-DoF 3D bounding boxes.

    Transforms the center coordinates and adjusts the yaw angle according to the
    rotation component of the transformation matrix. Dimensions (w, l, h) remain unchanged.

    .. warning::
       **7-DoF only.**  Reads ``bboxes[:, 6]`` as ``yaw``, ignoring any
       additional columns.  Passing a 9-DoF
       ``[x, y, z, w, l, h, pitch, roll, yaw]`` box will silently
       interpret the ``pitch`` column as ``yaw``, drop ``roll``, and
       return a ``(N, 7)`` array — almost certainly not what the
       caller wants.  This helper is also non-trivial under non-rigid
       transforms (it preserves dimensions verbatim) and currently has
       no production callers.  Prefer composing the rotation-matrix
       form yourself or using a per-box quaternion path.

    :param bboxes: NumPy array of shape (N, 7) where each row is [x, y, z, w, l, h, yaw].
    :type bboxes: numpy.ndarray
    :param transformation_matrix: A 4x4 NumPy array representing the transformation.
    :type transformation_matrix: numpy.ndarray
    :return: Transformed bounding boxes as a NumPy array of shape (N, 7).
    :rtype: numpy.ndarray
    """
    n = bboxes.shape[0]

    # Extract rotation matrix from transformation matrix
    rotation_matrix = transformation_matrix[:3, :3]
    translation = transformation_matrix[:3, 3]

    # Extract centers and create homogeneous coordinates
    centers = bboxes[:, :3]
    centers_homogeneous = np.hstack((centers, np.ones((n, 1))))

    # Transform centers
    transformed_centers = np.dot(centers_homogeneous, transformation_matrix.T)[:, :3]

    # Extract dimensions (width, length, height)
    dimensions = bboxes[:, 3:6]

    # Extract original yaw angles
    original_yaws = bboxes[:, 6]

    # Create rotation matrices for original yaws
    cos_yaws = np.cos(original_yaws)
    sin_yaws = np.sin(original_yaws)
    zeros = np.zeros_like(cos_yaws)
    ones = np.ones_like(cos_yaws)

    original_rotations = np.stack(
        [cos_yaws, -sin_yaws, zeros, sin_yaws, cos_yaws, zeros, zeros, zeros, ones],
        axis=1,
    ).reshape(-1, 3, 3)

    # Combine rotations
    combined_rotations = np.matmul(rotation_matrix, original_rotations)

    # Calculate new yaw angles
    new_yaws = np.arctan2(combined_rotations[:, 1, 0], combined_rotations[:, 0, 0])

    # Assemble transformed bounding boxes
    transformed_bboxes = np.column_stack((transformed_centers, dimensions, new_yaws))

    return transformed_bboxes


def transform_3d_bboxes_10d(bboxes, transform_matrix):
    """
    Apply a 4x4 transformation matrix to an array of 10-DoF 3D bounding boxes.

    Transforms the location and rotation (represented by quaternions).

    .. warning::
       **Non-canonical layout** ``[qw, qx, qy, qz, x, y, z, w, l, h]``
       — quaternion first, position next, dimensions last.  This
       differs from both the NVSchema 9-DoF layout
       ``[x, y, z, w, l, h, pitch, roll, yaw]`` and from nuScenes'
       ``Box`` shape, so this helper is **not** drop-in compatible
       with either pipeline.  The dimension transform uses
       ``np.linalg.norm`` over the rotation-scaled basis matrix,
       which is correct for pure rotations but can give surprising
       results under shears / anisotropic scales.  Currently no
       production callers; review carefully before reuse.

    Input format: [qw, qx, qy, qz, x, y, z, w, l, h]

    :param bboxes: NumPy array of shape (N, 10) representing bounding boxes with
                   quaternion rotation, location, and dimensions.
    :type bboxes: numpy.ndarray
    :param transform_matrix: A 4x4 NumPy array representing the transformation.
    :type transform_matrix: numpy.ndarray
    :return: Transformed bounding boxes as a NumPy array of shape (N, 10).
    :rtype: numpy.ndarray
    """
    bboxes = np.array(bboxes)
    num_bboxes = bboxes.shape[0]

    # Extract components
    quaternions = bboxes[:, :4]
    locations = bboxes[:, 4:7]
    dimensions = bboxes[:, 7:]

    # Convert quaternions to rotation matrices
    rotation_matrices = []
    for q in quaternions:
        rot_mat = quaternion_to_rotation_matrix(*q)
        rotation_matrices.append(rot_mat)
    rotation_matrices = np.array(rotation_matrices)

    # Create 4x4 matrices for each bounding box
    bbox_matrices = np.zeros((num_bboxes, 4, 4))
    bbox_matrices[:, :3, :3] = rotation_matrices
    bbox_matrices[:, :3, 3] = locations
    bbox_matrices[:, 3, 3] = 1

    # Apply transformation
    new_bbox_matrices = np.einsum("ij,njk->nik", transform_matrix, bbox_matrices)

    # Extract new rotation matrices and positions
    new_rotation_matrices = new_bbox_matrices[:, :3, :3]
    new_positions = new_bbox_matrices[:, :3, 3]

    # Convert new rotation matrices back to quaternions
    new_quaternions = []
    for rot_mat in new_rotation_matrices:
        q = rotation_matrix_to_quaternion(rot_mat)
        new_quaternions.append(q)
    new_quaternions = np.array(new_quaternions)

    # Transform dimensions
    scale_matrices = np.array(
        [np.diag(np.concatenate([dim, [1]])) for dim in dimensions]
    )
    new_scale_matrices = np.einsum(
        "ij,njk,nkl->nil", transform_matrix, bbox_matrices, scale_matrices
    )
    new_dimensions = np.linalg.norm(new_scale_matrices[:, :3, :3], axis=1)

    # Construct the new bbox vectors
    new_bboxes = np.concatenate(
        [new_quaternions, new_positions, new_dimensions], axis=1
    )

    return new_bboxes


def recenter_boxes(boxes, origin):
    """Shift 3D box centers so that a camera-group origin maps to ``(0, 0)``.

    This is the box-side counterpart of
    :func:`spatialai_data_utils.loaders.calibration.apply_recentering`
    (which shifts calibration extrinsics instead).  Both approaches produce
    identical projection results — choose based on context:

    * **Recenter boxes** — best for data preparation, evaluation, and
      format conversion (simple array math, no matrix inversion).
    * **Recenter calibration** — best for visualization when predictions
      are already in recentered coordinates.

    :param boxes: ``(N, 7+)`` array of 3D boxes ``[x, y, z, ...]``.
    :type boxes: numpy.ndarray
    :param origin: ``[x, y]`` group origin to subtract.
    :type origin: array-like
    :return: Copy of *boxes* with ``x`` and ``y`` shifted.
    :rtype: numpy.ndarray
    """
    boxes = np.asarray(boxes, dtype=np.float64).copy()
    boxes[:, 0] -= origin[0]
    boxes[:, 1] -= origin[1]
    return boxes


def unrecenter_boxes(boxes, origin):
    """Reverse recentering — map boxes back to original world coordinates.

    :param boxes: ``(N, 7+)`` array of recentered 3D boxes ``[x, y, z, ...]``.
    :type boxes: numpy.ndarray
    :param origin: ``[x, y]`` group origin that was subtracted.
    :type origin: array-like
    :return: Copy of *boxes* with ``x`` and ``y`` shifted back.
    :rtype: numpy.ndarray
    """
    boxes = np.asarray(boxes, dtype=np.float64).copy()
    boxes[:, 0] += origin[0]
    boxes[:, 1] += origin[1]
    return boxes


def box3d_to_corners(boxes3d, origin=(0.5, 0.5, 0.5)):
    """Compute the 8 corner vertices of 3D bounding boxes.

    Pure-numpy implementation (no torch / mmdet3d dependency).  Boxes
    follow NVSchema's canonical 9-DoF layout:
    ``[x, y, z, w, l, h, pitch, roll, yaw, ...]`` (matches the
    ``Bbox3d`` proto).  Full rotation ``R = R_z(yaw) · R_y(roll) ·
    R_x(pitch)`` is applied — ZYX-intrinsic convention, consistent with
    :func:`spatialai_data_utils.core.geometry.rotation.euler_from_quaternion`.
    Column 6 (``pitch``) rotates about world X (lateral axis under the
    heading-along-``-Y`` body-frame convention), column 7 (``roll``)
    about world Y (longitudinal / heading axis), column 8 (``yaw``)
    about world Z.

    Legacy 7-DoF arrays ``[x, y, z, w, l, h, yaw]`` are **not** accepted
    — callers holding yaw-only data must pad with ``pitch = roll = 0``
    before calling (``np.insert(box7, 6, 0, axis=-1)`` twice, or
    equivalent) so the canonical layout is enforced consistently with
    the NVSchema ``bbox3d.coordinates`` contract.

    The *origin* parameter controls where the ``(x, y, z)`` reference point
    sits inside the box, expressed as fractions along ``(w, l, h)``:

    - ``(0.5, 0.5, 0.5)`` — geometric centre (all three axes centred).
      **Default.**
    - ``(0.5, 0.5, 0.0)`` — centre of the bottom face (``z`` is at the bottom).
      Matches ``mmdet3d.LiDARInstance3DBoxes`` default.

    Corner ordering (before rotation, at zero rotation):

    - Bottom face (``w`` / ``l`` rectangle at low ``z``): indices 0, 3, 4, 7
    - Top face   (``w`` / ``l`` rectangle at high ``z``): indices 1, 2, 5, 6

    :param boxes3d: Array of 3D boxes with shape ``(N, 9+)``.
        Extra trailing columns beyond index 8 (e.g. velocity) are
        ignored.
    :type boxes3d: numpy.ndarray
    :param origin: ``(ox, oy, oz)`` in ``[0, 1]`` describing the box-local
        coordinates of the reference point ``(x, y, z)``. Defaults to
        geometric centre.
    :type origin: tuple(float, float, float)
    :return: Corner coordinates with shape ``(N, 8, 3)``.
    :rtype: numpy.ndarray
    :raises ValueError: If the last axis of *boxes3d* has fewer than 9
        elements.  Legacy 7-DoF ``[x, y, z, w, l, h, yaw]`` inputs are
        no longer supported.
    """
    boxes3d = np.asarray(boxes3d, dtype=np.float64)
    if boxes3d.ndim == 1:
        boxes3d = boxes3d[None]
    if boxes3d.shape[-1] < 9:
        raise ValueError(
            f"box3d_to_corners requires 9-DoF boxes "
            f"[x, y, z, w, l, h, pitch, roll, yaw, ...]; got "
            f"shape[-1]={boxes3d.shape[-1]}.  Legacy 7-DoF arrays "
            f"[x, y, z, w, l, h, yaw] must be padded with pitch=roll=0 "
            f"before calling (e.g. "
            f"``np.insert(box7, 6, 0.0, axis=-1)`` twice)."
        )
    n = boxes3d.shape[0]

    # Unit cube corners in {0, 1}^3, reordered to match the project-wide
    # convention (bottom 0-3-4-7, top 1-2-5-6).
    corners_norm = np.stack(
        np.unravel_index(np.arange(8), [2] * 3), axis=1
    ).astype(np.float64)
    corners_norm = corners_norm[[0, 1, 3, 2, 4, 5, 7, 6]]
    corners_norm = corners_norm - np.asarray(origin, dtype=np.float64)

    # Scale by (w, l, h) along each axis.
    whl = boxes3d[:, 3:6]  # (N, 3)
    corners = corners_norm[None] * whl[:, None, :]  # (N, 8, 3)

    # Build the analytic ZYX-intrinsic rotation matrix
    # R = R_z(yaw) @ R_y(roll) @ R_x(pitch) for each box.  Under
    # body-frame naming with heading along -Y, ``pitch`` rotates about
    # world X, ``roll`` rotates about world Y, ``yaw`` about world Z.
    pitch = boxes3d[:, 6]
    roll = boxes3d[:, 7]
    yaw = boxes3d[:, 8]

    cp, sp = np.cos(pitch), np.sin(pitch)
    cr, sr = np.cos(roll), np.sin(roll)
    cy, sy = np.cos(yaw), np.sin(yaw)

    rot = np.empty((n, 3, 3), dtype=np.float64)
    # Row 0
    rot[:, 0, 0] = cy * cr
    rot[:, 0, 1] = cy * sr * sp - sy * cp
    rot[:, 0, 2] = cy * sr * cp + sy * sp
    # Row 1
    rot[:, 1, 0] = sy * cr
    rot[:, 1, 1] = sy * sr * sp + cy * cp
    rot[:, 1, 2] = sy * sr * cp - cy * sp
    # Row 2
    rot[:, 2, 0] = -sr
    rot[:, 2, 1] = cr * sp
    rot[:, 2, 2] = cr * cp

    corners = np.einsum("nij,nkj->nki", rot, corners)

    # Translate to the box centre.
    corners += boxes3d[:, None, :3]
    return corners
