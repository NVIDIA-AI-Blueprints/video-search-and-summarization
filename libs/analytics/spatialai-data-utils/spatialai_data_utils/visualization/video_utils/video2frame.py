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
Video → frames extraction utilities.

Two public entry points:

* :func:`video_to_frames` — decode a single video and write its frames
  into an output directory.  Configurable frame skipping, start/end-
  frame trimming, output filename pattern, and overwrite semantics.
  Returns a status string useful for batch summaries (``"completed"``,
  ``"skipped"``, ``"file_not_found"``, etc.).
* :func:`video_to_frames_batch` — multiprocess wrapper that runs
  :func:`video_to_frames` over an explicit list of
  ``(video_path, output_dir)`` jobs.  Uses :mod:`concurrent.futures`'
  ``ProcessPoolExecutor`` so each worker gets its own video-decoder
  process (CV-decoding is CPU-bound; processes side-step the GIL).

CLI entry points live in
``tools/video_utils/video2frame.py`` — they pass user-supplied paths
and discovery options through to ``video_to_frames_batch``.

A diagnostic helper :func:`diagnose_video_file` is also available — it
probes a file for common decode-time issues (empty file, missing moov
atom, invalid properties) without extracting any frames.
"""

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Tuple

import cv2
import tqdm

logger = logging.getLogger(__name__)


# Status strings returned by :func:`video_to_frames`.  Public for
# consumers that want to branch on the outcome.
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
STATUS_FILE_NOT_FOUND = "file_not_found"
STATUS_EMPTY_FILE = "empty_file"
STATUS_CANNOT_OPEN = "cannot_open"
STATUS_INVALID_PROPERTIES = "invalid_properties"
STATUS_NO_FRAMES_EXTRACTED = "no_frames_extracted"
STATUS_INCOMPLETE_EXTRACTION = "incomplete_extraction"
STATUS_WRITE_ERROR = "write_error"

# Frame-name pattern presets for the canonical layouts the
# visualization stack already knows how to discover.  These are
# extracted from
# :func:`spatialai_data_utils.datasets.frame_paths._build_non_h5_frame_patterns`
# (the source-of-truth list of 9 candidate per-frame paths shared by
# every loose-JPG/PNG image consumer in the toolkit) so that frames
# extracted via this helper are automatically picked up by
# ``resolve_frame_path`` / ``get_frame_paths_of_multi_cameras``
# without a custom resolver.
#
# Map keys are stable preset names exposed by the CLI's ``--layout``
# flag; values are :py:meth:`str.format`-style patterns with one
# integer slot.  Pick the preset whose **subdir convention** matches
# what you want under the per-camera output directory.
FRAME_NAME_PATTERN_PRESETS: Dict[str, str] = {
    "aic":       "{:09d}.jpg",      # <cam>/images/000000006.jpg (AIC25 / Isaac mirror, default)
    "isaac_png": "rgb_{:05d}.png",  # <cam>/rgb/rgb_00006.png
    "isaac_jpg": "rgb_{:05d}.jpg",  # <cam>/rgb/rgb_00006.jpg
    "scout":     "image_{}.jpg",    # <cam>/image_6.jpg (no zero-pad)
    "bare_jpg":  "{}.jpg",          # <cam>/6.jpg
    "bare_png":  "{}.png",          # <cam>/6.png
}

# Default frame-name pattern: ``"{frame_id}.png"`` — no zero pad, so
# the produced files sort numerically only via the smart sort in
# :func:`spatialai_data_utils.visualization.video_utils.frame2video.list_frame_paths`
# (lex sort would otherwise put ``10.png`` before ``2.png``).
#
# Companion convention: downstream tools that DO have a wall-clock
# timestamp (e.g. NVSchema visualizers) emit
# ``"{frame_id}_{timestamp}.png"`` (ISO 8601, ``:`` replaced with ``-``
# for filesystem safety, e.g. ``5_2026-04-24T10-00-03.500Z.png``).
# :func:`video_to_frames` here doesn't have a wall-clock source and
# only emits the frame_id form; the smart sort in
# :func:`list_frame_paths` handles a directory mixing or matching
# either shape.
#
# Patterns may use named slots (``{frame_id}``) or legacy positional
# slots (e.g. ``"{:09d}.jpg"`` from the
# :data:`FRAME_NAME_PATTERN_PRESETS` table).  ``video_to_frames``
# always passes ``out_idx`` positionally AND ``frame_id=out_idx`` as
# a kwarg to :py:meth:`str.format` so either style works.
DEFAULT_FRAME_PATTERN = "{frame_id}.png"

# How many consecutive cv2.read() failures the inner loop tolerates
# before bailing out.  Some codecs return False mid-stream on
# perfectly valid frames; allowing a small streak lets us recover.
_MAX_CONSECUTIVE_DECODE_FAILURES = 10

# Cut-off below which an extraction is flagged as "incomplete" (i.e.
# we got noticeably fewer frames than expected for the configured
# range).  Pre-rename this was hard-coded at 80%; preserved as the
# default but factored out so callers can tune.
_INCOMPLETE_EXTRACTION_RATIO = 0.8


def video_to_frames(
    video_path: str,
    output_dir: str,
    *,
    frame_pattern: str = DEFAULT_FRAME_PATTERN,
    frame_skip: int = 1,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    overwrite: bool = False,
    progress: bool = False,
) -> str:
    """Extract frames from a video into ``output_dir``.

    The output filename for the *i*-th written frame is computed as
    ``frame_pattern.format(out_idx, frame_id=out_idx)`` (counting
    ``out_idx`` from 0 over **kept** frames, not source-video frames
    — so ``frame_skip > 1`` produces consecutively-numbered output
    filenames, no gaps).  Both positional (``"{:09d}.jpg"``) and
    named-slot (``"{frame_id}.png"``) patterns are supported because
    we always pass both forms to :py:meth:`str.format`.

    :param video_path: Path to the input video file.
    :type video_path: str
    :param output_dir: Directory to write frames into.  Created if
        missing.
    :type output_dir: str
    :param frame_pattern: ``str.format`` template.  Default
        ``"{frame_id}.png"`` (no zero pad — sort numerically via
        :func:`spatialai_data_utils.visualization.video_utils.frame2video.list_frame_paths`).
        Legacy positional patterns like ``"{:09d}.jpg"`` keep working.
        The extension drives :func:`cv2.imwrite`'s encoder choice.
    :type frame_pattern: str
    :param frame_skip: Keep every *frame_skip*-th decoded frame.  ``1``
        keeps everything (default).  ``2`` keeps every other frame, etc.
    :type frame_skip: int
    :param start_frame: Source-video index of the first frame to keep
        (0-indexed).  Frames before this are decoded and dropped.
    :type start_frame: int
    :param end_frame: One-past-last source-video index to keep, or
        ``None`` to read until the video ends.
    :type end_frame: int or None
    :param overwrite: When ``False`` (default), a fully-populated
        output directory short-circuits to ``"skipped"`` (matches
        existing files against the expected frame count).  When
        ``True``, the extraction always runs and overwrites any
        existing files at colliding paths.
    :type overwrite: bool
    :param progress: When ``True``, draw a tqdm progress bar tracking
        source-video position.  Off by default — batch mode (multiple
        workers writing to the same terminal) should keep this
        ``False``; the single-video CLI flips it on.
    :type progress: bool
    :return: Status string — one of ``STATUS_COMPLETED``,
        ``STATUS_SKIPPED``, ``STATUS_FILE_NOT_FOUND``,
        ``STATUS_EMPTY_FILE``, ``STATUS_CANNOT_OPEN``,
        ``STATUS_INVALID_PROPERTIES``,
        ``STATUS_NO_FRAMES_EXTRACTED``,
        ``STATUS_INCOMPLETE_EXTRACTION``, ``STATUS_WRITE_ERROR``.
    :rtype: str
    """
    if not os.path.exists(video_path):
        return STATUS_FILE_NOT_FOUND
    if os.path.getsize(video_path) == 0:
        return STATUS_EMPTY_FILE

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        cap.release()
        return STATUS_CANNOT_OPEN

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if total_frames <= 0 or fps <= 0 or width <= 0 or height <= 0:
        cap.release()
        return STATUS_INVALID_PROPERTIES

    # Compute how many frames we expect to write so we can both
    # short-circuit when the output dir is already populated and flag
    # under-decoded videos at the end.  Clamp ``frame_skip`` to ``>=1``
    # so a 0/negative value can't trigger ZeroDivisionError below in
    # ``expected_count`` or in the per-frame ``% skip`` stride check.
    # Mirrors the same clamp inside :func:`expected_extraction_count`
    # so the prediction matches actual extraction behaviour.
    skip = max(1, frame_skip)
    range_end = total_frames if end_frame is None else min(end_frame, total_frames)
    range_size = max(0, range_end - start_frame)
    expected_count = (range_size + skip - 1) // skip

    # Skip-detection: count files matching the pattern's extension to
    # decide whether the directory already holds a complete extraction.
    pattern_ext = os.path.splitext(frame_pattern)[1]
    if (
        not overwrite
        and pattern_ext
        and os.path.isdir(output_dir)
    ):
        existing = sum(
            1 for f in os.listdir(output_dir) if f.endswith(pattern_ext)
        )
        if existing >= expected_count and expected_count > 0:
            cap.release()
            return STATUS_SKIPPED

    os.makedirs(output_dir, exist_ok=True)

    consecutive_failures = 0
    src_idx = 0   # 0-indexed position in the source video
    out_idx = 0   # number of frames written so far

    # Progress bar tracks source-video frame index (the loop's natural
    # iteration count).  ``disable=not progress`` is the standard tqdm
    # idiom for opt-in bars — the bar is created and immediately
    # short-circuits to a no-op when callers don't want it.
    pbar = tqdm.tqdm(
        total=range_end,
        desc=os.path.basename(video_path) or "video2frame",
        unit="f",
        disable=not progress,
        leave=False,
    )

    try:
        while cap.isOpened() and consecutive_failures < _MAX_CONSECUTIVE_DECODE_FAILURES:
            ret, frame = cap.read()
            if not ret:
                # Decoder consumed the slot even though no frame was
                # delivered.  Bump src_idx so subsequent
                # start/end/skip comparisons stay aligned with the
                # actual source-video position; otherwise a transient
                # failure off-by-ones every downstream check.
                consecutive_failures += 1
                src_idx += 1
                pbar.update(1)
                continue
            consecutive_failures = 0

            if src_idx >= range_end:
                break

            # Keep this source frame iff it's in the [start_frame,
            # range_end) window AND on a frame_skip stride boundary.
            in_window = src_idx >= start_frame
            on_stride = (src_idx - start_frame) % skip == 0
            if in_window and on_stride:
                if frame is None or frame.size == 0:
                    src_idx += 1
                    pbar.update(1)
                    continue
                # str.format with both positional and named slots so
                # legacy patterns ("{:09d}.jpg") and the modern
                # default ("{frame_id}.png") both work.  This helper
                # never produces filenames with a {timestamp} slot —
                # those come from downstream tools (e.g. NVSchema
                # visualizers) that have a wall-clock source.
                fname = frame_pattern.format(out_idx, frame_id=out_idx)
                img_path = os.path.join(output_dir, fname)
                if not cv2.imwrite(img_path, frame):
                    cap.release()
                    return STATUS_WRITE_ERROR
                out_idx += 1

            src_idx += 1
            pbar.update(1)
    finally:
        pbar.close()

    cap.release()

    if out_idx == 0:
        return STATUS_NO_FRAMES_EXTRACTED
    if expected_count > 0 and out_idx < expected_count * _INCOMPLETE_EXTRACTION_RATIO:
        return STATUS_INCOMPLETE_EXTRACTION
    return STATUS_COMPLETED


def diagnose_video_file(video_path: str) -> Dict:
    """Probe a video file for common decode-time issues.

    Use this to investigate why :func:`video_to_frames` returned
    ``"cannot_open"`` / ``"invalid_properties"`` etc. on a specific
    file — the helper opens the video, reads the first frame, and
    reports any anomalies found.

    :param video_path: Path to the video file to diagnose.
    :type video_path: str
    :return: Dictionary with ``file_exists``, ``file_size``,
        ``can_open``, ``properties`` (dict of fps / dimensions /
        frame_count / fourcc), and ``issues`` (list of
        human-readable problem strings).
    :rtype: dict
    """
    diagnosis: Dict = {
        "file_exists": os.path.exists(video_path),
        "file_size": 0,
        "can_open": False,
        "properties": {},
        "issues": [],
    }

    if not diagnosis["file_exists"]:
        diagnosis["issues"].append("File does not exist")
        return diagnosis

    diagnosis["file_size"] = os.path.getsize(video_path)
    if diagnosis["file_size"] == 0:
        diagnosis["issues"].append("File is empty (0 bytes)")
        return diagnosis

    cap = cv2.VideoCapture(video_path)
    diagnosis["can_open"] = cap.isOpened()

    if not diagnosis["can_open"]:
        diagnosis["issues"].append(
            "Cannot open video file - likely corrupted or missing moov atom"
        )
        cap.release()
        return diagnosis

    properties = {
        "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fourcc": int(cap.get(cv2.CAP_PROP_FOURCC)),
    }
    diagnosis["properties"] = properties

    if properties["frame_count"] <= 0:
        diagnosis["issues"].append("Invalid frame count")
    if properties["fps"] <= 0:
        diagnosis["issues"].append("Invalid FPS")
    if properties["width"] <= 0 or properties["height"] <= 0:
        diagnosis["issues"].append("Invalid dimensions")

    ret, frame = cap.read()
    if not ret or frame is None:
        diagnosis["issues"].append("Cannot read first frame")

    cap.release()

    # Empty ``issues`` list (rather than a "no issues" sentinel) is
    # the canonical "ok" signal — callers should branch on
    # ``bool(diagnosis["issues"])`` rather than scanning for a magic
    # string.  Keeps the field's meaning consistent: every entry in
    # ``issues`` describes a real problem.

    return diagnosis


def expected_extraction_count(
    video_path: str,
    output_dir: str,
    *,
    frame_pattern: str = DEFAULT_FRAME_PATTERN,
    frame_skip: int = 1,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    overwrite: bool = False,
) -> int:
    """Predict how many frames :func:`video_to_frames` will write.

    Mirrors the same arithmetic + skip-detection logic that
    :func:`video_to_frames` runs internally before decoding, but
    without opening the file for decode (just a metadata probe via
    :func:`diagnose_video_file`).  Useful for CLIs that want to
    show an expected total / ETA / "all cameras will skip" message
    before dispatching the actual extraction.

    Returns ``0`` when:

    * The video file is missing / corrupt / has zero frames.
    * The ``[start_frame, end_frame)`` window has zero size after
      clipping to the source's actual frame count.
    * ``overwrite=False`` and the output directory already holds
      ``>= expected_count`` files matching ``frame_pattern``'s
      extension (i.e. the worker would short-circuit to
      ``STATUS_SKIPPED``).

    Otherwise returns the count of frames the extraction will write
    based on the source's frame count, the trim window, and
    ``frame_skip``.

    :param video_path: Path to the input video file.
    :param output_dir: Path to the per-camera output directory.  Used
        only for the skip-detection check (file count vs expected).
        Does not need to exist.
    :param frame_pattern: ``str.format`` filename pattern; only the
        extension is consulted (for the skip-detection file scan).
    :param frame_skip: Same semantics as :func:`video_to_frames`.
    :param start_frame: Same semantics as :func:`video_to_frames`.
    :param end_frame: Same semantics as :func:`video_to_frames`.
    :param overwrite: When ``True``, skip-detection is bypassed and
        the function returns ``expected_count`` regardless of
        existing files in ``output_dir``.
    :return: Predicted number of frames the extraction will write.
        ``0`` for any case that would result in
        ``STATUS_SKIPPED`` / ``STATUS_FILE_NOT_FOUND`` /
        ``STATUS_INVALID_PROPERTIES`` / etc.
    :rtype: int
    """
    diag = diagnose_video_file(video_path)
    if not diag.get("can_open"):
        return 0
    props = diag.get("properties") or {}
    total_frames = int(props.get("frame_count", 0))
    if total_frames <= 0:
        return 0

    range_end = total_frames if end_frame is None else min(
        end_frame, total_frames
    )
    range_size = max(0, range_end - start_frame)
    skip = max(1, frame_skip)
    expected_count = (range_size + skip - 1) // skip
    if expected_count <= 0:
        return 0

    if not overwrite:
        pattern_ext = os.path.splitext(frame_pattern)[1]
        if pattern_ext and os.path.isdir(output_dir):
            try:
                existing = sum(
                    1 for f in os.listdir(output_dir)
                    if f.endswith(pattern_ext)
                )
            except OSError:
                existing = 0
            if existing >= expected_count:
                return 0   # video_to_frames would short-circuit to SKIPPED

    return expected_count


# ---------------------------------------------------------------------------
# Multiprocess batch wrapper
# ---------------------------------------------------------------------------

def _run_one_job(args: Tuple) -> Tuple[str, str, str]:
    """Worker wrapper around :func:`video_to_frames`.

    Unpacks the ``(video_path, output_dir, kwargs_dict)`` tuple, calls
    the core helper, and returns ``(video_path, output_dir, status)``
    so the parent process can aggregate progress + per-job outcomes.

    Defaults ``progress=False`` so multi-worker tqdm bars don't fight
    on the same terminal — callers can override by setting
    ``progress=True`` explicitly in the per-job kwargs.
    """
    video_path, output_dir, kwargs = args
    kwargs = dict(kwargs)
    kwargs.setdefault("progress", False)
    try:
        status = video_to_frames(video_path, output_dir, **kwargs)
    except Exception as exc:  # noqa: BLE001 — surface anything to the parent
        status = f"exception: {exc!r}"
    return video_path, output_dir, status


def video_to_frames_batch(
    jobs: Iterable[Tuple[str, str]],
    *,
    max_workers: Optional[int] = None,
    progress_logger: Optional[logging.Logger] = None,
    **video_to_frames_kwargs,
) -> List[Tuple[str, str, str]]:
    """Run :func:`video_to_frames` over many videos in parallel.

    Each ``(video_path, output_dir)`` pair becomes one worker job.
    Same per-job kwargs (``frame_skip``, ``start_frame``,
    ``end_frame``, ``frame_pattern``, ``overwrite``) apply to every
    job — consistent with how the CLI surfaces them as
    ``argparse`` flags.

    :param jobs: Iterable of ``(video_path, output_dir)`` tuples.
    :type jobs: Iterable[tuple[str, str]]
    :param max_workers: Forwarded to
        :class:`concurrent.futures.ProcessPoolExecutor`.  ``None``
        lets Python pick a sensible default (CPU count).
    :type max_workers: int or None
    :param progress_logger: Optional logger to receive per-job
        ``"[i/N] <status> <video_path>"`` lines as workers complete.
        Defaults to this module's logger; pass an explicit logger
        configured by the CLI for unified output.
    :type progress_logger: logging.Logger or None
    :param video_to_frames_kwargs: Forwarded verbatim to every
        :func:`video_to_frames` call (``frame_skip``,
        ``start_frame``, ``end_frame``, ``frame_pattern``,
        ``overwrite``).
    :return: List of ``(video_path, output_dir, status)`` tuples in
        completion order.
    :rtype: list[tuple[str, str, str]]
    """
    log = progress_logger or logger
    job_list = [(v, o, dict(video_to_frames_kwargs)) for v, o in jobs]
    if not job_list:
        log.info("video_to_frames_batch: no jobs to run")
        return []

    log.info(
        "video_to_frames_batch: %d jobs, max_workers=%s",
        len(job_list), max_workers if max_workers is not None else "auto",
    )
    results: List[Tuple[str, str, str]] = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_job = {
            executor.submit(_run_one_job, j): j for j in job_list
        }
        for i, future in enumerate(as_completed(future_to_job), start=1):
            video_path, output_dir, status = future.result()
            results.append((video_path, output_dir, status))
            log.info(
                "[%d/%d] %s  %s",
                i, len(job_list), status, video_path,
            )
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
    log.info("video_to_frames_batch summary (%d jobs): %s", len(results), breakdown)
