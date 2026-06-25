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
Shared drawing and I/O utilities for visualization scripts.

Provides helper functions used across visualization workflows: camera-name
tags, per-box text-label generation, image loading (JPEG/PNG and H5), and
saving annotated images to disk.

Main Functions:
- draw_camera_tag: Overlay a camera name badge on an image
- generate_bbox_text: Build per-box text labels from metadata
- load_image: Load an image from a file path or H5 dataset
- save_viz: Save an annotated image to disk under a camera subdirectory
- build_world2img_from_calib: Assemble a 4x4 world-to-image matrix from a
  calibration dictionary
"""

import logging
import os
from typing import Any, List, Optional, Tuple, Union

import cv2
import numpy as np

from spatialai_data_utils.constants import (
    KEY_INTRINSIC_MATRIX,
    KEY_W2C_MATRIX,
)
from spatialai_data_utils.core.cameras.utils import get_calib_field

logger = logging.getLogger(__name__)

__all__ = [
    "draw_camera_tag",
    "generate_bbox_text",
    "load_image",
    "save_viz",
    "build_world2img_from_calib",
]

DEFAULT_FONT = cv2.FONT_HERSHEY_SIMPLEX
DEFAULT_FONT_SCALE = 2
DEFAULT_FONT_THICKNESS = 2
DEFAULT_CAM_TAG_BG_COLOR = (66, 66, 66)
DEFAULT_CAM_TAG_BG_ALPHA = 0.6
DEFAULT_CAM_TAG_TEXT_COLOR = (255, 255, 255)

# Internal layout constants for the camera-name badge.
#
# The pad geometry was originally inherited from the
# ``cv2.rectangle(..., (w + 64, h + 44))`` / ``putText(..., (32, 60))``
# magic numbers, which gave a single-line rectangle of ~94 px tall at
# the default font scale (top_pad=10 + cam_h≈50 + bottom_pad=34).  The
# legacy bottom_pad was visually heavy — about 70% of the text height
# of empty space below the baseline — so we trimmed it to a ratio that
# matches the OpenCV-recommended descender allowance (~30% of cap
# height), shaving ~18 px off the badge while preserving the
# top-half breathing room and the asymmetric "weight at the bottom"
# look that anchors the badge to the image corner.
_CAM_TAG_TOP_PAD = 12
_CAM_TAG_LEFT_PAD = 32
_CAM_TAG_RIGHT_PAD = 32
_CAM_TAG_BOTTOM_PAD = 18
_CAM_TAG_LINE_GAP_RATIO = 0.25
_CAM_TAG_TIMESTAMP_SCALE_RATIO = 0.5


def draw_camera_tag(
    image: np.ndarray,
    cam_name: str,
    timestamp: Optional[str] = None,
    font_scale: float = DEFAULT_FONT_SCALE,
    font_thickness: int = DEFAULT_FONT_THICKNESS,
    bg_color: Tuple[int, int, int] = DEFAULT_CAM_TAG_BG_COLOR,
    bg_alpha: float = DEFAULT_CAM_TAG_BG_ALPHA,
    text_color: Tuple[int, int, int] = DEFAULT_CAM_TAG_TEXT_COLOR,
) -> np.ndarray:
    """Overlay a camera-name badge in the top-left corner of an image.

    When *timestamp* is provided, a subordinate second line is rendered
    underneath the camera name at half the camera-name font scale, and
    the background rectangle widens to fit whichever line is longer.
    With *timestamp* ``None`` (or empty) the single-line layout is used.

    The background rectangle is rendered with alpha blending controlled
    by *bg_alpha* — at the default ``0.6`` the underlying image still
    shows through, so the badge looks like a translucent label rather
    than an opaque banner that hides scene content.  Pass ``bg_alpha=1``
    for the legacy fully-opaque behaviour.  The text is always drawn
    fully opaque on top of the (already-blended) rectangle so labels
    stay crisp and high-contrast.

    :param image: Input image ``(H, W, 3)`` in BGR, modified in-place.
    :type image: numpy.ndarray
    :param cam_name: Camera name string to display on the first line.
    :type cam_name: str
    :param timestamp: Optional timestamp / secondary label drawn on a
        subordinate second line.  Empty / ``None`` skips the second
        line entirely (the badge collapses to a single-line camera
        tag).  Useful for stamping NVSchema row timestamps onto
        per-row visualizations so reviewers can correlate frames
        across cameras.
    :type timestamp: str or None
    :param font_scale: OpenCV font scale for the camera-name line; the
        timestamp line is rendered at half this scale.
    :type font_scale: float
    :param font_thickness: OpenCV font thickness for the camera-name
        line; the timestamp line uses ``max(1, font_thickness - 1)``.
    :type font_thickness: int
    :param bg_color: Background rectangle colour (BGR).
    :type bg_color: tuple[int, int, int]
    :param bg_alpha: Alpha for the background rectangle, in ``[0, 1]``.
        ``1`` = fully opaque (legacy behaviour); the package default
        :data:`DEFAULT_CAM_TAG_BG_ALPHA` is ``0.6``, blending the
        rectangle with the underlying image so scene content stays
        visible behind the badge.  ``0`` skips the rectangle entirely
        and renders only the text on top of the original pixels.
    :type bg_alpha: float
    :param text_color: Text colour (BGR).  Always rendered fully
        opaque, regardless of *bg_alpha*.
    :type text_color: tuple[int, int, int]
    :return: Image with the camera tag drawn (and optionally the
        timestamp line).
    :rtype: numpy.ndarray
    """
    cam_w, cam_h = cv2.getTextSize(
        cam_name, DEFAULT_FONT, font_scale, font_thickness,
    )[0]
    cam_baseline_y = _CAM_TAG_TOP_PAD + cam_h

    if not timestamp:
        rect_w = cam_w + _CAM_TAG_LEFT_PAD + _CAM_TAG_RIGHT_PAD
        rect_h = cam_h + _CAM_TAG_TOP_PAD + _CAM_TAG_BOTTOM_PAD
        ts_scale = None
        ts_thickness = None
        ts_baseline_y = None
    else:
        ts_scale = font_scale * _CAM_TAG_TIMESTAMP_SCALE_RATIO
        ts_thickness = max(1, font_thickness - 1)
        ts_w, ts_h = cv2.getTextSize(
            timestamp, DEFAULT_FONT, ts_scale, ts_thickness,
        )[0]
        line_gap = int(_CAM_TAG_LINE_GAP_RATIO * cam_h)
        ts_baseline_y = cam_baseline_y + line_gap + ts_h
        rect_w = max(cam_w, ts_w) + _CAM_TAG_LEFT_PAD + _CAM_TAG_RIGHT_PAD
        # Rectangle bottom = timestamp baseline + the same bottom-pad the
        # single-line layout uses below ``cam_baseline_y``.  This keeps
        # the gap-below-text distance identical regardless of
        # whether the timestamp line is present.
        rect_h = ts_baseline_y + _CAM_TAG_BOTTOM_PAD

    # Step 1: paint the (possibly translucent) background rectangle.
    # Fast paths avoid an unnecessary array copy when alpha clamps to
    # the trivial endpoints.
    if bg_alpha >= 1.0:
        cv2.rectangle(image, (0, 0), (rect_w, rect_h), bg_color, -1)
    elif bg_alpha > 0.0:
        # Composite ``alpha * bg_color + (1 - alpha) * image`` only
        # within the rectangle's footprint; pixels outside are left
        # untouched because ``overlay`` is a copy of ``image``.
        overlay = image.copy()
        cv2.rectangle(overlay, (0, 0), (rect_w, rect_h), bg_color, -1)
        cv2.addWeighted(overlay, bg_alpha, image, 1.0 - bg_alpha, 0, dst=image)
    # bg_alpha <= 0: skip the rectangle entirely; just draw text on the
    # original image so callers can render a "text-only" badge.

    # Step 2: draw text on top of the (already-blended) rectangle.  Text
    # is always opaque so the label stays high-contrast even when the
    # background is heavily transparent.
    cv2.putText(
        image, cam_name, (_CAM_TAG_LEFT_PAD, cam_baseline_y),
        DEFAULT_FONT, font_scale, text_color,
        font_thickness, cv2.LINE_AA,
    )
    if timestamp:
        cv2.putText(
            image, timestamp, (_CAM_TAG_LEFT_PAD, ts_baseline_y),
            DEFAULT_FONT, ts_scale, text_color,
            ts_thickness, cv2.LINE_AA,
        )
    return image


def generate_bbox_text(
    n_boxes: int,
    labels: np.ndarray,
    scores: np.ndarray,
    track_ids: np.ndarray,
    class_names: List[str],
) -> List[str]:
    """Build a per-box text label list (e.g. ``"Person(12) 0.93"``).

    :param n_boxes: Number of boxes.
    :type n_boxes: int
    :param labels: Integer class-label indices ``(N,)``.
    :type labels: numpy.ndarray
    :param scores: Confidence scores ``(N,)``.
    :type scores: numpy.ndarray
    :param track_ids: Object / track IDs ``(N,)``.
    :type track_ids: numpy.ndarray
    :param class_names: List mapping label index to human-readable name.
    :type class_names: list[str]
    :return: Text strings, one per box.
    :rtype: list[str]
    """
    texts: List[str] = []
    for i in range(n_boxes):
        cls = class_names[labels[i]] if labels[i] < len(class_names) else "unknown"
        texts.append(f"{cls}({track_ids[i]}) {scores[i]:.2f}")
    return texts


def _is_h5_path(frame_path: Any) -> bool:
    """Return True if *frame_path* is an H5 reference ``(h5_path, key)``.

    A tuple / list with at least two string-like elements is treated as an
    H5 reference; anything else is assumed to be a regular file path.
    """
    return isinstance(frame_path, (list, tuple)) and len(frame_path) >= 2


def load_image(
    frame_path: Union[str, Tuple[str, str]],
    h5_file: Optional[bool] = None,
) -> Optional[np.ndarray]:
    """Load a camera image from disk (JPEG/PNG) or from an H5 dataset.

    Auto-detects the format based on *frame_path* type: a tuple is treated
    as ``(h5_path, dataset_key)``, a string is treated as a regular file
    path. The *h5_file* argument is retained for backwards compatibility
    but is usually unnecessary.

    :param frame_path: For regular files, a string path. For H5, a tuple
        ``(h5_path, dataset_key)``.
    :type frame_path: str or tuple[str, str]
    :param h5_file: Explicit format hint. ``None`` (default) auto-detects
        from *frame_path*. Pass ``True`` / ``False`` to force a mode.
    :type h5_file: bool or None
    :return: Loaded image ``(H, W, 3)`` in BGR, or None on failure.
    :rtype: numpy.ndarray or None
    """
    use_h5 = _is_h5_path(frame_path) if h5_file is None else h5_file
    if use_h5:
        import h5py
        with h5py.File(frame_path[0], "r") as f:
            return f[frame_path[1]][:]
    image = cv2.imread(frame_path)
    if image is None:
        logger.warning("Failed to load image %s", frame_path)
    return image


def save_viz(
    image: np.ndarray,
    vis_dir: str,
    cam_name: str,
    frame_path: Any,
    h5_file: Optional[bool] = None,
) -> None:
    """Save an annotated image under ``vis_dir/<cam_name>/<basename>``.

    Auto-detects the path format in the same way as :func:`load_image`.

    :param image: Annotated image to save.
    :type image: numpy.ndarray
    :param vis_dir: Root output directory.
    :type vis_dir: str
    :param cam_name: Camera name (used as subdirectory).
    :type cam_name: str
    :param frame_path: Original frame path (string or H5 tuple) — used to
        derive the output filename.
    :type frame_path: str or tuple
    :param h5_file: Explicit format hint. ``None`` (default) auto-detects
        from *frame_path*.
    :type h5_file: bool or None
    """
    use_h5 = _is_h5_path(frame_path) if h5_file is None else h5_file
    basename = os.path.basename(frame_path[1] if use_h5 else frame_path)
    out_path = os.path.join(vis_dir, cam_name, basename)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv2.imwrite(out_path, image)


def build_world2img_from_calib_info(
    calib_info: dict,
) -> np.ndarray:
    """Assemble a 4x4 world-to-image projection matrix from a **single-camera**
    ``calib_info`` dict.

    Canonical helper — the actual matrix-math implementation.  Combines
    the 3x3 intrinsic matrix with the 4x4 world-to-camera extrinsic by
    embedding ``K`` into the upper-left 3x3 of an identity 4x4, then
    pre-multiplying ``w2c``: ``world2img = [[K, 0; 0, 1]] @ w2c``.

    Both modes of the calibration plumbing reach this helper:

    * Multi-camera callers go through
      :func:`build_world2img_from_calib(calib_dict, cam_name)` which is
      a thin wrapper that indexes ``calib_dict[cam_name]`` and
      delegates here — used by
      :func:`spatialai_data_utils.visualization.render.process_frame_gt_json_aicity`
      where the calibration is held in flat
      ``{cam_name: calib_info}`` form.
    * Single-camera callers (e.g. :func:`draw_bbox3d_on_img` /
      :func:`draw_points3d_on_img` / :func:`draw_bbox3d_multicam` in
      :mod:`spatialai_data_utils.visualization.box_3d`) call this
      function directly.

    :param calib_info: Single-camera calibration dict with
        ``"intrinsic_matrix"`` (3x3) and ``"w2c_matrix"``
        (4x4) entries.  Legacy ``"intrinsic matrix"`` /
        ``"projection matrix w2c"`` keys are also accepted (read
        through :func:`spatialai_data_utils.core.cameras.utils.get_calib_field`'s
        fallback path).  Either nested numpy arrays or python lists
        work; ``w2c`` is reshaped to ``(4, 4)`` defensively in case
        the caller stored it as a flat 16-element list.
    :type calib_info: dict
    :return: 4x4 world-to-image projection matrix.
    :rtype: numpy.ndarray
    """
    intrin = np.eye(4)
    intrin[:3, :3] = np.asarray(get_calib_field(calib_info, KEY_INTRINSIC_MATRIX))
    w2c = np.asarray(get_calib_field(calib_info, KEY_W2C_MATRIX)).reshape(4, 4)
    return intrin @ w2c


def build_world2img_from_calib(
    calib_dict: dict,
    cam_name: str,
) -> np.ndarray:
    """Multi-camera wrapper around :func:`build_world2img_from_calib_info`.

    Indexes ``calib_dict[cam_name]`` to extract the single-camera
    ``calib_info`` dict and delegates the matrix assembly.  Provided
    so the per-frame renderer (which carries a flat
    ``{cam_name: calib_info}`` dict) can keep its call sites readable
    instead of inlining the lookup at every projection.

    :param calib_dict: Calibration dictionary keyed by camera name.
        Each entry must contain ``"intrinsic_matrix"`` (3x3) and
        ``"w2c_matrix"`` (4x4) — or their legacy equivalents
        ``"intrinsic matrix"`` / ``"projection matrix w2c"``.
    :type calib_dict: dict
    :param cam_name: Camera name to look up in *calib_dict*.
    :type cam_name: str
    :return: 4x4 world-to-image projection matrix.
    :rtype: numpy.ndarray
    """
    return build_world2img_from_calib_info(calib_dict[cam_name])
