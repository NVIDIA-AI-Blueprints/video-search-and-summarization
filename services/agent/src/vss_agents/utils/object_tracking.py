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

"""Object tracks for instruments and assets (``INCIDENT_OBJECT_TRACKING_ENABLED``, off by default).

People are tracked by the stock DeepStream stack (:mod:`vss_agents.utils.track_evidence`), which only knows
people. An instrument or an asset has no such detector, so each one is followed from the box the locate step
found for it: a single-object OpenCV tracker is seeded with that box at the item's second and run forwards and
backwards through the clip at a few frames a second. A tracker drifts, so every few seconds the open-vocabulary
detector (the one that located the item) is asked for the item's label on the current frame:

- a detection that overlaps the tracked box (IoU at least :attr:`ObjectTrackingSettings.reanchor_iou`) re-anchors
  the tracker and the box on the detector's box, and the sample counts as confirmed (its score is the detector's);
- no such detection is a miss. A track segment ends at the first miss when the tracker itself has failed (see
  below), keeping what the tracker followed until then, and after :attr:`ObjectTrackingSettings.max_misses` misses
  in a row when it has not, dropping the unconfirmed samples after the last confirmed one (the tracker may have
  drifted off the item since).

Which trackers exist depends on the OpenCV build. CSRT and KCF need ``opencv-contrib``; ``opencv-python-headless``
(the dependency) ships only MIL (plus the model-based Vit, Nano, DaSiamRPN and GOTURN trackers, which need model
files). :func:`select_tracker` takes the best one present. MIL never reports failure, it keeps returning a drifting
box, so a tracker is also treated as failed when its box leaves the frame (less than a quarter of it visible),
collapses or changes area by more than a factor of :data:`MAX_AREA_CHANGE`; otherwise the detector's misses alone
end a segment.

The result is a :class:`vss_agents.utils.track_evidence.Track` per item, in the shape the person tracks have
(samples with video seconds and boxes; tracker-only samples score ``-0.1``), marked with
``source=OBJECT_TRACK_SOURCE``. Every detector call counts against the request's ``CropCallBudget``, the whole run
has a time cap, and nothing here raises to the analysis: an item that cannot be tracked just has no track.
"""

import asyncio
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
import logging
import math
import os
import time
from typing import Any

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]
import numpy as np

from vss_agents.utils.crop_evidence import CropCallCapReachedError
from vss_agents.utils.crop_evidence import parse_detections
from vss_agents.utils.optional_cv2 import ensure_codecs
from vss_agents.utils.track_evidence import OBJECT_TRACK_SOURCE
from vss_agents.utils.track_evidence import ObjectSeed
from vss_agents.utils.track_evidence import ObjectTrackingOutcome
from vss_agents.utils.track_evidence import Sample
from vss_agents.utils.track_evidence import Track
from vss_agents.utils.track_evidence import box_iou

logger = logging.getLogger(__name__)

ENABLED_ENV_VAR = "INCIDENT_OBJECT_TRACKING_ENABLED"
SAMPLE_FPS_ENV_VAR = "INCIDENT_OBJECT_TRACKING_FPS"
DETECT_INTERVAL_ENV_VAR = "INCIDENT_OBJECT_TRACKING_DETECT_INTERVAL_SECONDS"
MIN_IOU_ENV_VAR = "INCIDENT_OBJECT_TRACKING_MIN_IOU"
SPAN_ENV_VAR = "INCIDENT_OBJECT_TRACKING_SPAN_SECONDS"
TIME_CAP_ENV_VAR = "INCIDENT_OBJECT_TRACKING_MAX_SECONDS"
DETECTOR_CALLS_ENV_VAR = "INCIDENT_OBJECT_TRACKING_MAX_DETECTOR_CALLS"

DEFAULT_SAMPLE_FPS = 4.0
DEFAULT_DETECT_INTERVAL_SECONDS = 2.5
DEFAULT_MIN_IOU = 0.3
DEFAULT_SPAN_SECONDS = 12.0
DEFAULT_TIME_CAP_SECONDS = 120.0
DEFAULT_MAX_DETECTOR_CALLS = 80

