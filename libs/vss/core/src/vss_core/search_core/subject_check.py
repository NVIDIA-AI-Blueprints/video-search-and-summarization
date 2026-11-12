# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Subject check: is the person who matched the attribute the one doing the action?

Fusion scores whole clips: the embed leg says *someone* in the clip does the
action and the attribute leg says *someone* in the clip has the appearance.
Nothing ties the two to one person, so a clip where a bystander wears the white
shirt and a different person pushes the cart ranks as a match.

This module closes that gap for the top fused results. For each one it takes
the attribute match's best frame, draws that object's box on it, and asks the
VLM whether the boxed person does what the query says that person does. The
query, not a parsed role, tells the VLM which action belongs to that person, so
"... while a person in black watches" is judged on watching.

Only the order changes. A ``no`` moves the result down a few places (three by
default), so a wrong ``no`` costs ranks rather than the hit; ``yes`` and
``unsure`` leave it where it is. Any failure (VST, VLM, an unusable box) is
``unsure``, so a broken dependency degrades to today's order and never fails
the search. No field is added to the results.

Each checked result is logged once at INFO as ``subject_check_verdict`` followed
by a JSON object (rank, result window, object, verdict), so an evaluation can
join the verdicts with ground truth without the response carrying them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import json
import logging
from typing import TYPE_CHECKING
from typing import Any

import httpx

from vss_core._foundation.sanitize import scrub_log
from vss_core._foundation.time_measure import TimeMeasure
from vss_core.vios.client import build_screenshot_url
from vss_core.vios.client import map_timestamp_to_timeline

from .critic import _extract_json

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from collections.abc import Callable

    from vss_core.vios.protocols import VSTSnapshot
    from vss_core.vlm.protocols import VLMImageAnalyzer

    from .models.attribute_search import AttributeSearchResult
    from .models.search import SearchResult

logger = logging.getLogger(__name__)

#: Stage names reported in ``SearchOutput.timings``. Kept apart from the
#: critic's ``vlm: inference`` so the two VLM costs can be read separately.
TIMER_TOTAL = "search: subject check"
TIMER_FETCH = "subject_check: fetch frame"
TIMER_DRAW = "subject_check: draw box"
TIMER_VLM = "subject_check: vlm inference"

#: Upper bound on results checked per search, whatever ``top_k`` is. HIT@1,
#: HIT@3 and most of mAP are decided at the top, and each check costs a VST
#: frame fetch plus a VLM call.
DEFAULT_MAX_CHECKS = 5

#: How many places a ``no`` moves a result down.
DEFAULT_DEMOTE_BY = 3

#: Prefix of the per-result INFO log line; the rest of the line is JSON.
VERDICT_LOG_MARKER = "subject_check_verdict"

_BOX_COLOR_BGR = (0, 255, 0)
_MAX_IMAGE_SIDE = 1280
_JPEG_QUALITY = 90
#: Pixel boxes may overshoot the decoded frame by this fraction before we treat
#: the coordinates as belonging to a different resolution and skip the check.
_BOX_OVERSHOOT_TOLERANCE = 0.02

SUBJECT_CHECK_PROMPT = """You are checking one result of a video search.

Search: "{query}"

The image is a frame from the result. One person is outlined with a green box.
That person was matched to this appearance: "{attribute}".

Decide whether the person inside the green box is doing what the search says
the person with that appearance is doing.

Rules:
- First read the search and work out which action, if any, it gives to the
  person with that appearance. That person may be the one doing the main action,
  or someone else in the scene (for example "while a person in black watches").
- Judge only the person inside the green box.
- Answer "yes" if the boxed person is doing that action, or could plausibly be
  in the middle of it in this frame, or if the search gives that person no
  action.
- Answer "no" only when the frame clearly shows it: a different person is the
  one doing that action, or the boxed person is clearly doing something that
  rules it out (for example standing still and empty-handed when the search
  says pushing a cart).
- Answer "unsure" when one still frame cannot show the action (for example
  stealing, running away, or an action that happens before or after this
  moment), or when the boxed person is hidden or the frame is unclear. When in
  doubt between "no" and "unsure", answer "unsure".

Return only JSON, one of:
{{"answer": "yes"}}
{{"answer": "no"}}
{{"answer": "unsure"}}"""


class SubjectVerdict(StrEnum):
    """The VLM's answer for one result."""

    YES = "yes"
    NO = "no"
    UNSURE = "unsure"


@dataclass(frozen=True)
class Subject:
    """The attribute-matched object to check: where its frame is and its box on it."""

    sensor_id: str
    object_id: str
    frame_timestamp: str
    bbox: dict[str, Any]
    score: float


