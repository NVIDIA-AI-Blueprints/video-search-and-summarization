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
"""CriticAgent — VLM-backed result verifier. EXPERIMENTAL in v1.

The VLM caller comes from an injected ``VLMAnalyzer`` protocol; callers must
supply a ``vlm_analyzer`` (the library cannot construct a default). The
``time_format="offset"`` path uses ``VSTSnapshot.resolve_stream_id`` and
``get_timeline`` to convert ISO timestamps to seconds-since-stream-start.

The critic ships EXPERIMENTAL: the constructor signature, the ``VLMAnalyzer``
protocol, and the wire format of ``CriticAgentOutput`` may all change before v1
stable.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
from typing import TYPE_CHECKING

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core._foundation.time import datetime_to_iso8601
from vss_core.vios.client import map_interval_to_timeline

from .models import CriticAgentInput
from .models import CriticAgentOutput
from .models import CriticAgentResult
from .models import TimeFormat
from .models import VideoResult

if TYPE_CHECKING:
    from vss_core.vios.protocols import VSTSnapshot
    from vss_core.vlm.protocols import VLMAnalyzer

    from .models import VideoInfo

logger = logging.getLogger(__name__)


# Subject-anchored decision rule shared with the legacy critic so CLI
# verification produces the same verdicts.
DEFAULT_CRITIC_PROMPT = """
You are a helpful assistant that will evaluate a video against the original prompt
and determine whether the requested parameters are met, using subject-anchored evaluation.

user_prompt: {user_prompt}

Using subject-anchored decompositon, your task is to:
- break down the user prompt into a specific subject and a set of requested parameters, which become criteria
- then evaluate whether the video satisfies those parameters by verifying that the SPECIFIC SUBJECT exhibits the specified attributes and performs the described actions
Only focus on actions and person attributes for criteria evaluation.

HARD CONSTRAINTS / INVARIANTS for decision rule:
- Prefix the subject with "subject:" as shown in the examples.
- Anchor and evaluate each criterion against THAT SAME subject relationally. Subject and action are tightly bound.
- Do NOT mark a criterion true if a different entity satisfies it. It is a relational failure.

Choose up to 3 criteria for a subject.

Example 1:
user_prompt: "Find the man wearing a blue shirt, dark pants, and carrying a backpack"

Return the output in the following format:
```json
{{
    "subject:man": true,
    "blue shirt": true,
    "dark pants": true,
    "carrying a backpack": true
}}
```

Example 2:
user_prompt: "Find the woman picking up a box"

Return the output in the following format:
```json
{{
    "subject:woman": true,
    "picking up a box": false
}}
```

Example 3:
user_prompt: "Find the running person in a green jacket"

Return the output in the following format:
```json
{{
    "subject:person": true,
    "running": false,
    "green jacket": true
}}
```

Example 4: (RELATIONAL EVALUATION)
user_prompt: "Find the red team player making a basket"

Situation:
The video shows two teams red vs green playing basketball: red player defends, green player made the basket.
Hence there is a relational failure.

