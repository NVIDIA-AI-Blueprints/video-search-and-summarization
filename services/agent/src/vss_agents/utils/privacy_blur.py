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

"""Privacy blur for stored incident evidence (``blur_faces`` on ``/analyze``).

No face model, and no dependence on person tracking: every stored frame is searched for heads on its own.

1. **Whole-frame head detection**: one open-vocabulary detector call per distinct frame second asks for every human
   head or face in the unblurred frame (:data:`HEADS_QUERY`; ``DefaultDetector.detect_all`` returns all of them). The
   detected heads are blurred wherever they are, for person and non-person items alike, so a wrong or missing track
   link or a model box on the wrong thing cannot leave a face showing.
2. **Known people, as additional regions** (a union, so a detector miss is still covered where the analysis knows a
   person): the head region (:func:`head_region`, the top :data:`HEAD_FRACTION` of the box, padded) of every person
   track's visible sample within :data:`TRACK_WINDOW_SECONDS` of the frame's second (all tracks, not only those
   matched to a report entity; tracker-only boxes that drift away from the last real detection are not blurred), and
   the head region of every located person item's own box at that second.

The same detection also runs on frames sampled every :data:`SAMPLE_STEP_SECONDS` over each item's evidence-clip window
(:func:`clip_sample_times`); the heads are stored beside the item (``<itemId>-heads.json``, :func:`heads_json`) and
the console blurs the clip on them second by second.

Detector calls draw on a dedicated per-request budget (:meth:`PrivacySummary.take_call`, :func:`call_cap`), never the
report's ``CropCallBudget``. When a frame's detection fails or the budget is spent, the frame is blurred on the known
person regions only (a fallback, counted); with none known the item stores no image at all (fail closed). A false blur
(a face on a screen, a poster) is accepted; a missed face is not.

Every stored frame is blurred on all those heads, and the crop is cut from the blurred frame. The blur is a strong
pixelation (:data:`PIXEL_BLOCKS` blocks across the region). It runs on the decoded frame before the JPEG is encoded, so
no unblurred head reaches storage; the zoomed second look and every model call still see the unblurred image.
"""

from collections.abc import Iterable
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
import json
import math
import os
from typing import Any

import cv2
import numpy as np

from vss_agents.utils import track_evidence

Box = tuple[int, int, int, int]  # [ymin, xmin, ymax, xmax] normalised to 0..1000

# The head is the top part of a person box: 30 % of its height, widened and raised by 10 % of the box so a head at
# the box edge (a detector box often cuts the crown) is still covered.
HEAD_FRACTION = 0.3
HEAD_PADDING = 0.1
# A detector head box is padded by a quarter of its own size on every side.
DETECTED_HEAD_PADDING = 0.25
# A track sample counts for a frame when it is this close to the frame's second (the tracks hold five samples a second).
TRACK_WINDOW_SECONDS = 0.5
# The blurred region is reduced to at most this many blocks across its longer side, then scaled back.
PIXEL_BLOCKS = 6
HEADS_QUERY = "every human head or face"
# Detector calls one request may spend on whole-frame head detection (stored frames and clip samples), and the
# environment variable that overrides the default.
CALL_CAP_ENV = "INCIDENT_BLUR_DETECTOR_CALL_CAP"
DEFAULT_CALL_CAP = 200
# Clip heads are sampled on a grid of this many seconds over each item's clip window. 2 fps: the console blurs a
# second on the union of the samples around it, so a head that moves between samples stays covered.
SAMPLE_STEP_SECONDS = 0.5
# The console's evidence clip: this many seconds centred on the item's second (``CLIP_SECONDS`` in its evidence-clip.ts).
CLIP_SECONDS = 4.0


def _clamp(value: float) -> int:
    return max(0, min(1000, round(value)))


def valid_box(box: Sequence[float]) -> bool:
    """Four coordinates with a positive height and width."""
    return len(box) == 4 and box[0] < box[2] and box[1] < box[3]


def pad_box(box: Sequence[float], fraction: float) -> Box:
    """``box`` grown by ``fraction`` of its own height and width on every side, clamped to 0..1000."""
    ymin, xmin, ymax, xmax = box
    dy, dx = (ymax - ymin) * fraction, (xmax - xmin) * fraction
    return (_clamp(ymin - dy), _clamp(xmin - dx), _clamp(ymax + dy), _clamp(xmax + dx))


