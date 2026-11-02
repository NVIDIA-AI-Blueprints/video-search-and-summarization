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

"""Person tracks for incident evidence, from the stock VSS perception stack.

After the VLM report and the open-vocabulary locate step, :func:`run_tracking` pushes the clip through the stack
(:mod:`vss_agents.utils.perception_client`), groups the per-frame ``Person`` boxes into tracks, and
:func:`associate_tracks` matches each located entity of the report to the track whose box overlaps the entity's
box. :func:`first_seen_sample` then picks the sample to re-cut the evidence image at (the track's first). Everything here is soft: a missing
binary, a stack that is down or a stream that never produces a frame returns a ``skipped`` reason and never raises
to the analysis.

Time base (measured by the Phase 0 scout): DeepStream numbers frames from the first frame it decodes and loses the
first ~85 to 91 frames of every replay, so frame ``n`` shows video second ``(n + loss) / fps``, where ``loss`` is
``clip_frames - last_frame_id`` once the stream reached the end of the file, else the 2.85 s measured on every clip.
At the end of the file DeepStream reconnects and replays the clip with the counter reset, so the read is cut at the
first frame-id decrease.
"""

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Coroutine
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import TYPE_CHECKING
from typing import Any

import httpx

from vss_agents.utils.perception_client import Frame
from vss_agents.utils.perception_client import HttpPerceptionClient
from vss_agents.utils.perception_client import PerceptionClient

if TYPE_CHECKING:
    from vss_agents.utils.crop_evidence import CropCallBudget

logger = logging.getLogger(__name__)

TRACKING_ENABLED_ENV_VAR = "INCIDENT_TRACKING_ENABLED"
MIN_IOU_ENV_VAR = "INCIDENT_TRACKING_MIN_IOU"
END_MARGIN_ENV_VAR = "INCIDENT_TRACKING_END_MARGIN_SECONDS"
DEFAULT_MIN_IOU = 0.3
DEFAULT_END_MARGIN_SECONDS = 20.0

PERSON_TYPE = "Person"
# Where a track came from (``Track.source``): the stock DeepStream detector for people, or the seeded OpenCV tracker of
# :mod:`vss_agents.utils.object_tracking` for instruments and assets. Only a track of another source is marked on its
# evidence entry (``track_source``), so an entry without the field is a person track, as it always was.
PERSON_TRACK_SOURCE = "deepstream"
OBJECT_TRACK_SOURCE = "object-tracker"
NO_FRAMES_TIMEOUT_SECONDS = 40.0
POLL_INTERVAL_SECONDS = 2.0
# After the last frames appear, wait for the indexer (Logstash flushes every few seconds) before reading.
SETTLE_SECONDS = 3.0
# The stream has reached the end of the file when its last frame id is within this many frames of the clip's.
END_REACHED_WITHIN_FRAMES = 100
FALLBACK_LOSS_SECONDS = 2.85
SAMPLES_PER_SECOND = 5
ASSOCIATION_WINDOW_SECONDS = 0.5
# A track backs an entity only when it is solid: at least this many real detections, and at
# least this share of its samples real. A ghost is one detection followed by tracker-only boxes drifting away.
MIN_REAL_SAMPLES = 2
MIN_REAL_SHARE = 0.5
# A tracker-only box counts as seen only within this long of the latest real detection before it; later ones are drift.
PREDICTED_GRACE_SECONDS = 0.6
DOWNLOAD_TIMEOUT_SECONDS = 600.0
_SENSOR_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
_HDR_TRANSFERS = frozenset({"arib-std-b67", "smpte2084"})

SDR_FILTER = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv,format=yuv420p"


def tracking_enabled() -> bool:
    """Whether tracking runs: off unless ``INCIDENT_TRACKING_ENABLED`` is truthy."""
    return (os.getenv(TRACKING_ENABLED_ENV_VAR) or "").strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        value = float((os.getenv(name) or "").strip())
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0 else default


