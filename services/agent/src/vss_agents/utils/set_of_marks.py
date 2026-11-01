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

"""Set-of-marks prompting: numbered person boxes drawn on the clip P1 sees (``INCIDENT_SOM_ENABLED``).

With person tracking on as well, :func:`prepare_marks` runs the perception stack before P1 instead of after it,
numbers the person tracks 1, 2, ... in order of first appearance, draws a box and its number on every frame
(one colour per number), and uploads the marked copy under the caption pass's scratch area
(``scratch/analyze/<video key>/<run id>-marks/``). Its signed URL becomes the P1 ``video_url`` and
:func:`marks_context` is one more user message asking for the mark number in brackets at the end of each person
entity's ``label``. The prompt file, ``inference.json`` and the schema are untouched.

After P1, :func:`apply_marks` reads the number from each entity's label, keeps it only when it names a track that
is present at the entity's ``timestamp_seconds``, and strips the bracketed number from every label, so the stored
report never carries it. Entities without a usable number fall back to the IoU association of
:func:`track_evidence.associate_tracks`. Crops, the zoomed look and stored frames are always cut from the original
clip. Everything here is soft: a failure leaves ``url`` empty with a ``skipped`` reason and P1 runs on the original
clip; the scratch object is deleted by :func:`cleanup_marks` whatever happened.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
import logging
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]
import numpy as np

from vss_agents import incident_contract
from vss_agents.utils import chunk_captions
from vss_agents.utils import track_evidence
from vss_agents.utils.optional_cv2 import ensure_codecs
from vss_agents.utils.r2 import is_r2_configured

logger = logging.getLogger(__name__)

SOM_ENABLED_ENV_VAR = "INCIDENT_SOM_ENABLED"
# A box is drawn on a frame within this of one of its track's samples (build_tracks keeps one every 0.2 s).
DRAW_WINDOW_SECONDS = 0.3
# A mark names a track present at the entity's second when a sample lies within this of it (the second is whole).
MARK_PRESENCE_SECONDS = 1.0
RENDER_TIMEOUT_SECONDS = 300.0
# One colour per mark number (BGR), distinct on dark and light scenes; numbers past ten reuse them in order.
PALETTE: tuple[tuple[int, int, int], ...] = (
    (48, 48, 255),
    (255, 160, 0),
    (0, 220, 0),
    (0, 215, 255),
    (255, 0, 200),
    (255, 255, 0),
    (0, 128, 255),
    (180, 105, 255),
    (255, 255, 255),
    (128, 0, 128),
)

MARKS_PREAMBLE = incident_contract.prompt("p1/marks-preamble")
_MARK_PATTERN = re.compile(r"\s*\[\s*#?\s*(\d{1,4})\s*\]")


def som_enabled() -> bool:
    """Whether set-of-marks prompting is on: off unless ``INCIDENT_SOM_ENABLED`` is truthy (and tracking is on)."""
    return (os.getenv(SOM_ENABLED_ENV_VAR) or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Mark:
    number: int
    track: track_evidence.Track


@dataclass
class MarkedClip:
    """What :func:`prepare_marks` produced: the marked copy's URL (or why there is none) and the run's tracks.

    ``outcome`` is the tracking run when it happened, so the evidence step reuses it instead of a second run.
    """

    url: str | None = None
    marks: list[Mark] = field(default_factory=list)
    outcome: track_evidence.TrackingOutcome | None = None
    skipped: str | None = None
    prefix: str | None = None
    store: chunk_captions.ScratchStore | None = None
    elapsed_ms: float = 0.0

    def telemetry(self) -> dict[str, Any]:
        """The ``tracking.marks`` record before association: whether the copy was rendered, and why not."""
        return {
            "rendered": self.url is not None,
            "parsed": 0,
            "fallback": 0,
            "invalid": 0,
            "skipped": self.skipped,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


def number_tracks(tracks: Sequence[track_evidence.Track]) -> list[Mark]:
    """Marks 1, 2, ... for the tracks with samples, in order of first appearance (track id breaks ties)."""
    ordered = sorted((t for t in tracks if t.samples), key=lambda t: (t.first_seen, t.track_id))
    return [Mark(number=index, track=track) for index, track in enumerate(ordered, start=1)]


def boxes_at(marks: Sequence[Mark], seconds: float) -> list[tuple[int, tuple[int, int, int, int]]]:
    """``(number, box)`` of every mark with a sample within :data:`DRAW_WINDOW_SECONDS` of ``seconds``."""
    drawn: list[tuple[int, tuple[int, int, int, int]]] = []
    for mark in marks:
        track = mark.track
        if seconds < track.first_seen - DRAW_WINDOW_SECONDS or seconds > track.last_seen + DRAW_WINDOW_SECONDS:
            continue
        nearest = min(track.samples, key=lambda sample: abs(sample.t - seconds))
        if abs(nearest.t - seconds) <= DRAW_WINDOW_SECONDS:
            drawn.append((mark.number, nearest.box))
    return drawn


def draw_marks(frame: np.ndarray, boxes: Sequence[tuple[int, Sequence[int]]]) -> None:
    """Draw each numbered ``[top, left, bottom, right]`` (0..1000) box on ``frame`` in place, its tag inside the frame."""
    ensure_codecs(cv2)
    height, width = frame.shape[:2]
    thickness = max(2, round(min(width, height) / 240))
    scale = max(0.5, min(width, height) / 800)
    for number, box in boxes:
        colour = PALETTE[(number - 1) % len(PALETTE)]
        top, left = round(box[0] / 1000 * height), round(box[1] / 1000 * width)
        bottom, right = round(box[2] / 1000 * height), round(box[3] / 1000 * width)
        cv2.rectangle(frame, (left, top), (right, bottom), colour, thickness)
        text = str(number)
        (text_w, text_h), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        tag_w, tag_h = text_w + 2 * thickness + 4, text_h + baseline + 2 * thickness
        tag_left = min(max(0, left), max(0, width - tag_w))
        # Above the box when there is room, else just inside its top edge, so the tag never leaves the frame.
        tag_top = top - tag_h if top - tag_h >= 0 else min(max(0, top), max(0, height - tag_h))
        cv2.rectangle(frame, (tag_left, tag_top), (tag_left + tag_w, tag_top + tag_h), colour, cv2.FILLED)
        ink = (0, 0, 0) if sum(colour) > 382 else (255, 255, 255)
        cv2.putText(
            frame,
            text,
            (tag_left + thickness + 2, tag_top + thickness + text_h),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            ink,
            thickness,
            cv2.LINE_AA,
        )


def render_marked_clip(source: Path, target: Path, marks: Sequence[Mark], ffmpeg: str) -> None:
    """Write ``source`` with every mark drawn to ``target`` (H.264, yuv420p, no audio, same size and frame rate).

    OpenCV decodes and draws; ffmpeg encodes from raw frames on its stdin, so the copy plays wherever the original
    does. Frame ``i`` shows video second ``i / fps``, the time base the tracks and the evidence frames use.
    """
    ensure_codecs(cv2)
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError("OpenCV could not open the clip")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        ok, frame = capture.read()
        if not ok or frame is None or not math.isfinite(fps) or fps <= 0:
            raise RuntimeError("OpenCV could not read the clip's frames")
        height, width = frame.shape[:2]
        with tempfile.TemporaryFile() as errors:
            encoder = subprocess.Popen(
                [
                    ffmpeg,
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "bgr24",
                    "-s",
                    f"{width}x{height}",
                    "-r",
                    f"{fps:.6f}",
                    "-i",
                    "-",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(target),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=errors,
            )
            assert encoder.stdin is not None
            index = 0
            try:
                while ok and frame is not None:
                    if frame.shape[:2] != (height, width):
                        frame = cv2.resize(frame, (width, height))
                    draw_marks(frame, boxes_at(marks, index / fps))
                    encoder.stdin.write(np.ascontiguousarray(frame).tobytes())
                    index += 1
                    ok, frame = capture.read()
                encoder.stdin.close()
                code = encoder.wait(timeout=RENDER_TIMEOUT_SECONDS)
            except BaseException:
                encoder.kill()
                encoder.wait()
                raise
            if code != 0:
                errors.seek(0)
                detail = errors.read().decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
                raise RuntimeError(f"ffmpeg failed encoding the marked copy: {detail[0][:200]}")
    finally:
        capture.release()


def marks_context(marks: Sequence[Mark]) -> str:
    """The P1 context message: the marks' meaning, the label rule and when each number is on screen."""
    spans = "\n".join(
        f"[{mark.number}] on screen {mark.track.first_seen:.1f}s to {mark.track.last_seen:.1f}s" for mark in marks
    )
    return f"{MARKS_PREAMBLE}\n\nNumbers drawn on this video:\n{spans}"


