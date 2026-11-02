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

"""Evidence crop service for natural-language search (MVP2 VM Step 3).

Finds each object in the video frame at VLM timestamp_seconds with an open-vocabulary detector port
(retrying one and two seconds either side when it finds nothing; each second tried is one budgeted detector
call), and stores two images per item through EvidenceStore (R2 in production): the whole frame at the video's
native size, which the console shows with the box drawn on it, and the object crop (5% margin padding clamped to
the frame, JPEG quality 85), which only evidence search embeds. When the object is not found the frame is stored
alone and the entry says so. The entries become the evidence of the AI revision the analysis run writes; the
console builds the searchable ``evidence_crops`` rows from a revision's evidence when it is made official.

Zoomed second look (``INCIDENT_ZOOM_NAMES_ENABLED``, default on): the whole-clip VLM pass names each item at
thumbnail resolution, so a crumpled bag seen at 25 pixels can become "soccer ball". Once an item is located, the
item is cut from the frame at native resolution with a 25% margin (upscaled to a short side of at least 256 px for
the model only; the stored crop is unchanged) and the same VLM the grounding detector uses takes two blind looks
(the frame with the box drawn; that frame plus the close-up), then a text-only merge weighs the looks against the
report's current name and description and may correct them only when the looks agree (a vision prompt that carries
the current name makes the model confirm a wrong one, and a close-up alone can name a neighbouring object). A correction is applied to the evidence entry
and to the matching item of the report payload when the model says ``changed`` with confidence at least 0.7; the
item id, label, timestamps and an entity's ``type`` are never changed. Zoom calls are outside ``CropCallBudget``
(the grounding cap of an experimental request) and a failed or slow call never fails the analysis.

Object tracking (``INCIDENT_OBJECT_TRACKING_ENABLED``, :mod:`vss_agents.utils.object_tracking`): each located instrument
and asset is followed through the clip from its box and gets a track file like a person's, marked ``track_source``.

Privacy blur (``blur_faces`` on ``/analyze``, :mod:`vss_agents.utils.privacy_blur`): every stored frame and crop has
every head in it pixelated before it is encoded, found by a whole-frame head detection of its own second (so it does
not depend on person tracking) and joined by the known person tracks and boxes. Heads are also detected at sampled
seconds over each item's evidence-clip window and stored beside the item. The second look still sees the unblurred
close-up.
"""

from abc import ABC
from abc import abstractmethod
import asyncio
import base64
from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
import copy
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from functools import partial
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
from typing import Protocol
from typing import TypeVar
from typing import runtime_checkable

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]
import httpx
import numpy as np
from PIL import Image
from PIL import ImageDraw

from vss_agents import incident_contract
from vss_agents.utils import privacy_blur
from vss_agents.utils import run_provenance
from vss_agents.utils import track_evidence
from vss_agents.utils.optional_cv2 import ensure_codecs
from vss_agents.utils.r2 import get_r2_client
from vss_agents.utils.r2 import is_r2_configured

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    """Bounding box detection produced by an open-vocabulary detector."""

    box: list[int]  # [ymin, xmin, ymax, xmax] normalized 0-1000
    score: float
    label: str = ""


@dataclass(frozen=True)
class CropResult:
    """One evidence item's frame and crop with the spatial grounding metadata.

    ``located`` is False when the detector found nothing at any second tried: the frame is kept, ``image_bytes``
    and ``normalized_box`` are None (never a whole-frame box), and ``box_seconds`` is the requested second.
    """

    image_bytes: bytes | None  # the object crop (evidence search embeds it); None when not located
    normalized_box: list[int] | None  # [ymin, xmin, ymax, xmax] normalized 0-1000; None when not located
    score: float
    frame_size: tuple[int, int]  # (width, height)
    frame_bytes: bytes | None = None  # the whole frame the box was found in, at the video's native size
    box_seconds: float | None = None  # the second the frame was read at (the requested one unless a retry hit)
    located: bool = True
    zoom_bytes: bytes | None = None  # the close-up the second look names (25% margin, upscaled); never stored
    zoom_frame_bytes: bytes | None = None  # the whole frame with the box drawn, the second look's context; never stored
    # Kept only when the privacy blur asks for it (``keep_frame``): the decoded frame and the crop's pixel box
    # [y0, x0, y1, x1], so the stored images can be encoded again from a blurred frame. Never stored.
    frame: np.ndarray | None = field(default=None, compare=False, repr=False)
    crop_px: tuple[int, int, int, int] | None = None
    # The detector's own box (before the 5 % crop padding), for the object tracker's seed. Never stored.
    detected_box: tuple[int, int, int, int] | None = field(default=None, compare=False, repr=False)


# Seconds tried, in order, around the requested one when the detector finds nothing there.
LOCATE_RETRY_OFFSETS: tuple[int, ...] = (1, -1, 2, -2)
FRAME_JPEG_QUALITY = 90
# Zoomed second look: the close-up's margin around the located box, the smallest short side the model sees, the
# confidence a correction needs before it is applied, and the flag that turns the second look off.
ZOOM_NAMES_ENV = "INCIDENT_ZOOM_NAMES_ENABLED"
ZOOM_CROP_PADDING = 0.25
ZOOM_MIN_SHORT_SIDE = 256
ZOOM_CONFIDENCE_THRESHOLD = 0.7
ZOOM_TIMEOUT_SECONDS = 60.0
_FALSE_FLAG_VALUES = frozenset({"0", "false", "no", "off"})


def zoom_names_enabled() -> bool:
    """Whether the zoomed second look runs: ``INCIDENT_ZOOM_NAMES_ENABLED`` unset or anything but 0/false/no/off."""
    return os.getenv(ZOOM_NAMES_ENV, "1").strip().lower() not in _FALSE_FLAG_VALUES


@dataclass(frozen=True)
class ZoomLook:
    """One blind look at the marked item: what the model sees, without the report's text."""

    name: str
    description: str
    confidence: float


@dataclass(frozen=True)
class ZoomVerdict:
    """The second look's outcome for one item: the name and description it stands by, and how sure it is.

    ``seen`` records the blind looks (``frame`` and ``close_up``: the name each gave an object, the description
    each gave a person) so a reviewer can see what the model saw even when nothing changed.
    """

    name: str
    description: str
    confidence: float
    changed: bool
    seen: dict[str, str] | None = None


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"zoom reply confidence is not a number in 0..1: {value!r}")
    return float(value)


def parse_look_reply(data: dict[str, Any]) -> ZoomLook:
    """A strict ``{name, description, confidence}`` object; anything else raises ``ValueError``."""
    name = data.get("name")
    description = data.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("zoom look has no name")
    if not isinstance(description, str):
        raise ValueError("zoom look has no description")
    return ZoomLook(
        name=" ".join(name.split()), description=description.strip(), confidence=_confidence(data.get("confidence"))
    )


def parse_zoom_reply(data: dict[str, Any]) -> ZoomVerdict:
    """A strict ``{name, description, confidence, changed}`` object; anything else raises ``ValueError``."""
    name = data.get("name")
    description = data.get("description")
    changed = data.get("changed")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("zoom reply has no name")
    if not isinstance(description, str):
        raise ValueError("zoom reply has no description")
    if not isinstance(changed, bool):
        raise ValueError(f"zoom reply changed is not a boolean: {changed!r}")
    return ZoomVerdict(
        name=" ".join(name.split()),
        description=description.strip(),
        confidence=_confidence(data.get("confidence")),
        changed=changed,
    )


@runtime_checkable
class ZoomNamer(Protocol):
    """Port for the zoomed second look: names a located item again from its close-up and marked frame."""

    def zoom_name(
        self, jpeg: bytes, *, item_type: str, name: str, description: str, context_jpeg: bytes | None = None
    ) -> ZoomVerdict:
        """Confirm or correct ``name`` and ``description`` of the ``item_type`` item in the close-up ``jpeg``.

        ``context_jpeg`` is the whole frame with the item's box drawn on it, so the model knows which object the
        close-up shows and sees its surroundings.
        """
        ...


def look_prompt(item_type: str, with_close_up: bool) -> str:
    """A blind look: name the marked item from the image(s) alone, with no report text to lean on."""
    kind = "entity" if item_type == "entity" else "object"
    return incident_contract.prompt(f"zoom/look-{kind}-{'close-up' if with_close_up else 'frame'}")


_LOOK_STOP_WORDS = frozenset({"a", "an", "the", "some", "small", "large", "big", "little"})


def _look_words(name: str) -> frozenset[str]:
    return frozenset(word for word in re.findall(r"[a-z0-9]+", name.lower()) if word not in _LOOK_STOP_WORDS)


def looks_agree(first: ZoomLook, second: ZoomLook) -> bool:
    """Whether two blind looks named the same thing: equal content words, or one name inside the other.

    "plastic bag" agrees with "crumpled plastic bag"; "wheelchair" does not agree with "shoe". Decided in code,
    not by the merge model, which proved unreliable at it.
    """
    a, b = _look_words(first.name), _look_words(second.name)
    return bool(a and b) and (a <= b or b <= a)


def merge_prompt(item_type: str, name: str, description: str, look: ZoomLook) -> str:
    """The text-only merge: the report's text against what the agreeing blind looks found.

    The report's name is kept out of the vision calls because a model that reads "soccer ball" next to a
    25-pixel smudge confirms "soccer ball"; here the model only reconciles what the looks found with what the
    report says, and it is only asked when the two looks agree (:func:`looks_agree`).
    """
    fields = {"name": name, "description": description, "look_name": look.name, "look_description": look.description}
    if item_type == "entity":
        return incident_contract.render_prompt("zoom/merge-entity", **fields)
    return incident_contract.render_prompt("zoom/merge-object", item_type=item_type, **fields)