def head_region(box: Sequence[float]) -> Box:
    """The head region of a person ``box``: its top :data:`HEAD_FRACTION`, padded by :data:`HEAD_PADDING`.

    The region is raised and widened by ``HEAD_PADDING`` of the box's height and width, and clamped to the frame.
    """
    ymin, xmin, ymax, xmax = box
    height, width = ymax - ymin, xmax - xmin
    return (
        _clamp(ymin - HEAD_PADDING * height),
        _clamp(xmin - HEAD_PADDING * width),
        _clamp(ymin + HEAD_FRACTION * height),
        _clamp(xmax + HEAD_PADDING * width),
    )


def centre_inside(region: Sequence[float], box: Sequence[float]) -> bool:
    """Whether the centre of ``region`` lies inside ``box`` (edges included)."""
    cy, cx = (region[0] + region[2]) / 2, (region[1] + region[3]) / 2
    return box[0] <= cy <= box[2] and box[1] <= cx <= box[3]


def overlaps(a: Sequence[float], b: Sequence[float]) -> bool:
    """Whether two boxes share any area."""
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def track_heads_at(
    tracks: Iterable[track_evidence.Track],
    seconds: float,
    window: float = TRACK_WINDOW_SECONDS,
    solid_only: bool = False,
) -> list[Box]:
    """The head region of every track's visible sample nearest to ``seconds``, for tracks with one within ``window``.

    Visible samples are real detections and tracker-only boxes soon after one (:func:`track_evidence.visible_samples`):
    a ghost track's drifting boxes blur nothing. With ``solid_only`` only solid tracks count
    (:func:`track_evidence.is_solid`), which is what decides whether a person's head is known from the tracks.
    """
    heads: list[Box] = []
    for track in tracks:
        if solid_only and not track_evidence.is_solid(track):
            continue
        samples = track_evidence.visible_samples(track)
        if not samples:
            continue
        nearest = min(samples, key=lambda sample: abs(sample.t - seconds))
        if abs(nearest.t - seconds) <= window and valid_box(nearest.box):
            heads.append(head_region(nearest.box))
    return heads


def unique_regions(regions: Iterable[Sequence[float]]) -> list[Box]:
    """Valid regions in first-seen order, without exact duplicates."""
    seen: dict[Box, None] = {}
    for region in regions:
        if len(region) == 4:
            box: Box = (_clamp(region[0]), _clamp(region[1]), _clamp(region[2]), _clamp(region[3]))
            if valid_box(box):
                seen.setdefault(box, None)
    return list(seen)


