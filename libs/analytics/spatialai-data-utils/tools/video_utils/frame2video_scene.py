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
Scene frames → stacked grid video CLI (multi-camera, single output).

Read every per-camera frame directory under a multi-camera scene
directory, stack each frame index into a grid layout, and encode the
result as one video file.  Wraps
:func:`spatialai_data_utils.visualization.video_utils.frame2video_grid.frames_to_video_grid`.

The defaults match the canonical ``data/mtmc/synthetic/<scene>/``
layout used elsewhere in the toolkit (Isaac-mirror convention)::

    <scene_dir>/
        Camera_01/
            rgb/                # ← per-camera frame dir
                rgb_00000.jpg
                rgb_00001.jpg
                ...
        Camera_02/
            rgb/...
        ...

Cameras are discovered by listing subdirectories of ``<scene_dir>``
via :func:`spatialai_data_utils.datasets.scenes.get_cam_names_in_scene`,
which natural-sorts names (``Camera_2`` before ``Camera_10``).  Each
camera's frame dir is ``<scene_dir>/<cam>/<--frames_subdir>/``
(default ``rgb``).

Grid layout is auto-selected via ``ceil(sqrt(N))`` columns where N
is the camera count — gives a near-square layout that lines up well
with typical 16:9 source frames:

* 4 cams → 2x2
* 9 cams → 3x3
* 12 cams → 4x3
* 16 cams → 4x4

Override with ``--cols N``.  Tile size is computed so the total
output video is approximately ``--target_height`` (default 1080)
pixels tall, with tile width preserving the source aspect ratio.

Usage::

    python tools/video_utils/frame2video_scene.py SCENE_DIR OUTPUT [options]

Examples::

    # Default: <scene>/Camera_*/rgb/rgb_<00000>.jpg → grid.mp4 (1080p)
    python tools/video_utils/frame2video_scene.py \\
        data/mtmc/synthetic/scene_001/ \\
        output/scene_001_grid.mp4

    # Force 6 columns, 720p output, suppress per-cam labels
    python tools/video_utils/frame2video_scene.py \\
        data/mtmc/synthetic/scene_001/ \\
        output/scene_001_grid.mp4 \\
        --cols 6 --target_height 720 --no_per_cam_label

    # First 200 frames at 60 fps with a fixed run label
    python tools/video_utils/frame2video_scene.py \\
        data/mtmc/synthetic/scene_001/ \\
        output/preview.mp4 \\
        --end_frame 200 --fps 60 --label 'scene_001 preview'