def zoom_crop(frame: np.ndarray, box: Sequence[int | float]) -> bytes | None:
    """The close-up the second look names: ``box`` cut from ``frame`` at native resolution with a 25% margin.

    A small cut is upscaled so its short side is at least ``ZOOM_MIN_SHORT_SIDE`` pixels (bicubic), which only
    helps the model read it; the stored evidence crop is cut separately and never upscaled.
    """
    frame_h, frame_w = frame.shape[:2]
    c_ymin, c_xmin, c_ymax, c_xmax = apply_box_padding(box, frame_w, frame_h, padding_fraction=ZOOM_CROP_PADDING)
    if c_ymin >= c_ymax or c_xmin >= c_xmax:
        return None
    cut = np.ascontiguousarray(frame[c_ymin:c_ymax, c_xmin:c_xmax])
    short_side = min(cut.shape[:2])
    if 0 < short_side < ZOOM_MIN_SHORT_SIDE:
        scale = ZOOM_MIN_SHORT_SIDE / short_side
        size = (max(1, round(cut.shape[1] * scale)), max(1, round(cut.shape[0] * scale)))
        cut = np.asarray(Image.fromarray(cut[..., ::-1]).resize(size, Image.Resampling.BICUBIC))[..., ::-1]
        cut = np.ascontiguousarray(cut)
    return encode_jpeg(cut, quality=85)


def zoom_context_frame(frame: np.ndarray, box: Sequence[int | float]) -> bytes | None:
    """The whole frame with ``box`` drawn in red (at most ``GROUNDING_MAX_EDGE_PIXELS`` on the long edge).

    Shown to the second look beside the close-up so the model knows which object is meant and sees its
    surroundings; a 25-pixel object is often only recognisable in context. Never stored.
    """
    frame_h, frame_w = frame.shape[:2]
    y0, x0, y1, x1 = apply_box_padding(box, frame_w, frame_h, padding_fraction=0.0)
    if y0 >= y1 or x0 >= x1:
        return None
    image = Image.fromarray(np.ascontiguousarray(frame[..., ::-1]))
    ImageDraw.Draw(image).rectangle(
        (x0, y0, x1, y1), outline=(255, 0, 0), width=max(2, round(max(frame_w, frame_h) / 400))
    )
    scale = min(1.0, GROUNDING_MAX_EDGE_PIXELS / max(frame_h, frame_w))
    if scale < 1.0:
        image = image.resize((round(frame_w * scale), round(frame_h * scale)))
    return encode_jpeg(np.ascontiguousarray(np.asarray(image)[..., ::-1]), quality=85)


def evidence_key(video_key: str, batch_id: str, item_id: str) -> str:
    """Return the R2 key for one evidence crop image: ``evidence/<video key>/<batchId>/<itemId>.jpg``.

    ``video_key`` is the video's own R2 key (``uploads/clip.mp4``), so the path reads like the video.
    ``batch_id`` is ``run-<n>``, one per analysis run of that video (:meth:`EvidenceStore.next_batch_id`),
    so a key is never reused and an image a revision references is never overwritten. Item ids
    (``E1``, ``I1``, ``A1``) are unique across the three collections, so no collection folder is needed.
    """
    return f"evidence/{video_key}/{batch_id}/{item_id}.jpg"


def evidence_frame_key(video_key: str, batch_id: str, item_id: str) -> str:
    """Return the R2 key for one evidence item's whole frame: ``evidence/<video key>/<batchId>/<itemId>-frame.jpg``."""
    return f"evidence/{video_key}/{batch_id}/{item_id}-frame.jpg"


def evidence_track_key(video_key: str, batch_id: str, item_id: str) -> str:
    """Return the R2 key for one matched entity's track: ``evidence/<video key>/<batchId>/<itemId>-track.json``."""
    return f"evidence/{video_key}/{batch_id}/{item_id}-track.json"


def evidence_heads_key(video_key: str, batch_id: str, item_id: str) -> str:
    """Return the R2 key for one item's clip heads: ``evidence/<video key>/<batchId>/<itemId>-heads.json``."""
    return f"evidence/{video_key}/{batch_id}/{item_id}-heads.json"


_BATCH_PATTERN = re.compile(r"^run-(\d+)$")


def next_run_batch_id(existing_batch_ids: Iterable[str]) -> str:
    """The batch id after the highest ``run-<n>`` in ``existing_batch_ids`` (``run-1`` when none)."""
    highest = 0
    for batch_id in existing_batch_ids:
        match = _BATCH_PATTERN.match(batch_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"run-{highest + 1}"


def apply_box_padding(
    box: Sequence[int | float],
    frame_w: int,
    frame_h: int,
    padding_fraction: float = 0.05,
) -> tuple[int, int, int, int]:
    """Denormalize coordinates and apply margin padding clamped to the frame boundaries.

    Args:
        box: [ymin, xmin, ymax, xmax] coordinates normalized to 0-1000.
        frame_w: Frame width in pixels.
        frame_h: Frame height in pixels.
        padding_fraction: Fractional padding margin (default 0.05 = 5%).

    Returns:
        Pixel coordinates (c_ymin, c_xmin, c_ymax, c_xmax) clamped to frame boundaries.
    """
    if len(box) != 4:
        raise ValueError(f"Expected 4 bounding box coordinates, got {len(box)}")

    ymin, xmin, ymax, xmax = box

    # Denormalize from 0-1000 to pixel coordinates
    p_ymin = round(ymin * frame_h / 1000.0)
    p_xmin = round(xmin * frame_w / 1000.0)
    p_ymax = round(ymax * frame_h / 1000.0)
    p_xmax = round(xmax * frame_w / 1000.0)

    bw = p_xmax - p_xmin
    bh = p_ymax - p_ymin
    px = max(1, round(bw * padding_fraction)) if bw > 0 else 0
    py = max(1, round(bh * padding_fraction)) if bh > 0 else 0

    c_ymin = max(0, p_ymin - py)
    c_xmin = max(0, p_xmin - px)
    c_ymax = min(frame_h, p_ymax + py)
    c_xmax = min(frame_w, p_xmax + px)

    return c_ymin, c_xmin, c_ymax, c_xmax


def normalize_box(
    c_ymin: int,
    c_xmin: int,
    c_ymax: int,
    c_xmax: int,
    frame_w: int,
    frame_h: int,
) -> list[int]:
    """Convert clamped pixel coordinates back to 0-1000 normalized integers."""
    if frame_h <= 0 or frame_w <= 0:
        return [0, 0, 0, 0]
    return [
        max(0, min(1000, round(c_ymin * 1000.0 / frame_h))),
        max(0, min(1000, round(c_xmin * 1000.0 / frame_w))),
        max(0, min(1000, round(c_ymax * 1000.0 / frame_h))),
        max(0, min(1000, round(c_xmax * 1000.0 / frame_w))),
    ]


def extract_frame(video_ref: str | Path, timestamp_seconds: int | float) -> np.ndarray | None:
    """Extract a single BGR video frame at the given timestamp in seconds.

    Supports local file paths and video URLs via OpenCV VideoCapture.
    """
    ensure_codecs(cv2)
    path_str = str(video_ref)
    cap = cv2.VideoCapture(path_str)
    if not cap.isOpened():
        logger.warning("Could not open video_ref: %s", path_str)
        return None

    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps > 0 and total_frames > 0:
            target_frame = min(total_frames - 1, max(0, math.floor(timestamp_seconds * fps)))
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        else:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(timestamp_seconds * 1000.0)))

        ret, frame = cap.read()
        if not ret or frame is None or frame.size == 0:
            logger.warning("Could not read frame at %ss from %s", timestamp_seconds, path_str)
            return None
        return frame
    finally:
        cap.release()


def encode_jpeg(frame_crop: np.ndarray, quality: int = 85) -> bytes | None:
    """Encode an image crop as JPEG bytes with the specified quality (quality 85)."""
    if cv2 is not None:
        success, buf = cv2.imencode(".jpg", frame_crop, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if success and buf is not None:
            return bytes(buf.tobytes())

    try:
        if len(frame_crop.shape) == 3 and frame_crop.shape[2] == 3:
            rgb_crop = cv2.cvtColor(frame_crop, cv2.COLOR_BGR2RGB) if cv2 is not None else frame_crop[..., ::-1]
            img = Image.fromarray(rgb_crop)
        else:
            img = Image.fromarray(frame_crop)
        bio = io.BytesIO()
        img.save(bio, format="JPEG", quality=quality)
        return bio.getvalue()
    except Exception as exc:
        logger.warning("Failed to encode crop as JPEG: %s", exc)
        return None


@runtime_checkable
class OpenVocabularyDetector(Protocol):
    """Port interface for open-vocabulary object detectors."""

    def detect(self, frame: np.ndarray, query: str) -> Sequence[Detection | dict[str, Any]]:
        """Run detection on the frame with the given text query."""
        ...


DEFAULT_GROUNDING_BASE_URL = "https://switchyard-13doh4lsz.brevlab.com"
DEFAULT_GROUNDING_MODEL = "nvidia/cosmos-3-super-reasoner"
GROUNDING_MAX_EDGE_PIXELS = 1280
GROUNDING_MAX_ATTEMPTS = 4
# The most boxes one whole-frame search (``DefaultDetector.detect_all``) returns.
MAX_DETECTIONS_PER_QUERY = 32
# Items of one report cropped at once; each crop is a blocking frame read plus a grounding call.
EVIDENCE_CROP_CONCURRENCY = 4
# The item types the object tracker follows (people keep the DeepStream person tracks).
OBJECT_TRACKING_TYPES = frozenset({"instrument", "asset"})
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_Checked = TypeVar("_Checked")
_JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$")


def _failed_status(resp: Any) -> bool:
    """Whether a reply's status is not 2xx (``raise_for_status`` raises on 1xx and 3xx too)."""
    status = getattr(resp, "status_code", None)
    return isinstance(status, int) and not 200 <= status < 300


class CropCallCapReachedError(RuntimeError):
    """An experimental request used up its grounding-call cap; the item keeps an ``image_gap``."""


class CropCallBudget:
    """The grounding HTTP attempts one experimental request may make (retries included), and what they used.

    One budget is shared by the request's concurrent crops, which run in worker threads, so it is locked.
    An attempt beyond ``limit`` is refused before it is sent (``CropCallCapReachedError``).
    """

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("a crop-call cap must be at least 1")
        self.limit = limit
        self.calls = 0
        self.refused = 0
        self.tokens = 0
        self.calls_without_usage = 0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            if self.calls >= self.limit:
                self.refused += 1
                raise CropCallCapReachedError(f"crop-call cap of {self.limit} reached")
            self.calls += 1

    def record_response(self, resp: httpx.Response) -> None:
        """Count the tokens a sent attempt reported; a reply without usage (other than a 429) is counted as such."""
        _, _, total = run_provenance.token_counts(run_provenance.usage_of(resp))  # never raises on a strange usage
        with self._lock:
            if total:
                self.tokens += total
            elif resp.status_code != 429:
                self.calls_without_usage += 1

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "cap": self.limit,
                "calls": self.calls,
                "refused_by_cap": self.refused,
                "cap_reached": self.refused > 0,
                "tokens": self.tokens,
                "calls_without_usage": self.calls_without_usage,
            }