def subject_from_attribute_result(result: AttributeSearchResult) -> Subject | None:
    """Build a :class:`Subject` when the box is known to belong to the frame we fetch.

    The box must come from the frame-level lookup (``frame_score`` is set), so
    it was measured on the frame at ``frame_timestamp``. A behavior-level box
    describes some other moment, and drawing it on this frame could outline the
    wrong person, so those results are not checked.
    """
    meta = result.metadata
    if meta.frame_score is None or not meta.frame_timestamp or not meta.bbox:
        return None
    if not meta.sensor_id or meta.sensor_id == "unknown":
        return None
    if not meta.object_id or meta.object_id == "unknown":
        return None
    score = meta.frame_score if meta.frame_score > 0.0 else meta.behavior_score
    return Subject(
        sensor_id=meta.sensor_id,
        object_id=meta.object_id,
        frame_timestamp=meta.frame_timestamp,
        bbox=dict(meta.bbox),
        score=float(score),
    )


def parse_verdict(text: str) -> SubjectVerdict:
    """Read the VLM's ``{"answer": ...}``; anything unreadable is ``unsure``."""
    try:
        payload = json.loads(_extract_json(text))
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = None
    answer: Any = payload.get("answer") if isinstance(payload, dict) else None
    if not isinstance(answer, str):
        stripped = text.strip().strip('"').lower()
        answer = stripped.split()[0].strip(".,:;!") if stripped else ""
    answer = answer.strip().lower()
    if answer in {SubjectVerdict.YES.value, SubjectVerdict.NO.value, SubjectVerdict.UNSURE.value}:
        return SubjectVerdict(answer)
    return SubjectVerdict.UNSURE


