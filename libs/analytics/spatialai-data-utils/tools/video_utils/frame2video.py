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
Frames → video CLI.

Encode a single directory of per-frame images into one video file.
Thin wrapper around
:func:`spatialai_data_utils.visualization.video_utils.frame2video.frames_to_video`.

Usage::

    python tools/video_utils/frame2video.py FRAME_DIR OUTPUT [options]

Examples::

    # Encode all JPG/PNG frames at the package default fps
    python tools/video_utils/frame2video.py output/frames/ output.mp4

    # 60 fps with a fixed text overlay
    python tools/video_utils/frame2video.py output/frames/ output.mp4 \\
        --fps 60 --label 'Run #42'

    # PNG frames only, half-resolution output
    python tools/video_utils/frame2video.py output/frames/ output.mp4 \\
        --filename_pattern '*.png' --down_sample 2
"""

import argparse
import glob as _glob
import logging
import os
import sys
import time

from spatialai_data_utils.constants import FPS as DEFAULT_FPS
from spatialai_data_utils.visualization.video_utils.format import (
    format_duration,
    format_size,
)
from spatialai_data_utils.visualization.video_utils.frame2video import (
    DEFAULT_CODEC,
    DEFAULT_GLOB_PATTERNS,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
    frames_to_video,
)

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the frame2video CLI."""
    parser = argparse.ArgumentParser(
        description="Encode a directory of image frames into a video file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "frame_dir", type=str,
        help="Input directory containing the per-frame images.  Files "
             "are picked in sorted-name order; ensure filenames sort "
             "lexicographically into playback order "
             "(e.g. '000000001.jpg' < '000000002.jpg').",
    )
    parser.add_argument(
        "output", type=str,
        help="Output video file path (extension picks the container; "
             "'.mp4' recommended).",
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
             "(shell glob matched against filenames in FRAME_DIR; e.g. "
             "'*.png' or 'frame_*.jpg').  Repeatable: "
             "'--filename_pattern *.jpg --filename_pattern *.png'.  "
             f"Default: {list(DEFAULT_GLOB_PATTERNS)}.",
    )
    parser.add_argument(
        "--start_frame", type=int, default=0,
        help="Index of the first frame (in sorted order) to include.",
    )
    parser.add_argument(
        "--end_frame", type=int, default=None,
        help="One-past-last frame index to include.  Default: read all.",
    )
    parser.add_argument(
        "--down_sample", type=int, default=1,
        help="Divide output resolution by this integer.  "
             "Default 1 (full resolution).",
    )
    parser.add_argument(
        "--label", type=str, default=None,
        help="Fixed text overlay drawn on every frame.  "
             "Use '\\n' for multi-line.",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Re-encode even when the output file already exists "
             "(default: skip).",
    )
    return parser.parse_args()


def main() -> None:
    """Parse arguments and call ``frames_to_video``."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()

    # Resolve absolute paths up-front: catches CWD / symlink confusion
    # that Python's os.getcwd() (which returns the *physical* path on
    # Linux) handles differently from bash's $PWD.
    frame_dir_abs = os.path.abspath(args.frame_dir)
    output_abs = os.path.abspath(args.output)

    glob_patterns = (
        tuple(args.filename_pattern)
        if args.filename_pattern
        else DEFAULT_GLOB_PATTERNS
    )

    # Pre-flight count of frames that will actually be encoded —
    # confirms the input dir + glob patterns line up before the long
    # encode starts.
    n_input_frames = 0
    if os.path.isdir(frame_dir_abs):
        seen: set = set()
        for pat in glob_patterns:
            for p in _glob.glob(os.path.join(frame_dir_abs, pat)):
                seen.add(p)
        n_input_frames = len(seen)

    logger.info("=== frame2video ===")
    logger.info(f"  Frames    : {frame_dir_abs}")
    logger.info(f"  Output    : {output_abs}")
    logger.info(f"  FPS       : {args.fps}")
    if args.codec != DEFAULT_CODEC:
        logger.info(f"  Codec     : {args.codec}")
    if args.filename_pattern is not None:
        logger.info(f"  Pattern   : {list(glob_patterns)}")
    logger.info(f"  Source    : {n_input_frames} matching frames")
    if args.start_frame or args.end_frame is not None:
        logger.info(
            f"  Range     : [{args.start_frame}, "
            f"{args.end_frame if args.end_frame is not None else 'end'})"
        )
    if args.down_sample != 1:
        logger.info(f"  Downsample: {args.down_sample}")
    if args.label is not None:
        logger.info(f"  Label     : {args.label!r}")
    if args.overwrite:
        logger.info("  Overwrite : True")
    logger.info("===================")

    t0 = time.perf_counter()
    status = frames_to_video(
        frame_dir_abs, output_abs,
        fps=args.fps,
        codec=args.codec,
        glob_patterns=glob_patterns,
        start_frame=args.start_frame,
        end_frame=args.end_frame,
        down_sample=args.down_sample,
        label=args.label,
        overwrite=args.overwrite,
    )
    elapsed = time.perf_counter() - t0

    # Post-flight: actual frames encoded after start/end-frame trim
    # equals the source-window size (frames_to_video reads all of it).
    end_frame = (
        n_input_frames if args.end_frame is None
        else min(args.end_frame, n_input_frames)
    )
    n_encoded = max(0, end_frame - args.start_frame)
    out_size = (
        os.path.getsize(output_abs) if os.path.exists(output_abs) else 0
    )

    logger.info("=== summary ===")
    logger.info(f"  Status   : {status}")
    logger.info(
        f"  Encoded  : {n_encoded} frames "
        f"→ {format_size(out_size)} on disk"
    )
    if elapsed > 0 and status == STATUS_COMPLETED and n_encoded > 0:
        logger.info(
            f"  Time     : {format_duration(elapsed)} "
            f"({n_encoded / elapsed:.1f} frames/sec)"
        )
    else:
        logger.info(f"  Time     : {format_duration(elapsed)}")
    logger.info("===============")

    sys.exit(0 if status in (STATUS_COMPLETED, STATUS_SKIPPED) else 1)


if __name__ == "__main__":
    main()