class BudgetedDetector:
    """Any detector under a ``CropCallBudget``: one budgeted call per ``detect``."""

    def __init__(self, detector: Any, budget: CropCallBudget) -> None:
        self.detector = detector
        self.budget = budget

    def is_available(self) -> bool:
        return bool(self.detector.is_available()) if hasattr(self.detector, "is_available") else True

    def detect(self, frame: np.ndarray, query: str) -> Sequence[Detection | dict[str, Any]]:
        self.budget.acquire()
        detections: Sequence[Detection | dict[str, Any]] = (
            self.detector.detect(frame, query) if hasattr(self.detector, "detect") else self.detector(frame, query)
        )
        return detections


def with_call_budget(detector: Any, budget: CropCallBudget) -> Any:
    """The detector for one budgeted request; ``DefaultDetector`` counts every HTTP attempt, including retries."""
    if isinstance(detector, DefaultDetector):
        return detector.with_call_budget(budget)
    return BudgetedDetector(detector, budget)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating Markdown code fences."""
    parsed = json.loads(_JSON_FENCE_PATTERN.sub("", text.strip()))
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}")
    return parsed


def with_zoom_budget(namer: Any, budget: CropCallBudget) -> Any:
    """``namer`` with its zoom calls capped by ``budget``; a namer other than ``DefaultDetector`` is returned as is."""
    if isinstance(namer, DefaultDetector):
        return namer.with_zoom_budget(budget)
    return namer


def unbudgeted(detector: Any) -> Any:
    """``detector`` without a ``CropCallBudget``: the privacy blur's head search has a budget of its own."""
    if isinstance(detector, BudgetedDetector):
        return detector.detector
    if isinstance(detector, DefaultDetector) and detector.call_budget is not None:
        free = copy.copy(detector)
        free.call_budget = None
        return free
    return detector


