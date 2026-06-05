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
3D Bounding Box Visualization Module

This module provides functions for drawing 3D bounding boxes on 2D camera images
and bird's eye view (BEV) representations. It supports projecting oriented 3D boxes
from world coordinates onto image planes using camera calibration matrices, rendering
wireframe cuboids with optional heading-face shading and text labels.

Key Features:
- Convert 3D bounding boxes (9-DoF canonical layout) to 8 corner
  vertices
- Project and draw 3D wireframe cuboids on camera images
- Render heading direction with semi-transparent face shading
- Draw 3D bounding boxes on bird's eye view (BEV) canvas
- Composite multi-camera views with BEV into a single visualization
- Project and draw 3D points on camera images

Main Functions:
- box3d_to_corners: Convert ``[x, y, z, w, l, h, pitch, roll, yaw]``
  boxes to an 8-corner representation
- draw_box3d_corners_on_img: Draw wireframe cuboids from pre-projected
  2D pixel corners (lower-level helper, distinct from
  ``draw_bbox3d_on_img`` which takes world-space boxes and projects
  them internally)
- draw_bbox3d_on_img: Project 3D boxes onto an image and draw them
- draw_bbox3d_on_bev: Draw 3D boxes on a bird's eye view canvas
- draw_bbox3d_multicam: Composite multi-camera + BEV visualization
- draw_points3d_on_img: Project and draw 3D points on an image

3D Box Format (canonical 9-DoF, matches NVSchema ``Bbox3d.coordinates``):
- [x, y, z, w, l, h, pitch, roll, yaw]
- x, y, z: center position in world coordinates
- w, l, h: width (X), length (Y), height (Z)
- pitch, roll, yaw: Euler angles in radians; rotation applied as
  ``R = R_z(yaw) · R_y(roll) · R_x(pitch)`` (ZYX-intrinsic).
  Under this codebase's heading-along-``-Y`` body-frame convention,
  ``pitch`` rotates about world X (the lateral axis), ``roll``
  rotates about world Y (the longitudinal / heading axis), and
  ``yaw`` rotates about world Z.

Corner Ordering (indices map to the ``(N, 8, 3)`` output of ``box3d_to_corners``):
- Bottom face (z = center - h/2): indices 0, 3, 4, 7
- Top face    (z = center + h/2): indices 1, 2, 5, 6

Projection Matrix:
- ``world2img``: 4x4 matrix mapping world homogeneous coordinates to image
  homogeneous coordinates (intrinsic @ extrinsic, i.e. world-to-pixel).
- Alternatively, use ``calib_info`` with keys ``"intrinsic_matrix"`` (3x3) and
  ``"w2c_matrix"`` (4x4) to build the transform automatically (legacy keys
  ``"intrinsic matrix"`` / ``"projection matrix w2c"`` are also accepted).