# Frames are scaled down to this longest edge for the tracker (the detector resizes on its own).
MAX_FRAME_EDGE = 960
# The tracker is treated as failed when less of its box than this is inside the frame, or when its area is more than
# this many times larger or smaller than at the last anchor.
MIN_VISIBLE_FRACTION = 0.25
MAX_AREA_CHANGE = 6.0
MIN_SIDE = 4.0  # on the 0..1000 scale
# A tracker-only sample, as in the person tracks (detector scores are never negative).
TRACKED_SCORE = -0.1
# The first frame of a pass must be this close to the seed's second, or the clip could not be read there.
SEED_FRAME_TOLERANCE_SECONDS = 1.0

TrackerFactory = Callable[[], Any]
Detect = Callable[[np.ndarray, str], Any]


def object_tracking_enabled() -> bool:
    """Whether object tracking runs: off unless ``INCIDENT_OBJECT_TRACKING_ENABLED`` is truthy."""
    return (os.getenv(ENABLED_ENV_VAR) or "").strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float((os.getenv(name) or "").strip())
    except ValueError:
        return default
    return value if math.isfinite(value) and value > minimum else default


@dataclass(frozen=True)
class ObjectTrackingSettings:
    """Rates, thresholds and caps of a run (the environment sets them; tests set them directly)."""

    sample_fps: float = DEFAULT_SAMPLE_FPS
    detect_interval_seconds: float = DEFAULT_DETECT_INTERVAL_SECONDS
    reanchor_iou: float = DEFAULT_MIN_IOU
    span_seconds: float = DEFAULT_SPAN_SECONDS  # how far each way from the seed an item is followed
    time_cap_seconds: float = DEFAULT_TIME_CAP_SECONDS  # for the whole request
    max_detector_calls: int = DEFAULT_MAX_DETECTOR_CALLS  # for the whole request
    max_misses: int = 2  # detector checks in a row without agreement before a segment ends
    concurrency: int = 3  # items followed at once
    max_edge: int = MAX_FRAME_EDGE

    @classmethod
    def from_env(cls) -> "ObjectTrackingSettings":
        return cls(
            sample_fps=_env_float(SAMPLE_FPS_ENV_VAR, DEFAULT_SAMPLE_FPS),
            detect_interval_seconds=_env_float(DETECT_INTERVAL_ENV_VAR, DEFAULT_DETECT_INTERVAL_SECONDS),
            reanchor_iou=_env_float(MIN_IOU_ENV_VAR, DEFAULT_MIN_IOU),
            span_seconds=_env_float(SPAN_ENV_VAR, DEFAULT_SPAN_SECONDS),
            time_cap_seconds=_env_float(TIME_CAP_ENV_VAR, DEFAULT_TIME_CAP_SECONDS),
            max_detector_calls=int(_env_float(DETECTOR_CALLS_ENV_VAR, DEFAULT_MAX_DETECTOR_CALLS, minimum=0.5)),
        )


# --- the tracker --------------------------------------------------------------------------------------------------


class TrackerUnavailableError(RuntimeError):
    """The installed OpenCV has no usable single-object tracker (``str(exc)`` is the ``skipped`` reason)."""


def select_tracker() -> tuple[str, TrackerFactory]:
    """The best single-object tracker the installed OpenCV has: CSRT, then KCF (both contrib), then MIL."""
    if cv2 is None:
        raise TrackerUnavailableError("OpenCV is not installed")
    legacy = getattr(cv2, "legacy", None)
    for name in ("CSRT", "KCF", "MIL"):
        for owner in (legacy, cv2):
            factory = getattr(owner, f"Tracker{name}_create", None) if owner is not None else None
            if factory is not None:
                return name, factory
    raise TrackerUnavailableError("the installed OpenCV has no CSRT, KCF or MIL tracker")