class DefaultDetector:
    """Open-vocabulary detector backed by the remote vision-language model.

    Sends the video frame and the item label to an OpenAI-compatible chat completions endpoint
    (Brev Switchyard) and asks for one tight bounding box, so no local detector weights are needed.
    Settings: ``VLM_GATEWAY_BASE_URL`` or ``VLM_BASE_URL``, ``VLM_GATEWAY_API_KEY`` or ``OPENAI_API_KEY``,
    and ``VLM_NAME`` (each overridable through the constructor).
    """

    def __init__(
        self,
        model_name: str | None = None,
        min_score: float = 0.2,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 120.0,
        call_budget: CropCallBudget | None = None,
        zoom_budget: CropCallBudget | None = None,
    ) -> None:
        self.model_name = model_name or os.getenv("VLM_NAME") or DEFAULT_GROUNDING_MODEL
        self.min_score = min_score
        self.base_url = (
            base_url or os.getenv("VLM_GATEWAY_BASE_URL") or os.getenv("VLM_BASE_URL") or DEFAULT_GROUNDING_BASE_URL
        ).rstrip("/")
        self.api_key = (
            api_key if api_key is not None else os.getenv("VLM_GATEWAY_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        )
        self.timeout_seconds = timeout_seconds
        self.call_budget = call_budget
        self.zoom_budget = zoom_budget

    def with_zoom_budget(self, budget: CropCallBudget) -> "DefaultDetector":
        """A copy for one request whose zoomed-second-look attempts draw on ``budget``."""
        capped = copy.copy(self)
        capped.zoom_budget = budget
        return capped

    def with_call_budget(self, budget: CropCallBudget) -> "DefaultDetector":
        """A copy for one request whose HTTP attempts draw on ``budget`` (the shared detector is unchanged)."""
        budgeted = copy.copy(self)
        budgeted.call_budget = budget
        return budgeted

    def is_available(self) -> bool:
        """Return True if the remote VLM endpoint is configured with credentials."""
        return bool(self.api_key)

    def _chat_url(self) -> str:
        root = self.base_url[: -len("/v1")] if self.base_url.endswith("/v1") else self.base_url
        return f"{root}/v1/chat/completions"

    def _count(self, stage: str | None, started: float, resp: Any, *, ok: bool) -> None:
        """Count one sent HTTP attempt in the analysis's run provenance (a no-op outside an analysis)."""
        usage = run_provenance.usage_of(resp) if resp is not None else None
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        run_provenance.record_call(stage, ok=ok, usage=usage, elapsed_ms=elapsed_ms, model=self.model_name)

    def _request_box(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST the grounding request, retrying rate limits, server errors and unparseable replies.

        Every attempt is counted in the run provenance under the current detector stage (grounding, object
        tracking or the privacy head search), refused, failed and retried ones included.
        """
        for attempt in range(1, GROUNDING_MAX_ATTEMPTS + 1):
            if self.call_budget is not None:
                try:
                    self.call_budget.acquire()  # before sending: a refused attempt is never made
                except CropCallCapReachedError:
                    run_provenance.record_refused()
                    raise
            started = time.perf_counter()
            try:
                resp = httpx.post(
                    self._chat_url(),
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.timeout_seconds,
                )
            except Exception:
                self._count(None, started, None, ok=False)
                raise
            if self.call_budget is not None:
                self.call_budget.record_response(resp)
            last_attempt = attempt == GROUNDING_MAX_ATTEMPTS
            if resp.status_code in _RETRYABLE_STATUS_CODES and not last_attempt:
                self._count(None, started, resp, ok=False)
                time.sleep(float(resp.headers.get("retry-after") or 2.0 * attempt))
                continue
            if _failed_status(resp):
                self._count(None, started, resp, ok=False)
            resp.raise_for_status()
            try:
                parsed = parse_json_object(resp.json()["choices"][0]["message"]["content"])
            except ValueError:
                self._count(None, started, resp, ok=False)
                if last_attempt:
                    raise
                logger.warning("Unparseable grounding reply (attempt %d); retrying", attempt)
                continue
            except Exception:
                self._count(None, started, resp, ok=False)
                raise
            self._count(None, started, resp, ok=True)
            return parsed
        raise RuntimeError("unreachable")  # pragma: no cover

    def _chat_json(
        self, images: Sequence[bytes], text: str, check: Callable[[dict[str, Any]], _Checked], max_tokens: int = 256
    ) -> _Checked:
        """One chat completion (images first, then ``text``) parsed as a JSON object and passed through ``check``
        (the strict reply parser). The attempt counts as failed in the run provenance unless ``check`` accepts the
        reply. Outside any grounding ``CropCallBudget``; an experiment's ``zoom_budget``, when set, is checked
        before each attempt.

        A rate limit or server error is retried once after ``retry-after`` (default 2 s); the gateway answers 429
        when a report's items are checked concurrently.
        """
        body = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        *(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
                                },
                            }
                            for image in images
                        ),
                        {"type": "text", "text": text},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        for attempt in (1, 2):
            if self.zoom_budget is not None:
                try:
                    self.zoom_budget.acquire()  # before sending: a refused attempt is never made
                except CropCallCapReachedError:
                    run_provenance.record_refused("zoom")
                    raise
            started = time.perf_counter()
            try:
                resp = httpx.post(
                    self._chat_url(),
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=ZOOM_TIMEOUT_SECONDS,
                )
            except Exception:
                self._count("zoom", started, None, ok=False)
                raise
            if self.zoom_budget is not None:
                self.zoom_budget.record_response(resp)
            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt == 1:
                self._count("zoom", started, resp, ok=False)
                time.sleep(float(resp.headers.get("retry-after") or 2.0))
                continue
            if _failed_status(resp):
                self._count("zoom", started, resp, ok=False)
            resp.raise_for_status()
            try:
                checked = check(parse_json_object(resp.json()["choices"][0]["message"]["content"]))
            except Exception:
                self._count("zoom", started, resp, ok=False)
                raise
            self._count("zoom", started, resp, ok=True)
            return checked
        raise RuntimeError("unreachable")  # pragma: no cover

    def zoom_name(
        self, jpeg: bytes, *, item_type: str, name: str, description: str, context_jpeg: bytes | None = None
    ) -> ZoomVerdict:
        """The second look on the same endpoint and model as grounding; outside any ``CropCallBudget``.

        Up to three small calls: a blind look at the frame with the box drawn (``context_jpeg``), a blind look at
        that frame plus the close-up ``jpeg``, and, only when the two looks agree (:func:`looks_agree`), a text-only
        merge of what they found with the report's text (:func:`merge_prompt`); disagreeing looks keep the report's
        text with no merge call. Without ``context_jpeg`` the close-up alone is the only look. The verdict's
        confidence is the lowest of the calls. Every call is a single attempt with its own, shorter timeout: the
        caller treats any error as "not checked" and the analysis stands. Raises ``httpx.HTTPError`` on transport
        or status errors and ``ValueError`` on a reply that is not the strict JSON asked for.
        """
        if context_jpeg is not None:
            frame_look = self._chat_json([context_jpeg], look_prompt(item_type, with_close_up=False), parse_look_reply)
            close_up_look = self._chat_json(
                [context_jpeg, jpeg], look_prompt(item_type, with_close_up=True), parse_look_reply
            )
        else:
            frame_look = close_up_look = self._chat_json([jpeg], look_prompt(item_type, False), parse_look_reply)
        seen = {
            "frame": frame_look.description if item_type == "entity" else frame_look.name,
            "close_up": close_up_look.description if item_type == "entity" else close_up_look.name,
        }
        look_confidence = min(frame_look.confidence, close_up_look.confidence)
        if not looks_agree(frame_look, close_up_look):  # no merge call: the report stands, both looks are recorded
            return ZoomVerdict(name=name, description=description, confidence=look_confidence, changed=False, seen=seen)
        # The close-up look carries the detail; the frame look confirmed it names the same thing.
        merged = self._chat_json([], merge_prompt(item_type, name, description, close_up_look), parse_zoom_reply)
        return ZoomVerdict(
            name=merged.name,
            description=merged.description,
            confidence=min(merged.confidence, look_confidence),
            changed=merged.changed,
            seen=seen,
        )

    def detect(self, frame: np.ndarray, query: str) -> list[Detection]:
        if not self.is_available():
            return []
        frame_h, frame_w = frame.shape[:2]
        scale = min(1.0, GROUNDING_MAX_EDGE_PIXELS / max(frame_h, frame_w))
        if scale < 1.0:
            frame = np.asarray(
                Image.fromarray(frame[..., ::-1]).resize((round(frame_w * scale), round(frame_h * scale)))
            )[..., ::-1]
        jpeg = encode_jpeg(np.ascontiguousarray(frame), quality=85)
        if not jpeg:
            return []

        prompt = incident_contract.render_prompt("grounding/best-match", query=query)
        body = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
        }
        data = self._request_box(body)
        raw_box = data.get("bbox_2d")
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            return []
        xmin, ymin, xmax, ymax = (max(0, min(1000, round(float(v)))) for v in raw_box)
        return [Detection(box=[ymin, xmin, ymax, xmax], score=1.0, label=str(data.get("label") or query))]

    def detect_all(self, frame: np.ndarray, query: str, limit: int = MAX_DETECTIONS_PER_QUERY) -> list[Detection]:
        """Every match of ``query`` in the frame (up to ``limit``), not only the best one: the whole-frame head search.

        One chat call on the same endpoint and model as :meth:`detect`, asking for a list of boxes. An empty list means
        the model saw none; a transport or status error raises, like :meth:`detect`.
        """
        if not self.is_available():
            return []
        frame_h, frame_w = frame.shape[:2]
        scale = min(1.0, GROUNDING_MAX_EDGE_PIXELS / max(frame_h, frame_w))
        if scale < 1.0:
            frame = np.asarray(
                Image.fromarray(frame[..., ::-1]).resize((round(frame_w * scale), round(frame_h * scale)))
            )[..., ::-1]
        jpeg = encode_jpeg(np.ascontiguousarray(frame), quality=85)
        if not jpeg:
            return []
        prompt = incident_contract.render_prompt("grounding/every-match", query=query)
        body = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
        }
        data = self._request_box(body)
        rows = data.get("detections")
        if rows is None and data.get("bbox_2d") is not None:
            rows = [data]  # a model that answered the single-box form
        detections: list[Detection] = []
        for row in rows if isinstance(rows, list) else []:
            raw_box = row.get("bbox_2d") if isinstance(row, dict) else None
            if not isinstance(raw_box, list) or len(raw_box) != 4:
                continue
            try:
                xmin, ymin, xmax, ymax = (max(0, min(1000, round(float(v)))) for v in raw_box)
            except (TypeError, ValueError):
                continue
            detections.append(Detection(box=[ymin, xmin, ymax, xmax], score=1.0, label=str(row.get("label") or query)))
        return detections[:limit]


class FakeDetector:
    """In-memory detector fixture for unit testing."""

    def __init__(self, detections: Sequence[Detection] | None = None) -> None:
        self.detections = list(detections) if detections is not None else []

    def is_available(self) -> bool:
        return True

    def detect(self, frame: np.ndarray, query: str) -> list[Detection]:  # noqa: ARG002
        return self.detections


def detect_heads(detector: Any, frame: np.ndarray) -> list[Detection]:
    """Every head in ``frame``: ``detect_all`` when the detector has it, else its ``detect`` (a port or a plain callable)."""
    if hasattr(detector, "detect_all"):
        raw = detector.detect_all(frame, privacy_blur.HEADS_QUERY)
    else:
        detect: Callable[[np.ndarray, str], Any] = detector.detect if hasattr(detector, "detect") else detector
        raw = detect(frame, privacy_blur.HEADS_QUERY)
    return parse_detections(raw, privacy_blur.HEADS_QUERY)


@dataclass(frozen=True)
class HeadScan:
    """The whole-frame head search of one second: padded head boxes, or ``failed`` (error, spent cap, unreadable frame)."""

    boxes: tuple[privacy_blur.Box, ...] = ()
    failed: bool = False


HeadSamples = dict[str, list[tuple[float, list[privacy_blur.Box], bool]]]
"""Per evidence item: ``(second, head boxes, from the known people only)`` over its clip window (``<itemId>-heads.json``)."""


def blurred_crop(crop: CropResult, regions: Sequence[privacy_blur.Box]) -> CropResult:
    """The crop result encoded again from its decoded frame blurred on ``regions`` (the decoded frame is dropped)."""
    if crop.frame is None:
        raise RuntimeError("the decoded frame was not kept")
    if not regions:
        return replace(crop, frame=None)
    frame = privacy_blur.blur_frame(crop.frame, list(regions))
    frame_bytes = encode_jpeg(frame, quality=FRAME_JPEG_QUALITY)
    image_bytes = None
    if crop.image_bytes is not None and crop.crop_px is not None:
        y0, x0, y1, x1 = crop.crop_px
        image_bytes = encode_jpeg(frame[y0:y1, x0:x1], quality=85)
        if image_bytes is None:
            raise RuntimeError("the blurred crop could not be encoded")
    if frame_bytes is None:
        raise RuntimeError("the blurred frame could not be encoded")
    return replace(crop, frame_bytes=frame_bytes, image_bytes=image_bytes, frame=None)


class EvidenceBlur:
    """The privacy blur of one run's evidence frames (``blur_faces``), shared by a fresh analysis and a re-blur.

    ``detector`` is the head search's own (unbudgeted) detector; its calls draw on ``privacy``'s cap instead.
    ``sample_clips`` also searches heads over each item's clip window for ``<itemId>-heads.json`` (it needs a video
    file or URL as ``video_ref`` and somewhere to store the result).
    """

    def __init__(
        self,
        *,
        privacy: privacy_blur.PrivacySummary,
        detector: Any,
        semaphore: asyncio.Semaphore,
        video_id: str,
        video_ref: str | Path | np.ndarray,
        video_seconds: float | None,
        sample_clips: bool,
    ) -> None:
        self.privacy = privacy
        self.detector = detector
        self.semaphore = semaphore
        self.video_id = video_id
        self.video_ref = video_ref
        self.video_seconds = video_seconds
        self.sample_clips = sample_clips
        self.head_scans: dict[float, asyncio.Task[HeadScan]] = {}

    def _scan_heads(self, seconds: float, frame_of: Callable[[], np.ndarray | None]) -> asyncio.Task[HeadScan]:
        """The whole-frame head search of one second, started once however many items ask for it."""
        key = round(seconds, 3)
        task = self.head_scans.get(key)
        if task is None:
            task = self.head_scans[key] = asyncio.ensure_future(self._find_heads(key, frame_of))
        return task

    async def _find_heads(self, seconds: float, frame_of: Callable[[], np.ndarray | None]) -> HeadScan:
        """One detector call for every head in the frame at ``seconds``; a failure is a ``failed`` scan, never raised."""
        privacy = self.privacy
        # The cap is taken before any await, so calls are granted in the order the scans were asked for: the
        # stored frames first, then the clip samples.
        if not privacy.take_call():
            run_provenance.record_refused("privacy_heads")  # the blur's cap refused it before sending
            privacy.fallbacks += 1
            return HeadScan(failed=True)
        try:
            async with self.semaphore:
                frame = await asyncio.to_thread(frame_of)
                if frame is None:
                    privacy.refund_call()  # nothing was sent
                    privacy.fallbacks += 1
                    return HeadScan(failed=True)
                with run_provenance.stage_scope("privacy_heads"):
                    detections = await asyncio.to_thread(detect_heads, self.detector, frame)
        except Exception as exc:
            logger.warning("Head detection failed for %s at %ss: %s", self.video_id, seconds, type(exc).__name__)
            privacy.fallbacks += 1
            return HeadScan(failed=True)
        boxes = tuple(
            privacy_blur.pad_box(d.box, privacy_blur.DETECTED_HEAD_PADDING)
            for d in detections
            if privacy_blur.valid_box(d.box)
        )
        privacy.head_detections += len(boxes)
        return HeadScan(boxes)

    async def run(
        self,
        entries: Sequence[dict[str, Any]],
        crops: dict[str, CropResult],
        tracks: Sequence[track_evidence.Track],
        carried: Mapping[str, Sequence[privacy_blur.Box]] | None = None,
    ) -> HeadSamples:
        """Blur every head in each frame of ``crops`` (replaced in place): the ones found in it, then the known people's.

        The known people are every track, every located person's own box, and the ``carried`` regions of an item
        (its earlier blur, when this is a re-blur). An item whose frame cannot be blurred loses its crop and gets an
        ``image_gap`` (fail closed). Returns each blurred item's clip heads when ``sample_clips`` is on.
        """
        privacy = self.privacy
        head_samples: HeadSamples = {}

        def seconds_of(entry: dict[str, Any], crop: CropResult) -> float:
            return float(crop.box_seconds if crop.box_seconds is not None else entry["timestamp_seconds"])

        people: list[tuple[float, privacy_blur.Box]] = []
        for entry in entries:
            crop = crops.get(entry["item_id"])
            if entry["item_type"] == "entity" and crop is not None and crop.located and crop.normalized_box:
                people.append((seconds_of(entry, crop), privacy_blur.head_region(crop.normalized_box)))

        def known_heads(seconds: float) -> list[privacy_blur.Box]:
            return privacy_blur.unique_regions(
                [
                    *privacy_blur.track_heads_at(tracks, seconds),
                    *(head for at, head in people if abs(at - seconds) <= privacy_blur.TRACK_WINDOW_SECONDS),
                ]
            )

        stored = {
            entry["item_id"]: (entry, crop, seconds_of(entry, crop))
            for entry in entries
            if (crop := crops.get(entry["item_id"])) is not None
        }
        frame_scans = {
            item_id: self._scan_heads(seconds, partial(_decoded_frame, crop))
            for item_id, (_, crop, seconds) in stored.items()
        }
        # The same search over each item's clip window, so the console can blur the clip second by second.
        plans: dict[str, list[float]] = {}
        video_ref = self.video_ref
        if self.sample_clips and not isinstance(video_ref, np.ndarray):
            for item_id, (_, _, seconds) in stored.items():
                plans[item_id] = [round(t, 3) for t in privacy_blur.clip_sample_times(seconds, self.video_seconds)]
            seen = set(self.head_scans)
            for times in plans.values():
                for t in times:
                    if t not in self.head_scans:
                        self._scan_heads(t, partial(extract_frame, video_ref, t))
            privacy.clip_samples = len(set(self.head_scans) - seen)
        scanned = dict(zip(self.head_scans, await asyncio.gather(*self.head_scans.values()), strict=True))

        for item_id, (entry, crop, seconds) in stored.items():
            scan = await frame_scans[item_id]
            known = privacy_blur.unique_regions([*known_heads(seconds), *((carried or {}).get(item_id) or ())])
            try:
                if scan.failed and not known:
                    raise RuntimeError("the heads could not be searched for and no person is known")
                regions = privacy_blur.unique_regions([*known, *scan.boxes])
                crops[item_id] = await asyncio.to_thread(blurred_crop, crop, regions)
            except Exception as exc:
                # Fail closed: an image that could not be blurred is never stored.
                logger.warning("Privacy blur failed for %s %s: %s", self.video_id, item_id, exc)
                crops.pop(item_id, None)
                entry["image_gap"] = f"privacy blur failed, so no image was stored: {type(exc).__name__}"
                privacy.failed += 1
                continue
            entry["blur_regions"] = [list(region) for region in regions]
            if regions:
                privacy.frames += 1
                privacy.regions += len(regions)
            if item_id in plans:
                head_samples[item_id] = [
                    (t, known_heads(t), True) if scanned[t].failed else (t, list(scanned[t].boxes), False)
                    for t in plans[item_id]
                ]
        return head_samples


def _decoded_frame(crop: CropResult) -> np.ndarray | None:
    return crop.frame


class FakeZoomNamer:
    """In-memory second-look fixture: a canned reply per current item name.

    A reply is a parsed JSON dict, a raw reply string (parsed like a model's, so a malformed one raises), a ready
    ``ZoomVerdict`` or an exception to raise. An item with no reply is confirmed unchanged at confidence 1. Every call is recorded.
    """

    def __init__(self, replies: dict[str, Any] | None = None) -> None:
        self.replies = dict(replies or {})
        self.calls: list[dict[str, Any]] = []

    def zoom_name(
        self, jpeg: bytes, *, item_type: str, name: str, description: str, context_jpeg: bytes | None = None
    ) -> ZoomVerdict:
        self.calls.append(
            {
                "jpeg_bytes": len(jpeg),
                "context_bytes": len(context_jpeg or b""),
                "item_type": item_type,
                "name": name,
                "description": description,
            }
        )
        reply = self.replies.get(name)
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            return ZoomVerdict(name=name, description=description, confidence=1.0, changed=False)
        if isinstance(reply, ZoomVerdict):
            return reply
        return parse_zoom_reply(parse_json_object(reply) if isinstance(reply, str) else reply)


def _not_located_result(frame: np.ndarray, seconds: int | float, keep_frame: bool = False) -> CropResult | None:
    """The frame alone: no crop and no box, so nothing downstream mistakes it for a located object."""
    frame_h, frame_w = frame.shape[:2]
    frame_bytes = encode_jpeg(frame, quality=FRAME_JPEG_QUALITY)
    if not frame_bytes:
        return None
    return CropResult(
        image_bytes=None,
        normalized_box=None,
        score=0.0,
        frame_size=(frame_w, frame_h),
        frame_bytes=frame_bytes,
        box_seconds=seconds,
        located=False,
        frame=frame if keep_frame else None,
    )


def parse_detections(raw_detections: Any, label: str) -> list[Detection]:
    """A detector's answer as ``Detection`` objects (it may return dicts or look-alike objects)."""
    parsed_detections: list[Detection] = []
    for d in raw_detections or []:
        if isinstance(d, Detection):
            parsed_detections.append(d)
        elif isinstance(d, dict):
            box = d.get("box") or d.get("bounding_box") or [0, 0, 0, 0]
            score = float(d.get("score", 0.0))
            lbl = str(d.get("label", label))
            parsed_detections.append(Detection(box=list(box), score=score, label=lbl))
        elif hasattr(d, "box") and hasattr(d, "score"):
            parsed_detections.append(Detection(box=list(d.box), score=float(d.score), label=getattr(d, "label", label)))
    return parsed_detections


def _locate_in_frame(
    frame: np.ndarray,
    seconds: int | float,
    raw_detections: Any,
    label: str,
    min_score: float,
    keep_frame: bool = False,
) -> CropResult | None:
    """The crop of the best usable detection of one frame, or None when the detector found no usable box."""
    parsed_detections = parse_detections(raw_detections, label)
    if not parsed_detections:
        return None

    best = max(parsed_detections, key=lambda d: d.score)
    if best.score < min_score or len(best.box) != 4:
        return None
    ymin, xmin, ymax, xmax = best.box
    if ymin >= ymax or xmin >= xmax:
        return None

    frame_h, frame_w = frame.shape[:2]
    c_ymin, c_xmin, c_ymax, c_xmax = apply_box_padding(best.box, frame_w, frame_h, padding_fraction=0.05)
    if c_ymin >= c_ymax or c_xmin >= c_xmax:
        return None
    jpeg_bytes = encode_jpeg(frame[c_ymin:c_ymax, c_xmin:c_xmax], quality=85)
    frame_bytes = encode_jpeg(frame, quality=FRAME_JPEG_QUALITY)
    if not jpeg_bytes or not frame_bytes:
        return None
    return CropResult(
        image_bytes=jpeg_bytes,
        normalized_box=normalize_box(c_ymin, c_xmin, c_ymax, c_xmax, frame_w, frame_h),
        score=best.score,
        frame_size=(frame_w, frame_h),
        frame_bytes=frame_bytes,
        box_seconds=seconds,
        zoom_bytes=zoom_crop(frame, best.box),
        zoom_frame_bytes=zoom_context_frame(frame, best.box),
        frame=frame if keep_frame else None,
        crop_px=(c_ymin, c_xmin, c_ymax, c_xmax),
        detected_box=(ymin, xmin, ymax, xmax),
    )


def crop_evidence_item(
    video_ref: str | Path | np.ndarray,
    timestamp_seconds: int | float,
    label: str,
    detector: Any | None = None,
    min_score: float = 0.0,
    not_located_fallback: bool = False,
    retry_offsets: Sequence[int] = (),
    keep_frame: bool = False,
) -> CropResult | None:
    """Locate an evidence object in a video at the specified timestamp using a detector.

    Args:
        video_ref: Video file path, URL, or pre-extracted BGR numpy frame (a frame is never retried at other seconds).
        timestamp_seconds: Seconds from video start where item is prominently visible.
        label: Short search query/label for the detector (e.g. 'crowbar', 'person in hoodie').
        detector: Detector port implementing detect(frame, query) or callable.
        min_score: Minimum confidence score threshold (default 0.0).
        not_located_fallback: When no second yields a usable box, return the requested second's frame alone
            (``located`` False, no crop, no box) instead of None. A missing frame still returns None.
        retry_offsets: Seconds added to ``timestamp_seconds`` and tried in order when the detector finds
            nothing there; the first hit wins and ``box_seconds`` records the second it was found at. Every
            second tried is one detector call against the request's crop-call budget.
        keep_frame: Keep the decoded frame on the result (``frame``) so the privacy blur can encode it again.

    Returns:
        CropResult or None when nothing is found or detector is unavailable.

    Raises:
        CropCallCapReachedError: when the budgeted detector has no calls left (propagated, never swallowed).
    """
    if detector is None:
        return None

    if hasattr(detector, "is_available") and not detector.is_available():
        return None

    attempts = [0, *([] if isinstance(video_ref, np.ndarray) else retry_offsets)]
    first_frame: np.ndarray | None = None
    for offset in attempts:
        seconds = timestamp_seconds + offset
        if seconds < 0:
            continue
        frame: np.ndarray | None = video_ref if isinstance(video_ref, np.ndarray) else extract_frame(video_ref, seconds)
        if frame is None or frame.size == 0 or min(frame.shape[:2]) <= 0:
            continue
        if first_frame is None:
            first_frame = frame
        try:
            if hasattr(detector, "detect"):
                raw_detections = detector.detect(frame, label)
            elif callable(detector):
                raw_detections = detector(frame, label)
            else:
                return None
        except CropCallCapReachedError:
            raise
        except Exception as exc:
            logger.warning("Detector execution failed for query '%s': %s", label, exc)
            return None
        found = _locate_in_frame(frame, seconds, raw_detections, label, min_score, keep_frame)
        if found is not None:
            return found

    if not_located_fallback and first_frame is not None:
        return _not_located_result(first_frame, timestamp_seconds, keep_frame)
    return None


class EvidenceStore(ABC):
    """Port for persisting evidence crop images."""

    @abstractmethod
    async def put_image(self, key: str, image_bytes: bytes, content_type: str = "image/jpeg") -> str:
        """Store image bytes under the given key and return the key or accessible URL."""
        ...

    @abstractmethod
    async def get_image(self, key: str) -> bytes | None:
        """Retrieve stored image bytes by key."""
        ...

    @abstractmethod
    async def next_batch_id(self, video_key: str) -> str:
        """The next unused ``run-<n>`` batch id under ``evidence/<video_key>/``."""
        ...


class InMemoryEvidenceStore(EvidenceStore):
    """In-memory adapter for hermetic unit testing and zero-cloud environments."""

    def __init__(self) -> None:
        self.images: dict[str, bytes] = {}

    async def put_image(self, key: str, image_bytes: bytes, content_type: str = "image/jpeg") -> str:  # noqa: ARG002
        self.images[key] = image_bytes
        return key

    async def get_image(self, key: str) -> bytes | None:
        return self.images.get(key)

    async def next_batch_id(self, video_key: str) -> str:
        prefix = f"evidence/{video_key}/"
        return next_run_batch_id(key[len(prefix) :].split("/")[0] for key in self.images if key.startswith(prefix))


class R2EvidenceStore(EvidenceStore):
    """Cloudflare R2 adapter for evidence crop images (the production store)."""

    def __init__(self, bucket: str | None = None, client: Any = None) -> None:
        self.bucket = bucket or os.getenv("R2_BUCKET", "")
        self._client = client

    def _get_client(self) -> Any:
        return self._client if self._client is not None else get_r2_client()

    async def put_image(self, key: str, image_bytes: bytes, content_type: str = "image/jpeg") -> str:
        # IfNoneMatch="*" makes R2 refuse (412) a key that exists, so a stored crop is never overwritten.
        await asyncio.to_thread(
            self._get_client().put_object,
            Bucket=self.bucket,
            Key=key,
            Body=image_bytes,
            ContentType=content_type,
            IfNoneMatch="*",
        )
        return key

    async def next_batch_id(self, video_key: str) -> str:
        def _list_batches() -> list[str]:
            prefix = f"evidence/{video_key}/"
            batches: list[str] = []
            for page in (
                self._get_client()
                .get_paginator("list_objects_v2")
                .paginate(Bucket=self.bucket, Prefix=prefix, Delimiter="/")
            ):
                batches.extend(
                    str(entry["Prefix"])[len(prefix) :].rstrip("/") for entry in page.get("CommonPrefixes", [])
                )
            return batches

        return next_run_batch_id(await asyncio.to_thread(_list_batches))

    async def get_image(self, key: str) -> bytes | None:
        try:
            obj = await asyncio.to_thread(self._get_client().get_object, Bucket=self.bucket, Key=key)
            return bytes(obj["Body"].read())
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise


def evidence_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The entities, instruments and assets of an incident-contract-v4 report as evidence entries.

    Each entry carries the ``evidence_crops`` item columns (``item_type``, ``item_id``,
    ``item_name``, ``description``, ``threat_level``, ``holder_entity_id``, ``timestamp_seconds``)
    plus the detector ``label``. Entities also carry ``first_seen_seconds`` and ``last_seen_seconds``
    (reports stored before ``incident-contract-v4`` lack them, so they fall back to ``timestamp_seconds``); :meth:`CropEvidenceService.process_incident_report` adds
    ``bounding_box``, ``image`` and ``image_gap``.
    """
    items: list[dict[str, Any]] = []
    for entity in report.get("entities") or []:
        seen_at = _whole_seconds(entity.get("timestamp_seconds"))
        items.append(
            {
                "item_type": "entity",
                "item_id": str(entity.get("entity_id") or ""),
                "item_name": str(entity.get("type") or "entity"),
                "description": str(entity.get("description") or ""),
                "threat_level": None,
                "holder_entity_id": None,
                "timestamp_seconds": seen_at,
                "first_seen_seconds": _whole_seconds(entity.get("first_seen_seconds", seen_at)),
                "last_seen_seconds": _whole_seconds(entity.get("last_seen_seconds", seen_at)),
                "label": str(entity.get("label") or entity.get("description") or entity.get("type") or "person"),
            }
        )
    for instrument in report.get("instruments") or []:
        items.append(
            {
                "item_type": "instrument",
                "item_id": str(instrument.get("instrument_id") or ""),
                "item_name": str(instrument.get("name") or "instrument"),
                "description": str(instrument.get("description") or ""),
                "threat_level": instrument.get("threat_level"),
                "holder_entity_id": instrument.get("entity_id"),
                "timestamp_seconds": _whole_seconds(instrument.get("timestamp_seconds")),
                "label": str(instrument.get("label") or instrument.get("name") or "instrument"),
            }
        )
    for asset in report.get("assets") or []:
        items.append(
            {
                "item_type": "asset",
                "item_id": str(asset.get("asset_id") or ""),
                "item_name": str(asset.get("name") or "asset"),
                "description": str(asset.get("description") or ""),
                "threat_level": None,
                "holder_entity_id": None,
                "timestamp_seconds": _whole_seconds(asset.get("timestamp_seconds")),
                "label": str(asset.get("label") or asset.get("name") or "asset"),
            }
        )
    return [item for item in items if item["item_id"]]


ZOOM_ENTRY_FIELDS: dict[str, Any] = {
    "zoom_checked": False,
    "zoom_confidence": None,
    "zoom_seen": None,
    "name_before": None,
    "zoom_gap": None,
}


def blank_evidence_entries(
    report: dict[str, Any], image_gap: str | None, zoom_gap: str | None = None
) -> list[dict[str, Any]]:
    """The evidence entries of ``report`` before any image or second look: every image field null, the gaps given."""
    return [
        {
            **item,
            "bounding_box": None,
            "image": None,
            "frame": None,
            "box_seconds": None,
            "located": None,
            "image_gap": image_gap,
            **ZOOM_ENTRY_FIELDS,
            "zoom_gap": zoom_gap,
        }
        for item in evidence_items(report)
    ]


_REPORT_ITEM_ID_FIELDS = {
    "entity": ("entities", "entity_id"),
    "instrument": ("instruments", "instrument_id"),
    "asset": ("assets", "asset_id"),
}


def apply_zoom_verdict(entry: dict[str, Any], report: dict[str, Any], verdict: ZoomVerdict) -> bool:
    """Record the second look on ``entry`` and apply its correction to the entry and the report payload item.

    A correction is applied only when the model says ``changed`` with confidence at least
    ``ZOOM_CONFIDENCE_THRESHOLD``: the entry's ``item_name`` and ``description`` and the matching report item's
    name and description (an entity keeps its ``type``; only its description changes) are replaced, and the entry
    keeps the previous name as ``name_before`` (and ``description_before``). ``zoom_seen`` records what the blind
    looks found. The item id, label and timestamps are never touched. Returns whether a correction was applied.
    """
    entry["zoom_checked"] = True
    entry["zoom_confidence"] = verdict.confidence
    entry["zoom_seen"] = verdict.seen
    if not verdict.changed:
        if entry["item_type"] != "entity" and verdict.seen and len(set(verdict.seen.values())) > 1:
            entry["zoom_gap"] = "looks disagree, report kept: " + ", ".join(
                f"{where} saw {what!r}" for where, what in verdict.seen.items()
            )
        return False
    if verdict.confidence < ZOOM_CONFIDENCE_THRESHOLD:
        entry["zoom_gap"] = (
            f"correction to {verdict.name!r} ignored: confidence {verdict.confidence:.2f} is below "
            f"{ZOOM_CONFIDENCE_THRESHOLD}"
        )
        return False
    is_entity = entry["item_type"] == "entity"
    new_name = entry["item_name"] if is_entity else verdict.name
    new_description = verdict.description or entry["description"]
    if new_name == entry["item_name"] and new_description == entry["description"]:
        return False  # the model said changed but repeated the current text
    entry["name_before"] = entry["item_name"]
    entry["description_before"] = entry["description"]
    entry["item_name"] = new_name
    entry["description"] = new_description
    collection, id_field = _REPORT_ITEM_ID_FIELDS[entry["item_type"]]
    for item in report.get(collection) or []:
        if str(item.get(id_field) or "") == entry["item_id"]:
            item["description"] = new_description
            if not is_entity:
                item["name"] = new_name
            break
    return True


def _whole_seconds(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 0
    seconds: float = value
    return max(0, round(seconds))


class CropEvidenceService:
    """Service orchestrating object crop extraction and image upload for one analysis run."""

    def __init__(
        self,
        detector: Any | None = None,
        store: EvidenceStore | None = None,
        namer: Any | None = None,
    ) -> None:
        self.detector = detector if detector is not None else DefaultDetector()
        self.store = store
        # The second look's VLM client: the detector itself when it can name (DefaultDetector does, unbudgeted).
        self.namer = namer if namer is not None else (self.detector if hasattr(self.detector, "zoom_name") else None)

    def is_detector_available(self) -> bool:
        """Check whether the active detector is ready and available."""
        if self.detector is None:
            return False
        if hasattr(self.detector, "is_available"):
            return bool(self.detector.is_available())
        return True

    def crop_item(
        self,
        video_ref: str | Path | np.ndarray,
        timestamp_seconds: int | float,
        label: str,
        detector: Any | None = None,
        keep_frame: bool = False,
    ) -> CropResult | None:
        """Locate an evidence item with the configured detector, retrying nearby seconds (the frame alone when not found)."""
        active_detector = detector if detector is not None else self.detector
        return crop_evidence_item(
            video_ref,
            timestamp_seconds,
            label,
            active_detector,
            not_located_fallback=True,
            retry_offsets=LOCATE_RETRY_OFFSETS,
            keep_frame=keep_frame,
        )

    async def process_incident_report(
        self,
        *,
        video_ref: str | Path | np.ndarray,
        video_id: str,
        report: dict[str, Any],
        video_key: str | None = None,
        batch_id: str | None = None,
        detector: Any | None = None,
        store: EvidenceStore | None = None,
        tracking: track_evidence.TrackingRequest | None = None,
        zoom_names: bool | None = None,
        privacy: privacy_blur.PrivacySummary | None = None,
        object_tracking: track_evidence.ObjectTrackingRequest | None = None,
        video_seconds: float | None = None,
        namer: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Locate every entity, instrument and asset of a report and upload its frame and crop.

        Returns one evidence entry per item (see :func:`evidence_items`) for the revision's
        ``evidence`` JSONB. ``frame`` is the R2 key ``evidence/<video key>/run-<n>/<itemId>-frame.jpg`` of the whole video frame
        (``video_key`` is the video's R2 key; callers without one, such as the prompt experiments, fall back to ``video_id``),
        ``image`` the key of the object crop beside it (``<itemId>.jpg``, what evidence search embeds), ``bounding_box`` the normalised
        crop box in that frame and ``box_seconds`` the second the frame was read at (a retry second when the requested one found
        nothing). ``located`` is true when the detector found the object; when it did not, only ``frame`` is stored, ``image``
        and ``bounding_box`` are null, ``located`` is false and ``image_gap`` says so. When no frame could be produced, ``frame``
        is null as well and ``image_gap`` says why. Never raises for a single item.

        Zoomed second look (``zoom_names``, default :func:`zoom_names_enabled`): each located item's close-up is
        shown to the service's ``namer`` (the grounding VLM) and :func:`apply_zoom_verdict` records the outcome on
        the entry (``zoom_checked``, ``zoom_confidence``, ``name_before`` when corrected, ``zoom_gap`` when not
        checked or ignored) and corrects the matching item of ``report`` **in place**, so the payload the caller
        persists agrees with the evidence. Zoom calls run inside the item's concurrency slot and outside any
        ``CropCallBudget``: ``max_crop_calls`` counts grounding attempts only. A zoom failure never fails the item.

        Privacy blur (``privacy`` given): after the locate step, tracking and the best-moment re-cut, and before
        anything is uploaded, every frame is blurred on the heads found in it by a whole-frame head detection of its
        own second, joined by the known person tracks and located person boxes (:class:`EvidenceBlur`), and the crop
        is cut from the blurred frame. Each entry records the regions as ``blur_regions`` (normalised boxes, ``[]``
        when none) and ``privacy`` is filled in. When a second's detection fails or the request's detector-call cap is
        spent, the frame is blurred on the known people only, and with none known the item stores no image at all
        (``image_gap`` says why), as for any other blur failure. The heads are also searched at sampled seconds over
        the item's evidence-clip window (``video_seconds``, the video's length when known) and stored as
        ``<itemId>-heads.json``, whose key the entry carries as ``heads``.

        Object tracking (``object_tracking`` given, :mod:`vss_agents.utils.object_tracking`): each located instrument
        and asset is followed through the clip from its box by a seeded OpenCV tracker that the detector keeps
        correcting, beside the person tracking run. The resulting track is stored like a person track
        (``track``, ``track_first_seen_seconds``, ``track_last_seen_seconds``) with ``track_source`` set to
        ``object-tracker``, and the item's frame is re-cut at the track's best moment.
        """
        active_detector = detector if detector is not None else self.detector
        zoom_on = zoom_names_enabled() if zoom_names is None else zoom_names
        # ``namer`` overrides the shared one for this call (an experiment's zoom-capped copy).
        namer = (namer if namer is not None else self.namer) if zoom_on else None
        active_store = store if store is not None else self.store
        folder = video_key or video_id
        batch = batch_id or (await active_store.next_batch_id(folder) if active_store is not None else "run-1")

        head_detector = unbudgeted(active_detector)  # the blur's head search has its own call cap
        if detector is not None:
            detector_ready = bool(detector.is_available()) if hasattr(detector, "is_available") else True
        else:
            detector_ready = self.is_detector_available()
        semaphore = asyncio.Semaphore(EVIDENCE_CROP_CONCURRENCY)
        crops: dict[str, CropResult] = {}
        head_samples: HeadSamples = {}

        tracking_task: asyncio.Task[track_evidence.TrackingOutcome] | None = None
        if tracking is not None:
            if active_store is None:
                tracking.summary.skipped = "no evidence image store is configured (R2 settings missing)"
            elif isinstance(video_ref, np.ndarray):
                tracking.summary.skipped = "tracking needs a video file or URL"
            else:
                # Runs beside the locate step; the images are uploaded only once both are done, because a stored
                # key is never overwritten and a matched entity's image is re-cut at its best moment.
                tracking_task = asyncio.create_task(
                    tracking.runner(video_ref, sensor_id=tracking.sensor_id, **tracking.options)
                )

        async def _crop(entry: dict[str, Any]) -> None:
            if active_store is None:
                entry["image_gap"] = "no evidence image store is configured (R2 settings missing)"
                return
            if not detector_ready:
                entry["image_gap"] = "no evidence detector is configured (VLM grounding key missing)"
                return
            async with semaphore:
                try:
                    # keep_frame only with the blur on, so a crop_item override with the older signature still works
                    crop = await asyncio.to_thread(
                        self.crop_item,
                        video_ref,
                        entry["timestamp_seconds"],
                        entry["label"],
                        active_detector,
                        **({"keep_frame": True} if privacy is not None else {}),
                    )
                except CropCallCapReachedError as exc:
                    entry["image_gap"] = f"crop-call cap reached: {exc}"
                    return
                except Exception as exc:
                    logger.warning("Evidence crop failed for %s %s: %s", video_id, entry["item_id"], exc)
                    entry["image_gap"] = f"crop failed: {type(exc).__name__}: {exc}"
                    return
            if crop is None or crop.frame_bytes is None:
                entry["image_gap"] = (
                    f"no frame produced at {entry['timestamp_seconds']}s (frame unreadable or detector failed)"
                )
                return
            crops[entry["item_id"]] = crop

        async def _upload(entry: dict[str, Any], track: track_evidence.Track | None) -> None:
            crop = crops.get(entry["item_id"])
            if crop is None or crop.frame_bytes is None or active_store is None:
                return
            frame_key = evidence_frame_key(folder, batch, entry["item_id"])
            key = evidence_key(folder, batch, entry["item_id"])
            track_key = evidence_track_key(folder, batch, entry["item_id"])
            try:
                await active_store.put_image(frame_key, crop.frame_bytes)
                if crop.image_bytes is not None:
                    await active_store.put_image(key, crop.image_bytes)
                if track is not None:
                    await active_store.put_image(track_key, track_evidence.track_json(track), "application/json")
            except Exception as exc:
                logger.warning("Evidence image upload failed for %s: %s", key, exc)
                entry["image_gap"] = f"image upload failed: {type(exc).__name__}: {exc}"
                return
            samples = head_samples.get(entry["item_id"])
            if samples is not None:
                heads_key = evidence_heads_key(folder, batch, entry["item_id"])
                try:
                    await active_store.put_image(heads_key, privacy_blur.heads_json(samples), "application/json")
                    entry["heads"] = heads_key
                except Exception as exc:  # the clip then falls back to the tracks and the recorded heads
                    logger.warning("Clip heads upload failed for %s: %s", heads_key, type(exc).__name__)
            entry["frame"] = frame_key
            entry["box_seconds"] = crop.box_seconds
            entry["located"] = crop.located
            if crop.located and crop.image_bytes is not None:
                entry["image"] = key
                entry["bounding_box"] = crop.normalized_box
                detected = getattr(crop, "detected_box", None)  # a custom cropper may not report it
                if detected is not None:  # the detector's own box, before the 5% crop padding
                    entry["detected_box"] = list(detected)
                if track is not None:
                    entry["track"] = track_key
                    entry["track_first_seen_seconds"] = track.first_seen
                    entry["track_last_seen_seconds"] = track.last_seen
                    if track.source != track_evidence.PERSON_TRACK_SOURCE:
                        entry["track_source"] = track.source
                    if tracking is not None and track.source == track_evidence.PERSON_TRACK_SOURCE:
                        tracking.summary.matched += 1
                        if tracking.summary.marks is not None:
                            path = "parsed" if entry["item_id"] in tracking.marked else "fallback"
                            tracking.summary.marks[path] += 1
                async with semaphore:
                    await _zoom_second_look(entry, crop)
            else:
                entry["image_gap"] = (
                    f"object not located at {entry['timestamp_seconds']}s or within {max(LOCATE_RETRY_OFFSETS)}s either side"
                )
                entry["zoom_gap"] = "object not located"

        async def _recut(entry: dict[str, Any], track: track_evidence.Track) -> track_evidence.Track | None:
            """Re-cut a matched entity's frame and crop at the track's best moment; None keeps today's result."""
            moment = track_evidence.best_moment(track)

            def _cut() -> CropResult | None:
                if isinstance(video_ref, np.ndarray):
                    return None
                frame = extract_frame(video_ref, moment.t)
                if frame is None:
                    return None
                return _locate_in_frame(
                    frame,
                    moment.t,
                    [Detection(box=list(moment.box), score=1.0)],
                    entry["label"],
                    0.0,
                    keep_frame=privacy is not None,
                )

            try:
                async with semaphore:
                    recut = await asyncio.to_thread(_cut)
            except Exception as exc:
                logger.warning("Track re-cut failed for %s %s: %s", video_id, entry["item_id"], exc)
                return None
            if recut is None:
                return None
            crops[entry["item_id"]] = recut
            return track

        async def _zoom_second_look(entry: dict[str, Any], crop: CropResult) -> None:
            if not zoom_on:
                entry["zoom_gap"] = f"zoomed second look disabled ({ZOOM_NAMES_ENV})"
                return
            if namer is None or (hasattr(namer, "is_available") and not namer.is_available()):
                entry["zoom_gap"] = "no second-look VLM client is configured (VLM grounding key missing)"
                return
            try:
                if crop.zoom_bytes is None:
                    entry["zoom_gap"] = "no close-up could be cut from the frame"
                    return
                verdict = await asyncio.to_thread(
                    namer.zoom_name,
                    crop.zoom_bytes,
                    item_type=entry["item_type"],
                    name=entry["item_name"],
                    description=entry["description"],
                    context_jpeg=crop.zoom_frame_bytes,
                )
            except CropCallCapReachedError as exc:
                entry["zoom_gap"] = f"zoom-call cap reached: {exc}"
                return
            except Exception as exc:
                logger.warning("Zoomed second look failed for %s %s: %s", video_id, entry["item_id"], exc)
                entry["zoom_gap"] = f"second look failed: {type(exc).__name__}: {exc}"
                return
            if apply_zoom_verdict(entry, report, verdict):
                logger.info(
                    "Zoomed second look corrected %s %s: name %r -> %r, description %r -> %r (confidence %.2f)",
                    video_id,
                    entry["item_id"],
                    entry["name_before"],
                    entry["item_name"],
                    entry["description_before"],
                    entry["description"],
                    verdict.confidence,
                )

        async def _track_objects() -> dict[str, track_evidence.Track]:
            """Follow each located instrument and asset through the clip; ``{item_id: track}`` of those that gained one."""
            assert object_tracking is not None
            summary = object_tracking.summary
            seeds = [
                track_evidence.ObjectSeed(
                    item_id=entry["item_id"],
                    label=entry["label"],
                    box=crop.detected_box,
                    seconds=float(crop.box_seconds if crop.box_seconds is not None else entry["timestamp_seconds"]),
                    score=crop.score,
                )
                for entry in entries
                if entry["item_type"] in OBJECT_TRACKING_TYPES
                and (crop := crops.get(entry["item_id"])) is not None
                and crop.located
                and crop.detected_box is not None
            ]
            summary.seeds = len(seeds)
            if active_store is None:
                summary.skipped = "no evidence image store is configured (R2 settings missing)"
                return {}
            if not seeds:
                summary.skipped = "no instrument or asset was located"
                return {}
            try:
                with run_provenance.stage_scope("object_tracking"):
                    outcome = await object_tracking.runner(
                        video_ref,
                        seeds,
                        detector=with_call_budget(active_detector, object_tracking.budget),
                        **object_tracking.options,
                    )
            except Exception as exc:  # run_object_tracking never raises; a custom runner might
                logger.warning("Object tracking failed for %s: %s", video_id, type(exc).__name__)
                outcome = track_evidence.ObjectTrackingOutcome(skipped=f"object tracking failed: {type(exc).__name__}")
            summary.tracked = len(outcome.tracks)
            summary.skipped = outcome.skipped
            summary.elapsed_ms = outcome.elapsed_ms
            summary.time_cap_reached = outcome.time_cap_reached
            summary.tracker = outcome.tracker
            summary.items = outcome.items
            summary.budget = object_tracking.budget.summary()
            return outcome.tracks

        async def _confirm_entities(
            tracks: Sequence[track_evidence.Track], skip: Collection[str], min_iou: float
        ) -> None:
            """Check each located person's box against the person tracks and move the ones no track confirms.

            A box is confirmed when a solid track's sample within 0.5 s of its second overlaps it (the test the
            association applies). An unconfirmed box, such as a person pictured on a screen, is retried at the
            locate seconds not yet tried and replaced by the first box a track confirms; with none, the box
            becomes the nearest track sample inside the entity's first to last seen span. No detector call is made
            when the first box is confirmed, and nothing changes without solid person tracks.
            """
            solid = [track for track in tracks if track_evidence.is_solid(track)]
            if not solid or isinstance(video_ref, np.ndarray):
                return

            def seconds_of(entry: dict[str, Any], crop: CropResult) -> float:
                return float(crop.box_seconds if crop.box_seconds is not None else entry["timestamp_seconds"])

            pending = [
                (entry, crop)
                for entry in entries
                if entry["item_type"] == "entity"
                and entry["item_id"] not in skip
                and (crop := crops.get(entry["item_id"])) is not None
                and crop.located
                and crop.normalized_box
                and not track_evidence.box_confirmed(crop.normalized_box, seconds_of(entry, crop), solid, min_iou)
            ]

            async def _retry(entry: dict[str, Any], crop: CropResult) -> CropResult | None:
                base = entry["timestamp_seconds"]
                attempts = [0, *LOCATE_RETRY_OFFSETS]
                found = seconds_of(entry, crop) - base
                untried = attempts[attempts.index(found) + 1 :] if found in attempts else []
                for offset in untried:
                    if base + offset < 0:
                        continue
                    try:
                        async with semaphore:
                            again = await asyncio.to_thread(
                                crop_evidence_item,
                                video_ref,
                                base + offset,
                                entry["label"],
                                active_detector,
                                keep_frame=privacy is not None,
                            )
                    except CropCallCapReachedError:
                        return None
                    except Exception as exc:
                        logger.warning("Track-confirm retry failed for %s %s: %s", video_id, entry["item_id"], exc)
                        return None
                    if (
                        again is not None
                        and again.located
                        and again.normalized_box
                        and track_evidence.box_confirmed(again.normalized_box, seconds_of(entry, again), solid, min_iou)
                    ):
                        return again
                return None

            retried = await asyncio.gather(*(_retry(entry, crop) for entry, crop in pending))

            def _cut(label: str, sample: track_evidence.Sample) -> CropResult | None:
                frame = extract_frame(video_ref, sample.t)
                if frame is None:
                    return None
                return _locate_in_frame(
                    frame,
                    sample.t,
                    [Detection(box=list(sample.box), score=1.0)],
                    label,
                    0.0,
                    keep_frame=privacy is not None,
                )

            taken: set[str] = set()
            for (entry, crop), again in zip(pending, retried, strict=True):
                if again is not None:
                    logger.info(
                        "Evidence box for %s %s moved from %ss to %ss: a person track confirms it",
                        video_id,
                        entry["item_id"],
                        seconds_of(entry, crop),
                        again.box_seconds,
                    )
                    crops[entry["item_id"]] = again
                    continue
                # Entities share nothing here, so each takes a track of its own where there are enough.
                nearest = track_evidence.nearest_sample(
                    solid,
                    seconds_of(entry, crop),
                    entry.get("first_seen_seconds", entry["timestamp_seconds"]),
                    entry.get("last_seen_seconds", entry["timestamp_seconds"]),
                    taken,
                )
                if nearest is None:
                    continue
                try:
                    async with semaphore:
                        cut = await asyncio.to_thread(_cut, entry["label"], nearest[1])
                except Exception as exc:
                    logger.warning("Track-confirm fallback failed for %s %s: %s", video_id, entry["item_id"], exc)
                    continue
                if cut is not None:
                    taken.add(nearest[0].track_id)
                    logger.info(
                        "Evidence box for %s %s moved to track %s at %ss: no located box overlapped a person track",
                        video_id,
                        entry["item_id"],
                        nearest[0].track_id,
                        nearest[1].t,
                    )
                    crops[entry["item_id"]] = cut

        entries = blank_evidence_entries(report, image_gap=None)
        by_id = {entry["item_id"]: entry for entry in entries}
        await asyncio.gather(*(_crop(entry) for entry in entries))

        # Beside the person tracking run: the seeds need only the locate step, not the people.
        object_task = asyncio.create_task(_track_objects()) if object_tracking is not None else None

        all_tracks: list[track_evidence.Track] = []
        matched_tracks: dict[str, track_evidence.Track] = {}
        if tracking is not None and tracking_task is not None:
            try:
                outcome = await tracking_task
            except Exception as exc:  # run_tracking never raises; a custom runner might
                logger.warning("Tracking failed for %s: %s", video_id, type(exc).__name__)
                outcome = track_evidence.TrackingOutcome(skipped=f"tracking failed: {type(exc).__name__}")
            all_tracks = outcome.tracks
            tracking.summary.tracks = len(outcome.tracks)
            tracking.summary.elapsed_ms = outcome.elapsed_ms
            tracking.summary.skipped = outcome.skipped
            # Entities set-of-marks matched keep their track; the rest are associated by box overlap with the
            # tracks no mark took.
            marked = {
                item_id: track
                for item_id, track in tracking.marked.items()
                if item_id in by_id and by_id[item_id]["item_type"] == "entity" and track_evidence.is_solid(track)
            }
            taken = {track.track_id for track in marked.values()}
            await _confirm_entities(outcome.tracks, marked.keys(), tracking.min_iou)
            located = [
                (
                    entry["item_id"],
                    crop.normalized_box,
                    crop.box_seconds if crop.box_seconds is not None else entry["timestamp_seconds"],
                )
                for entry in entries
                if entry["item_type"] == "entity"
                and entry["item_id"] not in marked
                and (crop := crops.get(entry["item_id"])) is not None
                and crop.located
                and crop.normalized_box
            ]
            free = [track for track in outcome.tracks if track.track_id not in taken]
            candidates = {**marked, **track_evidence.associate_tracks(located, free, tracking.min_iou)}
            recut = await asyncio.gather(*(_recut(by_id[item_id], track) for item_id, track in candidates.items()))
            matched_tracks = {item_id: track for item_id, track in zip(candidates, recut, strict=True) if track}

        if object_task is not None:
            try:
                object_tracks = await object_task
            except Exception as exc:  # an object track is a bonus; it never costs the report its evidence
                logger.warning("Object tracking failed for %s: %s", video_id, type(exc).__name__)
                object_tracks = {}
            for item_id, track in object_tracks.items():
                crop = crops.get(item_id)
                moment = track_evidence.best_moment(track)
                if crop is not None and moment.t != crop.box_seconds:
                    await _recut(by_id[item_id], track)  # a failed re-cut keeps the located frame and the track
                matched_tracks[item_id] = track

        if privacy is not None:
            head_samples = await EvidenceBlur(
                privacy=privacy,
                detector=head_detector,
                semaphore=semaphore,
                video_id=video_id,
                video_ref=video_ref,
                video_seconds=video_seconds,
                sample_clips=active_store is not None,
            ).run(entries, crops, all_tracks)

        await asyncio.gather(*(_upload(entry, matched_tracks.get(entry["item_id"])) for entry in entries))

        return entries


_default_crop_evidence_service: CropEvidenceService | None = None


def get_crop_evidence_service() -> CropEvidenceService:
    """Retrieve the global CropEvidenceService instance (images go to R2 when R2 is configured)."""
    global _default_crop_evidence_service
    if _default_crop_evidence_service is None:
        _default_crop_evidence_service = CropEvidenceService(store=R2EvidenceStore() if is_r2_configured() else None)
    return _default_crop_evidence_service


def set_crop_evidence_service(service: CropEvidenceService | None) -> None:
    """Set or override the global CropEvidenceService instance (useful for test isolation)."""
    global _default_crop_evidence_service
    _default_crop_evidence_service = service
