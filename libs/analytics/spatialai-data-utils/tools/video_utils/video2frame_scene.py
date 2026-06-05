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
Scene videos → frames CLI (multi-camera, parallel).

Decode every per-camera video file under a multi-camera scene
directory, in parallel.  Wraps
:func:`spatialai_data_utils.visualization.video_utils.video2frame.video_to_frames_batch`,
which uses :class:`concurrent.futures.ProcessPoolExecutor` to side-
step the GIL during CV-decoding.

The defaults match the canonical ``data/mtmc/synthetic/<scene>/``
layout used elsewhere in the toolkit (Isaac-mirror convention)::

    <scene_dir>/
        videos/                 # ← Layout B (default, --videos_dir 'videos')
            Camera_01.mp4
            Camera_02.mp4
            ...
        Camera_01/
            rgb/                # ← output (--frames_subdir 'rgb')
                rgb_00000.jpg   # ← --frame_pattern 'rgb_{:05d}.jpg'
                rgb_00001.jpg
                ...
        Camera_02/
            rgb/
                ...

  Cameras are discovered from filenames in ``<scene_dir>/<videos_dir>/``;
  any ``.mp4`` / ``.avi`` / ``.mov`` / ``.mkv`` / ``.m4v`` / ``.webm``
  file qualifies (case-insensitive).

For datasets that store videos inside per-camera subdirs instead, pass
``--videos_dir ''`` (empty string) to switch to **Layout A**::

    <scene_dir>/
        Camera_01/
            video.mp4           # name controlled by --video_filename
        Camera_02/
            video.mp4
        ...

  Cameras are discovered by listing subdirectories of ``<scene_dir>``
  (via :func:`spatialai_data_utils.datasets.scenes.get_cam_names_in_scene`).

In both layouts, output goes to
``<scene_dir>/<cam>/<--frames_subdir>/<--frame_pattern>`` per camera,
camera-name ordering is the same natural sort (``Camera_2`` before
``Camera_10``), and the resulting paths are auto-discovered by
``draw_3dbbox.py`` / ``get_frame_paths_of_multi_cameras`` without a
custom resolver.

For the single-video case, use :mod:`tools.video_utils.video2frame`.

Usage::

    python tools/video_utils/video2frame_scene.py SCENE_DIR [options]

Examples::

    # Default (Layout B): <scene>/videos/Camera_*.mp4 → <scene>/Camera_*/rgb/
    python tools/video_utils/video2frame_scene.py \\
        data/mtmc/synthetic/scene_001/

    # Same scene, 8 workers
    python tools/video_utils/video2frame_scene.py \\
        data/mtmc/synthetic/scene_001/ --workers 8

    # Layout A: <scene>/Camera_*/video.mp4 → <scene>/Camera_*/images/
    python tools/video_utils/video2frame_scene.py data/scene/ \\
        --videos_dir '' --frames_subdir images \\
        --frame_pattern '{:09d}.jpg'

    # Keep every 5th frame, capped at the first 200 source frames
    python tools/video_utils/video2frame_scene.py \\
        data/mtmc/synthetic/scene_001/ \\
        --frame_skip 5 --end_frame 200