def _pixel_rect(box: Sequence[float], width: int, height: int) -> tuple[int, int, int, int]:
    """A 0..1000 ``[ymin, xmin, ymax, xmax]`` box as the ``(x, y, w, h)`` pixel rectangle a tracker takes."""
    x0, y0 = round(box[1] * width / 1000.0), round(box[0] * height / 1000.0)
    x1, y1 = round(box[3] * width / 1000.0), round(box[2] * height / 1000.0)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def _area(box: Sequence[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _clip(box: Sequence[float]) -> list[float]:
    return [min(1000.0, max(0.0, value)) for value in box]


def _int_box(box: Sequence[float]) -> tuple[int, int, int, int] | None:
    """``box`` clipped to the frame and rounded; None when nothing sensible is left of it."""
    ymin, xmin, ymax, xmax = (round(value) for value in _clip(box))
    return (ymin, xmin, ymax, xmax) if ymin < ymax and xmin < xmax else None


class _Tracker:
    """One OpenCV tracker anchored on a box; ``update`` returns the new box and whether it can be trusted."""

    def __init__(self, factory: TrackerFactory) -> None:
        self._factory = factory
        self._tracker: Any = None
        self.anchor_area = 0.0

    def anchor(self, frame: np.ndarray, box: Sequence[float]) -> None:
        """(Re)start the tracker on ``box`` of ``frame`` (a new tracker each time, OpenCV trackers do not re-init well)."""
        height, width = frame.shape[:2]
        self._tracker = self._factory()
        self._tracker.init(frame, _pixel_rect(box, width, height))
        self.anchor_area = _area(box)

    def update(self, frame: np.ndarray) -> tuple[list[float] | None, bool]:
        """``(box, ok)``: the tracker's box clipped to the frame (None when it has none) and whether it failed."""
        height, width = frame.shape[:2]
        found, rect = self._tracker.update(frame)
        if not found:
            return None, False
        x, y, w, h = (float(value) for value in rect)
        raw = [y * 1000.0 / height, x * 1000.0 / width, (y + h) * 1000.0 / height, (x + w) * 1000.0 / width]
        raw_area = _area(raw)
        clipped = _clip(raw)
        if (
            not all(math.isfinite(value) for value in raw)
            or raw_area <= 0
            or _area(clipped) < MIN_VISIBLE_FRACTION * raw_area
            or min(clipped[2] - clipped[0], clipped[3] - clipped[1]) < MIN_SIDE
            or not (1 / MAX_AREA_CHANGE <= raw_area / max(self.anchor_area, 1e-9) <= MAX_AREA_CHANGE)
        ):
            return None, False
        return clipped, True


# --- reading the clip ---------------------------------------------------------------------------------------------


class ClipReader:
    """Sampled frames of a clip around a second, through the same OpenCV decoder the evidence frames use."""

    def __init__(self, video_ref: str | os.PathLike[str], max_edge: int = MAX_FRAME_EDGE) -> None:
        ensure_codecs(cv2)
        self._cap = cv2.VideoCapture(str(video_ref))
        self.max_edge = max_edge
        self.fps = float(self._cap.get(cv2.CAP_PROP_FPS)) if self._cap.isOpened() else 0.0
        self.frame_count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT)) if self._cap.isOpened() else 0

    @property
    def readable(self) -> bool:
        return self._cap.isOpened() and self.fps > 0 and self.frame_count > 0

    def close(self) -> None:
        self._cap.release()

    def _fit(self, frame: np.ndarray) -> np.ndarray:
        edge = max(frame.shape[:2])
        if edge <= self.max_edge:
            return frame
        scale = self.max_edge / edge
        return np.asarray(cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))

    def _read(self, indices: Sequence[int]) -> Iterator[tuple[float, np.ndarray]]:
        """The frames at ``indices`` (ascending), decoded in one sequential run from the first."""
        if not indices:
            return
        wanted = set(indices)
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, indices[0])
        for index in range(indices[0], indices[-1] + 1):
            if not self._cap.grab():
                return
            if index in wanted:
                ok, frame = self._cap.retrieve()
                if ok and frame is not None and frame.size:
                    yield index / self.fps, self._fit(frame)

    def frames(
        self, seconds: float, *, forward: bool, sample_fps: float, span_seconds: float
    ) -> Iterator[tuple[float, np.ndarray]]:
        """``(video second, frame)`` from the frame at ``seconds`` onwards (forward) or backwards, at ``sample_fps``.

        The first frame is the one at ``seconds`` (what the seed was found on). Backwards, the window is decoded
        forward into memory and then yielded in reverse, which is the only way a compressed clip can be read.
        """
        if not self.readable:
            return
        step = max(1, round(self.fps / sample_fps))
        seed_index = min(self.frame_count - 1, max(0, math.floor(seconds * self.fps)))
        span = int(span_seconds * self.fps)
        if forward:
            yield from self._read(range(seed_index, min(self.frame_count - 1, seed_index + span) + 1, step))
        else:
            first = max(0, seed_index - span)
            yield from reversed(list(self._read(sorted(range(seed_index, first - 1, -step)))))