Return the output in the following format:
```json
{{
    "subject:player": true,
    "red": true,
    "makes a basket": false
}}
```
"""


def _parse_iso(s: str | datetime) -> datetime:
    """Parse an ISO-8601 string into a datetime (passthrough if already datetime)."""
    if isinstance(s, datetime):
        return s
    normalized = s.rstrip("Z")
    return datetime.fromisoformat(normalized)


def _to_offset(ts: datetime | str, clip_start: datetime) -> float:
    """Convert an absolute timestamp to seconds since clip_start."""
    if isinstance(ts, str):
        ts = _parse_iso(ts)
    # Both datetimes need matching tz-awareness; if either is naive, normalize.
    if ts.tzinfo is None and clip_start.tzinfo is not None:
        ts = ts.replace(tzinfo=clip_start.tzinfo)
    elif ts.tzinfo is not None and clip_start.tzinfo is None:
        clip_start = clip_start.replace(tzinfo=ts.tzinfo)
    return (ts - clip_start).total_seconds()


def _extract_json(text: str) -> str:
    """Extract the JSON object from common VLM response wrappers."""
    if "<answer>" in text and "</answer>" in text:
        text = text.split("<answer>", 1)[1].split("</answer>", 1)[0].strip()
    if "```json" in text:
        return text.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def _parse_criteria(vlm_text: str) -> tuple[CriticAgentResult, dict[str, bool]]:
    """Parse the VLM's JSON response into (verdict, criteria_met).

    On parse failure, returns (UNVERIFIED, {}). An explicit ``"result"`` verdict
    (``confirmed`` / ``rejected`` / ``unverified``) is honored when present;
    otherwise the verdict is derived from ``criteria_met`` — any criterion False
    yields REJECTED, else CONFIRMED.
    """
    try:
        payload = json.loads(_extract_json(vlm_text))
        if not isinstance(payload, dict):
            raise TypeError(f"expected JSON object, got {type(payload).__name__}")

        explicit_result = payload.get("result")
        if isinstance(payload.get("criteria_met"), dict):
            raw_criteria = payload["criteria_met"]
        else:
            # No explicit criteria_met: treat the remaining top-level keys as
            # criteria, but drop the reserved 'result' verdict key so it isn't
            # bool()'d into a (possibly failing) criterion — e.g. {"result": ""}
            # would otherwise flip an otherwise-confirmable clip to REJECTED.
            raw_criteria = {k: v for k, v in payload.items() if k != "result"}
        if any(not isinstance(value, bool) for value in raw_criteria.values()):
            raise TypeError("criteria values must be JSON booleans")
        criteria = {str(k): value for k, value in raw_criteria.items()}

        # Honor an explicit verdict for all three vocabulary values (not just the
        # two negative ones) so a VLM that self-reports ``"confirmed"`` is trusted
        # even when a stray criterion parses False.
        if isinstance(explicit_result, str):
            normalized = explicit_result.strip().lower()
            if normalized == CriticAgentResult.UNVERIFIED.value:
                return CriticAgentResult.UNVERIFIED, criteria
            if normalized == CriticAgentResult.REJECTED.value:
                return CriticAgentResult.REJECTED, criteria
            if normalized == CriticAgentResult.CONFIRMED.value and criteria and all(criteria.values()):
                return CriticAgentResult.CONFIRMED, criteria
            if normalized == CriticAgentResult.CONFIRMED.value:
                return CriticAgentResult.UNVERIFIED, criteria

        if not criteria:
            return CriticAgentResult.UNVERIFIED, {}
        verdict = CriticAgentResult.CONFIRMED if all(criteria.values()) else CriticAgentResult.REJECTED
        return verdict, criteria
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        logger.error(f"Error parsing VLM response: {e}")
        return CriticAgentResult.UNVERIFIED, {}


class CriticAgent:
    """VLM-backed verification of search results."""

    def __init__(
        self,
        *,
        vlm_analyzer: VLMAnalyzer,
        vst: VSTSnapshot,
        prompt: str = DEFAULT_CRITIC_PROMPT,
        max_concurrent_verifications: int = 5,
        time_format: TimeFormat = "iso",
        num_videos_to_evaluate: int | None = None,
    ) -> None:
        if max_concurrent_verifications < 1:
            raise ConfigurationError("max_concurrent_verifications must be >= 1")
        if num_videos_to_evaluate is not None and num_videos_to_evaluate < 1:
            raise ConfigurationError("num_videos_to_evaluate must be >= 1 when provided")
        if time_format not in {"iso", "offset"}:
            raise ConfigurationError(f"unsupported critic time_format: {time_format!r}")
        self._vlm = vlm_analyzer
        self._vst = vst
        self._prompt = prompt
        self._max_concurrent = max_concurrent_verifications
        self._time_format = time_format
        self._default_eval_count = num_videos_to_evaluate
        # Per-sensor replay-timeline cache. File-source candidates need the real
        # timeline to rebase their synthetic-epoch bounds; caching it avoids
        # refetching the full /storage/timelines map for every candidate on the
        # same sensor (the dominant redundant cost in an all-hit critic run).
        self._timeline_cache: dict[str, tuple[str, str]] = {}
        self._timeline_locks: dict[str, asyncio.Lock] = {}

    async def run(self, inp: CriticAgentInput) -> CriticAgentOutput:
        """Verify each input video with the VLM; return per-video verdicts."""
        semaphore = asyncio.Semaphore(self._max_concurrent)

        # Filter out entries without a sensor_id BEFORE applying the eval cap, so
        # the cap counts only genuinely verifiable videos. Slicing first would let
        # skipped (empty-sensor) entries consume cap slots and silently starve
        # verifiable ones.
        verifiable = [v for v in inp.videos if v.sensor_id]

        # Cap the eval count: explicit input takes precedence over the
        # constructor default; both are capped by the verifiable count.
        video_count = min(
            inp.evaluation_count or self._default_eval_count or len(verifiable),
            len(verifiable),
        )
        candidates = verifiable[:video_count]

        tasks = [self._evaluate_video(semaphore, v, inp.query) for v in candidates]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[VideoResult] = []
        for index, (video, outcome) in enumerate(zip(candidates, outcomes, strict=True)):
            if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
                raise outcome
            if isinstance(outcome, Exception):
                logger.error(
                    "Unexpected critic failure for candidate %d (%s); marking only that candidate unverified",
                    index,
                    type(outcome).__name__,
                )
                results.append(
                    VideoResult(
                        video_info=video,
                        result=CriticAgentResult.UNVERIFIED,
                        criteria_met={},
                    )
                )
            else:
                results.append(outcome)

        confirmed = sum(1 for r in results if r.result == CriticAgentResult.CONFIRMED)
        rejected = sum(1 for r in results if r.result == CriticAgentResult.REJECTED)
        logger.info(f"Critic agent: {confirmed} confirmed, {rejected} rejected, {len(results)} total")
        return CriticAgentOutput(video_results=results)

    async def _timeline_for(self, sensor_id: str) -> tuple[str, str]:
        """Return the (start_iso, end_iso) replay timeline for a sensor, cached.

        The VST timelines endpoint returns the full map for every stream, so
        refetching it per candidate is the dominant redundant cost in an all-hit
        critic run. Cache it once per sensor; a per-sensor lock keeps the first
        concurrent batch from each refetching before the cache is warm. A failure
        is raised per-candidate so one bad sensor does not sink the others.
        """
        cached = self._timeline_cache.get(sensor_id)
        if cached is not None:
            return cached
        lock = self._timeline_locks.setdefault(sensor_id, asyncio.Lock())
        async with lock:
            cached = self._timeline_cache.get(sensor_id)
            if cached is not None:
                return cached
            # ``VSTClient.get_timeline`` takes a sensor_id and resolves the
            # stream_id internally, so no separate resolve call is needed here.
            # It raises VSTError on a missing/short timeline; that propagates as a
            # per-candidate failure so one bad sensor does not sink the others.
            timeline = await self._vst.get_timeline(sensor_id)
            self._timeline_cache[sensor_id] = timeline
            return timeline

    async def _evaluate_video(
        self,
        semaphore: asyncio.Semaphore,
        video: VideoInfo,
        query: str,
    ) -> VideoResult:
        """Evaluate a single video against the user query via the VLM."""
        async with semaphore:
            formatted_prompt = self._prompt.format(user_prompt=query)
            logger.debug(f"Formatted prompt: {formatted_prompt}")

            try:
                if self._time_format == "iso":
                    # Emit the VSS-canonical 'Z'-suffixed ISO form (what the
                    # rest of the system and the legacy critic used), not
                    # datetime.isoformat()'s '+00:00' form, so a downstream
                    # video-analysis tool doing exact-string handling matches.
                    start_iso = datetime_to_iso8601(video.start_timestamp)
                    end_iso = datetime_to_iso8601(video.end_timestamp)
                    if video.source_type == "video_file":
                        # File hits are indexed on the synthetic midnight epoch
                        # while VST records the file at ingestion wall-clock, so
                        # the bounds must be rebased onto the real replay timeline
                        # before the VLM fetches the clip. The timeline is fetched
                        # once per sensor (cached), not once per candidate.
                        clip_start_iso, clip_end_iso = await self._timeline_for(video.sensor_id)
                        start_iso, end_iso = map_interval_to_timeline(
                            start_iso,
                            end_iso,
                            clip_start_iso,
                            clip_end_iso,
                        )
                        if _parse_iso(end_iso) <= _parse_iso(start_iso):
                            raise BackendUnreachableError(
                                "vst",
                                f"rebased file interval is empty for sensor {video.sensor_id}",
                            )
                    # Live/rtsp bounds are real wall-clock and pass through
                    # unchanged. An interval outside the recording is rejected by
                    # VST at clip-URL time and fails open to `unverified` — the
                    # honest answer.
                    vlm_response = await self._vlm.analyze(
                        sensor_id=video.sensor_id,
                        start_timestamp=start_iso,
                        end_timestamp=end_iso,
                        prompt=formatted_prompt,
                        time_format="iso",
                    )
                else:
                    # offset-time: convert ISO timestamps to seconds-since-stream-start
                    # using VST's timeline endpoint.
                    stream_id = await self._vst.resolve_stream_id(video.sensor_id)
                    if stream_id is None:
                        raise BackendUnreachableError(
                            "vst",
                            f"stream_id resolution failed for sensor {video.sensor_id}",
                        )
                    clip_start_iso, clip_end_iso = await self._vst.get_timeline(stream_id)
                    clip_start_dt = _parse_iso(clip_start_iso)
                    # File-search hits use a synthetic midnight-anchored date,
                    # while VST records the same file at ingestion wall-clock,
                    # so those bounds have to be rebased onto the real stream
                    # timeline. Restrict the rebase to that case: a wall-clock
                    # bound that falls outside the timeline (an aged-out live
                    # segment, a re-ingested file whose timeline moved) would
                    # otherwise be silently re-anchored onto unrelated footage
                    # and earn a confident confirmed/rejected verdict. Left
                    # literal, VST rejects it and the candidate fails open to
                    # `unverified`, which is the honest answer.
                    start_iso = datetime_to_iso8601(video.start_timestamp)
                    end_iso = datetime_to_iso8601(video.end_timestamp)
                    if video.source_type == "video_file":
                        start_iso, end_iso = map_interval_to_timeline(
                            start_iso,
                            end_iso,
                            clip_start_iso,
                            clip_end_iso,
                        )
                    start_offset = _to_offset(start_iso, clip_start_dt)
                    end_offset = _to_offset(end_iso, clip_start_dt)
                    # Clamp end_offset to the clip's actual end if the caller asked
                    # for more than is available (matches original L259-260).
                    clip_end_offset = _to_offset(_parse_iso(clip_end_iso), clip_start_dt)
                    if end_offset > clip_end_offset:
                        end_offset = clip_end_offset
                    if start_offset < 0 or start_offset >= clip_end_offset or end_offset <= start_offset:
                        # The interval is not inside this recording — a negative
                        # offset, a start past the end, or an interval that
                        # clamping collapsed to zero length. Any of those would
                        # ask the VLM to judge frames the caller never retrieved,
                        # so fail open to `unverified` instead.
                        raise BackendUnreachableError(
                            "vst",
                            f"requested interval lies outside the recorded timeline for sensor {video.sensor_id}",
                        )
                    vlm_response = await self._vlm.analyze(
                        sensor_id=video.sensor_id,
                        start_timestamp=str(start_offset),
                        end_timestamp=str(end_offset),
                        prompt=formatted_prompt,
                        time_format="offset",
                    )
            except BackendUnreachableError as e:
                logger.error(f"Error calling VLM analyzer: {e}")
                return VideoResult(
                    video_info=video,
                    result=CriticAgentResult.UNVERIFIED,
                    criteria_met={},
                )

            logger.info(f"VLM response for {video.sensor_id}: {vlm_response}")
            verdict, criteria = _parse_criteria(vlm_response)
            logger.debug(f"Video {video.sensor_id} verdict={verdict.value} criteria={criteria}")
            return VideoResult(video_info=video, result=verdict, criteria_met=criteria)

    async def aclose(self) -> None:
        return None