"""

import argparse
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterable, Iterator, List, Optional, Tuple

from spatialai_data_utils.datasets.scenes import (
    get_cam_names_in_scene,
    sort_cam_names_by_id,
)
from spatialai_data_utils.visualization.video_utils.format import (
    format_duration,
    format_size,
)
from spatialai_data_utils.visualization.video_utils.video2frame import (
    expected_extraction_count,
    video_to_frames_batch,
)

logger = logging.getLogger(__name__)


@contextmanager
def _disk_poll_heartbeat(
    jobs: Iterable[Tuple[str, str]],
    pattern_ext: str,
    log: logging.Logger,
    *,
    interval: float = 5.0,
    total_expected: int = 0,
) -> Iterator[None]:
    """Background thread: every ``interval`` seconds, poll each
    per-camera output directory and log how many frames have been
    written *during this invocation*.

    Multi-worker mode otherwise has zero in-flight feedback — bare
    :func:`video_to_frames_batch` only logs ``[i/N] completed <path>``
    after a whole camera finishes, which is several minutes for long
    videos and indistinguishable from a hang.  Polling the file
    system from the parent process gives users a "still alive +
    making progress" signal without per-worker tqdm bars colliding.

    Crucially, files are filtered by ``mtime >= heartbeat_start`` so
    the count reflects **new writes during this run** rather than
    files-on-disk.  This matters when re-extracting over a previous
    partial run: workers overwrite ``rgb_00000.jpg`` etc. in place,
    so the raw file count stays flat while writes are actively
    happening.  mtime is touched by every ``cv2.imwrite`` call
    (close-time on Linux), so the mtime filter advances even when
    file count doesn't.

    The poll is approximate (workers can be writing files while we
    scan) but that's fine for a progress display.

    :param jobs: Same job list passed to ``video_to_frames_batch``;
        we read ``output_dir`` from each.
    :param pattern_ext: Extension of the frame pattern (e.g.
        ``".jpg"``).  Frames with this suffix are counted; others
        ignored.
    :param log: Logger to emit ``"  ... <total> frames written"``
        lines on.
    :param interval: Poll period in seconds.  Default 5.0.
    :param total_expected: Pre-computed sum of expected new frames
        across all cameras (see :func:`expected_extraction_count`).  When
        > 0, heartbeat lines include percentage + ETA derived from
        the lifetime average rate.  When 0 (probe failed / unknown),
        heartbeat falls back to "X frames written" without
        progress fraction.
    """
    stop_event = threading.Event()
    job_list = list(jobs)
    start_perf = time.perf_counter()
    # Wall-clock start, captured BEFORE the worker pool is spawned.
    # Files re-written by workers during this run will have
    # ``mtime > start_wall`` (cv2.imwrite touches mtime on close).
    # A small epsilon (-1.0s) absorbs filesystem timestamp granularity
    # and any tiny clock skew so we don't lose writes that landed
    # within the same second the heartbeat thread started.
    start_wall = time.time() - 1.0

    def _poll() -> None:
        # Skip the immediate first poll so we don't spam at t=0; wait
        # one interval, then loop.  ``stop_event.wait`` returns True
        # when the event fires (= time to stop), False on timeout
        # (= keep polling).
        while not stop_event.wait(interval):
            total = 0
            for _video_path, output_dir in job_list:
                if not pattern_ext:
                    continue
                try:
                    with os.scandir(output_dir) as it:
                        for e in it:
                            if not e.is_file() or not e.name.endswith(pattern_ext):
                                continue
                            try:
                                if e.stat().st_mtime >= start_wall:
                                    total += 1
                            except OSError:
                                # File vanished between scandir and
                                # stat; skip it.
                                continue
                except (FileNotFoundError, NotADirectoryError, OSError):
                    # Output dir may not exist yet (worker hasn't
                    # created it), or vanish mid-scan; both are fine.
                    continue
            elapsed = time.perf_counter() - start_perf
            rate = total / elapsed if elapsed > 0 else 0.0
            if total_expected > 0:
                pct = 100.0 * total / total_expected
                remaining = max(0, total_expected - total)
                # ETA from the lifetime average rate — more stable
                # than instantaneous, less twitchy than per-tick deltas.
                eta_str = (
                    format_duration(remaining / rate) if rate > 0 else "?"
                )
                log.info(
                    f"  ... {total} / {total_expected} frames "
                    f"({pct:.1f}%), elapsed {format_duration(elapsed)} "
                    f"({rate:.1f} fps, ETA {eta_str})"
                )
            else:
                log.info(
                    f"  ... {total} frames written, "
                    f"elapsed {format_duration(elapsed)} "
                    f"({rate:.1f} frames/sec)"
                )

    thread = threading.Thread(
        target=_poll, name="video2frame-heartbeat", daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=interval + 1.0)


# Video file extensions accepted in Layout B (case-insensitive match
# against filenames in <scene_dir>/<videos_dir>/).  Picked to cover the
# common containers; users with anything else can rename or symlink.
VIDEO_EXTENSIONS: Tuple[str, ...] = (
    ".mp4", ".avi", ".mov", ".mkv", ".m4v", ".webm",
)

# Defaults aligned with the canonical data/mtmc/synthetic/<scene>/
# layout (e.g. scene_001): videos under <scene>/videos/, frames
# under <scene>/<cam>/rgb/ following the Isaac-mirror naming convention
# (``rgb_<NNNNN>.jpg``).  These match the ``isaac_jpg`` entry in the
# library's FRAME_NAME_PATTERN_PRESETS, so frames extracted with the
# defaults are auto-discovered by draw_3dbbox.py without a custom
# resolver.  For Layout A users, pass ``--videos_dir ''`` and override
# ``--frames_subdir`` / ``--frame_pattern`` as needed.
DEFAULT_VIDEOS_DIR = "videos"
DEFAULT_FRAMES_SUBDIR = "rgb"
DEFAULT_FRAME_PATTERN = "rgb_{:05d}.jpg"
DEFAULT_VIDEO_FILENAME = "video.mp4"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the video2frame_scene CLI."""
    parser = argparse.ArgumentParser(
        description="Decode every per-camera video file under a "
                    "multi-camera scene directory in parallel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "scene_dir", type=str,
        help="Scene root.  Default discovery follows the canonical "
             "data/mtmc/synthetic/<scene>/ layout: videos at "
             "<scene_dir>/videos/<cam>.<ext>, frames written to "
             "<scene_dir>/<cam>/rgb/.  Pass --videos_dir '' for the "
             "alternate per-camera-subdir layout.",
    )

    # ---- Discovery / output layout ----
    parser.add_argument(
        "--videos_dir", type=str, default=DEFAULT_VIDEOS_DIR,
        help="Layout B (default): videos are at "
             "<scene_dir>/<videos_dir>/<cam>.<ext> (cameras discovered "
             "from filenames; any common video extension accepted).  "
             f"Default: '{DEFAULT_VIDEOS_DIR}'.  Pass an empty string "
             "(--videos_dir '') to switch to Layout A: "
             "<scene_dir>/<cam>/<--video_filename>.",
    )
    parser.add_argument(
        "--video_filename", type=str, default=DEFAULT_VIDEO_FILENAME,
        help="Layout A only: name of the video file inside each "
             f"per-camera subdir.  Default: '{DEFAULT_VIDEO_FILENAME}'.  "
             "Ignored when --videos_dir is set (the default).",
    )
    parser.add_argument(
        "--frames_subdir", type=str, default=DEFAULT_FRAMES_SUBDIR,
        help="Per-camera output subdirectory name "
             "(<scene_dir>/<cam>/<frames_subdir>/).  Default: "
             f"'{DEFAULT_FRAMES_SUBDIR}'.  Applies to both layouts.",
    )

    # ---- Parallelism ----
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Number of worker processes.  Default: auto (Python's "
             "ProcessPoolExecutor picks based on CPU count).",
    )

    # ---- Per-job extraction knobs (forwarded to every worker) ----
    parser.add_argument(
        "--frame_pattern", type=str, default=DEFAULT_FRAME_PATTERN,
        help="Output filename pattern, str.format-style with one "
             "integer slot.  Extension drives the image encoder.  "
             f"Default: '{DEFAULT_FRAME_PATTERN}' (matches the "
             "Isaac-mirror layout used by data/mtmc/synthetic/).",
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
             "until each video ends.",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Re-extract even when the per-camera output dir already "
             "appears fully populated (default: skip that camera).",
    )
    return parser.parse_args()