# --- following one item -------------------------------------------------------------------------------------------


@dataclass
class _Pass:
    """What one direction of one item produced: samples in the order of travel (the seed's own is not among them)."""

    samples: list[Sample] = field(default_factory=list)
    detector_calls: int = 0
    detector_errors: int = 0
    reanchors: int = 0
    lost: bool = False  # the segment ended because the item was lost, not because the clip or the span did
    capped: str | None = None  # "time" or "detector-budget" when a cap stopped the pass


def _agreeing(raw: Any, reference: Sequence[float], label: str, min_iou: float) -> tuple[tuple[int, ...], float] | None:
    """The detection overlapping ``reference`` most (at least ``min_iou``) as ``(clipped box, score)``."""
    best: tuple[tuple[int, ...], float] | None = None
    best_iou = min_iou
    for detection in parse_detections(raw, label):
        box = _int_box(detection.box) if len(detection.box) == 4 else None
        if box is None:
            continue
        iou = box_iou(box, reference)
        if iou >= best_iou:
            best, best_iou = (box, float(detection.score)), iou
    return best


def follow(
    frames: Iterator[tuple[float, np.ndarray]],
    seed: ObjectSeed,
    *,
    detect: Detect,
    settings: ObjectTrackingSettings,
    tracker_factory: TrackerFactory,
    deadline: float,
) -> _Pass:
    """Follow ``seed`` through ``frames`` (the first is the seed's own, the rest in the order of travel)."""
    result = _Pass()
    first = next(frames, None)
    if first is None or abs(first[0] - seed.seconds) > SEED_FRAME_TOLERANCE_SECONDS:
        return result
    tracker = _Tracker(tracker_factory)
    tracker.anchor(first[1], seed.box)
    last_box: Sequence[float] = seed.box
    last_check = seed.seconds
    confirmed = 0  # how many samples are known good: up to the last one the detector agreed with
    misses = 0
    for second, frame in frames:
        if time.monotonic() >= deadline:
            result.capped = "time"
            break
        box, ok = tracker.update(frame)
        due = abs(second - last_check) >= settings.detect_interval_seconds
        agreed: tuple[tuple[int, ...], float] | None = None
        checked = False
        if due or not ok:
            last_check = second
            try:
                raw = detect(frame, seed.label)
                result.detector_calls += 1
                checked = True
                agreed = _agreeing(raw, box if ok and box is not None else last_box, seed.label, settings.reanchor_iou)
            except CropCallCapReachedError:
                result.capped = "detector-budget"
                break
            except Exception as exc:
                logger.warning("Object tracking detector call failed for '%s': %s", seed.label, type(exc).__name__)
                result.detector_calls += 1
                result.detector_errors += 1
        if agreed is not None:
            agreed_box = agreed[0]
            result.samples.append(
                Sample(second, (agreed_box[0], agreed_box[1], agreed_box[2], agreed_box[3]), agreed[1])
            )
            tracker.anchor(frame, agreed_box)
            result.reanchors += 1
            last_box = agreed_box
            confirmed = len(result.samples)
            misses = 0
            continue
        if checked:
            misses += 1
        # Lost: the tracker failed and the detector did not confirm the item (a failed tracker forces a check), or
        # the detector found nothing where the tracker still saw a box, several checks in a row.
        tracked = _int_box(box) if ok and box is not None else None
        if tracked is None or misses >= settings.max_misses:
            result.lost = True
            if tracked is not None:  # ended by misses: the tracker may have drifted since the last agreement
                del result.samples[confirmed:]
            break
        result.samples.append(Sample(second, tracked, TRACKED_SCORE))
        last_box = tracked
    return result