"""

import argparse
import logging
import math
import os
import sys
import time
from glob import glob
from typing import List

from spatialai_data_utils.constants import FPS as DEFAULT_FPS
from spatialai_data_utils.datasets.scenes import get_cam_names_in_scene
from spatialai_data_utils.visualization.video_utils.format import (
    format_duration,
    format_size,
)
from spatialai_data_utils.visualization.video_utils.frame2video import (
    DEFAULT_CODEC,
    DEFAULT_GLOB_PATTERNS,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
)
from spatialai_data_utils.visualization.video_utils.frame2video_grid import (
    DEFAULT_TARGET_HEIGHT,
    auto_grid_cols,
    frames_to_video_grid,
)

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the frame2video_scene CLI."""
    parser = argparse.ArgumentParser(
        description="Stack frames from multiple per-camera directories "
                    "into a grid layout and encode as one video",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "scene_dir", type=str,
        help="Scene root containing per-camera subdirectories.  Each "
             "subdir is expected to hold a frames directory at "
             "<scene_dir>/<cam>/<--frames_subdir>/ (default 'rgb').",
    )
    parser.add_argument(
        "output", type=str,
        help="Output video file path (extension picks the container; "
             "'.mp4' recommended).",
    )

    # ---- Discovery / input layout ----
    parser.add_argument(
        "--frames_subdir", type=str, default="rgb",
        help="Per-camera frame directory name "
             "(<scene_dir>/<cam>/<frames_subdir>/).  Default: 'rgb'.  "
             "Pass '' (empty string) to look directly inside each "
             "<scene_dir>/<cam>/ for frames.",
    )

    # ---- Grid layout ----
    parser.add_argument(
        "--cols", type=int, default=None,
        help="Number of grid columns.  Default: auto via "
             "ceil(sqrt(N)) where N is the camera count.",
    )

    # ---- Output sizing / encoding ----
    parser.add_argument(
        "--target_height", type=int, default=DEFAULT_TARGET_HEIGHT,
        help=f"Target output video height in pixels.  Tile size is "
             f"computed so the total grid is approximately this tall, "
             f"with tile width preserving source aspect ratio.  "
             f"Default: {DEFAULT_TARGET_HEIGHT}.  Pass 0 to keep "
             f"source resolution per tile (output may balloon).",
    )
    parser.add_argument(
        "--fps", type=float, default=DEFAULT_FPS,
        help=f"Output frame rate.  Default: package FPS ({DEFAULT_FPS}).",
    )
    parser.add_argument(
        "--codec", type=str, default=DEFAULT_CODEC,
        help="FourCC codec passed to cv2.VideoWriter_fourcc.  "
             f"Default: '{DEFAULT_CODEC}'.",
    )
    parser.add_argument(
        "--filename_pattern", action="append", default=None,
        help="Filename pattern(s) selecting which frames to include "
             "(shell glob matched against filenames in each per-camera "
             "directory; e.g. '*.png' or 'rgb_*.jpg').  Repeatable.  "
             f"Default: {list(DEFAULT_GLOB_PATTERNS)}.",
    )
    parser.add_argument(
        "--start_frame", type=int, default=0,
        help="Index (in the sorted master list — first camera's frame "
             "names) of the first frame to include.",
    )
    parser.add_argument(
        "--end_frame", type=int, default=None,
        help="One-past-last frame index to include.  Default: read all.",
    )

    # ---- Labels ----
    parser.add_argument(
        "--label", type=str, default=None,
        help="Fixed text overlay drawn on the FINAL composed video "
             "frame (top-left, large).  Use '\\n' for multi-line.",
    )
    parser.add_argument(
        "--no_per_cam_label", action="store_true",
        help="Suppress the per-tile camera-name labels (default: each "
             "tile gets its camera name as a small top-left label).",
    )

    parser.add_argument(
        "--overwrite", action="store_true",
        help="Re-encode even when the output file already exists "
             "(default: skip).",
    )

    # ---- Parallelism ----
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Thread-pool size for parallel per-camera tile decode + "
             "resize + label.  cv2.imread / cv2.resize release the "
             "GIL, so threading actually accelerates the work (the "
             "video writer itself stays single-threaded — frames must "
             "land in order).  Default: auto (Python's "
             "ThreadPoolExecutor default ≈ CPU count).  Pass 1 to "
             "disable the pool entirely.",
    )
    return parser.parse_args()


def discover_frame_dirs(
    scene_dir: str, frames_subdir: str,
) -> List[str]:
    """Build per-camera frame directory paths.

    Filters to camera dirs that actually contain the configured
    frames-subdir via :func:`get_cam_names_in_scene`'s
    ``must_contain`` kwarg, so non-camera siblings inside the scene
    root (e.g. ``videos/``, ``resources/``, ``calibration/``
    directories) are silently dropped instead of polluting the grid
    with black tiles.  Honours the same natural-sort camera-name
    ordering used elsewhere in the toolkit (Camera_2 before
    Camera_10).
    """
    cam_names = get_cam_names_in_scene(
        scene_dir, must_contain=frames_subdir or None,
    )
    return [
        os.path.join(scene_dir, cam, frames_subdir) if frames_subdir
        else os.path.join(scene_dir, cam)
        for cam in cam_names
    ]