@dataclass(frozen=True)
class TrackingSettings:
    """Thresholds and timings of a tracking run (the environment sets the first two; tests set the rest)."""

    min_iou: float = DEFAULT_MIN_IOU
    end_margin_seconds: float = DEFAULT_END_MARGIN_SECONDS
    no_frames_timeout_seconds: float = NO_FRAMES_TIMEOUT_SECONDS
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS
    settle_seconds: float = SETTLE_SECONDS

    @classmethod
    def from_env(cls) -> "TrackingSettings":
        return cls(
            min_iou=_env_float(MIN_IOU_ENV_VAR, DEFAULT_MIN_IOU),
            end_margin_seconds=_env_float(END_MARGIN_ENV_VAR, DEFAULT_END_MARGIN_SECONDS),
        )


class TrackingUnavailableError(RuntimeError):
    """Tracking cannot run for a reason that is not a fault of the stack (``str(exc)`` is the ``skipped`` reason)."""


@dataclass(frozen=True)
class ClipInfo:
    width: int
    height: int
    fps: float
    frames: int
    duration_seconds: float
    color_primaries: str = ""
    color_transfer: str = ""


@dataclass(frozen=True)
class Sample:
    t: float  # video seconds
    box: tuple[int, int, int, int]  # [top, left, bottom, right] normalised to 0..1000
    score: float  # detector score; -0.1 means tracked, not detected


@dataclass
class Track:
    track_id: str
    samples: list[Sample] = field(default_factory=list)
    source: str = PERSON_TRACK_SOURCE

    @property
    def first_seen(self) -> float:
        return self.samples[0].t

    @property
    def last_seen(self) -> float:
        return self.samples[-1].t


def is_real(sample: Sample) -> bool:
    """Whether the detector saw the person in this sample (score -0.1 is a tracker-only box)."""
    return sample.score >= 0


def visible_samples(track: Track) -> list[Sample]:
    """The samples to trust: real detections, and tracker-only boxes within :data:`PREDICTED_GRACE_SECONDS` of the
    latest real detection before them (a short occlusion). Predicted boxes after that, or with no detection
    before them, are drift."""
    kept: list[Sample] = []
    last_real: float | None = None
    for sample in track.samples:
        if is_real(sample):
            last_real = sample.t
            kept.append(sample)
        elif last_real is not None and sample.t - last_real <= PREDICTED_GRACE_SECONDS:
            kept.append(sample)
    return kept


def is_solid(track: Track) -> bool:
    """Whether the track is a person the detector really followed: enough real detections, and mostly real."""
    real = sum(1 for sample in track.samples if is_real(sample))
    return real >= MIN_REAL_SAMPLES and real >= MIN_REAL_SHARE * len(track.samples)


@dataclass
class TrackingSummary:
    """What ``/analyze`` reports about the tracking stage (``enabled`` false means it did not run).

    ``marks`` is the set-of-marks record (``utils/set_of_marks.py``) and is left out of the dict when that is off.
    """

    enabled: bool = False
    matched: int = 0
    tracks: int = 0
    elapsed_ms: float = 0.0
    skipped: str | None = None
    marks: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "enabled": self.enabled,
            "matched": self.matched,
            "tracks": self.tracks,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "skipped": self.skipped,
        }
        if self.marks is not None:
            summary["marks"] = self.marks
        return summary


@dataclass
class TrackingOutcome:
    tracks: list[Track] = field(default_factory=list)
    skipped: str | None = None
    elapsed_ms: float = 0.0


# --- clip probe and HDR transcode -------------------------------------------------------------------------------


def _parse_rate(value: str) -> float:
    numerator, _, denominator = value.partition("/")
    try:
        rate = float(numerator) / (float(denominator) if denominator else 1.0)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return rate if math.isfinite(rate) else 0.0


