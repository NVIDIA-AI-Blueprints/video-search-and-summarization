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
3D <-> 2D Projection Module

Pure-numpy utilities for projecting 3D points and bounding boxes into 2D
image coordinates, and for unprojecting 2D pixels (with a known
camera-space depth) back to 3D world coordinates.  All functions use
camera calibration parameters.  No torch / mmdet3d dependency — box
corners are computed by
:func:`spatialai_data_utils.core.boxes.box_3d.box3d_to_corners`.

Main Functions (low-level -> high-level):

- :func:`vertices_to_aabb`      — collapse ``(N, 2)`` pixel vertices to a
  flat ``[left, top, right, bottom]`` AABB.
- :func:`project_points_3d_to_image` — project ``(..., 3)`` points to 2D
  pixel coordinates via a 3x4 or 4x4 world-to-image matrix (applies
  perspective divide; does not filter visibility).
- :func:`unproject_points_2d_to_3d` — inverse of
  :func:`project_points_3d_to_image`: given 2D pixels and their
  camera-space depths, recover 3D world coordinates via the same
  3x4 or 4x4 world-to-image matrix.
- :func:`unproject_points_2d_to_ground` — depth-free variant: intersect
  each pixel's viewing ray with a horizontal world plane (default
  ``Z = 0``, the XOY ground plane).  Returns the 3D world point on
  the plane plus a validity mask (above-horizon / parallel-ray
  pixels are flagged invalid).
- :func:`unproject_bbox3d_via_ground` — reconstruct a yaw-only 3D bbox
  from its 8 projected 2D corners: bottom corners are anchored on
  ``Z = ground_z`` via :func:`unproject_points_2d_to_ground`; the bbox
  height ``H`` is solved in least-squares form from the 4 top corners'
  pixels (8 linear equations in one unknown).
- :func:`project_boxes_3d_to_2d` — project 3D boxes' 8 corners to 2D
  pixel coordinates (9-DoF input
  ``[x, y, z, w, l, h, pitch, roll, yaw, ...]``) and filter boxes not
  visible in the image.
- :func:`project_bev_objects_bbox_in_image` — NVSchema-aware stage-1 wrapper:
  take a frame of NVSchema object dicts and enrich each visible box
  with its projected 2D corners / AABB for a specific sensor.

Projection Pipeline:

1. 3D world coordinates -> 3D camera coordinates (world-to-camera).
2. 3D camera coordinates -> 2D pixel coordinates (intrinsic + divide).
3. Visibility filtering (depth check, image-bounds check).

Visibility Criteria (used by :func:`project_boxes_3d_to_2d`):

