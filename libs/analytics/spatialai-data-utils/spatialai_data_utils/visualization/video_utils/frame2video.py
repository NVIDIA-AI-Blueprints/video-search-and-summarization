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
Frames → video assembly utilities.

Two public entry points:

* :func:`frames_to_video` — read a directory of image frames and
  encode them as an MP4/AVI video.  Configurable fps, codec, glob
  pattern (``*.jpg`` vs ``*.png``), start/end-frame trimming,
  optional resolution downsample, optional text label overlay.
* :func:`frames_to_video_batch` — multiprocess wrapper that runs
  :func:`frames_to_video` over an explicit list of
  ``(frame_dir, output_path)`` jobs.  Encoding is CPU-bound; the
  process pool side-steps the GIL.

CLI entry points live in
``tools/video_utils/frame2video.py`` — they pass user-supplied paths
+ knobs through to ``frames_to_video_batch``.

Frame requirements:

* Filenames sort into playback order — either lexicographically
  (e.g. ``000000001.jpg``, ``000000002.jpg``, …) or via the smart
  sort in :func:`list_frame_paths` (timestamp-int → frame-id-int →
  lexicographic fallback).
* The **first** frame defines the output resolution
  ``(out_w, out_h) = (width // down_sample, height // down_sample)``.
  Every subsequent frame whose shape differs is automatically
  resized to ``(out_w, out_h)`` before being written, regardless of
  whether ``down_sample`` is set — there is no silent-failure path
  on size mismatch.
"""

import glob
import logging
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Tuple

import cv2
import tqdm

from spatialai_data_utils.constants import FPS as DEFAULT_FPS
from spatialai_data_utils.visualization.video_utils.text_writer import plot_frame_label

logger = logging.getLogger(__name__)


# Status strings (parallels :mod:`video2frame` for symmetric reporting).
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
STATUS_NO_FRAMES_FOUND = "no_frames_found"
STATUS_READ_ERROR = "read_error"
STATUS_WRITE_ERROR = "write_error"

# Default codec / glob pattern: MP4-V is widely supported and matches
# the encoding used by the dataset preprocessing pipeline.  The glob
# pattern accepts JPG and PNG by default — the most common outputs of
# :mod:`video_to_frames`.
DEFAULT_CODEC = "mp4v"
DEFAULT_GLOB_PATTERNS: Tuple[str, ...] = ("*.jpg", "*.jpeg", "*.png")


# Filename parser: extracts ``(frame_id, timestamp_int)`` from
# basenames like ``5.png`` (frame_id=5, ts=None) or
# ``5_2026-04-24T10-00-03.500Z.png`` (frame_id=5, ts=epoch_ms_int).
# Anchors on a leading run of digits so legacy zero-padded names
# (``000000005.jpg``) parse cleanly when applicable.  Timestamps are
# converted to ``int`` (concatenated zero-padded calendar fields) so
# the smart sort below uses fast integer comparisons rather than
# string compares — see :func:`_filename_ts_to_int` for the format.
_FRAME_NAME_RE = re.compile(r"^(\d+)(?:_(.+))?$")
_TS_DIGITS_RE = re.compile(r"\d+")


def _filename_ts_to_int(ts: str) -> Optional[int]:
    """Convert a filesystem-safe ISO timestamp to a sortable int.

    Concatenates the timestamp's zero-padded calendar fields
    (``YYYYMMDDHHMMSSmmm``) into a single integer — fits in 64 bits
    through the year 9999 and gives a ``int`` sort key that's
    cheaper than the equivalent string compare and avoids any
    ``datetime`` parsing on the hot loop.

    Accepts both the filesystem-safe shape produced by the toolkit
    (``2026-04-24T10-00-03.500Z``) and the standard NVSchema shape
    (``2026-04-24T10:00:03.500Z``) — the conversion is just digit
    extraction so the separator doesn't matter.

    Returns ``None`` if the string yields too few digits to be a
    plausible timestamp (i.e. anything that doesn't carry at least
    a year + month + day + hour + minute + second).
    """
    digits = "".join(_TS_DIGITS_RE.findall(ts))
    # YYYYMMDDHHMMSS = 14 digits minimum.  Pad / truncate the
    # millisecond tail to exactly 3 so two timestamps with different
    # sub-second precision still compare correctly.
    if len(digits) < 14:
        return None
    if len(digits) >= 17:
        digits = digits[:17]
    else:
        digits = digits.ljust(17, "0")
    return int(digits)


def parse_frame_filename(
    path: str,
) -> Tuple[Optional[int], Optional[int]]:
    """Parse ``<frame_id>(_<timestamp>)?.<ext>`` from ``path``'s basename.

    :return: ``(frame_id, ts_int)``.  Either field is ``None`` when
        the basename can't be parsed in that shape — e.g. an arbitrary
        filename that doesn't start with digits returns ``(None, None)``,
        and a basename without an underscore-suffix returns
        ``(<int>, None)``.  When the timestamp suffix is present but
        doesn't yield a parseable timestamp, ``ts_int`` falls back to
        ``None``.  See :func:`_filename_ts_to_int` for the int form.
    :rtype: tuple[int or None, int or None]
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _FRAME_NAME_RE.match(stem)
    if not m:
        return None, None
    frame_id = int(m.group(1))
    ts_str = m.group(2)
    ts_int = _filename_ts_to_int(ts_str) if ts_str is not None else None
    return frame_id, ts_int


def list_frame_paths(
    frame_dir: str, glob_patterns: Iterable[str],
) -> List[str]:
    """Return frame file paths in *frame_dir* matching any of *glob_patterns*.

    Sort precedence (richest signal first):

    1. **Timestamp** — when every basename parses to an integer
       timestamp via :func:`_filename_ts_to_int`, sort by that int
       (fast integer comparison; chronological since we concat the
       calendar fields ``YYYYMMDDHHMMSSmmm``).  Ties broken by
       ``frame_id``.
    2. **Frame_id** — when every basename matches
       ``<frame_id>(_<rest>)?.<ext>``, sort by the parsed integer
       ``frame_id``.  Handles the new no-pad default
       (``5.png`` < ``10.png``) and legacy zero-padded names
       (``000000005.jpg`` < ``000000010.jpg``) identically.
    3. **Lex** — fallback when basenames don't follow either shape
       (e.g. ``rgb_00005.jpg`` from the Isaac-mirror layout).

    De-duped on the off-chance two patterns overlap (e.g.
    ``*.jpg`` + ``*.JPG`` on case-insensitive filesystems).

    Public helper — intentionally without the leading underscore so
    sibling modules in the package (e.g.
    :mod:`spatialai_data_utils.visualization.video_utils.frame2video_grid`)
    can import it as a stable named export rather than reaching into
    a private symbol.
    """
    seen = set()
    out: List[str] = []
    for pat in glob_patterns:
        for p in glob.glob(os.path.join(frame_dir, pat)):
            if p not in seen:
                seen.add(p)
                out.append(p)
    if not out:
        return out

    parsed = [parse_frame_filename(p) for p in out]
    if all(ts is not None for _fid, ts in parsed):
        # Tie-break on frame_id when two frames share a timestamp
        # (rare but possible at sub-millisecond capture rates).
        return [p for p, _ in sorted(
            zip(out, parsed, strict=True),
            key=lambda po: (po[1][1], po[1][0]),
        )]
    if all(fid is not None for fid, _ts in parsed):
        return [p for p, _ in sorted(
            zip(out, parsed, strict=True),
            key=lambda po: po[1][0],
        )]
    out.sort()
    return out


def frames_to_video(
    frame_dir: str,
    output_path: str,
    *,
    fps: float = DEFAULT_FPS,
    codec: str = DEFAULT_CODEC,
    glob_patterns: Iterable[str] = DEFAULT_GLOB_PATTERNS,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    down_sample: int = 1,
    label: Optional[str] = None,
    overwrite: bool = True,
    progress: bool = True,
) -> str:
    """Assemble an MP4 video from a sorted directory of image frames.

    Reads frames in sorted-name order (configurable extensions),
    optionally trims to a sub-range, optionally downsamples the
    output resolution, optionally draws a fixed text label on every
    frame, and writes everything to *output_path*.

    :param frame_dir: Directory containing the input frames.
    :type frame_dir: str
    :param output_path: Path to write the encoded video to (extension
        determines container; ``.mp4`` recommended).
    :type output_path: str
    :param fps: Output video frame rate.  Defaults to the package
        :data:`spatialai_data_utils.constants.FPS` (``30.0`` at the
        time of writing).
    :type fps: int | float
    :param codec: FourCC codec passed to
        :func:`cv2.VideoWriter_fourcc`.  ``"mp4v"`` is the widely-
        supported default; ``"avc1"`` / ``"H264"`` work where the
        OpenCV build links against the right backend.
    :type codec: str
    :param glob_patterns: Iterable of glob patterns (relative to
        *frame_dir*) selecting which frames to include.  Defaults
        to ``("*.jpg", "*.jpeg", "*.png")``.
    :type glob_patterns: Iterable[str]
    :param start_frame: Index of the first frame (in sorted order)
        to include.  Frames before this are skipped.
    :type start_frame: int
    :param end_frame: One-past-last frame index, or ``None`` to read
        through the end of the directory.
    :type end_frame: int or None
    :param down_sample: Integer divisor applied to the first frame's
        resolution to produce the output size — useful for cutting
        file size on multi-camera previews.  ``1`` keeps full
        resolution.
    :type down_sample: int
    :param label: Optional fixed text overlaid on every frame via
        :func:`plot_frame_label`.  Pass ``None`` to skip the
        overlay.  Newline-separated multi-line labels are
        supported.
    :type label: str or None
    :param overwrite: When ``False``, an existing *output_path*
        causes the helper to short-circuit to ``"skipped"``
        without re-encoding.  When ``True`` (default), always
        overwrites.
    :type overwrite: bool
    :param progress: Toggle the tqdm progress bar (off in batch
        mode where workers shouldn't all print at once).
    :type progress: bool
    :return: Status string — one of ``STATUS_COMPLETED``,
        ``STATUS_SKIPPED``, ``STATUS_NO_FRAMES_FOUND``,
        ``STATUS_READ_ERROR``, ``STATUS_WRITE_ERROR``.
    :rtype: str
    """
    if not overwrite and os.path.exists(output_path):
        return STATUS_SKIPPED

    frame_paths = list_frame_paths(frame_dir, glob_patterns)
    if not frame_paths:
        return STATUS_NO_FRAMES_FOUND

    end = len(frame_paths) if end_frame is None else min(end_frame, len(frame_paths))
    selected = frame_paths[start_frame:end]
    if not selected:
        return STATUS_NO_FRAMES_FOUND

    first = cv2.imread(selected[0])
    if first is None:
        return STATUS_READ_ERROR
    height, width = first.shape[:2]
    out_w = max(1, width // max(1, down_sample))
    out_h = max(1, height // max(1, down_sample))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    writer = cv2.VideoWriter(
        output_path, cv2.VideoWriter_fourcc(*codec), float(fps), (out_w, out_h),
    )
    if not writer.isOpened():
        return STATUS_WRITE_ERROR

    iterator = tqdm.tqdm(selected, desc=os.path.basename(frame_dir)) if progress else selected
    for path in iterator:
        frame = cv2.imread(path)
        if frame is None:
            writer.release()
            return STATUS_READ_ERROR
        if (frame.shape[1], frame.shape[0]) != (out_w, out_h):
            frame = cv2.resize(frame, (out_w, out_h))
        if label is not None:
            frame = plot_frame_label(frame, label)
        writer.write(frame)
    writer.release()
    return STATUS_COMPLETED


# ---------------------------------------------------------------------------
# Multiprocess batch wrapper
# ---------------------------------------------------------------------------

def _run_one_job(args: Tuple) -> Tuple[str, str, str]:
    """Worker wrapper around :func:`frames_to_video`.

    Workers don't tqdm — multi-process bars conflict on the same
    terminal — so the wrapper hard-codes ``progress=False``.
    """
    frame_dir, output_path, kwargs = args
    kwargs = dict(kwargs)
    kwargs.setdefault("progress", False)
    try:
        status = frames_to_video(frame_dir, output_path, **kwargs)
    except Exception as exc:  # noqa: BLE001 — surface to parent
        status = f"exception: {exc!r}"
    return frame_dir, output_path, status


def frames_to_video_batch(
    jobs: Iterable,
    *,
    max_workers: Optional[int] = None,
    progress_logger: Optional[logging.Logger] = None,
    **frames_to_video_kwargs,
) -> List[Tuple[str, str, str]]:
    """Run :func:`frames_to_video` over many cameras in parallel.

    Each job item can be either:

    * ``(frame_dir, output_path)`` — uses the shared
      ``frames_to_video_kwargs`` for that job.
    * ``(frame_dir, output_path, per_job_kwargs)`` — same as above
      but ``per_job_kwargs`` (a dict) overrides the shared kwargs.
      Lets callers parallelise across cameras while injecting
      per-camera knobs (e.g. a per-camera ``label`` overlay) without
      serialising the encoding.

    :param jobs: Iterable of either 2-tuples or 3-tuples; see above.
    :type jobs: Iterable[tuple[str, str] or tuple[str, str, dict]]
    :param max_workers: Forwarded to
        :class:`concurrent.futures.ProcessPoolExecutor`.  ``None``
        picks the Python default (CPU count).
    :type max_workers: int or None
    :param progress_logger: Optional logger for per-job completion
        lines.  Defaults to this module's logger.
    :type progress_logger: logging.Logger or None
    :param frames_to_video_kwargs: Shared kwargs forwarded to every
        :func:`frames_to_video` call (overridden per-job by the
        3-tuple form's dict).
    :return: List of ``(frame_dir, output_path, status)`` tuples in
        completion order.
    :rtype: list[tuple[str, str, str]]
    """
    log = progress_logger or logger
    job_list: List[Tuple[str, str, dict]] = []
    for job in jobs:
        if len(job) == 2:
            frame_dir, output_path = job
            per_job: dict = {}
        elif len(job) == 3:
            frame_dir, output_path, per_job = job
        else:
            raise ValueError(
                f"Each job must be a 2- or 3-tuple "
                f"(frame_dir, output_path[, kwargs]); got {len(job)} elements."
            )
        merged = dict(frames_to_video_kwargs)
        merged.update(per_job)
        job_list.append((frame_dir, output_path, merged))
    if not job_list:
        log.info("frames_to_video_batch: no jobs to run")
        return []

    log.info(
        "frames_to_video_batch: %d jobs, max_workers=%s",
        len(job_list), max_workers if max_workers is not None else "auto",
    )
    results: List[Tuple[str, str, str]] = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_job = {
            executor.submit(_run_one_job, j): j for j in job_list
        }
        for i, future in enumerate(as_completed(future_to_job), start=1):
            frame_dir, output_path, status = future.result()
            results.append((frame_dir, output_path, status))
            log.info("[%d/%d] %s  %s -> %s",
                     i, len(job_list), status, frame_dir, output_path)
    _log_batch_summary(log, results)
    return results


def _log_batch_summary(log: logging.Logger, results: List[Tuple[str, str, str]]) -> None:
    """Emit one INFO line summarising per-status counts at end of batch."""
    if not results:
        return
    counts: Dict[str, int] = {}
    for _, _, status in results:
        counts[status] = counts.get(status, 0) + 1
    breakdown = ", ".join(
        f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    log.info("frames_to_video_batch summary (%d jobs): %s", len(results), breakdown)