def parse_probe(payload: dict[str, Any]) -> ClipInfo:
    """A :class:`ClipInfo` from ``ffprobe -of json`` output (first video stream)."""
    streams = payload.get("streams") or []
    if not streams:
        raise TrackingUnavailableError("the clip has no video stream")
    stream = streams[0]
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    fps = _parse_rate(str(stream.get("r_frame_rate") or "0/1"))
    if width <= 0 or height <= 0 or fps <= 0:
        raise TrackingUnavailableError("ffprobe could not read the clip's size and frame rate")
    duration = float(stream.get("duration") or (payload.get("format") or {}).get("duration") or 0.0)
    try:
        frames = int(stream.get("nb_frames") or 0)
    except ValueError:
        frames = 0
    if frames <= 0:
        frames = round(duration * fps)
    if frames <= 0 or duration <= 0:
        duration = duration or frames / fps
    if frames <= 0:
        raise TrackingUnavailableError("ffprobe could not read the clip's length")
    return ClipInfo(
        width=width,
        height=height,
        fps=fps,
        frames=frames,
        duration_seconds=duration,
        color_primaries=str(stream.get("color_primaries") or ""),
        color_transfer=str(stream.get("color_transfer") or ""),
    )


def is_hdr(info: ClipInfo) -> bool:
    """BT.2020 primaries or an HLG or PQ transfer: such clips crash DeepStream and are transcoded to SDR."""
    return info.color_primaries.lower().startswith("bt2020") or info.color_transfer.lower() in _HDR_TRANSFERS


async def _run(*args: str, binary: str) -> tuple[bytes, bytes, int]:
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL
        )
    except FileNotFoundError as exc:
        raise TrackingUnavailableError(f"{binary} not found on the agent host") from exc
    stdout, stderr = await process.communicate()
    return stdout, stderr, process.returncode if process.returncode is not None else -1


async def probe_clip(path: Path) -> ClipInfo:
    stdout, stderr, code = await _run(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,nb_frames,duration,color_primaries,color_transfer:format=duration",
        "-of",
        "json",
        str(path),
        binary="ffprobe",
    )
    if code != 0:
        raise TrackingUnavailableError(f"ffprobe failed: {stderr.decode(errors='replace').strip()[-200:]}")
    return parse_probe(json.loads(stdout or b"{}"))


async def transcode_sdr(source: Path, target: Path) -> Path:
    """A BT.709 SDR copy of ``source`` at ``target``; only the colour tags and pixel format change, not the picture size."""
    _, stderr, code = await _run(
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-vf",
        SDR_FILTER,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-an",
        str(target),
        binary="ffmpeg",
    )
    if code != 0:
        raise TrackingUnavailableError(f"ffmpeg SDR transcode failed: {stderr.decode(errors='replace').strip()[-200:]}")
    return target


async def fetch_clip(video_ref: str | Path, directory: Path) -> Path:
    """The clip as a local file: a path is used as is, an http(s) URL is downloaded into ``directory``."""
    ref = str(video_ref)
    if not ref.startswith(("http://", "https://")):
        if not Path(ref).is_file():
            raise TrackingUnavailableError("the clip is not a readable file")
        return Path(ref)
    target = directory / "source.mp4"
    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client, client.stream("GET", ref) as response:
            response.raise_for_status()
            with target.open("wb") as out:
                async for chunk in response.aiter_bytes():
                    out.write(chunk)
    except httpx.HTTPError as exc:
        # Never the exception text: it can carry the signed URL.
        raise TrackingUnavailableError(f"the clip could not be downloaded ({type(exc).__name__})") from exc
    return target


# --- frames to tracks -------------------------------------------------------------------------------------------


def cut_at_replay(frames: Sequence[Frame]) -> list[Frame]:
    """The frames (oldest first) up to the first frame-id decrease, where DeepStream replays the file."""
    kept: list[Frame] = []
    for frame in frames:
        if kept and frame.frame_id < kept[-1].frame_id:
            break
        kept.append(frame)
    return kept


def _clamp(value: float) -> int:
    return max(0, min(1000, round(value)))