def box_to_pixels(bbox: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int] | None:
    """Convert a ``leftX/rightX/topY/bottomY`` box to pixel corners on this frame.

    Normalised boxes (every value <= 1) are scaled to the frame. Pixel boxes
    are used as they are, but one that falls well outside the frame means the
    detector ran at another resolution; the box cannot be placed reliably, so
    ``None`` is returned and the result is left unchecked.
    """
    try:
        left, right, top, bottom = (float(bbox[key]) for key in ("leftX", "rightX", "topY", "bottomY"))
    except (KeyError, TypeError, ValueError):
        return None
    if right <= left or bottom <= top or min(left, top) < 0:
        return None
    if max(left, right, top, bottom) <= 1.0:
        left, right = left * width, right * width
        top, bottom = top * height, bottom * height
    elif right > width * (1 + _BOX_OVERSHOOT_TOLERANCE) or bottom > height * (1 + _BOX_OVERSHOOT_TOLERANCE):
        return None
    x1, y1 = max(0, round(left)), max(0, round(top))
    x2, y2 = min(width - 1, round(right)), min(height - 1, round(bottom))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def draw_subject_box(image_bytes: bytes, bbox: dict[str, Any]) -> bytes | None:
    """Return a JPEG of the frame with the subject's box drawn, or ``None``."""
    # Imported here so importing search (which imports this module) does not
    # pay OpenCV's import cost on paths that never run the check.
    import cv2
    import numpy as np

    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if frame is None:
        return None
    height, width = frame.shape[:2]
    corners = box_to_pixels(bbox, width, height)
    if corners is None:
        return None
    x1, y1, x2, y2 = corners
    thickness = max(2, round(min(width, height) / 200))
    cv2.rectangle(frame, (x1, y1), (x2, y2), _BOX_COLOR_BGR, thickness)
    longest = max(width, height)
    if longest > _MAX_IMAGE_SIDE:
        scale = _MAX_IMAGE_SIDE / longest
        frame = cv2.resize(frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok:
        return None
    return bytes(encoded.tobytes())


def _log_verdict(rank: int, result: SearchResult, subject: Subject, verdict: SubjectVerdict) -> None:
    """One machine-readable INFO line per checked result (see the module docstring)."""
    if not logger.isEnabledFor(logging.INFO):
        return
    record = {
        "rank": rank + 1,
        "video_name": result.video_name,
        "sensor_id": result.sensor_id,
        "start_time": result.start_time,
        "end_time": result.end_time,
        "object_id": subject.object_id,
        "frame_timestamp": subject.frame_timestamp,
        "verdict": verdict.value,
    }
    logger.info("%s %s", VERDICT_LOG_MARKER, scrub_log(json.dumps(record, sort_keys=True)))


class _RunCache:
    """Per-rerank VST lookups shared by every result on the same sensor."""

    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], asyncio.Task[Any]] = {}

    async def get(self, kind: str, key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
        task = self._tasks.get((kind, key))
        if task is None:
            task = asyncio.ensure_future(factory())
            self._tasks[(kind, key)] = task
        return await task


class SubjectChecker:
    """Reorder fused results so a boxed person the VLM rejects moves to the bottom."""

    def __init__(
        self,
        *,
        image_analyzer: VLMImageAnalyzer,
        vst: VSTSnapshot,
        vst_internal_url: str,
        max_checks: int = DEFAULT_MAX_CHECKS,
        demote_by: int = DEFAULT_DEMOTE_BY,
        max_concurrent: int = 5,
        frame_timeout_seconds: float = 10.0,
        prompt: str = SUBJECT_CHECK_PROMPT,
    ) -> None:
        if max_checks < 1:
            raise ValueError("max_checks must be >= 1")
        if demote_by < 1:
            raise ValueError("demote_by must be >= 1")
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._vlm = image_analyzer
        self._vst = vst
        self._vst_internal_url = vst_internal_url
        self._max_checks = max_checks
        self._demote_by = demote_by
        self._max_concurrent = max_concurrent
        self._frame_timeout = frame_timeout_seconds
        self._prompt = prompt

    async def rerank(
        self,
        results: list[SearchResult],
        subject_for: Callable[[SearchResult], Subject | None],
        *,
        query: str,
        attributes: list[str],
        limit: int | None = None,
    ) -> list[SearchResult]:
        """Check the top results and move each one answered ``no`` down ``demote_by`` places.

        ``limit`` caps how many results from the top are checked (normally the
        caller's ``top_k``); it is itself capped at ``max_checks``.
        """
        query = query.strip()
        attribute = "; ".join(a.strip() for a in attributes if a and a.strip())
        if not results or not query or not attribute:
            return results
        cap = self._max_checks if limit is None else max(0, min(limit, self._max_checks))
        jobs = [(index, subject_for(results[index])) for index in range(min(cap, len(results)))]
        checks = [(index, subject) for index, subject in jobs if subject is not None]
        if not checks:
            return results

        semaphore = asyncio.Semaphore(self._max_concurrent)
        cache = _RunCache()
        prompt = self._prompt.format(query=query, attribute=attribute)
        async with httpx.AsyncClient(timeout=self._frame_timeout) as client:
            with TimeMeasure(TIMER_TOTAL):
                verdicts = await asyncio.gather(
                    *(self._check(subject, prompt, semaphore, cache, client) for _, subject in checks)
                )
        for (index, subject), verdict in zip(checks, verdicts, strict=True):
            _log_verdict(index, results[index], subject, verdict)

        rejected = {index for (index, _), verdict in zip(checks, verdicts, strict=True) if verdict == SubjectVerdict.NO}
        counts = {v.value: sum(1 for verdict in verdicts if verdict == v) for v in SubjectVerdict}
        logger.info(
            "Subject check on %d of %d results: %d yes, %d no, %d unsure",
            len(checks),
            len(results),
            counts["yes"],
            counts["no"],
            counts["unsure"],
        )
        if not rejected:
            return results
        # Each rejected result sorts as if it sat ``demote_by`` places lower;
        # the half step puts it after the result already at that place.
        # Python's sort is stable, so everything else keeps its order.
        position = {
            index: index + (self._demote_by + 0.5 if index in rejected else 0.0) for index in range(len(results))
        }
        return [results[index] for index in sorted(range(len(results)), key=position.__getitem__)]

    async def _check(
        self,
        subject: Subject,
        prompt: str,
        semaphore: asyncio.Semaphore,
        cache: _RunCache,
        client: httpx.AsyncClient,
    ) -> SubjectVerdict:
        """One result's verdict; every failure is ``unsure``."""
        try:
            async with semaphore:
                with TimeMeasure(TIMER_FETCH):
                    frame = await self._fetch_frame(subject, cache, client)
                with TimeMeasure(TIMER_DRAW):
                    image = draw_subject_box(frame, subject.bbox)
                if image is None:
                    logger.info(
                        "Subject check skipped for object %s on %s: box could not be placed on the frame",
                        scrub_log(subject.object_id),
                        scrub_log(subject.sensor_id),
                    )
                    return SubjectVerdict.UNSURE
                with TimeMeasure(TIMER_VLM):
                    answer = await self._vlm.analyze_image(image_bytes=image, prompt=prompt)
            return parse_verdict(answer)
        except Exception as e:
            logger.warning(
                "Subject check failed for object %s on %s (%s); leaving its rank unchanged",
                scrub_log(subject.object_id),
                scrub_log(subject.sensor_id),
                scrub_log(f"{type(e).__name__}: {e}"),
            )
            return SubjectVerdict.UNSURE

    async def _fetch_frame(self, subject: Subject, cache: _RunCache, client: httpx.AsyncClient) -> bytes:
        """Fetch the subject's frame from VST at its (timeline-mapped) timestamp."""
        stream_id: str = await cache.get(
            "stream", subject.sensor_id, lambda: self._vst.resolve_stream_id(subject.sensor_id)
        )
        start, end = await cache.get("timeline", stream_id, lambda: self._vst.get_timeline(stream_id))
        timestamp = map_timestamp_to_timeline(subject.frame_timestamp, start, end)
        url = build_screenshot_url(self._vst_internal_url, stream_id, timestamp)
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            # The URL can carry credentials; report the status only.
            raise RuntimeError(f"VST picture request returned HTTP {e.response.status_code}") from None
        except httpx.HTTPError as e:
            raise RuntimeError(f"VST picture request failed ({type(e).__name__})") from None
        if not response.content:
            raise RuntimeError("VST picture request returned no image")
        return response.content