def main() -> None:
    """Parse arguments and call ``frames_to_video_grid``."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()

    # Resolve absolute paths up-front: catches CWD / symlink confusion
    # that Python's os.getcwd() (which returns the *physical* path on
    # Linux) handles differently from bash's $PWD.
    scene_dir_abs = os.path.abspath(args.scene_dir)
    output_abs = os.path.abspath(args.output)

    frame_dirs = discover_frame_dirs(scene_dir_abs, args.frames_subdir)
    if not frame_dirs:
        sys.exit(
            f"error: no cameras discovered under {scene_dir_abs!r}; "
            f"check the directory layout and --frames_subdir flag."
        )

    cam_names = [
        os.path.basename(os.path.dirname(d.rstrip(os.sep)))
        if args.frames_subdir
        else os.path.basename(d.rstrip(os.sep))
        for d in frame_dirs
    ]

    n_cams = len(frame_dirs)
    n_cols = args.cols if args.cols and args.cols > 0 else auto_grid_cols(n_cams)
    n_rows = math.ceil(n_cams / n_cols)
    target_height = args.target_height if args.target_height > 0 else None

    glob_patterns = (
        tuple(args.filename_pattern)
        if args.filename_pattern
        else DEFAULT_GLOB_PATTERNS
    )

    # Pre-flight count of frames in the master (= first cam) dir to
    # confirm the input layout is right and give the user a sense of
    # scale before encoding kicks off.
    n_master_frames = 0
    if os.path.isdir(frame_dirs[0]):
        seen: set[str] = set()
        for pat in glob_patterns:
            for p in glob(os.path.join(frame_dirs[0], pat)):
                seen.add(p)
        n_master_frames = len(seen)
    end = (
        n_master_frames if args.end_frame is None
        else min(args.end_frame, n_master_frames)
    )
    n_to_encode = max(0, end - args.start_frame)

    logger.info("=== frame2video_scene ===")
    logger.info(f"  Scene dir : {scene_dir_abs}")
    logger.info(f"  Output    : {output_abs}")
    logger.info(f"  Cameras   : {n_cams} ({', '.join(cam_names)})")
    logger.info(
        f"  Frame dirs: <scene>/<cam>/{args.frames_subdir}/"
        if args.frames_subdir else "  Frame dirs: <scene>/<cam>/"
    )
    logger.info(f"  Grid      : {n_cols} cols x {n_rows} rows")
    logger.info(f"  Target h  : "
                f"{target_height if target_height else 'source resolution'}")
    logger.info(f"  FPS       : {args.fps}")
    if args.workers == 1:
        logger.info("  Workers   : 1 (sequential)")
    else:
        logger.info(
            f"  Workers   : {args.workers if args.workers is not None else 'auto'}"
        )
    if args.codec != DEFAULT_CODEC:
        logger.info(f"  Codec     : {args.codec}")
    if args.filename_pattern is not None:
        logger.info(f"  Pattern   : {list(glob_patterns)}")
    logger.info(f"  Source    : {n_master_frames} frames in master dir")
    if args.start_frame or args.end_frame is not None:
        logger.info(
            f"  Range     : [{args.start_frame}, "
            f"{args.end_frame if args.end_frame is not None else 'end'})"
        )
    logger.info(f"  Encoding  : {n_to_encode} frames")
    if args.label is not None:
        logger.info(f"  Label     : {args.label!r}")
    if args.no_per_cam_label:
        logger.info("  Per-cam   : disabled")
    if args.overwrite:
        logger.info("  Overwrite : True")
    logger.info("=========================")

    t0 = time.perf_counter()
    status = frames_to_video_grid(
        frame_dirs,
        output_abs,
        cam_labels=cam_names,
        fps=args.fps,
        codec=args.codec,
        glob_patterns=glob_patterns,
        start_frame=args.start_frame,
        end_frame=args.end_frame,
        n_cols=n_cols,
        target_height=target_height,
        label=args.label,
        per_cam_label=not args.no_per_cam_label,
        overwrite=args.overwrite,
        max_workers=args.workers,
    )
    elapsed = time.perf_counter() - t0

    out_size = (
        os.path.getsize(output_abs) if os.path.exists(output_abs) else 0
    )

    logger.info("=== summary ===")
    logger.info(f"  Status   : {status}")
    logger.info(
        f"  Encoded  : {n_to_encode} frames "
        f"→ {format_size(out_size)} on disk"
    )
    if elapsed > 0 and status == STATUS_COMPLETED and n_to_encode > 0:
        logger.info(
            f"  Time     : {format_duration(elapsed)} "
            f"({n_to_encode / elapsed:.1f} frames/sec)"
        )
    else:
        logger.info(f"  Time     : {format_duration(elapsed)}")
    logger.info("===============")

    sys.exit(0 if status in (STATUS_COMPLETED, STATUS_SKIPPED) else 1)


if __name__ == "__main__":
    main()