def build_tracks(frames: Sequence[Frame], clip: ClipInfo) -> list[Track]:
    """``Person`` tracks of one clip from its ``/frames`` (oldest first), on the clip's own time base.

    Boxes are normalised to 0..1000 as ``[top, left, bottom, right]`` from the clip's pixel size. Every sample is
    kept, including those with score -0.1, but at most :data:`SAMPLES_PER_SECOND` a second per track (the first of
    each 200 ms bucket).
    """
    frames = cut_at_replay(frames)
    if not frames:
        return []
    last_frame_id = frames[-1].frame_id
    lost_frames = clip.frames - last_frame_id
    if 0 <= lost_frames <= END_REACHED_WITHIN_FRAMES:
        loss_seconds = lost_frames / clip.fps
    else:
        loss_seconds = FALLBACK_LOSS_SECONDS

    tracks: dict[str, Track] = {}
    buckets: dict[str, int] = {}
    for frame in frames:
        t = round(frame.frame_id / clip.fps + loss_seconds, 3)
        bucket = math.floor(t * SAMPLES_PER_SECOND)
        for obj in frame.objects:
            if obj.type != PERSON_TYPE:
                continue
            if buckets.get(obj.object_id) == bucket:
                continue
            left, top, right, bottom = obj.bbox
            box = (
                _clamp(top / clip.height * 1000),
                _clamp(left / clip.width * 1000),
                _clamp(bottom / clip.height * 1000),
                _clamp(right / clip.width * 1000),
            )
            buckets[obj.object_id] = bucket
            tracks.setdefault(obj.object_id, Track(track_id=obj.object_id)).samples.append(
                Sample(t, box, obj.confidence)
            )
    return list(tracks.values())