def parse_mark(label: str) -> tuple[int | None, str]:
    """``(number, label without it)``: the last bracketed number of ``label``, and every bracketed number removed."""
    numbers = [int(match.group(1)) for match in _MARK_PATTERN.finditer(label)]
    return (numbers[-1] if numbers else None), _MARK_PATTERN.sub("", label).strip()


def track_present(track: track_evidence.Track, seconds: float) -> bool:
    """Whether ``track`` has a sample within :data:`MARK_PRESENCE_SECONDS` of ``seconds``."""
    return any(abs(sample.t - seconds) <= MARK_PRESENCE_SECONDS for sample in track.samples)


def apply_marks(report: dict[str, Any], marks: Sequence[Mark]) -> tuple[dict[str, track_evidence.Track], int]:
    """Strip the mark from every entity label of ``report`` in place; return ``({entity_id: track}, invalid)``.

    A number is used when it names a mark whose track is present at the entity's ``timestamp_seconds`` and no
    earlier entity took it; ``invalid`` counts the numbers given that were not used.
    """
    by_number = {mark.number: mark.track for mark in marks}
    matched: dict[str, track_evidence.Track] = {}
    taken: set[int] = set()
    invalid = 0
    for entity in report.get("entities") or []:
        label = entity.get("label")
        if not isinstance(label, str):
            continue
        number, entity["label"] = parse_mark(label)
        if number is None:
            continue
        track = by_number.get(number)
        entity_id = str(entity.get("entity_id") or "")
        seconds = entity.get("timestamp_seconds")
        if (
            track is None
            or number in taken
            or not entity_id
            or not isinstance(seconds, (int, float))
            or not track_present(track, float(seconds))
        ):
            invalid += 1
            continue
        matched[entity_id] = track
        taken.add(number)
    return matched, invalid


