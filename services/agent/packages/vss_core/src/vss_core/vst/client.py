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
"""Reusable VST client and helpers.

Includes the VST helpers (get_name_to_stream_id_map, get_stream_id, get_timeline)
ported from services/agent/src/agent/tools/vst/{utils,timeline}.py with
these adjustments: no env reads (callers must pass internal URL explicitly);
retries are limited to connection/timeout errors so deterministic 4xx/parse
failures fail fast; and framework/parse exceptions are wrapped in the library
error hierarchy (VSTError, a BackendUnreachableError) so no raw aiohttp/stdlib
exception leaks to callers.

build_screenshot_url stays a free function for callers that don't need the
OO wrapper.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Literal
import urllib.parse

import aiohttp

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.retry import create_retry_strategy
from vss_core._foundation.sanitize import quote_path_segment
from vss_core._foundation.time import iso8601_to_datetime

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30
_FILE_TIMELINE_EPOCH = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)

# Only transient connection/timeout failures are worth retrying. Deterministic
# failures (4xx, JSON/parse/validation errors, VSTError) must fail fast rather
# than burning three attempts on an outcome that cannot change.
_VST_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
    aiohttp.ServerTimeoutError,
    TimeoutError,
)

# Keep the retry policy deliberately narrow, but make the public helper
# boundary total for aiohttp failures. Errors raised while reading a response
# body (for example ClientPayloadError) are not all connection subclasses.
_VST_BOUNDARY_ERRORS: tuple[type[Exception], ...] = (aiohttp.ClientError, TimeoutError)


# ---------------------------------------------------------------------- types


class VSTError(BackendUnreachableError):
    """Error raised by the VST helpers.

    Subclasses :class:`BackendUnreachableError` (backend ``"vst"``), so VST
    failures carry ``.backend`` and no raw framework exception leaks. Mirrors
    the intent of tools/vst/utils.py:64.
    """

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__("vst", message, cause)


# ----------------------------------------------------------------- free helpers


def build_screenshot_url(vst_external_url: str, stream_id: str, timestamp: str) -> str:
    """Build a client-facing screenshot URL.

    Mirrors tools/vst/snapshot.py:49. ``stream_id`` is percent-encoded as a
    single path segment and ``timestamp`` as a query value so a user-controlled
    identifier cannot alter the URL structure (URL path injection).
    """
    vst_external_url = vst_external_url.rstrip("/")
    stream_seg = quote_path_segment(stream_id)
    ts_value = urllib.parse.quote(str(timestamp), safe="")
    return f"{vst_external_url}/vst/api/v1/replay/stream/{stream_seg}/picture?startTime={ts_value}"


def map_timestamp_to_timeline(timestamp: str, timeline_start: str, timeline_end: str) -> str:
    """Map an ES hit timestamp onto a stream's VST replay timeline.

    File-ingested sources are indexed on a synthetic, midnight-anchored epoch
    (e.g. ``2025-01-01T00:01:00Z`` = 60s into the file) while VST anchors the
    replay timeline at ingest wall-clock. A raw ES timestamp therefore points
    outside the recording and VST rejects the picture request
    (``VMSInternalError: no valid stream found for given timestamps``). Live
    RTSP sources index real wall-clock, which lands inside the timeline and
    must pass through unchanged.

    Rules:
      - timestamp within [start, end]: returned unchanged (live sources)
      - otherwise: the elapsed offset from the fixed uploaded-file epoch
        (2025-01-01T00:00:00Z) is re-based onto ``timeline_start``, clamped to
        the real timeline. Keeping the date component preserves offsets in
        files longer than 24 hours.

    Any parse failure returns the original timestamp (best-effort — a raw URL
    that may 404 beats dropping the hit).
    """
    try:
        ts = iso8601_to_datetime(timestamp)
        start = iso8601_to_datetime(timeline_start)
        end = iso8601_to_datetime(timeline_end)
    except (TypeError, ValueError):
        return timestamp
    if start <= ts <= end:
        return timestamp
    offset = ts - _FILE_TIMELINE_EPOCH
    mapped = start + offset
    if mapped > end:
        mapped = end
    elif mapped < start:
        mapped = start
    return mapped.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def map_interval_to_timeline(
    start_timestamp: str,
    end_timestamp: str,
    timeline_start: str,
    timeline_end: str,
) -> tuple[str, str]:
    """Rebase a synthetic file interval while preserving its duration.

    Mapping the two bounds independently loses the date component used to
    express elapsed time. In particular, an interval crossing synthetic
    midnight can map its end before its start. Anchor the start once and add
    the original duration, clamping only at the real recording end.

    Parse failures or non-positive input ranges are returned unchanged so this
    helper retains :func:`map_timestamp_to_timeline`'s best-effort contract.
    """
    try:
        source_start = iso8601_to_datetime(start_timestamp)
        source_end = iso8601_to_datetime(end_timestamp)
        real_end = iso8601_to_datetime(timeline_end)
    except (TypeError, ValueError):
        return start_timestamp, end_timestamp
    duration = source_end - source_start
    if duration.total_seconds() <= 0:
        return start_timestamp, end_timestamp

    mapped_start_text = map_timestamp_to_timeline(start_timestamp, timeline_start, timeline_end)
    try:
        mapped_start = iso8601_to_datetime(mapped_start_text)
    except (TypeError, ValueError):
        return start_timestamp, end_timestamp
    mapped_end = min(mapped_start + duration, real_end)
    return (
        mapped_start_text,
        mapped_end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    )


async def get_timelines_map(
    vst_internal_url: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    retries: int = 3,
) -> dict[str, tuple[str, str]]:
    """Return {stream_id: (start_iso, end_iso)} for every stream VST knows.

    One call to ``/vst/api/v1/storage/timelines`` covers all streams (unlike
    :func:`get_timeline`, which filters to one). Streams with several recorded
    segments are collapsed to their envelope (first start, last end). Raises
    VSTError on transport/API failure; callers doing best-effort screenshot
    enrichment should catch it and continue unmapped.
    """
    base = vst_internal_url.rstrip("/")
    if base.endswith("/vst"):
        base = base[:-4]
    timelines_url = f"{base}/vst/api/v1/storage/timelines"
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async for retry in create_retry_strategy(retries=retries, exceptions=_VST_RETRYABLE_ERRORS):
                with retry:
                    async with session.get(timelines_url) as response:
                        if response.status != 200:
                            raise VSTError(f"VST timelines API returned status {response.status}")
                        payload = json.loads(await response.text())
                        out: dict[str, tuple[str, str]] = {}
                        if isinstance(payload, dict):
                            for stream_id, segments in payload.items():
                                if not (isinstance(segments, list) and segments):
                                    continue
                                starts = [
                                    str(s["startTime"]) for s in segments if isinstance(s, dict) and s.get("startTime")
                                ]
                                ends = [str(s["endTime"]) for s in segments if isinstance(s, dict) and s.get("endTime")]
                                if starts and ends:
                                    out[str(stream_id)] = (min(starts), max(ends))
                        return out
    except _VST_BOUNDARY_ERRORS as e:
        raise VSTError("Failed to get timelines map after retrying transport errors", e) from e
    return {}  # unreachable; satisfies mypy


async def get_video_clip_url(
    *,
    stream_id: str,
    start_time: float | str | None = None,
    end_time: float | str | None = None,
    vst_internal_url: str,
    disable_audio: bool = True,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Return a temporary VST clip URL for a stream and optional time range.

    NAT's ``vst.video_clip`` tool owns this in the agent path. This reusable
    helper keeps critic/VLM verification usable without importing NAT or
    invoking the agent. ``start_time`` / ``end_time`` may be ISO strings or
    second offsets from the stream timeline.
    """
    if isinstance(start_time, str) != isinstance(end_time, str):
        raise VSTError("start_time and end_time must both be ISO strings or both be second offsets")

    if isinstance(start_time, str) and isinstance(end_time, str):
        start_time_iso = start_time
        end_time_iso = end_time
    else:
        start_timestamp, end_timestamp = await get_timeline(
            stream_id, vst_internal_url, timeout_seconds=timeout_seconds
        )
        start_dt = datetime.datetime.fromisoformat(start_timestamp.replace("Z", "+00:00"))
        end_dt = datetime.datetime.fromisoformat(end_timestamp.replace("Z", "+00:00"))
        start_ms = start_dt.timestamp() * 1000
        end_ms = end_dt.timestamp() * 1000

        if start_time is not None and not isinstance(start_time, str):
            clip_start_ms = min(float(start_time) * 1000 + start_ms, end_ms)
        else:
            clip_start_ms = start_ms
        if end_time is not None and not isinstance(end_time, str):
            clip_end_ms = min(float(end_time) * 1000 + start_ms, end_ms)
        else:
            clip_end_ms = end_ms

        if clip_start_ms < start_ms or clip_end_ms > end_ms or clip_end_ms < clip_start_ms:
            raise VSTError(
                f"Clip times must be within the stream timeline {start_timestamp}..{end_timestamp} "
                f"and start <= end, got {clip_start_ms}..{clip_end_ms}"
            )

        start_time_iso = (
            datetime.datetime.fromtimestamp(clip_start_ms / 1000, tz=datetime.UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        end_time_iso = (
            datetime.datetime.fromtimestamp(clip_end_ms / 1000, tz=datetime.UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    query_params = urllib.parse.urlencode(
        {
            "startTime": start_time_iso,
            "endTime": end_time_iso,
            "blocking": "true",
            "disableAudio": "true" if disable_audio else "false",
        }
    )
    stream_seg = quote_path_segment(stream_id)
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/storage/file/{stream_seg}/url?{query_params}"

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async for retry in create_retry_strategy(retries=3, exceptions=_VST_RETRYABLE_ERRORS):
                with retry:
                    async with session.get(url) as response:
                        if response.status != 200:
                            raise VSTError(f"Failed to get video clip URL: HTTP {response.status}")
                        text = await response.text()
                        try:
                            payload = json.loads(text)
                        except json.JSONDecodeError as e:
                            raise VSTError(f"Invalid JSON in VST clip response: {e}") from e
                        if not isinstance(payload, dict):
                            raise VSTError(f"Unexpected VST clip response shape: {type(payload).__name__}")
                        video_url = payload.get("videoUrl")
                        if not video_url:
                            raise VSTError("No videoUrl in VST clip response")
                        return str(video_url)
    except _VST_BOUNDARY_ERRORS as e:
        raise VSTError("Failed to get video clip URL after retrying transport errors", e) from e

    raise VSTError("Failed to get video clip URL")


async def get_name_to_stream_id_map(
    vst_internal_url: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, str]:
    """Fetch `/api/v1/sensor/streams` and return `{sensor_name: stream_id}`.

    Mirrors tools/vst/utils.py:70-97 with the env-fallback removed. Parse/shape
    errors are wrapped in :class:`VSTError` (never leaked raw), and the response
    shape is validated defensively so a malformed payload maps cleanly.
    """
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/sensor/streams"
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async for retry in create_retry_strategy(retries=3, exceptions=_VST_RETRYABLE_ERRORS):
                with retry:
                    async with session.get(url) as response:
                        if response.status != 200:
                            raise VSTError(f"VST streams API returned status {response.status}")
                        text = await response.text()
                        try:
                            payload = json.loads(text)
                            if not isinstance(payload, list):
                                raise VSTError(f"Unexpected VST streams response shape: {type(payload).__name__}")
                            mapping: dict[str, str] = {}
                            for file in payload:
                                if not isinstance(file, dict) or not file:
                                    logger.warning("Skipping malformed VST stream entry")
                                    continue
                                stream_id = next(iter(file))
                                entries = file[stream_id]
                                if isinstance(entries, list) and len(entries) > 0 and isinstance(entries[0], dict):
                                    name = entries[0].get("name")
                                    if name is not None:
                                        mapping[name] = stream_id
                                else:
                                    logger.warning(f"Stream ID {stream_id} is empty, skipping")
                            return mapping
                        except VSTError:
                            raise
                        except Exception as e:
                            raise VSTError(f"Error parsing name to stream ID map: {e}") from e
    except _VST_BOUNDARY_ERRORS as e:
        raise VSTError("Failed to get name to stream ID map after retrying transport errors", e) from e
    return {}  # unreachable; satisfies mypy


async def get_streams_info(
    vst_internal_url: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, dict[str, str]]:
    """Return `{stream_id: {"name": name, "url": rtsp_url}}` from VST.

    Mirrors tools/vst/utils.py:420-453. Used by the Search orchestrator to
    resolve video_sources by name when source_type='rtsp'. Parse/shape errors
    are wrapped in :class:`VSTError` (never leaked raw).
    """
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/sensor/streams"
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async for retry in create_retry_strategy(retries=3, exceptions=_VST_RETRYABLE_ERRORS):
                with retry:
                    async with session.get(url) as response:
                        if response.status != 200:
                            raise VSTError(f"VST streams API returned status {response.status}")
                        text = await response.text()
                        try:
                            payload = json.loads(text)
                            if not isinstance(payload, list):
                                raise VSTError(f"Unexpected VST streams response shape: {type(payload).__name__}")
                            result: dict[str, dict[str, str]] = {}
                            for entry in payload:
                                if not isinstance(entry, dict) or not entry:
                                    logger.warning("Skipping malformed VST stream entry")
                                    continue
                                stream_id = next(iter(entry))
                                stream_list = entry[stream_id]
                                if (
                                    isinstance(stream_list, list)
                                    and len(stream_list) > 0
                                    and isinstance(stream_list[0], dict)
                                ):
                                    result[stream_id] = {
                                        "name": stream_list[0].get("name", ""),
                                        "url": stream_list[0].get("url", ""),
                                    }
                            return result
                        except VSTError:
                            raise
                        except Exception as e:
                            raise VSTError(f"Error parsing streams info: {e}") from e
    except _VST_BOUNDARY_ERRORS as e:
        raise VSTError("Failed to get streams info after retrying transport errors", e) from e
    return {}  # unreachable; satisfies mypy


async def get_stream_id(
    sensor_id: str,
    vst_internal_url: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Resolve sensor_id → stream_id via VST. Mirrors tools/vst/utils.py:99-117.

    ``sensor_id`` may already be a stream_id (UUID); the function tolerates that.
    """
    stream_id_map = await get_name_to_stream_id_map(vst_internal_url, timeout_seconds=timeout_seconds)
    stream_id = stream_id_map.get(sensor_id)
    if not stream_id:
        if sensor_id in stream_id_map.values():
            stream_id = sensor_id
        else:
            raise VSTError(
                f"streamId not found for '{sensor_id}'. Available: {sorted(stream_id_map.keys())}"
                if stream_id_map
                else "streamId not found"
            )
    return stream_id


async def get_sensor_id_from_stream_id(
    stream_id: str,
    vst_internal_url: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Reverse lookup: stream_id (UUID) → sensor_id (camera name).

    Mirrors tools/vst/utils.py:119-153. If ``stream_id`` is already a sensor
    name (and present in the VST map), returns it as-is. Raises VSTError on miss.
    """
    name_to_stream_id_map = await get_name_to_stream_id_map(vst_internal_url, timeout_seconds=timeout_seconds)
    stream_id_to_name_map = {sid: name for name, sid in name_to_stream_id_map.items()}
    sensor_id = stream_id_to_name_map.get(stream_id)
    if not sensor_id:
        if stream_id in name_to_stream_id_map:
            sensor_id = stream_id
        else:
            raise VSTError(
                f"sensorId not found for stream_id '{stream_id}'. "
                f"Available stream_ids: {sorted(stream_id_to_name_map.keys())[:10]}..."
                if stream_id_to_name_map
                else "sensorId not found"
            )
    return sensor_id


async def get_timeline(
    stream_id: str,
    vst_internal_url: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> tuple[str, str]:
    """Return (start_iso, end_iso) for a stream's replay timeline.

    Mirrors tools/vst/timeline.py:69-125. Tolerates being given a sensor name
    instead of a stream_id (re-resolves via get_stream_id if the first lookup
    misses). Raises VSTError if the timeline is missing or shorter than 1s.
    """
    # Defensive: drop a trailing /vst if some caller already added it. Strip
    # trailing slashes FIRST so '<url>/vst/' is handled too — otherwise the
    # suffix check misses and the path doubles to '<url>/vst/vst/api/...'.
    base = vst_internal_url.rstrip("/")
    if base.endswith("/vst"):
        base = base[:-4]
    timelines_url = f"{base}/vst/api/v1/storage/timelines"

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async for retry in create_retry_strategy(retries=3, exceptions=_VST_RETRYABLE_ERRORS):
                with retry:
                    async with session.get(timelines_url) as response:
                        if response.status != 200:
                            raise VSTError(f"VST timelines API returned status {response.status}")
                        text = await response.text()
                    try:
                        timelines_data = json.loads(text)
                        if not isinstance(timelines_data, dict):
                            raise VSTError(f"Unexpected VST timelines response shape: {type(timelines_data).__name__}")
                        timeline_list = timelines_data.get(stream_id, [])
                        if not timeline_list:
                            logger.info("no timeline for input; trying to resolve as sensor name")
                            stream_id = await get_stream_id(
                                stream_id, vst_internal_url, timeout_seconds=timeout_seconds
                            )
                            timeline_list = timelines_data.get(stream_id, [])
                            if not timeline_list:
                                raise VSTError(f"No timeline found for stream {stream_id}")
                        logger.info("Timeline for stream %s: %s", stream_id, timeline_list)
                        start = timeline_list[0].get("startTime")
                        end = timeline_list[0].get("endTime")
                        start_dt = iso8601_to_datetime(start)
                        end_dt = iso8601_to_datetime(end)
                        if (end_dt - start_dt).total_seconds() < 1:
                            raise VSTError(f"Timeline duration is too short for stream {stream_id}")
                        return start, end
                    except VSTError:
                        raise
                    except Exception as e:
                        raise VSTError(f"Error getting timeline for stream {stream_id}: {e}") from e
    except _VST_BOUNDARY_ERRORS as e:
        raise VSTError(f"Failed to get timeline for stream {stream_id} after retrying transport errors", e) from e
    return "", ""  # unreachable; satisfies mypy


# ---------------------------------------------------------------------- client


class VSTClient:
    """Implements the VSTSnapshot protocol.

    All methods accept URLs and timeouts explicitly; no runtime, environment,
    or NAT state is read. resolve_stream_id and get_timeline forward to the
    free helpers above.
    """

    def __init__(
        self,
        *,
        internal_url: str,
        external_url: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        rewrite_internal_clip_url: bool = False,
    ) -> None:
        self._internal_url = internal_url
        self._external_url = external_url
        self._timeout_seconds = timeout_seconds
        self._rewrite_internal_clip_url = rewrite_internal_clip_url

    def build_screenshot_url(
        self,
        *,
        sensor_id: str,
        timestamp: str,
        internal: bool = False,
    ) -> str:
        """Build a screenshot URL. By default uses the external URL (client-facing);
        pass internal=True for in-cluster URLs.

        Today sensor_id and stream_id are treated as interchangeable
        (FIXME at tools/search.py:1638). We pass sensor_id straight through.
        """
        base = self._internal_url if internal else self._external_url
        return build_screenshot_url(base, sensor_id, timestamp)

    async def get_timelines_map(self) -> dict[str, tuple[str, str]]:
        """Return {stream_id: (start_iso, end_iso)} for all streams.

        One call, single attempt: this feeds best-effort screenshot-timestamp
        mapping, which must not add retry backoff to every search when VST is
        slow or down.
        """
        return await get_timelines_map(self._internal_url, timeout_seconds=self._timeout_seconds, retries=1)

    async def get_name_to_stream_id_map(self) -> dict[str, str]:
        """Return the current VST ``{source name: stream id}`` mapping."""
        return await get_name_to_stream_id_map(self._internal_url, timeout_seconds=self._timeout_seconds)

    async def get_video_clip_url(
        self,
        *,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str,
        time_format: Literal["iso", "offset"],
        internal: bool = True,
        disable_audio: bool = True,
    ) -> str:
        """Return a VST clip URL for VLM analysis.

        ``time_format='offset'`` treats timestamps as seconds from the stream
        start; ``time_format='iso'`` passes ISO strings through. Internal URLs
        are best for in-cluster VLMs; external URLs are useful when the VLM can
        reach only the public VIOS endpoint.
        """
        stream_id = await self.resolve_stream_id(sensor_id)
        if time_format == "offset":
            start: float | str | None = float(start_timestamp)
            end: float | str | None = float(end_timestamp)
        else:
            start = start_timestamp
            end = end_timestamp

        video_url = await get_video_clip_url(
            stream_id=stream_id,
            start_time=start,
            end_time=end,
            vst_internal_url=self._internal_url,
            disable_audio=disable_audio,
            timeout_seconds=self._timeout_seconds,
        )
        if internal and not self._rewrite_internal_clip_url:
            return video_url
        target_base = self._internal_url.rstrip("/") if internal else self._external_url.rstrip("/")
        parsed = urllib.parse.urlparse(video_url)
        suffix = parsed.path
        if parsed.query:
            suffix = f"{suffix}?{parsed.query}"
        if parsed.fragment:
            suffix = f"{suffix}#{parsed.fragment}"
        return f"{target_base}{suffix}"

    async def resolve_stream_id(self, sensor_id: str) -> str:
        """Resolve sensor_id → stream_id via the VST API. Raises VSTError on miss."""
        return await get_stream_id(sensor_id, self._internal_url, timeout_seconds=self._timeout_seconds)

    async def get_timeline(self, sensor_id: str) -> tuple[str, str]:
        """Return (start_iso, end_iso) for a sensor/stream's replay range."""
        # The free helper handles sensor-name → stream_id fallback internally.
        return await get_timeline(sensor_id, self._internal_url, timeout_seconds=self._timeout_seconds)

    async def aclose(self) -> None:
        return None