def discover_jobs(
    scene_dir: str,
    video_filename: str,
    frames_subdir: str,
    videos_dir: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """Build per-camera ``(video_path, output_dir)`` job tuples.

    Dispatches between two input layouts (see module docstring).
    Output dirs are always at ``<scene_dir>/<cam>/<frames_subdir>/``,
    so downstream consumers see the same per-camera structure either
    way.  Camera-name ordering is the natural sort used elsewhere in
    the toolkit (``Camera_2`` before ``Camera_10``) in both layouts.

    :param scene_dir: Scene root.
    :param video_filename: Layout A only — video file name inside each
        per-camera subdir.
    :param frames_subdir: Per-camera output subdir name.
    :param videos_dir: Layout B switch — when truthy, videos are at
        ``<scene_dir>/<videos_dir>/<cam>.<ext>``.  When ``None`` or an
        empty string, Layout A is used (the empty-string form lets
        users override the CLI default ``--videos_dir`` from the
        command line without having to spell a sentinel).
    :return: List of ``(video_path, output_dir)`` tuples in
        natural-sort order.
    """
    if not videos_dir:
        # Layout A: <scene_dir>/<cam>/<video_filename>.  Filter to
        # cams that actually have the expected video file inside, so
        # non-camera siblings (videos/, resources/, calibration/, ...)
        # don't produce file_not_found jobs.
        cam_names = get_cam_names_in_scene(
            scene_dir, must_contain=video_filename,
        )
        return [
            (
                os.path.join(scene_dir, cam, video_filename),
                os.path.join(scene_dir, cam, frames_subdir),
            )
            for cam in cam_names
        ]

    # Layout B: <scene_dir>/<videos_dir>/<cam>.<ext>
    videos_path = os.path.join(scene_dir, videos_dir)
    if not os.path.isdir(videos_path):
        return []
    cam_to_video: dict = {}
    for entry in os.scandir(videos_path):
        if not entry.is_file():
            continue
        if not entry.name.lower().endswith(VIDEO_EXTENSIONS):
            continue
        cam = os.path.splitext(entry.name)[0]
        cam_to_video[cam] = entry.path
    sorted_cams = sort_cam_names_by_id(list(cam_to_video.keys()))
    return [
        (cam_to_video[cam], os.path.join(scene_dir, cam, frames_subdir))
        for cam in sorted_cams
    ]


def main() -> None:
    """Parse arguments, discover cameras, and dispatch to ``video_to_frames_batch``."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()

    # Resolve the scene_dir absolute path up-front: catches CWD /
    # symlink confusion that Python's os.getcwd() (which returns the
    # *physical* path on Linux) handles differently from bash's $PWD.
    scene_dir_abs = os.path.abspath(args.scene_dir)

    jobs = discover_jobs(
        scene_dir_abs,
        args.video_filename,
        args.frames_subdir,
        videos_dir=args.videos_dir,
    )
    if not jobs:
        if args.videos_dir:
            hint = (
                f"check that {os.path.join(scene_dir_abs, args.videos_dir)!r} "
                f"exists and contains files with extensions {VIDEO_EXTENSIONS}."
            )
        else:
            hint = (
                "check the directory layout and --video_filename / "
                "--frames_subdir flags, or set --videos_dir for the "
                "central-folder layout."
            )
        sys.exit(f"error: no cameras discovered under {scene_dir_abs!r}; {hint}")

    pattern_ext = os.path.splitext(args.frame_pattern)[1]

    # Pre-flight probe: open each video for metadata and add up the
    # expected output frames per camera, accounting for skip-detection
    # (cams whose output dirs already hold >= expected frames will
    # short-circuit and contribute 0).  Probing is just a header read,
    # not a full decode — milliseconds per file.  Done before the log
    # block so the user sees the "Expected" line in the same place
    # as the other pre-flight info.
    total_expected = sum(
        expected_extraction_count(
            v, o,
            frame_pattern=args.frame_pattern,
            frame_skip=args.frame_skip,
            start_frame=args.start_frame,
            end_frame=args.end_frame,
            overwrite=args.overwrite,
        )
        for v, o in jobs
    )

    logger.info("=== video2frame_scene ===")
    logger.info(f"  Scene dir : {scene_dir_abs}")
    logger.info(f"  Cameras   : {len(jobs)}")
    if args.videos_dir:
        logger.info(
            f"  Layout    : B (videos at <scene>/{args.videos_dir}/<cam>.<ext>)"
        )
    else:
        logger.info(
            f"  Layout    : A (videos at <scene>/<cam>/{args.video_filename})"
        )
    logger.info(f"  Output    : <scene>/<cam>/{args.frames_subdir}/")
    logger.info(f"  Pattern   : {args.frame_pattern}")
    logger.info(f"  Workers   : {args.workers if args.workers is not None else 'auto'}")
    if total_expected > 0:
        logger.info(f"  Expected  : {total_expected} new frames this run")
    else:
        logger.info("  Expected  : 0 new frames (all cameras will skip)")
    if args.frame_skip != 1:
        logger.info(f"  Skip      : every {args.frame_skip} frame(s)")
    if args.start_frame or args.end_frame is not None:
        logger.info(
            f"  Range     : [{args.start_frame}, "
            f"{args.end_frame if args.end_frame is not None else 'end'})"
        )
    if args.overwrite:
        logger.info("  Overwrite : True")
    logger.info("=========================")

    t0 = time.perf_counter()
    # Wrap the batch dispatch with a disk-polling heartbeat so users
    # see "frames written so far" every 5 s while workers grind
    # through long videos (per-job log lines from the library only
    # appear AFTER each camera finishes — minutes of silence
    # otherwise).
    with _disk_poll_heartbeat(
        jobs, pattern_ext, logger, total_expected=total_expected,
    ):
        results = video_to_frames_batch(
            jobs,
            max_workers=args.workers,
            progress_logger=logger,
            frame_pattern=args.frame_pattern,
            frame_skip=args.frame_skip,
            start_frame=args.start_frame,
            end_frame=args.end_frame,
            overwrite=args.overwrite,
        )
    elapsed = time.perf_counter() - t0

    # Post-flight: aggregate frames + bytes across every camera that
    # actually wrote output (skipped cameras already had populated
    # dirs we didn't touch — we still tally them so the summary
    # matches what's on disk).
    total_frames = 0
    total_bytes = 0
    for _video_path, output_dir, _status in results:
        if not pattern_ext or not os.path.isdir(output_dir):
            continue
        for entry in os.scandir(output_dir):
            if entry.is_file() and entry.name.endswith(pattern_ext):
                total_frames += 1
                total_bytes += entry.stat().st_size

    bad = [
        (v, o, s) for v, o, s in results
        if s not in ("completed", "skipped")
    ]

    logger.info("=== summary ===")
    logger.info(f"  Cameras  : {len(results)} ({len(results) - len(bad)} ok, {len(bad)} bad)")
    logger.info(
        f"  Frames   : {total_frames} on disk "
        f"({format_size(total_bytes)} total)"
    )
    if elapsed > 0 and total_frames > 0 and not bad:
        logger.info(
            f"  Time     : {format_duration(elapsed)} "
            f"({total_frames / elapsed:.1f} frames/sec aggregate)"
        )
    else:
        logger.info(f"  Time     : {format_duration(elapsed)}")
    logger.info("===============")

    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