def pixel_box(region: Sequence[float], frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """A normalised region as clamped pixel ``(y0, x0, y1, x1)``, rounded outwards so the region is fully covered."""
    y0 = max(0, math.floor(region[0] * frame_h / 1000.0))
    x0 = max(0, math.floor(region[1] * frame_w / 1000.0))
    y1 = min(frame_h, math.ceil(region[2] * frame_h / 1000.0))
    x1 = min(frame_w, math.ceil(region[3] * frame_w / 1000.0))
    return y0, x0, y1, x1


def _pixelate(patch: np.ndarray) -> np.ndarray:
    height, width = patch.shape[:2]
    block = max(1, math.ceil(max(height, width) / PIXEL_BLOCKS))
    small = (max(1, math.ceil(width / block)), max(1, math.ceil(height / block)))
    reduced = cv2.resize(patch, small, interpolation=cv2.INTER_AREA)
    return np.asarray(cv2.resize(reduced, (width, height), interpolation=cv2.INTER_NEAREST))


def blur_frame(frame: np.ndarray, regions: Iterable[Sequence[float]]) -> np.ndarray:
    """A copy of ``frame`` with every normalised region pixelated; ``frame`` itself is never changed."""
    blurred = frame.copy()
    frame_h, frame_w = frame.shape[:2]
    for region in regions:
        y0, x0, y1, x1 = pixel_box(region, frame_w, frame_h)
        if y0 < y1 and x0 < x1:
            blurred[y0:y1, x0:x1] = _pixelate(np.ascontiguousarray(blurred[y0:y1, x0:x1]))
    return blurred


def call_cap() -> int:
    """The per-request detector-call cap: :data:`CALL_CAP_ENV` when it is a positive integer, else :data:`DEFAULT_CALL_CAP`."""
    try:
        value = int(os.getenv(CALL_CAP_ENV, ""))
    except ValueError:
        return DEFAULT_CALL_CAP
    return value if value > 0 else DEFAULT_CALL_CAP


def clip_window(centre: float, duration: float | None) -> tuple[float, float]:
    """The console's clip ``(start, length)`` for an item at ``centre`` (``clipWindow`` in its evidence-clip.ts).

    :data:`CLIP_SECONDS` centred on ``centre``, shifted to stay inside a video of ``duration`` seconds (``None``: the
    start is only clamped at 0); a video shorter than the clip is clipped whole.
    """
    length = CLIP_SECONDS if duration is None else min(CLIP_SECONDS, duration)
    latest_start = math.inf if duration is None else duration - length
    start = max(0.0, min(centre - CLIP_SECONDS / 2, latest_start))
    return round(start, 3), round(length, 3)


def clip_sample_times(centre: float, duration: float | None) -> list[float]:
    """Video seconds to look for heads at for the clip of an item at ``centre``, nearest to ``centre`` first.

    The grid points of :data:`SAMPLE_STEP_SECONDS` from one step before the clip window to one step after it (the
    console blurs a second on the samples within one step of it), inside the video.
    """
    start, length = clip_window(centre, duration)
    first = math.ceil((start - SAMPLE_STEP_SECONDS) / SAMPLE_STEP_SECONDS - 1e-9)
    last = math.floor((start + length + SAMPLE_STEP_SECONDS) / SAMPLE_STEP_SECONDS + 1e-9)
    times = [k * SAMPLE_STEP_SECONDS for k in range(max(0, first), last + 1)]
    if duration is not None:
        times = [t for t in times if t < duration]
    return sorted(times, key=lambda t: (abs(t - centre), t))


def heads_json(samples: Iterable[tuple[float, Sequence[Box], bool]]) -> bytes:
    """The heads file of one item (``<itemId>-heads.json``): ``{step_seconds, samples: [{t, boxes, fallback?}]}``.

    ``boxes`` are the regions to blur at video second ``t``, normalised ``[ymin, xmin, ymax, xmax]``; ``fallback`` is
    set on a sample whose detection failed, whose boxes are then the known person heads only.
    """
    rows = []
    for t, boxes, fallback in sorted(samples, key=lambda sample: sample[0]):
        row: dict[str, Any] = {"t": round(t, 3), "boxes": [list(box) for box in boxes]}
        if fallback:
            row["fallback"] = True
        rows.append(row)
    return json.dumps({"step_seconds": SAMPLE_STEP_SECONDS, "samples": rows}, separators=(",", ":")).encode()


@dataclass
class PrivacySummary:
    """What ``/analyze`` reports about the privacy blur (``privacy``) of a request that asked for it."""

    blur_faces: bool = True
    regions: int = 0  # head regions blurred, counted per stored frame
    frames: int = 0  # stored frames that had at least one region blurred
    head_detections: int = 0  # heads the whole-frame detections found (per distinct second looked at)
    detector_calls: int = 0  # whole-frame head detection calls made
    call_cap: int = field(default_factory=call_cap)
    cap_refused: int = 0  # detections not made because the call cap was spent
    clip_samples: int = 0  # extra seconds looked at only for the evidence clips
    fallbacks: int = 0  # seconds whose detection failed or was refused: blurred on the known person heads only
    failed: int = 0  # items whose images were dropped because the blur failed (never stored unblurred)

    def take_call(self) -> bool:
        """Count one detector call against the cap; False (and counted as refused) when the cap is spent."""
        if self.detector_calls >= self.call_cap:
            self.cap_refused += 1
            return False
        self.detector_calls += 1
        return True

    def refund_call(self) -> None:
        """Give back a call taken for a frame that could not be read, so nothing was sent."""
        self.detector_calls = max(0, self.detector_calls - 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blur_faces": self.blur_faces,
            "regions": self.regions,
            "frames": self.frames,
            "head_detections": self.head_detections,
            "detector_calls": self.detector_calls,
            "call_cap": self.call_cap,
            "cap_refused": self.cap_refused,
            "clip_samples": self.clip_samples,
            "fallbacks": self.fallbacks,
            "failed": self.failed,
        }