def track_item(
    video_ref: str | os.PathLike[str],
    seed: ObjectSeed,
    *,
    detect: Detect,
    settings: ObjectTrackingSettings,
    tracker_factory: TrackerFactory,
    deadline: float,
) -> tuple[Track | None, dict[str, Any]]:
    """Follow one item forwards and backwards from its seed: its track (None when it gained no sample) and telemetry."""
    started = time.monotonic()
    passes: dict[str, _Pass] = {}
    clip = ClipReader(video_ref, settings.max_edge)
    try:
        for name, forward in (("forward", True), ("backward", False)):
            frames = clip.frames(
                seed.seconds, forward=forward, sample_fps=settings.sample_fps, span_seconds=settings.span_seconds
            )
            passes[name] = follow(
                frames, seed, detect=detect, settings=settings, tracker_factory=tracker_factory, deadline=deadline
            )
    finally:
        clip.close()
    ahead, behind = passes["forward"], passes["backward"]
    seed_sample = Sample(seed.seconds, seed.box, seed.score)
    samples = [*reversed(behind.samples), seed_sample, *ahead.samples]
    capped = ahead.capped or behind.capped
    telemetry: dict[str, Any] = {
        "samples": len(samples),
        "forward_samples": len(ahead.samples),
        "backward_samples": len(behind.samples),
        "detector_calls": ahead.detector_calls + behind.detector_calls,
        "detector_errors": ahead.detector_errors + behind.detector_errors,
        "reanchors": ahead.reanchors + behind.reanchors,
        "lost": int(ahead.lost) + int(behind.lost),
        "capped": capped,
        "elapsed_ms": round((time.monotonic() - started) * 1000.0, 1),
    }
    if len(samples) < 2:
        telemetry["samples"] = 0
        return None, telemetry
    telemetry["first_seen_seconds"] = samples[0].t
    telemetry["last_seen_seconds"] = samples[-1].t
    return Track(track_id=f"object-{seed.item_id}", samples=samples, source=OBJECT_TRACK_SOURCE), telemetry


# --- the run ------------------------------------------------------------------------------------------------------


async def run_object_tracking(
    video_ref: str | os.PathLike[str] | np.ndarray,
    seeds: Sequence[ObjectSeed],
    *,
    detector: Any,
    settings: ObjectTrackingSettings | None = None,
    tracker_factory: TrackerFactory | None = None,
) -> ObjectTrackingOutcome:
    """Follow every seed through the clip. Never raises: a problem is a ``skipped`` reason or an item without a track.

    ``detector`` is the request's detector under its ``CropCallBudget`` (``detect(frame, query)``); the run's items
    share that budget and one time cap (:attr:`ObjectTrackingSettings.time_cap_seconds`).
    """
    started = time.monotonic()
    outcome = ObjectTrackingOutcome()
    active = settings or ObjectTrackingSettings.from_env()
    if isinstance(video_ref, np.ndarray):
        outcome.skipped = "object tracking needs a video file or URL"
        return outcome
    try:
        if tracker_factory is not None:
            outcome.tracker, factory = "custom", tracker_factory
        else:
            outcome.tracker, factory = select_tracker()
    except TrackerUnavailableError as exc:
        outcome.skipped = str(exc)
        return outcome
    detect: Detect = detector.detect if hasattr(detector, "detect") else detector
    deadline = started + active.time_cap_seconds
    semaphore = asyncio.Semaphore(max(1, active.concurrency))

    async def _one(seed: ObjectSeed) -> None:
        async with semaphore:
            if time.monotonic() >= deadline:
                outcome.items[seed.item_id] = {"samples": 0, "capped": "time"}
                return
            try:
                track, telemetry = await asyncio.to_thread(
                    track_item,
                    video_ref,
                    seed,
                    detect=detect,
                    settings=active,
                    tracker_factory=factory,
                    deadline=deadline,
                )
            except Exception as exc:
                logger.warning("Object tracking failed for %s: %s", seed.item_id, type(exc).__name__)
                outcome.items[seed.item_id] = {"samples": 0, "error": type(exc).__name__}
                return
            outcome.items[seed.item_id] = telemetry
            if track is not None:
                outcome.tracks[seed.item_id] = track

    await asyncio.gather(*(_one(seed) for seed in seeds))
    outcome.time_cap_reached = any(item.get("capped") == "time" for item in outcome.items.values())
    outcome.elapsed_ms = (time.monotonic() - started) * 1000.0
    return outcome