def box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection over union of two [ymin, xmin, ymax, xmax] boxes."""
    inter_h = min(a[2], b[2]) - max(a[0], b[0])
    inter_w = min(a[3], b[3]) - max(a[1], b[1])
    if inter_h <= 0 or inter_w <= 0:
        return 0.0
    inter = inter_h * inter_w
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _sample_near(track: Track, seconds: float) -> Sample | None:
    samples = visible_samples(track)
    if not samples:
        return None
    nearest = min(samples, key=lambda sample: abs(sample.t - seconds))
    return nearest if abs(nearest.t - seconds) <= ASSOCIATION_WINDOW_SECONDS else None


def box_confirmed(box: Sequence[float], seconds: float, tracks: Sequence[Track], min_iou: float) -> bool:
    """Whether a solid track's visible sample nearest to ``seconds`` (within 0.5 s) overlaps ``box`` by ``min_iou``.

    The test :func:`associate_tracks` applies to one pair, asked of every solid track at once.
    """
    for track in tracks:
        if not is_solid(track):
            continue
        sample = _sample_near(track, seconds)
        if sample is not None and box_iou(box, sample.box) >= min_iou:
            return True
    return False


def nearest_sample(
    tracks: Sequence[Track], seconds: float, first_seen: float, last_seen: float, exclude: Collection[str] = ()
) -> tuple[Track, Sample] | None:
    """The visible sample of a solid track nearest to ``seconds`` within ``first_seen`` to ``last_seen``, or None.

    Tracks whose id is in ``exclude`` are skipped, so two entities do not fall back to the same person.
    """
    best: tuple[Track, Sample] | None = None
    for track in tracks:
        if track.track_id in exclude or not is_solid(track):
            continue
        for sample in visible_samples(track):
            if first_seen <= sample.t <= last_seen and (
                best is None or abs(sample.t - seconds) < abs(best[1].t - seconds)
            ):
                best = (track, sample)
    return best


def associate_tracks(
    entities: Sequence[tuple[str, Sequence[float], float]], tracks: Sequence[Track], min_iou: float
) -> dict[str, Track]:
    """Match entities to tracks: ``{item_id: track}``.

    ``entities`` are ``(item_id, box, seconds)`` with the locate step's box and the second it was found at. An
    entity's candidate for a track is the visible sample nearest to ``seconds`` (within 0.5 s); pairs with an IoU of
    at least ``min_iou`` are taken greedily, best first, so every entity gets at most one track and every track at
    most one entity. Only solid tracks (:func:`is_solid`) are candidates: a ghost with one detection and drifting
    tracker-only boxes matches nothing, so the entity keeps its own box.
    """
    pairs: list[tuple[float, int, int]] = []
    for e_index, (_, box, seconds) in enumerate(entities):
        for t_index, track in enumerate(tracks):
            if not is_solid(track):
                continue
            sample = _sample_near(track, seconds)
            if sample is None:
                continue
            iou = box_iou(box, sample.box)
            if iou >= min_iou:
                pairs.append((iou, e_index, t_index))
    matched: dict[str, Track] = {}
    used_tracks: set[int] = set()
    for _, e_index, t_index in sorted(pairs, key=lambda pair: -pair[0]):
        item_id = entities[e_index][0]
        if item_id in matched or t_index in used_tracks:
            continue
        matched[item_id] = tracks[t_index]
        used_tracks.add(t_index)
    return matched


def first_seen_sample(track: Track) -> Sample:
    """The first sample of the track: the second the item is first seen, which is its evidence image."""
    return track.samples[0]


def track_json(track: Track) -> bytes:
    """The track as stored beside the evidence: ``[{t, box, score}]``."""
    return json.dumps(
        [{"t": s.t, "box": list(s.box), "score": s.score} for s in track.samples], separators=(",", ":")
    ).encode()


# --- the run ----------------------------------------------------------------------------------------------------


def sensor_id_for(video_key: str, run_id: str) -> str:
    """One unique sensor id per run: ``incident-<short hash of the video key>-<run id>``."""
    short = hashlib.sha256(video_key.encode()).hexdigest()[:8]
    return f"incident-{short}-{_SENSOR_UNSAFE.sub('-', run_id).strip('-')[:40] or 'run'}"


async def run_tracking(
    video_ref: str | Path,
    *,
    sensor_id: str,
    client: PerceptionClient | None = None,
    settings: TrackingSettings | None = None,
    probe: Callable[[Path], Awaitable[ClipInfo]] = probe_clip,
    transcode: Callable[[Path, Path], Awaitable[Path]] = transcode_sdr,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> TrackingOutcome:
    """Push the clip through the perception stack and return its person tracks. Never raises.

    The stream and the NvStreamer file are removed in a ``finally`` block, whatever happened, because a stream left
    on DeepStream is replayed forever and NvStreamer has only ten RTSP slots.
    """
    active_client = client if client is not None else HttpPerceptionClient()
    active = settings if settings is not None else TrackingSettings.from_env()
    started = clock()

    def done(tracks: Sequence[Track] = (), skipped: str | None = None) -> TrackingOutcome:
        return TrackingOutcome(tracks=list(tracks), skipped=skipped, elapsed_ms=(clock() - started) * 1000.0)

    with tempfile.TemporaryDirectory(prefix="incident-tracking-") as tmp:
        directory = Path(tmp)
        try:
            local = await fetch_clip(video_ref, directory)
            clip = await probe(local)
            push = await transcode(local, directory / "sdr.mp4") if is_hdr(clip) else local
        except TrackingUnavailableError as exc:
            logger.warning("incident tracking skipped: %s", exc)
            return done(skipped=str(exc))
        except Exception as exc:
            logger.warning("incident tracking skipped: clip preparation failed: %s", type(exc).__name__)
            return done(skipped=f"clip preparation failed: {type(exc).__name__}")

        stream_id: str | None = None
        camera_url: str | None = None
        add_attempted = False
        try:
            uploaded = await active_client.upload(push, f"{sensor_id}.mp4", sensor_id)
            stream_id = uploaded.stream_id
            camera_url = await active_client.stream_url(stream_id)
            add_attempted = True
            await active_client.add_stream(sensor_id, camera_url)
            added_at = clock()
            highest: int | None = None
            deadline = clip.duration_seconds + active.end_margin_seconds
            while True:
                try:
                    latest = await active_client.latest_frame_id(sensor_id)
                except Exception as exc:  # the index lags the stream; a failed poll is retried
                    logger.info("incident tracking: frame poll failed (%s)", type(exc).__name__)
                    latest = None
                if latest is not None:
                    highest = latest if highest is None else max(highest, latest)
                waited = clock() - added_at
                if highest is None and waited > active.no_frames_timeout_seconds:
                    logger.warning("incident tracking: no frames within %.0f s of the stream add", waited)
                    return done(skipped="no frames from perception")
                if highest is not None and clip.frames - highest <= END_REACHED_WITHIN_FRAMES:
                    await sleep(active.settle_seconds)
                    break
                if waited > deadline:
                    if highest is None:
                        return done(skipped="no frames from perception")
                    break
                await sleep(active.poll_interval_seconds)
            frames = await active_client.frames(sensor_id)
            return done(tracks=build_tracks(frames, clip))
        except Exception as exc:
            logger.warning("incident tracking skipped: %s: %s", type(exc).__name__, exc)
            return done(skipped=f"perception failed: {type(exc).__name__}")
        finally:
            if add_attempted and camera_url is not None:
                try:
                    await active_client.remove_stream(sensor_id, camera_url)
                except Exception as exc:
                    logger.warning("incident tracking: stream remove failed: %s", type(exc).__name__)
            if stream_id is not None:
                try:
                    await active_client.delete_file(stream_id)
                except Exception as exc:
                    logger.warning("incident tracking: NvStreamer file delete failed: %s", type(exc).__name__)


@dataclass
class TrackingRequest:
    """One analysis run's tracking: the sensor id, the summary it fills in and, for tests, what the run uses.

    ``options`` are keyword arguments of :func:`run_tracking` (``client``, ``settings``, ``probe``, ...).
    ``marked`` maps entity ids to the tracks set-of-marks prompting matched them to; the evidence step takes those
    as they are and associates only the other entities by box overlap.
    """

    sensor_id: str
    summary: TrackingSummary = field(default_factory=lambda: TrackingSummary(enabled=True))
    runner: Callable[..., Coroutine[Any, Any, TrackingOutcome]] = run_tracking
    options: dict[str, Any] = field(default_factory=dict)
    marked: dict[str, Track] = field(default_factory=dict)

    def reuse(self, outcome: TrackingOutcome) -> None:
        """Serve ``outcome`` (a run made earlier in the request) instead of running the stack again."""

        async def _earlier(*_: Any, **__: Any) -> TrackingOutcome:
            return outcome

        self.runner = _earlier

    @property
    def min_iou(self) -> float:
        settings = self.options.get("settings")
        return settings.min_iou if isinstance(settings, TrackingSettings) else TrackingSettings.from_env().min_iou


# --- object tracks (instruments and assets) ---------------------------------------------------------------------


@dataclass(frozen=True)
class ObjectSeed:
    """One instrument or asset to follow: the box the locate step found for it and the second it was found at."""

    item_id: str
    label: str
    box: tuple[int, int, int, int]  # [ymin, xmin, ymax, xmax] normalised to 0..1000, the detector's own (unpadded)
    seconds: float
    score: float = 1.0


@dataclass
class ObjectTrackingOutcome:
    tracks: dict[str, Track] = field(default_factory=dict)  # {item_id: track}, only items that gained a track
    items: dict[str, dict[str, Any]] = field(default_factory=dict)  # per-item telemetry, every seed
    skipped: str | None = None
    elapsed_ms: float = 0.0
    time_cap_reached: bool = False
    tracker: str | None = None  # the OpenCV tracker used (CSRT, KCF or MIL)


@dataclass
class ObjectTrackingSummary:
    """What ``/analyze`` reports about object tracking (``object_tracking``); filled in by the evidence step."""

    enabled: bool = True
    seeds: int = 0
    tracked: int = 0
    skipped: str | None = None
    elapsed_ms: float = 0.0
    time_cap_reached: bool = False
    tracker: str | None = None
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    budget: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "seeds": self.seeds,
            "tracked": self.tracked,
            "tracker": self.tracker,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "time_cap_reached": self.time_cap_reached,
            "skipped": self.skipped,
            "detector": self.budget,
            "items": self.items,
        }


@dataclass
class ObjectTrackingRequest:
    """One analysis run's object tracking: its summary, the detector-call budget and, for tests, what the run uses.

    ``runner`` is :func:`vss_agents.utils.object_tracking.run_object_tracking` (set by the caller, which keeps this
    module free of the OpenCV and detector code); ``options`` are its keyword arguments (``settings``,
    ``tracker_factory``, ...). Every detector call the run makes counts against ``budget``.
    """

    budget: "CropCallBudget"
    runner: Callable[..., Coroutine[Any, Any, ObjectTrackingOutcome]]
    summary: ObjectTrackingSummary = field(default_factory=ObjectTrackingSummary)
    options: dict[str, Any] = field(default_factory=dict)