- All 8 corners must have positive depth (in front of camera).
- At least one projected corner must fall inside the image.
- Boxes failing either check are filtered out.
"""

import json
from typing import Dict, List, Optional, Tuple

import numpy as np

from spatialai_data_utils.constants import (
    IMAGE_SIZE,
    KEY_BBOX3D,
    KEY_COORDINATES,
    KEY_INFO,
    KEY_INTRINSIC_MATRIX,
    KEY_SENSOR_ID,
    KEY_VERTICES,
    KEY_W2C_MATRIX,
)
from spatialai_data_utils.core.cameras.utils import get_calib_field
from spatialai_data_utils.core.boxes.box_3d import (
    box3d_to_corners,
    check_nvschema_coords_len,
)

_EMPTY_CORNERS_2D = np.empty((0, 8, 2), dtype=np.float64)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _to_homogeneous(points: np.ndarray) -> np.ndarray:
    """Append a trailing 1 to each point to form homogeneous coordinates.

    Works on arbitrary leading shapes: ``(..., D) -> (..., D + 1)``.

    :param points: Array with last axis of size ``D``.
    :return: Array with last axis of size ``D + 1`` and dtype ``float64``.
    """
    points = np.asarray(points, dtype=np.float64)
    ones = np.ones(points.shape[:-1] + (1,), dtype=np.float64)
    return np.concatenate([points, ones], axis=-1)


def _any_corner_inside_image(
    corners_pix: np.ndarray,
    image_size: Optional[Tuple[int, int]],
) -> np.ndarray:
    """Per-box 'any corner inside image' check — vectorized.

    :param corners_pix: ``(N, K, 2)`` pixel coordinates (``K`` corners per box).
    :param image_size: ``(width, height)`` or ``None`` for the package default.
    :return: ``(N,)`` bool array.
    """
    w, h = image_size if image_size is not None else IMAGE_SIZE
    xs = corners_pix[..., 0]
    ys = corners_pix[..., 1]
    inside = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)  # (N, K)
    return inside.any(axis=-1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def vertices_to_aabb(verts):
    """Convert projected 2D corner vertices to a flat axis-aligned box.

    Given an ``(N, 2)`` array of pixel coordinates (typically the 8 projected
    corners of a 3D cuboid), return the enclosing ``[left, top, right, bottom]``
    axis-aligned bounding box as four ``float`` values.

    :param verts: 2D points with shape ``(N, 2)`` (N >= 1).
    :type verts: numpy.ndarray or Sequence[Sequence[float]]
    :return: Flat list ``[left, top, right, bottom]`` of floats.
    :rtype: list[float]
    """
    verts = np.asarray(verts, dtype=np.float64)
    xs = verts[..., 0]
    ys = verts[..., 1]
    return [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())]


def project_points_3d_to_image(points, world2img, min_depth=1e-5):
    """Project 3D points to 2D pixel coordinates via a world-to-image matrix.

    Multiplies homogeneous 3D points by the projection matrix and
    performs the perspective divide (``x/z``, ``y/z``) to produce
    pixel coordinates.  Shape-preserving on the leading axes:
    ``(..., 3) -> (..., 2)`` for pixels, ``(...,)`` for the mask.

    Points whose projected camera-space depth is ``<= min_depth``
    (behind the camera or too close to the image plane) have no
    geometrically valid image projection.  For those entries the
    z-divide is clipped at ``min_depth`` to avoid division-by-zero,
    but the resulting pixel values are **not meaningful** — consult
    the returned ``front_mask`` to identify valid projections.

    :param points: 3D points with any shape ``(..., 3)``.
    :type points: numpy.ndarray
    :param world2img: 3x4 or 4x4 world-to-image projection matrix
        (often ``intrinsic @ [R|t]``).
    :type world2img: numpy.ndarray
    :param min_depth: Depth threshold used both to gate the mask
        (``z > min_depth``) and to clip the divisor to avoid
        division-by-zero.
    :type min_depth: float
    :returns: Tuple ``(pixels, front_mask)``:

        - **pixels** (``numpy.ndarray``): 2D pixel coordinates with
          shape ``(..., 2)`` and dtype ``float64``.  Entries where
          ``front_mask`` is ``False`` contain garbage — do not use
          without checking the mask.
        - **front_mask** (``numpy.ndarray``): shape ``(...)``, dtype
          ``bool``; ``True`` iff the corresponding point is in front
          of the camera (``z > min_depth``) and therefore has a
          geometrically valid image projection.
    :rtype: tuple(numpy.ndarray, numpy.ndarray)
    """
    mat = np.asarray(world2img, dtype=np.float64)
    scaled = (_to_homogeneous(points) @ mat.T)[..., :3]  # (..., 3) x, y*z, z
    z = scaled[..., 2]
    front_mask = z > min_depth
    safe_z = np.clip(z, a_min=min_depth, a_max=None)
    pixels = scaled[..., :2] / safe_z[..., None]
    return pixels, front_mask


def unproject_points_2d_to_3d(points_2d, world2img, depths):
    """Unproject 2D pixel coordinates back to 3D world coordinates.

    Inverse of :func:`project_points_3d_to_image`.  Given a 2D pixel
    ``(u, v)`` and its camera-space depth ``z`` (the same ``z`` that
    the forward projection divides by), recover the 3D world point
    ``(X, Y, Z)`` that maps to it.

    Math::

        Forward:  [u*z, v*z, z]^T = world2img[:3, :] @ [X, Y, Z, 1]^T
        Inverse:  [X, Y, Z]^T     = M^{-1} @ ([u*z, v*z, z]^T - t)

    where ``M = world2img[:3, :3]`` and ``t = world2img[:3, 3]``.
    The 3x3 block ``M`` must be invertible — true for any well-posed
    pinhole camera (``M = intrinsic @ R`` with both factors invertible).

    Shape-preserving on the leading axes: ``(..., 2) -> (..., 3)``.
    *depths* must broadcast to ``points_2d.shape[:-1]`` — a Python
    scalar / 0-d array works for the single-point case, and a per-pixel
    ``(..., )`` array works for batches.

    :param points_2d: 2D pixel coordinates with any shape ``(..., 2)``.
    :type points_2d: numpy.ndarray
    :param world2img: 3x4 or 4x4 world-to-image projection matrix
        (often ``intrinsic @ [R|t]``).  Only the first 3 rows are used,
        matching :func:`project_points_3d_to_image`.
    :type world2img: numpy.ndarray
    :param depths: Camera-space depth(s) for the input pixels — the
        same ``z`` returned (and divided by) by
        :func:`project_points_3d_to_image`.  Must broadcast to
        ``points_2d.shape[:-1]``.  Negative or zero depths are accepted
        (they describe points on the back-extended viewing ray); the
        caller is responsible for any visibility filtering.
    :type depths: float or numpy.ndarray
    :return: 3D world coordinates with shape ``(..., 3)`` and dtype
        ``float64``.
    :rtype: numpy.ndarray
    :raises ValueError: If *points_2d*'s last axis is not of size 2.
    :raises numpy.linalg.LinAlgError: If the leading 3x3 block of
        *world2img* is singular (degenerate camera).
    """
    pts = np.asarray(points_2d, dtype=np.float64)
    if pts.ndim == 0 or pts.shape[-1] != 2:
        raise ValueError(
            f"points_2d last dim must be 2, got shape {pts.shape}"
        )
    depth = np.asarray(depths, dtype=np.float64)
    mat = np.asarray(world2img, dtype=np.float64)

    M = mat[:3, :3]
    t = mat[:3, 3]

    # Build [u*z, v*z, z] in a broadcasting-safe way: depth may be a
    # scalar or any shape that broadcasts to pts.shape[:-1].
    u_z = pts[..., 0] * depth
    v_z = pts[..., 1] * depth
    z = np.broadcast_to(depth, u_z.shape)
    scaled = np.stack([u_z, v_z, z], axis=-1)             # (..., 3)

    # Solve M @ [X, Y, Z]^T = scaled - t for [X, Y, Z]; applied row-wise
    # across the leading axes via M_inv.T (one inverse for the whole batch).
    rhs = scaled - t                                      # (..., 3)
    return rhs @ np.linalg.inv(M).T                       # (..., 3)


# Absolute-value floor on the world-Z component of the viewing ray
# direction.  Rays whose |d_z| falls below this are considered parallel
# to the ground plane (no finite intersection); we still produce a
# finite output value via clipping, but the returned mask flags them
# invalid so callers ignore the placeholder coordinates.
_PARALLEL_RAY_FLOOR = 1e-12


def unproject_points_2d_to_ground(
    points_2d, world2img, ground_z=0.0, min_depth=1e-5,
):
    """Unproject 2D pixels onto a horizontal world plane (default: Z = 0).

    Variant of :func:`unproject_points_2d_to_3d` that does **not**
    require a per-pixel depth: instead, each pixel's viewing ray is
    intersected with the world plane ``Z = ground_z`` (default ``0`` —
    the XOY ground plane).  Useful when image-space detections are
    known to lie on the ground (e.g. person foot keypoints, vehicle
    wheel-contact points) and a measured depth is unavailable.

    Math::

        Each pixel (u, v) corresponds to a viewing ray in world coords:
            P(z) = C + z * d
        where:
            M = world2img[:3, :3]
            t = world2img[:3, 3]
            C = -M^{-1} @ t            (3,)   camera centre in world
            d =  M^{-1} @ [u, v, 1]^T  (3,)   ray direction in world
        and z is the camera-space depth (the same ``z`` that the
        forward map :func:`project_points_3d_to_image` divides by).

        Intersecting with the plane Z = ground_z:
            z_int = (ground_z - C[2]) / d[2]
            P_int = C + z_int * d

    Pixels above the horizon (ray points away from the plane) yield
    ``z_int < 0`` and are flagged invalid.  Pixels exactly on the
    horizon have ``d[2] ~ 0`` (ray parallel to the plane) — they are
    also flagged invalid via an absolute-value floor on ``d[2]``,
    avoiding division-by-zero in the output.

    Shape-preserving on the leading axes: ``(..., 2) -> (..., 3)``.
    The returned ``world_pts`` always have ``Z`` set exactly to
    ``ground_z`` (no float drift from the geometry).

    :param points_2d: 2D pixel coordinates with any shape ``(..., 2)``.
    :type points_2d: numpy.ndarray
    :param world2img: 3x4 or 4x4 world-to-image projection matrix
        (often ``intrinsic @ [R|t]``).  Only the first 3 rows are used,
        matching :func:`project_points_3d_to_image`.
    :type world2img: numpy.ndarray
    :param ground_z: World-Z coordinate of the (axis-aligned) ground
        plane.  Defaults to ``0.0`` — the XOY plane.  Pass a non-zero
        value when the dataset's ground level is offset from the world
        origin.
    :type ground_z: float
    :param min_depth: Minimum camera-space depth of the ray-plane
        intersection for it to be flagged valid.  Same role as in
        :func:`project_points_3d_to_image`.
    :type min_depth: float
    :returns: Tuple ``(world_pts, front_mask)``:

        - **world_pts** (``numpy.ndarray``): shape ``(..., 3)``, dtype
          ``float64``.  World coordinates of the ray-plane
          intersection, with the Z component set exactly to
          ``ground_z``.  Entries where ``front_mask`` is ``False``
          contain garbage — do not use without checking the mask.
        - **front_mask** (``numpy.ndarray``): shape ``(...,)``, dtype
          ``bool``. ``True`` iff the ray hits the plane in front of
          the camera (``z_int > min_depth``) and is not parallel to
          the plane.
    :rtype: tuple(numpy.ndarray, numpy.ndarray)
    :raises ValueError: If *points_2d*'s last axis is not of size 2.
    :raises numpy.linalg.LinAlgError: If the leading 3x3 block of
        *world2img* is singular (degenerate camera).
    """
    pts = np.asarray(points_2d, dtype=np.float64)
    if pts.ndim == 0 or pts.shape[-1] != 2:
        raise ValueError(
            f"points_2d last dim must be 2, got shape {pts.shape}"
        )
    mat = np.asarray(world2img, dtype=np.float64)

    M_inv = np.linalg.inv(mat[:3, :3])
    C = -M_inv @ mat[:3, 3]                              # (3,)

    # Ray direction in world coords for each pixel: d = M^{-1} @ [u, v, 1]^T.
    pix_homo = np.concatenate(
        [pts, np.ones(pts.shape[:-1] + (1,), dtype=np.float64)],
        axis=-1,
    )                                                    # (..., 3)
    d = pix_homo @ M_inv.T                               # (..., 3)

    # Intersect with the Z = ground_z plane: z_int = (ground_z - C[2]) / d[2].
    # Clip |d[2]| so parallel rays do not produce inf / NaN; those
    # entries are flagged invalid via the mask anyway.
    dz = d[..., 2]
    not_parallel = np.abs(dz) > _PARALLEL_RAY_FLOOR
    safe_dz = np.where(not_parallel, dz, _PARALLEL_RAY_FLOOR)
    z_int = (ground_z - C[2]) / safe_dz                  # (...,)
    front_mask = not_parallel & (z_int > min_depth)

    # Intersection point P = C + z * d. Force Z exactly to ground_z to
    # eliminate floating-point drift in the constrained component.
    p_xy = C[:2] + z_int[..., None] * d[..., :2]         # (..., 2)
    p_z = np.full(p_xy.shape[:-1] + (1,), ground_z, dtype=np.float64)
    world_pts = np.concatenate([p_xy, p_z], axis=-1)     # (..., 3)
    return world_pts, front_mask


# Denominator floor for the 1-D LSQ solve in :func:`unproject_bbox3d_via_ground`.
# Below this, the height-fit system is degenerate (e.g., all top corners
# project to the horizon line) and the bbox is flagged invalid.
_HEIGHT_LSQ_FLOOR = 1e-12

# NVSchema bbox-corner ordering used by
# :func:`spatialai_data_utils.core.boxes.box_3d.box3d_to_corners`:
# the four bottom-face corners (min-z) are at indices (0, 3, 4, 7) and
# the four top-face corners are at (1, 2, 5, 6).  ``BBOX3D_TOP_INDICES[i]``
# is the corner directly above ``BBOX3D_BOTTOM_INDICES[i]`` (same box-local
# (x, y); see ``test_bottom_face_indices``).
BBOX3D_BOTTOM_INDICES = (0, 3, 4, 7)
BBOX3D_TOP_INDICES = (1, 2, 5, 6)


def unproject_bbox3d_via_ground(
    vertices_2d,
    world2img,
    bottom_indices=BBOX3D_BOTTOM_INDICES,
    top_indices=BBOX3D_TOP_INDICES,
    ground_z=0.0,
    min_depth=1e-5,
):
    """Reconstruct a yaw-only 3D bbox from its 8 projected 2D corners.

    Lifts the 8 image-space corners of a 3D bbox back to world
    coordinates under the assumption that the bbox has **zero roll
    and zero pitch** — i.e. the bottom face lies on the world plane
    ``Z = ground_z`` and the top face is parallel above it at
    ``Z = ground_z + H``.  The bbox's yaw (and footprint shape) is
    fully encoded in the four bottom corners' world ``(X, Y)``.

    Workflow::

        1. Slice out the 4 bottom 2D corners (per ``bottom_indices``)
           and the 4 top 2D corners (per ``top_indices``).
        2. Unproject each bottom 2D corner onto the ground plane
           Z = ground_z via :func:`unproject_points_2d_to_ground`,
           yielding 4 world anchors (X_i, Y_i, ground_z).
        3. For each top corner i, the unknown 3D point is
           (X_i, Y_i, ground_z + H) — same (X, Y) as its bottom
           counterpart.  Forward-projecting that with the camera matrix
           gives two linear equations in H (one from u, one from v),
           so the 4 top corners contribute 8 equations.  Solve the
           overdetermined 1-D system in least-squares form:
               H = sum(alpha * beta) / sum(alpha * alpha)
           with
               q       = world2img[:3, 2]               # column for Z
               a_i     = world2img @ [X_i, Y_i, ground_z, 1]^T
               alpha_u = q[0] - u_i * q[2]
               beta_u  = u_i * a_i[2] - a_i[0]
               alpha_v = q[1] - v_i * q[2]
               beta_v  = v_i * a_i[2] - a_i[1]
        4. Top corners are placed at (X_i, Y_i, ground_z + H).
        5. The bbox foot is the mean of the 4 bottom 3D corners.

    For a yaw-only bbox the recovered (X_i, Y_i, Z) match the original
    3D corners exactly (modulo floating-point error) — the LSQ
    over-determination just makes the H estimate robust to image-space
    noise on the top corners.

    Pairing convention: ``top_indices[i]`` is the corner directly above
    ``bottom_indices[i]`` (sharing the same box-local (x, y)).  The
    defaults ``(0, 3, 4, 7)`` / ``(1, 2, 5, 6)`` match the NVSchema
    layout produced by
    :func:`spatialai_data_utils.core.boxes.box_3d.box3d_to_corners`
    (see ``test_bottom_face_indices``).  Together they must form a
    disjoint partition of ``{0, ..., 7}``.

    Shape-preserving on the leading axes:
    ``(..., 8, 2) -> ((..., 8, 3), (..., 3), (...,))``.

    :param vertices_2d: 2D pixel coordinates of the 8 bbox corners
        with shape ``(..., 8, 2)``.
    :type vertices_2d: numpy.ndarray
    :param world2img: 3x4 or 4x4 world-to-image projection matrix
        (often ``intrinsic @ [R|t]``).
    :type world2img: numpy.ndarray
    :param bottom_indices: 4 indices into the 8-vertex axis identifying
        the bottom-face corners.  Default ``(0, 3, 4, 7)``.
    :type bottom_indices: tuple(int, int, int, int)
    :param top_indices: 4 indices into the 8-vertex axis identifying
        the top-face corners, in the order *positionally corresponding*
        to ``bottom_indices``.  Default ``(1, 2, 5, 6)``.
    :type top_indices: tuple(int, int, int, int)
    :param ground_z: World-Z of the (axis-aligned) ground plane on
        which the bbox sits.  Default ``0.0`` — the XOY plane.
    :type ground_z: float
    :param min_depth: Minimum camera-space depth for each bottom-corner
        ground projection to be flagged valid.
    :type min_depth: float
    :returns: Tuple ``(vertices_3d, foot_world, valid_mask)``:

        - **vertices_3d** (``numpy.ndarray``): shape ``(..., 8, 3)``,
          dtype ``float64``.  World coordinates of the 8 bbox corners
          in the *same index order* as the input ``vertices_2d``.
          Bottom corners have Z exactly ``ground_z``; top corners have
          Z exactly ``ground_z + H``.  Entries with
          ``valid_mask=False`` contain garbage.
        - **foot_world** (``numpy.ndarray``): shape ``(..., 3)``,
          dtype ``float64``.  Mean of the 4 bottom 3D corners — the
          bbox's centre-bottom (foot) on the ground plane.
        - **valid_mask** (``numpy.ndarray``): shape ``(...,)``, dtype
          ``bool``. ``True`` iff every bottom corner projects to a
          valid in-front-of-camera ground intersection, the
          height-fit system is well-posed, AND the recovered height
          is strictly positive (top face above the bottom face).
    :rtype: tuple(numpy.ndarray, numpy.ndarray, numpy.ndarray)
    :raises ValueError: If ``vertices_2d`` does not have shape
        ``(..., 8, 2)``, or if ``bottom_indices`` / ``top_indices``
        do not form a disjoint partition of ``{0, ..., 7}``.
    :raises numpy.linalg.LinAlgError: If the leading 3x3 block of
        ``world2img`` is singular.
    """
    pts = np.asarray(vertices_2d, dtype=np.float64)
    if pts.ndim < 2 or pts.shape[-2:] != (8, 2):
        raise ValueError(
            f"vertices_2d must have shape (..., 8, 2), got {pts.shape}"
        )

    bottom_idx = np.asarray(bottom_indices, dtype=np.intp)
    top_idx = np.asarray(top_indices, dtype=np.intp)
    if bottom_idx.shape != (4,) or top_idx.shape != (4,):
        raise ValueError(
            f"bottom_indices and top_indices must each have exactly "
            f"4 elements, got {tuple(bottom_indices)} / {tuple(top_indices)}"
        )
    bot_set = set(bottom_idx.tolist())
    top_set = set(top_idx.tolist())
    if bot_set & top_set or bot_set | top_set != set(range(8)):
        raise ValueError(
            f"bottom_indices and top_indices must form a disjoint "
            f"partition of {{0, ..., 7}}; got bottom={tuple(bottom_indices)}, "
            f"top={tuple(top_indices)}"
        )

    mat = np.asarray(world2img, dtype=np.float64)

    # 1. Slice bottom / top 2D corners (same positional pairing).
    bottom_2d = pts[..., bottom_idx, :]                  # (..., 4, 2)
    top_2d = pts[..., top_idx, :]                        # (..., 4, 2)

    # 2. Unproject the 4 bottom corners to the ground plane.
    bottom_world, bottom_valid = unproject_points_2d_to_ground(
        bottom_2d, world2img, ground_z=ground_z, min_depth=min_depth,
    )                                                    # (..., 4, 3), (..., 4)
    valid_bottom = bottom_valid.all(axis=-1)             # (...,)

    # 3. Build the per-corner LSQ coefficients (alpha, beta) for height H.
    #    Forward-projecting [X_i, Y_i, ground_z + H, 1]^T with world2img
    #    gives [u_i*z, v_i*z, z]; eliminating z and isolating H yields
    #    one linear equation per (corner, image-axis).
    q = mat[:3, 2]                                       # (3,) — Z column
    bottom_homo = np.concatenate(
        [bottom_world,
         np.ones(bottom_world.shape[:-1] + (1,), dtype=np.float64)],
        axis=-1,
    )                                                    # (..., 4, 4)
    a = bottom_homo @ mat[:3].T                          # (..., 4, 3)

    u = top_2d[..., 0]                                   # (..., 4)
    v = top_2d[..., 1]                                   # (..., 4)
    alpha_u = q[0] - u * q[2]
    beta_u = u * a[..., 2] - a[..., 0]
    alpha_v = q[1] - v * q[2]
    beta_v = v * a[..., 2] - a[..., 1]
    alpha = np.concatenate([alpha_u, alpha_v], axis=-1)  # (..., 8)
    beta = np.concatenate([beta_u, beta_v], axis=-1)     # (..., 8)

    # 1-D LSQ: H = (alpha . beta) / (alpha . alpha).
    numerator = (alpha * beta).sum(axis=-1)              # (...,)
    denominator = (alpha * alpha).sum(axis=-1)           # (...,)
    well_posed = denominator > _HEIGHT_LSQ_FLOOR
    safe_denom = np.where(well_posed, denominator, _HEIGHT_LSQ_FLOOR)
    height = numerator / safe_denom                      # (...,)

    # 4. Top corners share (X, Y) with their bottom counterparts; lift
    # them to Z = ground_z + H.
    top_world = bottom_world.copy()                      # (..., 4, 3)
    top_world[..., 2] = ground_z + height[..., None]

    # 5. Foot = mean of the 4 bottom 3D corners.
    foot_world = bottom_world.mean(axis=-2)              # (..., 3)

    # 6. Re-assemble the 8 vertices in the input's original index order.
    vertices_3d = np.empty(pts.shape[:-1] + (3,), dtype=np.float64)
    vertices_3d[..., bottom_idx, :] = bottom_world
    vertices_3d[..., top_idx, :] = top_world

    # ``height > 0`` rejects physically degenerate fits where the
    # top corners unproject at or below the bottom face — possible
    # when the LSQ is formally well-posed but numerically driven
    # negative (e.g. near-horizon top corners), or when the caller
    # mislabels the top/bottom partition.
    valid_mask = valid_bottom & well_posed & (height > 0)
    return vertices_3d, foot_world, valid_mask


def project_boxes_3d_to_2d(
    boxes3d, calib_info, origin=(0.5, 0.5, 0.5), image_size=None,
):
    """Project 3D bounding boxes into 2D image coordinates.

    Computes the 8 corner vertices of each box (pure numpy, no mmdet3d),
    transforms them into camera space via ``projection matrix w2c``, and
    projects to pixel coordinates via ``intrinsic matrix``.  A box is kept
    if **all** corners are in front of the camera *and* **any** projected
    corner lies inside the image.

    :param boxes3d: Array of 3D bounding boxes in the canonical 9-DoF
        NVSchema layout ``[x, y, z, w, l, h, pitch, roll, yaw, ...]``
        (shape ``(N, 9+)``).  Full ZYX-intrinsic rotation is applied —
        see
        :func:`spatialai_data_utils.core.boxes.box_3d.box3d_to_corners`
        for the convention.  Legacy 7-DoF ``[x, y, z, w, l, h, yaw]``
        arrays are no longer accepted; pad with ``pitch=roll=0``
        before calling.
    :type boxes3d: numpy.ndarray
    :param calib_info: Single-camera calibration dict with
        ``"intrinsic_matrix"`` (3x3) and ``"w2c_matrix"`` (4x4)
        entries — i.e. one row of the flat ``calib_dict`` produced
        by the loaders, not the multi-camera dict itself.  Legacy
        ``"intrinsic matrix"`` / ``"projection matrix w2c"`` keys
        are also accepted via the
        :func:`spatialai_data_utils.core.cameras.utils.get_calib_field`
        fallback.
    :type calib_info: dict
    :param origin: Reference point of the boxes within ``(w, l, h)``.
        Defaults to ``(0.5, 0.5, 0.5)`` (geometric centre).  Pass
        ``(0.5, 0.5, 0.0)`` for the legacy ``LiDARInstance3DBoxes``
        convention (centre of the bottom face).
    :type origin: tuple(float, float, float)
    :param image_size: ``(width, height)`` used for the image-bounds
        visibility check. ``None`` uses the package default
        :data:`spatialai_data_utils.constants.IMAGE_SIZE`.
    :type image_size: tuple(int, int) or None
    :return: Tuple ``(bboxes_2d_vertices, bbox_ids_visible)`` where

        - ``bboxes_2d_vertices`` has shape ``(M, 8, 2)`` with the 2D
          projected corners of the ``M`` visible boxes.  When no boxes
          are visible the array has shape ``(0, 8, 2)`` so the trailing
          axes are always consistent.
        - ``bbox_ids_visible`` is the list of original indices (into
          *boxes3d*) for the visible boxes, in ascending order.
    :rtype: tuple(numpy.ndarray, list[int])
    """
    boxes3d = np.asarray(boxes3d, dtype=np.float64)
    if boxes3d.ndim == 1:
        boxes3d = boxes3d[None]
    if len(boxes3d) == 0:
        return _EMPTY_CORNERS_2D, []

    intrinsic = np.asarray(
        get_calib_field(calib_info, KEY_INTRINSIC_MATRIX), dtype=np.float64,
    )
    w2c = np.asarray(
        get_calib_field(calib_info, KEY_W2C_MATRIX), dtype=np.float64,
    )
    world2img = intrinsic @ w2c[:3]                                   # (3, 4)

    # World corners -> pixel coords + per-corner front-of-camera mask.
    corners_w = box3d_to_corners(boxes3d, origin=origin)              # (N, 8, 3)
    corners_pix, corners_front = project_points_3d_to_image(
        corners_w, world2img,
    )  # (N, 8, 2), (N, 8)

    # Visibility masks: keep a box iff all 8 corners are in front of the
    # camera AND at least one projected corner lies inside the image.
    front_mask = corners_front.all(axis=-1)                            # (N,)
    inside_mask = _any_corner_inside_image(corners_pix, image_size)    # (N,)
    visible_mask = front_mask & inside_mask
    visible_ids = np.where(visible_mask)[0].tolist()

    if not visible_ids:
        return _EMPTY_CORNERS_2D, visible_ids
    return corners_pix[visible_mask], visible_ids


# ---------------------------------------------------------------------------
# NVSchema-aware wrapper (stage 1 of the visualization pipeline)
# ---------------------------------------------------------------------------

def project_bev_objects_bbox_in_image(
    sensor_id: str,
    calib_dict: Dict[str, Dict],
    bev_objects: List[Dict],
    origin: Tuple[float, float, float] = (0.5, 0.5, 0.5),
    image_size: Optional[Tuple[int, int]] = None,
) -> List[Dict]:
    """Project a list of BEV 3D objects to a given camera's image plane.

    Stage 1 of the split visualization pipeline — consumes a list of raw
    NVSchema object dicts (as produced by
    :func:`spatialai_data_utils.loaders.nvschema.load_nvschema`) and
    returns a filtered copy containing only boxes visible on *sensor_id*.
    Each returned dict preserves its native NVSchema fields and is
    enriched in-place on the existing ``"bbox3d"`` block by adding
    (or merging into) the proto-native ``bbox3d.info``
    ``map<string, string>``::

        "bbox3d": {
            "coordinates": [x, y, z, w, l, h, pitch, roll, yaw],
            "embedding":   [...],
            "confidence":  float,
            "info": {           # map<string, string> per NVSchema proto
                "sensorId": "<sensor_id>",
                "vertices": "[[x0, y0], ..., [x7, y7]]",  # json.dumps'd
                # ...any pre-existing info keys are preserved
            },
        }

    Every NVSchema input row already scopes its objects to one
    observing camera, and this function projects onto one target
    camera, so each enriched object carries exactly one projection —
    hence a single ``info`` dict per object (not a list).  If a caller
    needs projections onto several cameras, they should keep the raw
    input and call this function once per target.

    ``info`` values are strings (per the NVSchema proto
    ``map<string, string>``).  ``sensorId`` mirrors the top-level
    NVSchema frame's camelCase key and is a plain string; ``vertices``
    is a ``json.dumps``-serialised 8 × 2 float array (the 2D projection
    of the 3D cuboid's corners) — consumers must ``json.loads`` it
    before numeric use.  Any keys already present in ``bbox3d.info``
    on the input are preserved; only ``sensorId`` and ``vertices`` are
    written / overwritten.

    Boxes outside the camera frustum or fully off-screen are dropped
    (same visibility rule as :func:`project_boxes_3d_to_2d`).

    Full ``R_z(yaw) · R_y(roll) · R_x(pitch)`` rotation is applied to
    the 8 corners — ZYX-intrinsic convention, matching
    :func:`spatialai_data_utils.core.geometry.rotation.euler_from_quaternion`.

    Expected input per-object schema (raw NVSchema)::

        {
            "id":         str,
            "type":       str,
            "confidence": float,
            "coordinate": {"x", "y", "z"},
            "bbox3d": {
                "coordinates": [x, y, z, w, l, h, pitch, roll, yaw],
                "embedding":   [...],
                "confidence":  float,
            },
        }

    :param sensor_id: Camera name to project onto.  Must be a key in
        *calib_dict*.
    :type sensor_id: str
    :param calib_dict: Calibration dict keyed by sensor name.  Each
        entry must contain ``"intrinsic_matrix"`` (3x3) and
        ``"w2c_matrix"`` (4x4) — or their legacy equivalents
        ``"intrinsic matrix"`` / ``"projection matrix w2c"``.
    :type calib_dict: dict[str, dict]
    :param bev_objects: List of raw NVSchema object dicts.  Can be empty.
    :type bev_objects: list[dict]
    :param origin: Reference point of the boxes within ``(w, l, h)``.
        Defaults to ``(0.5, 0.5, 0.5)`` (geometric centre).
    :type origin: tuple(float, float, float)
    :param image_size: ``(width, height)`` used for the image-bounds
        visibility check.  ``None`` uses the package default
        :data:`spatialai_data_utils.constants.IMAGE_SIZE`.
    :type image_size: tuple(int, int) or None
    :return: Filtered list of enriched NVSchema object dicts (visible
        boxes only, preserving original input order).
    :rtype: list[dict]
    :raises KeyError: If *sensor_id* is not present in *calib_dict*, or
        if any object dict is missing ``"bbox3d"`` /
        ``"bbox3d.coordinates"``.
    """
    if sensor_id not in calib_dict:
        raise KeyError(
            f"Sensor '{sensor_id}' not found in calib_dict. "
            f"Available: {sorted(calib_dict.keys())}"
        )

    if not bev_objects:
        return []

    sensor_calib = calib_dict[sensor_id]

    boxes_3d = []
    for idx, det in enumerate(bev_objects):
        if KEY_BBOX3D not in det or KEY_COORDINATES not in det[KEY_BBOX3D]:
            raise KeyError(
                f"Detection at index {idx} is missing required "
                f"'{KEY_BBOX3D}.{KEY_COORDINATES}' field (raw NVSchema)."
            )
        c = det[KEY_BBOX3D][KEY_COORDINATES]
        check_nvschema_coords_len(c)
        # Pass all 9 NVSchema values straight through: box3d_to_corners
        # applies the full R_z(yaw)·R_y(roll)·R_x(pitch) rotation
        # (ZYX-intrinsic) when it sees shape (*, >=9).
        boxes_3d.append(list(c))
    boxes_3d = np.asarray(boxes_3d, dtype=np.float64)

    vertices_2d, visible_ids = project_boxes_3d_to_2d(
        boxes_3d,
        sensor_calib,
        origin=origin,
        image_size=image_size,
    )

    enriched_objects: List[Dict] = []
    for local_idx, orig_idx in enumerate(visible_ids):
        # Shallow-copy both the object and its ``bbox3d`` sub-dict so we
        # never mutate the caller's input, then merge the projection
        # metadata into the ``bbox3d.info`` map.  Any pre-existing
        # ``info`` entries are preserved.
        enriched = dict(bev_objects[orig_idx])
        bbox3d_copy = dict(enriched[KEY_BBOX3D])
        info = dict(bbox3d_copy.get(KEY_INFO, {}))
        info[KEY_SENSOR_ID] = sensor_id
        info[KEY_VERTICES] = json.dumps(vertices_2d[local_idx].tolist())
        bbox3d_copy[KEY_INFO] = info
        enriched[KEY_BBOX3D] = bbox3d_copy
        enriched_objects.append(enriched)
    return enriched_objects


def _select_bbox3d_projection(
    det: Dict,
    det_idx: int,
    sensor_id: Optional[str],
) -> Optional[Dict]:
    """Return the 2D projection payload from ``bbox3d.info``, sensor-filtered.

    Counterpart to :func:`project_bev_objects_bbox_in_image`: the producer
    *writes* the camera-projected corners of each visible cuboid into
    the detection's native ``bbox3d.info`` map (``map<string, string>``
    per the NVSchema ``Bbox3d`` proto)::

        det["bbox3d"]["info"] = {
            "sensorId": str,
            "vertices": "<json.dumps'd [[x,y],...]>",
            # ...other caller-provided entries are preserved
        }

    This helper *parses it back out*: validates the shape, decodes
    the JSON-encoded ``vertices`` out of ``info``, and returns a
    normalised view::

        {"sensorId": str, "vertices": [[x0, y0], ..., [x7, y7]]}

    so downstream drawing code can index ``entry[KEY_VERTICES]`` as a
    plain list.  When a *sensor_id* filter is supplied and the
    projection's ``info.sensorId`` doesn't match, the helper returns
    ``None`` (caller should skip the detection).

    Co-located with its producer above so the ``bbox3d.info`` data
    contract (the JSON-encoded vertices wire format, and the sensor
    filter semantics) lives in one module.

    :raises KeyError: If ``bbox3d`` or ``bbox3d.info`` is missing / not
        a dict, or if ``info`` is missing ``"vertices"``.
    :raises ValueError: If ``info.vertices`` is not valid JSON.
    """
    bbox3d = det.get(KEY_BBOX3D)
    if not isinstance(bbox3d, dict):
        raise KeyError(
            f"Detection at index {det_idx} is missing required "
            f"'{KEY_BBOX3D}' dict. Call project_bev_objects_bbox_in_image "
            f"first to enrich the BEV objects."
        )

    info = bbox3d.get(KEY_INFO)
    if not isinstance(info, dict):
        raise KeyError(
            f"Detection at index {det_idx} has malformed "
            f"'{KEY_BBOX3D}.{KEY_INFO}': expected a dict, got "
            f"{type(info).__name__}.  Call project_bev_objects_bbox_in_image "
            f"first to populate the projection metadata."
        )

    entry_sensor = info.get(KEY_SENSOR_ID)
    if sensor_id is not None and entry_sensor != sensor_id:
        return None

    if KEY_VERTICES not in info:
        raise KeyError(
            f"Detection at index {det_idx} is missing required "
            f"'{KEY_BBOX3D}.{KEY_INFO}.{KEY_VERTICES}' field."
        )

    raw_verts = info[KEY_VERTICES]
    # ``vertices`` is a json.dumps'd string per the NVSchema
    # ``Bbox3d.info`` ``map<string, string>`` convention.  Tolerate an
    # already-decoded list (for tests / hand-crafted fixtures).
    if isinstance(raw_verts, str):
        try:
            vertices = json.loads(raw_verts)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Detection at index {det_idx} has invalid JSON in "
                f"'{KEY_BBOX3D}.{KEY_INFO}.{KEY_VERTICES}': {exc}"
            ) from exc
    else:
        vertices = raw_verts

    return {KEY_SENSOR_ID: entry_sensor, KEY_VERTICES: vertices}
