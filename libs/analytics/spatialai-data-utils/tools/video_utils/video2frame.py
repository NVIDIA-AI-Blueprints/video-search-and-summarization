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
Video → frames CLI.

Decode a single video file into a directory of per-frame images.
Thin wrapper around
:func:`spatialai_data_utils.visualization.video_utils.video2frame.video_to_frames`.

Usage::

    python tools/video_utils/video2frame.py VIDEO OUTPUT_DIR [options]

Examples::

    # Default: <frame_id>.png (no zero-pad; sorts numerically via the
    # smart sort in frames_to_video / list_frame_paths).
    python tools/video_utils/video2frame.py video.mp4 output/frames/

    # Keep every 5th frame, capped at the first 200 source frames
    python tools/video_utils/video2frame.py video.mp4 output/frames/ \\
        --frame_skip 5 --end_frame 200

    # Custom pattern (legacy positional or named slots both work)
    python tools/video_utils/video2frame.py video.mp4 output/frames/ \\
        --frame_pattern 'frame_{:05d}.png'
"""

import argparse
import logging
import os
import sys
import time

from spatialai_data_utils.visualization.video_utils.format import (
    format_duration,
    format_size,
)
from spatialai_data_utils.visualization.video_utils.video2frame import (
    DEFAULT_FRAME_PATTERN,
    diagnose_video_file,
    video_to_frames,
)

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the video2frame CLI."""
    parser = argparse.ArgumentParser(
        description="Decode a video file into a directory of per-frame images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "video", type=str,
        help="Input video file path.",
    )
    parser.add_argument(
        "output_dir", type=str,
        help="Output directory for the extracted frames "
             "(created if missing).",
    )
    parser.add_argument(
        "--frame_pattern", type=str, default=DEFAULT_FRAME_PATTERN,
        help="Output filename pattern, str.format-style with one "
             "integer slot.  Extension drives the image encoder.  "
             f"Default: '{DEFAULT_FRAME_PATTERN}'.",
    )
    parser.add_argument(
        "--frame_skip", type=int, default=1,
        help="Keep every Nth decoded frame (default 1 = all frames).",
    )
    parser.add_argument(
        "--start_frame", type=int, default=0,
        help="0-indexed source-video index of the first frame to keep.",
    )
    parser.add_argument(
        "--end_frame", type=int, default=None,
        help="One-past-last source-video index to keep.  Default: read "
             "until the video ends.",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Re-extract even when the output dir already appears "
             "fully populated (default: skip).",
    )
    return parser.parse_args()


def main() -> None:
    """Parse arguments and call ``video_to_frames``."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()

    # Resolve absolute paths up-front: catches CWD / symlink confusion
    # that Python's os.getcwd() (which returns the *physical* path on
    # Linux) handles differently from bash's $PWD.
    video_abs = os.path.abspath(args.video)
    output_abs = os.path.abspath(args.output_dir)

    # Pre-flight probe — confirms the decoder picked up the file
    # correctly and lets the user verify expected scale before
    # committing to a long extraction.
    diag = diagnose_video_file(video_abs)
    props = diag.get("properties", {}) or {}

    logger.info("=== video2frame ===")
    logger.info(f"  Video    : {video_abs}")
    logger.info(f"  Output   : {output_abs}")
    logger.info(f"  Pattern  : {args.frame_pattern}")
    if diag["can_open"] and props:
        logger.info(
            f"  Source   : {props.get('frame_count', '?')} frames @ "
            f"{props.get('fps', 0):.1f} fps, "
            f"{props.get('width', '?')}x{props.get('height', '?')} "
            f"({format_size(diag['file_size'])})"
        )
    elif diag["issues"]:
        logger.info(f"  Probe    : {'; '.join(diag['issues'])}")
    if args.frame_skip != 1:
        logger.info(f"  Skip     : every {args.frame_skip} frame(s)")
    if args.start_frame or args.end_frame is not None:
        logger.info(
            f"  Range    : [{args.start_frame}, "
            f"{args.end_frame if args.end_frame is not None else 'end'})"
        )
    if args.overwrite:
        logger.info("  Overwrite: True")
    logger.info("===================")

    t0 = time.perf_counter()
    status = video_to_frames(
        video_abs, output_abs,
        frame_pattern=args.frame_pattern,
        frame_skip=args.frame_skip,
        start_frame=args.start_frame,
        end_frame=args.end_frame,
        overwrite=args.overwrite,
        progress=True,
    )
    elapsed = time.perf_counter() - t0

    # Post-flight summary: count what actually landed on disk and
    # report wall-time + throughput.  os.scandir filtered by the
    # pattern's extension keeps the count accurate even if the
    # output directory was pre-populated by an earlier partial run.
    pattern_ext = os.path.splitext(args.frame_pattern)[1]
    if pattern_ext and os.path.isdir(output_abs):
        frame_files = [
            e for e in os.scandir(output_abs)
            if e.is_file() and e.name.endswith(pattern_ext)
        ]
        n_frames = len(frame_files)
        total_bytes = sum(e.stat().st_size for e in frame_files)
    else:
        n_frames = 0
        total_bytes = 0

    logger.info("=== summary ===")
    logger.info(f"  Status   : {status}")
    logger.info(
        f"  Written  : {n_frames} frames "
        f"({format_size(total_bytes)} on disk)"
    )
    if elapsed > 0 and status == "completed":
        logger.info(
            f"  Time     : {format_duration(elapsed)} "
            f"({n_frames / elapsed:.1f} frames/sec)"
        )
    else:
        logger.info(f"  Time     : {format_duration(elapsed)}")
    logger.info("===============")

    sys.exit(0 if status in ("completed", "skipped") else 1)


if __name__ == "__main__":
    main()