async def prepare_marks(
    video_url: str,
    *,
    video_key: str,
    run_id: str,
    tracking: track_evidence.TrackingRequest,
    store: chunk_captions.ScratchStore | None = None,
) -> MarkedClip:
    """Track the clip, render the marked copy and upload it to scratch. Never raises.

    The clip is downloaded once and handed to the tracking runner as a local file. Without tracks, ffmpeg or R2,
    or when rendering or upload fails, ``url`` stays ``None`` with a ``skipped`` reason; ``outcome`` is kept
    whenever tracking ran, so the evidence step does not track twice.
    """
    started = time.perf_counter()
    result = MarkedClip()

    def done(skipped: str | None = None) -> MarkedClip:
        result.skipped = skipped
        result.elapsed_ms = (time.perf_counter() - started) * 1000.0
        if skipped:
            logger.warning("set of marks skipped: %s", skipped)
        return result

    ffmpeg = chunk_captions.ffmpeg_path()
    if store is None and not is_r2_configured():
        return done("R2 is not configured on the agent (the marked copy needs R2_* settings)")
    with tempfile.TemporaryDirectory(prefix="incident-marks-") as workdir:
        source = Path(workdir) / "source.mp4"
        try:
            await chunk_captions.download_video(video_url, source)
        except Exception as exc:
            # Never the exception text: it can carry the signed URL.
            return done(f"the clip could not be downloaded ({type(exc).__name__})")
        try:
            result.outcome = await tracking.runner(source, sensor_id=tracking.sensor_id, **tracking.options)
        except Exception as exc:  # run_tracking never raises; a custom runner might
            result.outcome = track_evidence.TrackingOutcome(skipped=f"tracking failed: {type(exc).__name__}")
        if result.outcome.skipped:
            return done(f"no tracks to mark (tracking skipped: {result.outcome.skipped})")
        result.marks = number_tracks(result.outcome.tracks)
        if not result.marks:
            return done("no person tracks to mark")
        if ffmpeg is None:
            return done("ffmpeg is not installed on the agent host")
        target = Path(workdir) / "marked.mp4"
        try:
            await asyncio.to_thread(render_marked_clip, source, target, result.marks, ffmpeg)
        except Exception as exc:
            return done(f"rendering the marked copy failed: {type(exc).__name__}: {exc}")
        active = store or chunk_captions.ScratchStore()
        prefix = chunk_captions.scratch_prefix(video_key, f"{run_id}-marks")
        result.store, result.prefix = active, prefix
        try:
            key = await active.put_video(f"{prefix}marked.mp4", target)
            result.url = await active.signed_url(key)
        except Exception as exc:
            result.url = None
            return done(f"uploading the marked copy failed ({type(exc).__name__})")
    return done()


async def cleanup_marks(marked: MarkedClip | None) -> None:
    """Delete the marked copy's scratch prefix. Never raises."""
    if marked is None or marked.store is None or marked.prefix is None:
        return
    try:
        deleted = await marked.store.delete_prefix(marked.prefix)
        logger.info("set of marks: deleted %d scratch objects under %s", deleted, marked.prefix)
    except Exception as exc:
        logger.warning("set of marks: could not delete the scratch prefix %s: %s", marked.prefix, exc)
