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
Multi-camera frames → grid video assembly.

Public entry points:

* :func:`frames_to_video_grid` — read multiple per-camera frame
  directories, stack each frame index into a grid layout, and stream
  the result to a single MP4.  Auto-selects grid dimensions, scales
  output to a target height, optional per-tile labels, and returns a
  status string consistent with
  :func:`spatialai_data_utils.visualization.video_utils.frame2video.frames_to_video`.
* :func:`auto_grid_cols` — grid-selection heuristic used by the
  function when ``n_cols=None``.  Exposed so CLI tools can pre-flight
  log the chosen layout before encoding.

For the single-directory case, use
:func:`spatialai_data_utils.visualization.video_utils.frame2video.frames_to_video`.
"""

import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, List, Optional

import cv2
import numpy as np
import tqdm

from spatialai_data_utils.constants import FPS as DEFAULT_FPS
from spatialai_data_utils.visualization.draw_utils import draw_camera_tag
from spatialai_data_utils.visualization.video_utils.frame2video import (
    DEFAULT_CODEC,
    DEFAULT_GLOB_PATTERNS,
    STATUS_COMPLETED,
    STATUS_NO_FRAMES_FOUND,
    STATUS_READ_ERROR,
    STATUS_SKIPPED,
    STATUS_WRITE_ERROR,
    list_frame_paths,
)
from spatialai_data_utils.visualization.video_utils.text_writer import (
    plot_frame_label,
)

logger = logging.getLogger(__name__)


# Default target output video height.  1080p is the widest "everything
# still plays in a normal media player" choice for typical multi-cam
# grids; tile sizes are derived from this and the row count.
DEFAULT_TARGET_HEIGHT = 1080


def auto_grid_cols(n_items: int) -> int:
    """Pick the column count for an N-item grid.

    Uses ``ceil(sqrt(N))`` — produces a near-square layout that lines
    up well with 16:9 source frames.  For example:

    * N=2  → 2 cols x 1 row
    * N=4  → 2 cols x 2 rows
    * N=6  → 3 cols x 2 rows
    * N=9  → 3 cols x 3 rows
    * N=12 → 4 cols x 3 rows
    * N=16 → 4 cols x 4 rows

    :param n_items: Number of cameras / tiles to fit into the grid.
        ``<= 0`` is clamped to 1 column.
    :return: Number of columns.  Pair with
        ``rows = ceil(n_items / cols)`` to get the full grid shape.
    """
    return max(1, math.ceil(math.sqrt(max(1, n_items))))


def _prepare_tile(
    path: str,
    label: str,
    *,
    tile_h: int,
    tile_w: int,
    with_label: bool,
):
    """Read one frame, resize to ``(tile_w, tile_h)``, optionally label.

    Returns a black tile of the target size when the file is missing
    or unreadable (so the grid stays synchronized across cameras with
    differing frame ranges).  Modifies the returned array in place
    when labelling.

    Per-tile labels reuse the toolkit's polished
    :func:`spatialai_data_utils.visualization.draw_utils.draw_camera_tag`
    badge — translucent grey background by default so the scene
    behind the label is still visible — with the font scale picked
    proportionally to the tile height so labels stay legible across
    very different tile sizes (e.g. 360-px tiles in a 12-cam grid vs
    1080-px tiles in a 1-cam preview).

    Module-level (rather than a closure inside
    :func:`frames_to_video_grid`) so the same function backs both the
    sequential and the ``ThreadPoolExecutor.map`` paths and shows up
    cleanly in profiling traces.  ``cv2.imread`` and ``cv2.resize``
    release the GIL during their CPU-heavy regions, which is what
    lets threading parallelise per-frame decode work.
    """
    if path and os.path.exists(path):
        tile = cv2.imread(path)
    else:
        tile = None
    if tile is None:
        tile = np.zeros((tile_h, tile_w, 3), dtype=np.uint8)
    elif tile.shape[:2] != (tile_h, tile_w):
        tile = cv2.resize(tile, (tile_w, tile_h))
    if with_label and label:
        # Scale font with tile height so labels stay proportional —
        # ~1.0 at 540-px tiles, scales down for smaller / up for
        # larger.  draw_camera_tag handles the translucent
        # background, padding, and font selection internally.
        font_scale = max(0.5, tile_h / 540.0)
        font_thickness = max(1, int(2 * font_scale))
        draw_camera_tag(
            tile, label,
            font_scale=font_scale,
            font_thickness=font_thickness,
        )
    return tile


def _compose_grid(
    tiles: List, n_cols: int, tile_h: int, tile_w: int,
):
    """Arrange ready-sized tiles into a ``(rows x n_cols)`` grid.

    Tiles are assumed to already be ``(tile_h, tile_w, 3)`` uint8
    (callers should use :func:`_prepare_tile` which handles resize +
    fallback to black for missing frames).  Empty cells (when
    ``len(tiles) < n_rows * n_cols``) are padded with black so the
    final output has consistent dimensions
    ``(n_cols * tile_w, n_rows * tile_h)`` throughout the video.
    """
    n_rows = math.ceil(len(tiles) / n_cols)
    padded = list(tiles)
    while len(padded) < n_rows * n_cols:
        padded.append(np.zeros((tile_h, tile_w, 3), dtype=np.uint8))
    rows = [
        cv2.hconcat(padded[r * n_cols : (r + 1) * n_cols])
        for r in range(n_rows)
    ]
    return cv2.vconcat(rows)


def _auto_cam_labels(frame_dirs: List[str]) -> List[str]:
    """Pick sensible labels for each frame_dir without explicit input.

    Heuristic:

    * If every dir shares the **same basename** (e.g. all end in
      ``rgb``, suggesting a ``<cam>/<frames_subdir>/`` layout), use
      each dir's parent basename — that's the camera name in the
      canonical layout.
    * Otherwise (each dir has a unique basename, suggesting the dirs
      ARE the camera dirs directly), use each dir's basename.

    Triggered only when the caller passes ``cam_labels=None`` —
    explicit labels always win.
    """
    rstripped = [d.rstrip(os.sep) for d in frame_dirs]
    basenames = [os.path.basename(d) for d in rstripped]
    if len(rstripped) > 1 and len(set(basenames)) == 1:
        # Shared basename → frames-subdir layout; cam name is the parent.
        return [
            os.path.basename(os.path.dirname(d)) or basenames[i]
            for i, d in enumerate(rstripped)
        ]
    return basenames


def frames_to_video_grid(
    frame_dirs: List[str],
    output_path: str,
    *,
    cam_labels: Optional[List[str]] = None,
    fps: float = DEFAULT_FPS,
    codec: str = DEFAULT_CODEC,
    glob_patterns: Iterable[str] = DEFAULT_GLOB_PATTERNS,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    n_cols: Optional[int] = None,
    target_height: Optional[int] = DEFAULT_TARGET_HEIGHT,
    label: Optional[str] = None,
    per_cam_label: bool = True,
    overwrite: bool = True,
    progress: bool = True,
    max_workers: Optional[int] = None,
) -> str:
    """Stack frames from multiple per-camera directories into a grid
    layout and encode as a single video.

    For each frame index, reads the corresponding frame from each
    directory in ``frame_dirs``, optionally adds per-camera labels,
    arranges them in a ``cols x rows`` grid, and writes the result
    to ``output_path``.

    Frame iteration: uses the **first** directory's sorted-name list
    as the master frame sequence.  Cameras with a missing frame at a
    given index get a black tile (so all cameras stay synchronized
    even if some are shorter or have gaps).

    Grid layout:

    * ``n_cols`` columns x ``ceil(N / n_cols)`` rows where N =
      ``len(frame_dirs)``.
    * When ``n_cols=None`` (default), auto-selects via
      :func:`auto_grid_cols` (``ceil(sqrt(N))``).
    * Empty grid cells are padded with black tiles so the output
      video has consistent dimensions throughout.

    Output sizing:

    * When ``target_height`` is set, ``tile_h = target_height /
      n_rows`` and ``tile_w`` follows the source aspect ratio (the
      first readable frame's dimensions).  Output resolution is
      ``(n_cols * tile_w, n_rows * tile_h)``.
    * When ``target_height=None``, tile dimensions equal the source
      frame's full resolution — handy when you want a lossless grid
      but the output can balloon for many-cam scenes.

    :param frame_dirs: Per-camera directories.  Order is preserved
        (top-left → right → next row).
    :param output_path: Output video file path; container is picked
        from the extension.
    :param cam_labels: Optional list of labels (one per ``frame_dir``).
        ``None`` → auto-derive via :func:`_auto_cam_labels` (use
        parent basename when all dirs share the same name like
        ``rgb``; otherwise use the dir's own basename).
    :param fps: Output frame rate.  Default: package
        :data:`spatialai_data_utils.constants.FPS`.
    :param codec: FourCC codec passed to
        :func:`cv2.VideoWriter_fourcc`.  Default: ``"mp4v"``.
    :param glob_patterns: Glob patterns matched against each
        per-camera frame dir to discover frame files.
    :param start_frame: Index (in the sorted master list) of the
        first frame to include.
    :param end_frame: One-past-last frame index, or ``None`` for the
        full sequence.
    :param n_cols: Number of grid columns; ``None`` → auto.
    :param target_height: Target output video height in pixels;
        ``None`` keeps source resolution.
    :param label: Optional video-wide overlay drawn on the FINAL
        composed frame via :func:`plot_frame_label`.  Distinct from
        ``per_cam_label`` (which adds per-tile labels).
    :param per_cam_label: When ``True`` (default), draw the
        corresponding ``cam_labels[i]`` (or auto-derived name) on
        each tile.
    :param overwrite: When ``False``, an existing ``output_path``
        causes the helper to short-circuit to ``"skipped"`` without
        re-encoding.  When ``True`` (default), always overwrites.
    :param progress: Toggle the tqdm progress bar.
    :param max_workers: When set to ``> 1`` (or ``None`` for
        Python's ``ThreadPoolExecutor`` default), parallelises the
        per-master-frame ``cv2.imread`` + ``cv2.resize`` + label
        work across cameras using a thread pool — both of those cv2
        calls release the GIL during their CPU-heavy regions, so
        threading actually accelerates decode.  ``1`` disables the
        pool entirely (sequential, low overhead — best for very few
        cameras or fast SSDs).  Default ``None`` ≈ auto.  The video
        writer itself stays single-threaded (frames must land in
        order).
    :return: Status string — one of ``STATUS_COMPLETED``,
        ``STATUS_SKIPPED``, ``STATUS_NO_FRAMES_FOUND``,
        ``STATUS_READ_ERROR``, ``STATUS_WRITE_ERROR``.
    :rtype: str
    """
    if not overwrite and os.path.exists(output_path):
        return STATUS_SKIPPED

    if not frame_dirs:
        return STATUS_NO_FRAMES_FOUND

    n_cams = len(frame_dirs)
    n_cols = max(1, n_cols or auto_grid_cols(n_cams))
    n_rows = math.ceil(n_cams / n_cols)

    # Master frame sequence: sorted-name list from the FIRST cam.
    # Other cams look up by basename — missing frames → black tile.
    master_paths = list_frame_paths(frame_dirs[0], glob_patterns)
    if not master_paths:
        return STATUS_NO_FRAMES_FOUND
    end = (
        len(master_paths) if end_frame is None
        else min(end_frame, len(master_paths))
    )
    master_paths = master_paths[start_frame:end]
    if not master_paths:
        return STATUS_NO_FRAMES_FOUND

    # Read the first frame to determine source size + tile size.
    first_frame = cv2.imread(master_paths[0])
    if first_frame is None:
        return STATUS_READ_ERROR
    src_h, src_w = first_frame.shape[:2]
    if target_height is None:
        tile_h, tile_w = src_h, src_w
    else:
        tile_h = max(1, target_height // n_rows)
        # Preserve source aspect ratio in tile dimensions.
        tile_w = max(1, round(src_w * tile_h / src_h))
    out_h, out_w = tile_h * n_rows, tile_w * n_cols

    if cam_labels is None:
        cam_labels = _auto_cam_labels(frame_dirs)

    os.makedirs(
        os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True,
    )
    writer = cv2.VideoWriter(
        output_path, cv2.VideoWriter_fourcc(*codec), float(fps), (out_w, out_h),
    )
    if not writer.isOpened():
        return STATUS_WRITE_ERROR

    # ``max_workers=1`` opts out of the thread pool entirely (saves
    # the per-frame executor-dispatch overhead on tiny scenes); any
    # other value (None or >1) spins up a pool that parallelises the
    # per-camera decode work across master frames.
    use_pool = max_workers is None or max_workers > 1

    def _prepare_one(p_l):
        path, lbl = p_l
        return _prepare_tile(
            path, lbl,
            tile_h=tile_h, tile_w=tile_w, with_label=per_cam_label,
        )

    # One-time naming-convention sanity check: each non-master camera
    # is expected to use the same per-frame filenames as the master so
    # the by-basename lookup below pairs up correctly.  When a cam's
    # dir lacks the master's first frame name, every tile from that
    # cam will silently fall back to black — warn loudly here so
    # misconfigured inputs surface during pre-flight rather than as
    # invisible empty quadrants in the final video.
    master_first = os.path.basename(master_paths[0])
    for d, lbl in zip(frame_dirs[1:], cam_labels[1:], strict=True):
        if not os.path.exists(os.path.join(d, master_first)):
            logger.warning(
                "Camera %r (%s) has no file named %r; its tiles will "
                "fall back to black.  Check that all cameras share "
                "the master camera's frame-naming convention.",
                lbl, d, master_first,
            )

    iterator = tqdm.tqdm(
        master_paths, desc=os.path.basename(output_path), unit="f",
        disable=not progress,
    )
    executor = (
        ThreadPoolExecutor(max_workers=max_workers) if use_pool else None
    )
    try:
        for master_path in iterator:
            frame_name = os.path.basename(master_path)
            jobs = [
                (os.path.join(d, frame_name), lbl)
                for d, lbl in zip(frame_dirs, cam_labels, strict=True)
            ]
            if executor is not None:
                # ``map`` preserves input order so cam_labels align.
                tiles = list(executor.map(_prepare_one, jobs))
            else:
                tiles = [_prepare_one(j) for j in jobs]

            grid = _compose_grid(tiles, n_cols, tile_h, tile_w)
            if label is not None:
                grid = plot_frame_label(grid, label)
            if grid.shape[:2] != (out_h, out_w):
                # Defensive: should not happen given _compose_grid's
                # padding, but resize if it ever drifts.
                grid = cv2.resize(grid, (out_w, out_h))
            writer.write(grid)
    finally:
        if executor is not None:
            executor.shutdown(wait=True)
        writer.release()

    return STATUS_COMPLETED