Typical Usage:
1. Load 3D bounding boxes (e.g. from model output or ground truth)
2. Load camera calibration (intrinsic + extrinsic)
3. Call ``draw_bbox3d_on_img`` to render boxes on a camera image
4. Or call ``draw_bbox3d_on_bev`` for a top-down view
5. Or call ``draw_bbox3d_multicam`` for a combined multi-camera + BEV layout
"""

from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

from spatialai_data_utils.core.boxes import box_3d as _core_box3d_mod
from spatialai_data_utils.core.boxes.box_3d import (
    BOX3D_BOTTOM_FACE,
    BOX3D_EDGES,
    BOX3D_HEADING_FACE,
    box3d_to_corners,
)
from spatialai_data_utils.core.geometry.projection import project_points_3d_to_image

__all__ = [
    "box3d_to_corners",
    "draw_box3d_corners_on_img",
    "draw_bbox3d_on_img",
    "draw_bbox3d_on_bev",
    "draw_bbox3d_multicam",
    "draw_points3d_on_img",
]

# 9-DoF box-vector layout — module-level aliases pointing to the
# canonical definitions in :mod:`spatialai_data_utils.core.boxes.box_3d`.
_X = _core_box3d_mod.X
_Y = _core_box3d_mod.Y
_Z = _core_box3d_mod.Z
_W = _core_box3d_mod.W
_L = _core_box3d_mod.L
_H = _core_box3d_mod.H
_PITCH = _core_box3d_mod.PITCH
_ROLL = _core_box3d_mod.ROLL
_YAW = _core_box3d_mod.YAW

# Corner topology — module-level aliases pointing to the canonical
# definitions in :mod:`spatialai_data_utils.core.boxes.box_3d` so any
# existing code importing these names keeps working.
_EDGE_INDICES = BOX3D_EDGES
_HEADING_FACE_CORNERS = BOX3D_HEADING_FACE
_BEV_BOTTOM_CORNERS = BOX3D_BOTTOM_FACE

_DEFAULT_COLOR = (0, 255, 0)
_DEFAULT_THICKNESS = 1
_DEFAULT_FONT_SCALE = 0.8
_HEADING_ALPHA = 0.5


def _to_numpy(arr):
    """Convert torch.Tensor to numpy array if needed."""
    if hasattr(arr, "detach"):
        return arr.detach().cpu().numpy()
    return np.asarray(arr)


# Single-camera world-to-image matrix assembly is owned by
# :mod:`spatialai_data_utils.visualization.draw_utils` so the math
# lives in exactly one place; this alias keeps the historical
# private name in scope for the call sites below without introducing
# an import-ordering surprise at the top of the module.
from spatialai_data_utils.visualization.draw_utils import (
    build_world2img_from_calib_info as _build_world2img,
)


def draw_box3d_corners_on_img(
    img: np.ndarray,
    num_boxes: int,
    box_corners: np.ndarray,
    box_texts: Optional[List[str]] = None,
    color: Union[Tuple[int, int, int], List[Tuple[int, int, int]]] = _DEFAULT_COLOR,
    thickness: int = _DEFAULT_THICKNESS,
    fontscale: float = _DEFAULT_FONT_SCALE,
    shade_heading: bool = True,
) -> np.ndarray:
    """Draw 3D-box wireframes on an image from pre-projected 2D corners.

    Lower-level helper distinct from :func:`draw_bbox3d_on_img`: this one
    consumes **already-projected pixel corners** ``(N, 8, 2)`` rather
    than world-space boxes and a calibration, so the caller owns the
    3D→2D projection step (typically via
    :func:`spatialai_data_utils.core.geometry.projection.project_boxes_3d_to_2d`
    or :func:`project_bev_objects_bbox_in_image`).  Renders 12 edges of each
    cuboid and optionally shades the heading face (corners 1-5-4-0)
    with a semi-transparent overlay.

    :param img: Input image ``(H, W, 3)`` in BGR.
    :type img: numpy.ndarray
    :param num_boxes: Number of 3D boxes (cuboids) to draw.
    :type num_boxes: int
    :param box_corners: Projected 2D pixel corners ``(N, 8, 2)``.  Each
        cuboid contributes its 8 image-space vertices in the canonical
        order documented at the top of this module.
    :type box_corners: numpy.ndarray
    :param box_texts: Optional text labels, one per box.  Drawn at the
        third corner of each cuboid in white.
    :type box_texts: list[str] or None
    :param color: Single BGR colour or per-box list of BGR colours.
    :type color: tuple or list[tuple]
    :param thickness: Line thickness in pixels.
    :type thickness: int
    :param fontscale: Font scale for text labels.
    :type fontscale: float
    :param shade_heading: If True, shade the heading face semi-transparently.
    :type shade_heading: bool
    :return: Image with the cuboid wireframes drawn.
    :rtype: numpy.ndarray
    """
    h, w = img.shape[:2]

    if shade_heading:
        for i in range(num_boxes):
            corners = np.clip(box_corners[i], -1e4, 1e5).astype(np.int32)
            heading_face = np.array(
                [corners[j] for j in BOX3D_HEADING_FACE], dtype=np.int32
            )
            valid = all(
                -1e4 < c[0] < 1e4 and -1e4 < c[1] < 1e4 for c in heading_face
            )
            if valid:
                box_color = color if isinstance(color[0], int) else color[i]
                overlay = img.copy()
                cv2.fillPoly(overlay, [heading_face], box_color)
                img = cv2.addWeighted(
                    overlay, _HEADING_ALPHA, img, 1 - _HEADING_ALPHA, 0
                )

    for i in range(num_boxes):
        corners = np.clip(box_corners[i], -1e4, 1e5).astype(np.int32)
        box_color = color if isinstance(color[0], int) else color[i]

        for start, end in BOX3D_EDGES:
            start_outside = (
                corners[start, 1] >= h or corners[start, 1] < 0
                or corners[start, 0] >= w or corners[start, 0] < 0
            )
            end_outside = (
                corners[end, 1] >= h or corners[end, 1] < 0
                or corners[end, 0] >= w or corners[end, 0] < 0
            )
            if start_outside and end_outside:
                continue

            cv2.line(
                img,
                (corners[start, 0], corners[start, 1]),
                (corners[end, 0], corners[end, 1]),
                box_color,
                thickness,
                cv2.LINE_AA,
            )

        if box_texts is not None:
            cv2.putText(
                img,
                box_texts[i],
                tuple(corners[3]),
                cv2.FONT_HERSHEY_SIMPLEX,
                fontscale,
                (255, 255, 255),
                thickness,
                cv2.LINE_AA,
            )

    return img.astype(np.uint8)


def draw_bbox3d_on_img(
    bboxes3d: np.ndarray,
    raw_img: np.ndarray,
    world2img: Optional[np.ndarray] = None,
    calib_info: Optional[Dict[str, np.ndarray]] = None,
    bboxes3d_text: Optional[List[str]] = None,
    color: Union[Tuple[int, int, int], List[Tuple[int, int, int]]] = _DEFAULT_COLOR,
    thickness: int = _DEFAULT_THICKNESS,
    fontscale: float = _DEFAULT_FONT_SCALE,
    shade_heading: bool = True,
) -> np.ndarray:
    """Project 3D bounding boxes onto a camera image and draw wireframe cuboids.

    Accepts either a single 4x4 ``world2img`` matrix (world-to-pixel) or a
    ``calib_info`` with ``"intrinsic_matrix"`` and ``"w2c_matrix"`` keys
    (legacy ``"intrinsic matrix"`` / ``"projection matrix w2c"`` keys also
    accepted).  Exactly one of the two must be provided.

    :param bboxes3d: 3D bounding boxes ``(N, 9+)`` in the canonical
        ``[x, y, z, w, l, h, pitch, roll, yaw, ...]`` layout (NVSchema
        ``Bbox3d.coordinates`` order).  Legacy 7-DoF arrays are not
        accepted — see
        :func:`spatialai_data_utils.core.boxes.box_3d.box3d_to_corners`.
    :type bboxes3d: numpy.ndarray
    :param raw_img: Input camera image ``(H, W, 3)`` in BGR.
    :type raw_img: numpy.ndarray
    :param world2img: 4x4 world-to-image projection matrix.
    :type world2img: numpy.ndarray or None
    :param calib_info: Calibration dictionary with ``"intrinsic_matrix"`` (3x3)
        and ``"w2c_matrix"`` (4x4) — or their legacy equivalents
        ``"intrinsic matrix"`` / ``"projection matrix w2c"``.
    :type calib_info: dict or None
    :param bboxes3d_text: Optional text labels for each box.
    :type bboxes3d_text: list[str] or None
    :param color: Single BGR colour or per-box list of BGR colours.
    :type color: tuple or list[tuple]
    :param thickness: Line thickness in pixels.
    :type thickness: int
    :param fontscale: Font scale for text labels.
    :type fontscale: float
    :param shade_heading: If True, shade the heading face semi-transparently.
    :type shade_heading: bool
    :return: Image with 3D bounding boxes drawn.
    :rtype: numpy.ndarray
    """
    if world2img is None and calib_info is None:
        raise ValueError(
            "Exactly one of 'world2img' or 'calib_info' must be provided."
        )
    if world2img is not None and calib_info is not None:
        raise ValueError(
            "Provide only one of 'world2img' or 'calib_info', not both."
        )

    bboxes3d = _to_numpy(bboxes3d)
    if bboxes3d.ndim == 1:
        bboxes3d = bboxes3d[None]

    img = raw_img.copy()
    corners_3d = box3d_to_corners(bboxes3d)  # (N, 8, 3)
    num_bbox = corners_3d.shape[0]
    if num_bbox == 0:
        return img

    if world2img is None:
        world2img = _build_world2img(calib_info)

    imgfov_pts_2d, front_mask = project_points_3d_to_image(
        corners_3d, world2img,
    )  # (N, 8, 2), (N, 8)

    # Drop entire boxes that have any corner behind the camera — their
    # projected pixels are not geometrically meaningful and would
    # otherwise render as wireframe diagonals flying off-screen.
    keep = front_mask.all(axis=-1)  # (N,)
    if not keep.any():
        return img
    imgfov_pts_2d = imgfov_pts_2d[keep]
    num_bbox = int(keep.sum())

    # Filter the parallel per-box inputs so draw_box3d_corners_on_img
    # sees consistent lengths.
    if bboxes3d_text is not None:
        bboxes3d_text = [t for t, k in zip(bboxes3d_text, keep) if k]
    if isinstance(color, list):
        color = [c for c, k in zip(color, keep) if k]

    return draw_box3d_corners_on_img(
        img, num_bbox, imgfov_pts_2d, bboxes3d_text,
        color, thickness, fontscale, shade_heading,
    )


def draw_points3d_on_img(
    points: np.ndarray,
    img: np.ndarray,
    world2img: Optional[np.ndarray] = None,
    calib_info: Optional[Dict[str, np.ndarray]] = None,
    color: Union[Tuple[int, int, int], List[Tuple[int, int, int]]] = _DEFAULT_COLOR,
    radius: int = 4,
) -> np.ndarray:
    """Project 3D points onto a camera image and draw them as circles.

    Accepts either a 4x4 ``world2img`` matrix or a ``calib_info``.

    :param points: 3D point coordinates ``(N, M, 3)`` where *N* is the number
        of objects and *M* the number of points per object.
    :type points: numpy.ndarray
    :param img: Input camera image ``(H, W, 3)`` in BGR.
    :type img: numpy.ndarray
    :param world2img: 4x4 world-to-image projection matrix.
    :type world2img: numpy.ndarray or None
    :param calib_info: Calibration dictionary (see :func:`draw_bbox3d_on_img`).
    :type calib_info: dict or None
    :param color: Single BGR colour or per-object list of BGR colours.
    :type color: tuple or list[tuple]
    :param radius: Circle radius in pixels.
    :type radius: int
    :return: Image with points drawn.
    :rtype: numpy.ndarray
    """
    if world2img is None and calib_info is None:
        raise ValueError(
            "Exactly one of 'world2img' or 'calib_info' must be provided."
        )
    if world2img is not None and calib_info is not None:
        raise ValueError(
            "Provide only one of 'world2img' or 'calib_info', not both."
        )

    points = _to_numpy(points)
    img = img.copy()

    if world2img is None:
        world2img = _build_world2img(calib_info)

    pts_2d, front_mask = project_points_3d_to_image(
        points, world2img,
    )  # (N, M, 2), (N, M)
    # front_mask is False for points behind the camera; their pixel
    # values are garbage and must not be drawn.  Safe to cast the
    # still-finite in-front pixels to int32 for cv2.circle.
    pts_2d_int = np.where(
        front_mask[..., None], pts_2d, 0,
    ).astype(np.int32)

    N = pts_2d_int.shape[0]
    for i in range(N):
        pt_color = color if isinstance(color[0], int) else color[i]
        for j, pt in enumerate(pts_2d_int[i]):
            if not front_mask[i, j]:
                continue
            cv2.circle(img, pt.tolist(), radius, pt_color, thickness=-1)

    return img.astype(np.uint8)


def draw_bbox3d_on_bev(
    bboxes_3d: np.ndarray,
    bev_size: Union[int, Tuple[int, int]],
    bev_range: float = 115,
    color: Union[Tuple[int, int, int], List[Tuple[int, int, int]]] = (255, 0, 0),
    thickness: int = 3,
) -> np.ndarray:
    """Draw 3D bounding boxes on a bird's eye view (BEV) canvas.

    Creates a blank canvas with range-circle markings and a center cross,
    then renders the bottom face of each box as a quadrilateral.

    The world X-axis maps to image columns (right = positive X) and the
    world Y-axis maps to image rows (up = positive Y, flipped on canvas).

    :param bboxes_3d: 3D bounding boxes ``(N, 9+)`` in the canonical
        ``[x, y, z, w, l, h, pitch, roll, yaw, ...]`` layout.
    :type bboxes_3d: numpy.ndarray
    :param bev_size: Canvas size as a single int (square) or ``(height, width)``.
    :type bev_size: int or tuple[int, int]
    :param bev_range: Total range in metres covered by the canvas
        (centred on the origin).
    :type bev_range: float
    :param color: Single BGR colour or per-box list of BGR colours.
    :type color: tuple or list[tuple]
    :param thickness: Line thickness in pixels.
    :type thickness: int
    :return: BEV image ``(H, W, 3)`` with bounding boxes drawn.
    :rtype: numpy.ndarray
    """
    bboxes_3d = _to_numpy(bboxes_3d)

    if isinstance(bev_size, (list, tuple)):
        bev_h, bev_w = bev_size
    else:
        bev_h, bev_w = bev_size, bev_size

    bev = np.zeros([bev_h, bev_w, 3], dtype=np.uint8)
    marking_color = (127, 127, 127)
    bev_res_x = bev_range / bev_w
    bev_res_y = bev_range / bev_h

    for cir in range(int(bev_range / 2 / 10)):
        cv2.ellipse(
            bev,
            (int(bev_w / 2), int(bev_h / 2)),
            (int((cir + 1) * 10 / bev_res_x), int((cir + 1) * 10 / bev_res_y)),
            0, 0, 360,
            marking_color,
            thickness=thickness,
        )

    cv2.line(bev, (0, int(bev_h / 2)), (bev_w, int(bev_h / 2)), marking_color)
    cv2.line(bev, (int(bev_w / 2), 0), (int(bev_w / 2), bev_h), marking_color)

    if len(bboxes_3d) != 0:
        bev_corners = box3d_to_corners(bboxes_3d)[:, BOX3D_BOTTOM_FACE][..., [0, 1]]
        xs = bev_corners[..., 0] / bev_res_x + bev_w / 2
        ys = -bev_corners[..., 1] / bev_res_y + bev_h / 2

        for obj_idx, (x, y) in enumerate(zip(xs, ys)):
            box_color = (
                color[obj_idx] if isinstance(color[0], (list, tuple)) else color
            )
            for p1, p2 in ((0, 1), (0, 2), (1, 3), (2, 3)):
                cv2.line(
                    bev,
                    (int(x[p1]), int(y[p1])),
                    (int(x[p2]), int(y[p2])),
                    box_color,
                    thickness=thickness,
                )

    return bev


def draw_bbox3d_multicam(
    bboxes_3d: np.ndarray,
    imgs: List[np.ndarray],
    world2imgs: Optional[List[np.ndarray]] = None,
    calib_info_list: Optional[List[Dict[str, np.ndarray]]] = None,
    color: Union[Tuple[int, int, int], List[Tuple[int, int, int]]] = (255, 0, 0),
    shade_heading: bool = True,
) -> np.ndarray:
    """Draw 3D boxes on multiple camera images and combine with a BEV panel.

    Produces a composite image with the BEV on the left and camera views
    arranged in a grid on the right.  Provide either ``world2imgs`` (one 4x4
    matrix per camera) or ``calib_info_list`` (one calibration dict per camera).

    :param bboxes_3d: 3D bounding boxes ``(N, 9+)`` in the canonical
        ``[x, y, z, w, l, h, pitch, roll, yaw, ...]`` layout.
    :type bboxes_3d: numpy.ndarray
    :param imgs: List of camera images ``(H, W, 3)`` in BGR.
    :type imgs: list[numpy.ndarray]
    :param world2imgs: List of 4x4 world-to-image matrices, one per camera.
    :type world2imgs: list[numpy.ndarray] or None
    :param calib_info_list: List of calibration dicts, one per camera.
    :type calib_info_list: list[dict] or None
    :param color: Single BGR colour or per-box list of BGR colours.
    :type color: tuple or list[tuple]
    :param shade_heading: If True, shade the heading face on camera views.
    :type shade_heading: bool
    :return: Combined visualisation image.
    :rtype: numpy.ndarray
    """
    if world2imgs is None and calib_info_list is None:
        raise ValueError(
            "Exactly one of 'world2imgs' or 'calib_info_list' must be provided."
        )
    if world2imgs is not None and calib_info_list is not None:
        raise ValueError(
            "Provide only one of 'world2imgs' or 'calib_info_list', not both."
        )

    bboxes_3d = _to_numpy(bboxes_3d)
    num_cams = len(imgs)

    if world2imgs is not None:
        transforms = [_to_numpy(m) for m in world2imgs]
    else:
        transforms = [_build_world2img(cd) for cd in calib_info_list]

    vis_imgs = []
    for img, transform in zip(imgs, transforms):
        vis_imgs.append(
            draw_bbox3d_on_img(
                bboxes_3d, img, world2img=transform,
                color=color, shade_heading=shade_heading,
            )
        )

    if num_cams < 4 or num_cams % 2 != 0:
        vis_imgs = np.concatenate(vis_imgs, axis=1)
    else:
        vis_imgs = np.concatenate(
            [
                np.concatenate(vis_imgs[: num_cams // 2], axis=1),
                np.concatenate(vis_imgs[num_cams // 2:], axis=1),
            ],
            axis=0,
        )

    bev = draw_bbox3d_on_bev(bboxes_3d, vis_imgs.shape[0], color=color)
    vis_imgs = np.concatenate([bev, vis_imgs], axis=1)

    return vis_imgs
