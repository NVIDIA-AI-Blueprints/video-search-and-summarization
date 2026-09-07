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

import asyncio
import contextlib
import dataclasses
import datetime
import json
import logging
import pathlib
import re
import time
from typing import Literal
import urllib.parse

import aiohttp

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import LibraryError
from vss_core._foundation.retry import create_retry_strategy
from vss_core._foundation.sanitize import quote_path_segment
from vss_core._foundation.time import iso8601_to_datetime

logger = logging.getLogger(__name__)

#: Indirection so tests can drive the deadline without sleeping.
_monotonic = time.monotonic

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


# ------------------------------------------------------------ media plane
#
# Operations behind `vss vios`. Ported from vss_agents/tools/vst/* so the CLI
# does not depend on vss_agents (which pulls nvidia-nat, and the CLI is
# NAT-free). Single copy: where the agent carried two implementations of a
# delete or a timeline read, only one lands here.


class VIOSInvalidInputError(LibraryError):
    """A caller error: a bad name, a missing file, an ambiguous handle.

    Separate from :class:`VSTError` so the CLI can exit 2 rather than 3 --
    "you asked for something impossible" and "VIOS is unreachable" need
    different responses from whatever is driving the CLI.
    """


class VIOSNotFoundError(LibraryError):
    """No sensor answers to the given name or id (exit 5)."""


#: Uploaded filenames become the sensor's name, so they are the addressable
#: handle. VIOS rejects whitespace outright; the rest of this is convention.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclasses.dataclass(frozen=True)
class SensorRef:
    """A resolved VIOS sensor: what the caller named, and what VIOS calls it."""

    name: str
    sensor_id: str
    stream_id: str
    url: str
    #: "video" for a file-backed sensor, "stream" for an RTSP one.
    kind: str
    #: True when no stream was flagged isMain and the first was taken instead.
    main_stream_assumed: bool = False


def classify_media_source(source: str) -> str:
    """What an `add` argument is, read from the argument itself.

    An RTSP URL is a live stream; anything else -- a local path, an http(s)
    URL to a file -- is a video. The caller already said which by what they
    typed, so asking them to restate it as a flag only creates a way to
    disagree with themselves.
    """
    return "stream" if source.lower().startswith(("rtsp://", "rtsps://")) else "video"


def classify_source(url: str) -> str:
    """Provenance of a sensor, read from its stream URL.

    `rtsp://` is a live camera; anything else (a filesystem path) is an
    uploaded file. This is the discriminator VIOS itself behaves differently
    on -- it decides the teardown flow -- which is why it is preferred over a
    `type` field (documented on `record/streams` but not emitted by current
    VIOS source).
    """
    if not url:
        # VIOS gave us no url, so we cannot tell. Saying "video" here would
        # send `delete --type video` down the wrong teardown flow.
        return "unknown"
    return "stream" if url.lower().startswith(("rtsp://", "rtsps://")) else "video"


def validate_media_name(filename: str) -> None:
    """Reject a filename VIOS would refuse, before spending the upload on it."""
    if not _FILENAME_RE.match(filename):
        raise VIOSInvalidInputError(
            f"invalid media name {filename!r}: the filename becomes the sensor name, so it must "
            f"start alphanumeric and contain only letters, digits, dot, dash or underscore "
            f"(no whitespace)"
        )


async def _get_json(url: str, timeout_seconds: float, what: str) -> object:
    """GET returning parsed JSON, with the module's retry and error policy."""
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async for retry in create_retry_strategy(retries=3, exceptions=_VST_RETRYABLE_ERRORS):
                with retry:
                    async with session.get(url) as response:
                        if response.status != 200:
                            raise VSTError(f"VIOS {what} returned status {response.status}")
                        try:
                            return json.loads(await response.text())
                        except VSTError:
                            raise
                        except Exception as e:
                            raise VSTError(f"Error parsing {what}: {e}") from e
    except _VST_BOUNDARY_ERRORS as e:
        raise VSTError(f"Failed to read {what} after retrying transport errors", e) from e
    return None  # unreachable; satisfies mypy


async def list_sensors(
    vst_internal_url: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, object]]:
    """`GET /sensor/list` -- every sensor's `sensorId` and `name`."""
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/sensor/list"
    payload = await _get_json(url, timeout_seconds, "sensor list")
    if not isinstance(payload, list):
        raise VSTError(f"Unexpected sensor list shape: {type(payload).__name__}")
    return [entry for entry in payload if isinstance(entry, dict)]


async def _sensor_streams(
    vst_internal_url: str,
    sensor_id: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, object]]:
    """`GET /sensor/{sensorId}/streams`, addressed by the id VIOS reported."""
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/sensor/{quote_path_segment(sensor_id)}/streams"
    payload = await _get_json(url, timeout_seconds, f"streams for {sensor_id}")
    if isinstance(payload, dict):
        return [payload]
    if not isinstance(payload, list):
        raise VSTError(f"Unexpected streams shape for {sensor_id}: {type(payload).__name__}")
    return [entry for entry in payload if isinstance(entry, dict)]


def _pick_stream(streams: list[dict[str, object]], sensor_name: str) -> tuple[dict[str, object], bool]:
    """Choose the stream to act on: isMain, else the only one, else refuse.

    A camera may publish a full-resolution main stream plus lower-resolution
    substreams. Silently taking the first yields degraded frames with no
    error anywhere, so an ambiguous multi-stream sensor is a hard failure and
    an assumed main is reported to the caller.
    """
    if not streams:
        raise VIOSNotFoundError(f"sensor {sensor_name!r} has no streams")
    main = [s for s in streams if s.get("isMain")]
    if len(main) == 1:
        return main[0], False
    if len(streams) == 1:
        return streams[0], True
    ids = ", ".join(str(s.get("streamId", "?")) for s in streams)
    raise VIOSInvalidInputError(
        f"sensor {sensor_name!r} has {len(streams)} streams and none is flagged isMain; "
        f"re-run with --sensor set to one of these streamIds: {ids}"
    )


async def resolve_sensor(
    vst_internal_url: str,
    handle: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> SensorRef:
    """Resolve a sensor **name** (preferred) or a raw id to its ids and stream.

    Name first, because the name is the stable handle: `sensorId` may carry a
    `_N` uniqueifier for auto-discovered files, is a fresh UUID for
    PUT-uploaded ones, and is occasionally an empty string for POST-uploaded
    ones. The id is therefore always *read from* `/sensor/list` by matching
    `.name` and never constructed from the name.

    A raw `sensorId` is accepted as an exact-id fallback once the name lookup
    misses, so a script already holding ids keeps working.
    """
    sensors = await list_sensors(vst_internal_url, timeout_seconds)

    by_name = [s for s in sensors if s.get("name") == handle]
    if len(by_name) > 1:
        ids = ", ".join(str(s.get("sensorId", "?")) for s in by_name)
        raise VIOSInvalidInputError(f"{len(by_name)} sensors are named {handle!r}; re-run addressing one by id: {ids}")

    match = by_name[0] if by_name else next((s for s in sensors if s.get("sensorId") == handle), None)
    wanted_stream = ""
    scanned_entries: list[dict[str, object]] = []
    if match is None:
        # Last resort: a streamId. _pick_stream tells an ambiguous caller to
        # address one explicitly, so the resolver has to accept what it asked for.
        scan_failure: VSTError | None = None
        for sensor in sensors:
            candidate = str(sensor.get("sensorId") or "")
            if not candidate:
                continue
            try:
                entries = await _sensor_streams(vst_internal_url, candidate, timeout_seconds)
            except VSTError as exc:
                # Keep the first failure. One unreadable sensor should not stop
                # the search, but if the search then finds nothing we must not
                # call it "not found" -- VIOS may simply have been unable to answer.
                scan_failure = scan_failure or exc
                continue
            if any(str(entry.get("streamId") or "") == handle for entry in entries):
                # Keep what the scan already read. Re-reading the same sensor
                # opens a window where the stream can vanish between the two
                # calls, and the second lookup then finds nothing.
                match, wanted_stream, scanned_entries = sensor, handle, entries
                break
    if match is None:
        if scan_failure is not None:
            raise scan_failure
        raise VIOSNotFoundError(f"no VIOS sensor named {handle!r} (and no sensor or stream with that id)")

    sensor_id = str(match.get("sensorId") or "")
    name = str(match.get("name") or handle)
    if not sensor_id:
        raise VSTError(f"VIOS reported sensor {name!r} with no sensorId")

    if wanted_stream:
        entries = scanned_entries
        found = next((e for e in entries if str(e.get("streamId") or "") == wanted_stream), None)
        if found is None:
            # Unreachable while `scanned_entries` is what matched, but a bare
            # `next()` raising StopIteration inside a coroutine surfaces as
            # `RuntimeError: coroutine raised StopIteration` -- exit 1 and a
            # traceback from a group whose contract is typed exits.
            raise VIOSNotFoundError(f"stream {wanted_stream!r} is no longer listed under {name!r}")
        stream, assumed = found, False
    else:
        entries = await _sensor_streams(vst_internal_url, sensor_id, timeout_seconds)
        stream, assumed = _pick_stream(entries, name)
    stream_id = str(stream.get("streamId") or "")
    if not stream_id:
        raise VSTError(f"VIOS reported a stream for {name!r} with no streamId")
    stream_url = str(stream.get("url") or "")
    return SensorRef(
        name=name,
        sensor_id=sensor_id,
        stream_id=stream_id,
        url=stream_url,
        kind=classify_source(stream_url),
        main_stream_assumed=assumed,
    )


async def list_media(
    vst_internal_url: str,
    kind: str | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, object]]:
    """Sensors joined with their streams, optionally filtered by provenance.

    The join is mandatory rather than an optimisation: provenance lives in the
    stream URL, so `/sensor/list` alone cannot answer `--type`.
    """
    rows: list[dict[str, object]] = []
    for sensor in await list_sensors(vst_internal_url, timeout_seconds):
        sensor_id = str(sensor.get("sensorId") or "")
        name = str(sensor.get("name") or "")
        if not sensor_id:
            # POST-uploaded sources sometimes report an empty sensorId. Say so
            # in the row rather than dropping the sensor from the listing.
            rows.append(
                {
                    "name": name,
                    "sensor_id": "",
                    "stream_id": "",
                    "type": "unknown",
                    "state": sensor.get("state"),
                    "is_main": False,
                    "has_timeline": bool(sensor.get("isTimelinePresent")),
                    "error": "VIOS reported no sensorId",
                }
            )
            continue
        # Deliberately not caught: if VIOS cannot answer, `list` must fail with
        # exit 3, never return a short list that reads as "these are all of them".
        streams = await _sensor_streams(vst_internal_url, sensor_id, timeout_seconds)
        if not streams:
            # A registered sensor with no stream rows still exists. Dropping it
            # from a successful listing reads as "not registered", and the
            # caller's next move is to create it -- vss-ask-video lists and
            # uploads when the name is absent, so this becomes a duplicate
            # upload and a 409. Report it, inventing nothing.
            #
            rows.append(
                {
                    "name": name,
                    "sensor_id": sensor_id,
                    "stream_id": "",
                    "type": "unknown",
                    "state": sensor.get("state"),
                    "is_main": False,
                    "has_timeline": bool(sensor.get("isTimelinePresent")),
                    "error": "VIOS lists this sensor with no streams",
                }
            )
            continue
        for stream in streams:
            stream_url = str(stream.get("url") or "")
            stream_id = str(stream.get("streamId") or "")
            row = {
                "name": name,
                "sensor_id": sensor_id,
                "stream_id": stream_id,
                "type": classify_source(stream_url),
                "state": sensor.get("state"),
                "is_main": bool(stream.get("isMain")),
                "has_timeline": bool(sensor.get("isTimelinePresent")),
            }
            # Only an RTSP sensor has a source worth reporting -- it is the
            # camera's address, and `vss-manage-alerts` reads it as
            # live_stream_url. For an uploaded file the equivalent is a path
            # inside the VIOS container, which duplicates `name` behind a
            # constant prefix and cannot be fetched, so it is omitted.
            if row["type"] == "stream":
                row["source"] = stream_url
            if not stream_id:
                row["error"] = "VIOS reported a stream with no streamId"
            elif not stream_url:
                # Without a url the provenance is unknowable, so this row is as
                # unclassifiable as the other two -- and the filter keeps a row
                # only when it matches --type or carries an error. One rule:
                # unclassifiable carries an error, so it is never hidden.
                row["error"] = "VIOS reported a stream with no url"
            rows.append(row)
    if kind is None:
        return rows
    # Filter, but never hide. A caller asking for videos is entitled to a list
    # of videos -- and a sensor we could not classify must still be visible,
    # because the question that list answers is usually "does this name already
    # exist", and a wrong "no" creates a duplicate.
    return [row for row in rows if row["type"] == kind or row.get("error")]


async def get_snapshot_url(
    vst_internal_url: str,
    stream_id: str,
    at: str | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Temporary picture URL: the latest live frame, or the frame nearest `at`.

    Ported from tools/vst/snapshot.py, minus the seconds-offset branch and the
    overlay config -- neither is reachable from the CLI surface.
    """
    base = vst_internal_url.rstrip("/")
    segment = quote_path_segment(stream_id)
    if at is None:
        url = f"{base}/vst/api/v1/live/stream/{segment}/picture/url"
        what = "live snapshot url"
    else:
        query = urllib.parse.urlencode({"startTime": at})
        url = f"{base}/vst/api/v1/replay/stream/{segment}/picture/url?{query}"
        what = "replay snapshot url"
    payload = await _get_json(url, timeout_seconds, what)
    image_url = payload.get("imageUrl") if isinstance(payload, dict) else None
    if not image_url:
        raise VSTError(f"VIOS returned no imageUrl for {what}")
    return str(image_url)


def normalise_media_url(media_url: str, origin: str) -> str:
    """Re-anchor a VIOS-issued URL on the origin the caller can actually reach.

    VIOS 3.2.0 can answer with a doubled scheme (``http://http://host/...``), a
    bare ``/storage/...`` path, or a ``localhost`` host that does not resolve
    from inside the VLM's container. Reduce whatever came back to its path and
    re-attach the configured origin, so the handle works for whoever we give it
    to rather than only for VIOS itself.

    Unconditional on purpose. Re-anchoring a host that was already right is a
    no-op, and the case it would harm -- VIOS answering with a legitimately
    external host -- does not arise for stored media, which VIOS serves itself.
    Making it conditional would mean deciding which foreign hosts to trust, to
    protect a case that does not occur.
    """
    if not media_url:
        raise VSTError("VIOS returned an empty media url")

    remainder = media_url
    while remainder.startswith(("http://", "https://")):
        remainder = remainder.split("://", 1)[1]
        if not remainder.startswith(("http://", "https://")):
            # `remainder` is now host[/path]; keep the path only.
            remainder = "/" + remainder.split("/", 1)[1] if "/" in remainder else "/"
            break

    path = remainder if remainder.startswith("/") else f"/{remainder}"
    if not path.startswith("/vst/"):
        path = f"/vst{path}"
    return f"{origin.rstrip('/').removesuffix('/vst')}{path}"


async def warm_media_url(media_url: str, timeout_seconds: float = 120.0, attempts: int = 3) -> bool:
    """GET the handle once so VIOS renders it before anyone else asks.

    VIOS renders a clip lazily on first fetch, and a HEAD answers 404, so this
    is a real GET. Best-effort by design: a cold URL is still a valid handle,
    and failing the whole command because the warm-up timed out would be worse
    than handing back a URL whose first read is slow.
    """
    # One deadline across every attempt, so warm-up is bounded whatever the
    # retries do.
    deadline = _monotonic() + timeout_seconds
    for _ in range(attempts):
        remaining = deadline - _monotonic()
        if remaining <= 0:
            break
        timeout = aiohttp.ClientTimeout(total=remaining)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(media_url) as response,
            ):
                if response.status == 200:
                    # One chunk, not the body. Rendering starts on first read,
                    # and `read()` would pull an entire clip into memory --
                    # gigabytes for a long recording, three times over.
                    async for _chunk in response.content.iter_chunked(_WARM_CHUNK_BYTES):
                        break
                    return True
        except _VST_BOUNDARY_ERRORS as exc:
            logger.info("Warm-up attempt failed for %s: %s", media_url, exc)
    logger.info("Could not warm %s; returning it cold", media_url)
    return False


async def add_stream(
    vst_internal_url: str,
    sensor_url: str,
    name: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Register an RTSP source (`POST /sensor/add`); returns its `sensorId`.

    Ported from tools/vst/utils.py:add_sensor without the `VST_INTERNAL_URL`
    environment fallback -- the CLI resolves its origin from `vss configure`
    and reads no process env.
    """
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/sensor/add"
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(url, json={"sensorUrl": sensor_url, "name": name}) as response,
        ):
            body = await response.text()
            if response.status not in (200, 201):
                raise VSTError(f"VIOS sensor/add returned {response.status}: {_vios_error(body)}")
            try:
                result = json.loads(body)
            except Exception as e:
                raise VSTError(f"Error parsing sensor/add response: {e}") from e
    except _VST_BOUNDARY_ERRORS as e:
        raise VSTError("Failed to reach VIOS while adding the sensor", e) from e
    sensor_id = result.get("sensorId") or result.get("id") if isinstance(result, dict) else None
    if not sensor_id:
        # The sensor was created; VIOS just did not say what it called it
        # (a documented quirk of some upload paths). Re-resolve by name rather
        # than reporting a failure the caller would retry into a duplicate.
        ref = await resolve_sensor(vst_internal_url, name, timeout_seconds)
        return ref.sensor_id
    return str(sensor_id)


async def upload_media(
    vst_internal_url: str,
    path: pathlib.Path,
    timestamp: str = "2025-01-01T00:00:00.000Z",
    timeout_seconds: float = 600.0,
    name: str | None = None,
) -> dict[str, object]:
    """`PUT /storage/file/{filename}` -- register a local file as a sensor.

    The stored filename becomes the sensor's name, so it is validated first: a
    rejected name here costs nothing, where VIOS would spend the whole upload
    before answering 400. Pass ``name`` to store it under something other than
    the local basename -- the usual reason being that the local name is not a
    name you would want to address the sensor by.
    """
    stored_name = name or path.name
    validate_media_name(stored_name)
    if not path.is_file():
        raise VIOSInvalidInputError(f"no such file: {path}")
    size = path.stat().st_size
    query = urllib.parse.urlencode({"timestamp": timestamp})
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/storage/file/{quote_path_segment(stored_name)}?{query}"
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        headers = {"Content-Type": "application/octet-stream", "Content-Length": str(size)}
        # `path.open` is a sync context manager: it cannot join the `async with`.
        with path.open("rb") as handle:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.put(url, data=handle, headers=headers) as response,
            ):
                body = await response.text()
                if response.status == 409:
                    raise VIOSInvalidInputError(
                        f"VIOS already holds a file named {stored_name!r}; delete it first "
                        f"(`vss vios delete --type video --sensor {pathlib.Path(stored_name).stem}`) "
                        f"or pass --name to store it under another"
                    )
                if response.status not in (200, 201):
                    raise VSTError(f"VIOS upload returned {response.status}: {_vios_error(body)}")
                try:
                    result = json.loads(body)
                except Exception as e:
                    raise VSTError(f"Error parsing upload response: {e}") from e
    except _VST_BOUNDARY_ERRORS as e:
        raise VSTError("Failed to reach VIOS while uploading", e) from e
    if not isinstance(result, dict):
        raise VSTError(f"Unexpected upload response shape: {type(result).__name__}")
    return result


#: A fetched source is streamed straight through, so this bounds what a URL
#: can make the CLI pull rather than what it can hold in memory.
_FETCH_MAX_BYTES = 8 * 1024 * 1024 * 1024
_FETCH_TIMEOUT_SECONDS = 3600.0


async def upload_from_url(
    vst_internal_url: str,
    source_url: str,
    name: str | None = None,
    timestamp: str = "2025-01-01T00:00:00.000Z",
    timeout_seconds: float = _FETCH_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Stream an http(s) video into VIOS without landing it on disk.

    The bytes are piped from the source response into the PUT, so a large
    file costs bandwidth rather than memory or a temp file.

    VIOS requires Content-Length on the upload, so the source has to declare
    its size. A chunked source is refused with that reason rather than being
    buffered to discover it -- buffering is exactly what this avoids.
    """
    parsed = urllib.parse.urlparse(source_url)
    if parsed.scheme not in ("http", "https"):
        raise VIOSInvalidInputError(f"{source_url!r} is not an http(s) URL")

    stored_name = name or pathlib.PurePosixPath(parsed.path).name
    if not stored_name:
        raise VIOSInvalidInputError(f"cannot derive a name from {source_url!r}; pass --name")
    validate_media_name(stored_name)

    query = urllib.parse.urlencode({"timestamp": timestamp})
    target = f"{vst_internal_url.rstrip('/')}/vst/api/v1/storage/file/{quote_path_segment(stored_name)}?{query}"
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    try:
        # The session and the source response are separate context managers
        # only because the PUT below needs both open at once: the response body
        # is the PUT's payload.
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            # max_redirects rather than allow_redirects=False: a CDN link is
            # normally a redirect, but an unbounded chain is not.
            session.get(source_url, max_redirects=5) as source,
        ):
            if source.status != 200:
                raise VIOSInvalidInputError(f"{source_url} returned HTTP {source.status}")
            size = source.content_length
            if size is None:
                raise VIOSInvalidInputError(
                    f"{source_url} does not declare Content-Length, which VIOS requires for an "
                    f"upload; download it first and pass the local path"
                )
            if size > _FETCH_MAX_BYTES:
                raise VIOSInvalidInputError(f"{source_url} is {size} bytes, over the {_FETCH_MAX_BYTES}-byte limit")
            headers = {"Content-Type": "application/octet-stream", "Content-Length": str(size)}
            async with session.put(target, data=source.content, headers=headers) as response:
                body = await response.text()
                if response.status == 409:
                    raise VIOSInvalidInputError(
                        f"VIOS already holds a file named {stored_name!r}; delete it first or pass --name"
                    )
                if response.status not in (200, 201):
                    raise VSTError(f"VIOS upload returned {response.status}: {_vios_error(body)}")
                try:
                    result = json.loads(body)
                except Exception as e:
                    raise VSTError(f"Error parsing upload response: {e}") from e
    except _VST_BOUNDARY_ERRORS as e:
        raise VSTError(f"Failed while streaming {source_url} into VIOS", e) from e
    if not isinstance(result, dict):
        raise VSTError(f"Unexpected upload response shape: {type(result).__name__}")
    return result


def _vios_error(body: str) -> str:
    """VIOS's `error_message` when it sent one, else the raw body."""
    try:
        parsed = json.loads(body)
    except Exception:
        return body.strip()
    if isinstance(parsed, dict):
        return str(parsed.get("error_message") or parsed)
    return str(parsed)


async def _delete(url: str, timeout_seconds: float, what: str) -> None:
    """DELETE where 404 means the goal state already holds.

    Idempotency is load-bearing: VIOS storage deletion can cascade the sensor
    registration away before the paired sensor delete runs, and counting that
    404 as a failure downgrades a fully clean teardown to `partial`.
    """
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session, session.delete(url) as response:
            if response.status in (200, 204, 404):
                return
            raise VSTError(f"VIOS {what} returned {response.status}: {_vios_error(await response.text())}")
    except _VST_BOUNDARY_ERRORS as e:
        raise VSTError(f"Failed to reach VIOS during {what}", e) from e


def _as_instant(value: str, origin: datetime.datetime, field: str, allow_offset: bool) -> datetime.datetime:
    """One window bound, as an absolute instant.

    A bare number is an offset in seconds from the start of the recording --
    the natural way to say "ten seconds in" about a file. An ISO-8601 string
    is absolute. A live stream accepts only the latter: there is no natural
    zero to count from.
    """
    text = value.strip()
    if not text:
        raise VIOSInvalidInputError(f"{field} is empty")

    try:
        seconds = float(text)
    except ValueError:
        seconds = None

    if seconds is not None:
        if seconds != seconds or seconds in (float("inf"), float("-inf")):
            # float() accepts nan/inf/1e400; timedelta does not, and the
            # resulting ValueError/OverflowError is unmapped -- exit 1 with a
            # traceback from a group whose contract is typed exits.
            raise VIOSInvalidInputError(f"{field}={value!r} is not a finite number of seconds")
        if not allow_offset:
            raise VIOSInvalidInputError(
                f"{field}={value!r} is a second offset, which only means something for a recorded "
                f"file; a live stream needs an absolute ISO-8601 time"
            )
        if seconds < 0:
            raise VIOSInvalidInputError(f"{field}={value!r} is negative")
        return origin + datetime.timedelta(seconds=seconds)

    try:
        return iso8601_to_datetime(text)
    except Exception as exc:
        hint = " (or a second offset)" if allow_offset else ""
        raise VIOSInvalidInputError(
            f"{field}={value!r} is not an ISO-8601 timestamp{hint}; expected e.g. 2025-01-01T00:00:00.000Z"
        ) from exc


def _isoformat(moment: datetime.datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _segment_holding(
    moment: datetime.datetime,
    segments: list[tuple[str, str]],
    label: str,
) -> tuple[str, str]:
    """The recorded segment containing `moment`, or a caller error naming it."""
    for seg_start, seg_end in segments:
        if iso8601_to_datetime(seg_start) <= moment <= iso8601_to_datetime(seg_end):
            return seg_start, seg_end
    if len(segments) == 1:
        # Nothing to disambiguate, so say which end it fell outside rather than
        # making the caller compare timestamps.
        only_start, only_end = segments[0]
        window = f"recorded window is {only_start} to {only_end}"
        side = "before the recording starts" if moment < iso8601_to_datetime(only_start) else "after the recording ends"
        raise VIOSInvalidInputError(f"{label} {_isoformat(moment)} is {side}; {window}")
    listed = ", ".join(f"{a} to {b}" for a, b in segments)
    raise VIOSInvalidInputError(f"{label} {_isoformat(moment)} is not inside a recorded segment; recorded: {listed}")


def resolve_window(
    segments: list[tuple[str, str]],
    start: str | None,
    end: str | None,
    kind: str,
) -> tuple[str, str]:
    """Turn requested bounds into a window VIOS can serve, or refuse.

    Checked here rather than by VIOS, which answers a malformed or
    out-of-range window with a bare HTTP 400 that says nothing about which
    bound was wrong or what range was available.

    A recorded file may omit either bound -- the recording's own start and end
    are the obvious defaults -- and may express one as a second offset. A live
    stream must state both, absolutely.
    """
    if kind != "video" and (not start or not end):
        raise VIOSInvalidInputError(
            "a live stream has no default window: pass both --start-time and --end-time as ISO-8601"
        )
    if not segments:
        if not start or not end:
            raise VIOSInvalidInputError("nothing is recorded for this sensor, so there is no window to default to")
        first = _as_instant(start, iso8601_to_datetime(start), "--start-time", allow_offset=False)
        last = _as_instant(end, first, "--end-time", allow_offset=False)
        if first > last:
            raise VIOSInvalidInputError(f"--start-time {_isoformat(first)} is after --end-time {_isoformat(last)}")
        return _isoformat(first), _isoformat(last)

    # Offsets count from the start of the recording, which is the earliest
    # segment: "ten seconds in" means ten seconds into the video.
    allow_offset = kind == "video"
    origin = iso8601_to_datetime(segments[0][0])
    given_first = _as_instant(start, origin, "--start-time", allow_offset) if start else None
    given_last = _as_instant(end, origin, "--end-time", allow_offset) if end else None

    if given_first is None and given_last is None:
        return segments[0]

    # One bound given: complete it from the segment that bound falls in, not
    # from the earliest. Defaulting to the earliest refuses a window that
    # genuinely exists -- `--start-time 13:02` on a stream recorded 12:00-12:10
    # and 13:00-13:10 would pair with 12:10 and fail as "start after end".
    if given_last is None and given_first is not None:
        return _isoformat(given_first), _segment_holding(given_first, segments, "--start-time")[1]
    if given_first is None and given_last is not None:
        return _segment_holding(given_last, segments, "--end-time")[0], _isoformat(given_last)

    assert given_first is not None and given_last is not None
    first, last = given_first, given_last

    if first > last:
        raise VIOSInvalidInputError(f"--start-time {_isoformat(first)} is after --end-time {_isoformat(last)}")

    # The window must sit inside ONE segment. VIOS rejects a range spanning a
    # gap, and validating against the envelope would accept 12:30 on a stream
    # recorded 12:00-12:10 and 13:00-13:10.
    for seg_start, seg_end in segments:
        if iso8601_to_datetime(seg_start) <= first and last <= iso8601_to_datetime(seg_end):
            return _isoformat(first), _isoformat(last)

    if len(segments) == 1:
        # Nothing to disambiguate, so name the bound that is wrong rather than
        # leaving the caller to compare two timestamps themselves.
        only_start, only_end = segments[0]
        window = f"recorded window is {only_start} to {only_end}"
        for label, moment in (("--start-time", first), ("--end-time", last)):
            if moment < iso8601_to_datetime(only_start):
                raise VIOSInvalidInputError(f"{label} {_isoformat(moment)} is before the recording starts; {window}")
            if moment > iso8601_to_datetime(only_end):
                raise VIOSInvalidInputError(f"{label} {_isoformat(moment)} is after the recording ends; {window}")

    listed = ", ".join(f"{a} to {b}" for a, b in segments)
    raise VIOSInvalidInputError(
        f"{_isoformat(first)} to {_isoformat(last)} is not inside a single recorded segment; recorded: {listed}"
    )


async def recorded_segments(
    vst_internal_url: str,
    stream_id: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[tuple[str, str]]:
    """Every recorded segment for one stream, earliest first.

    Segments, not an envelope. A stream recorded 12:00-12:10 and 13:00-13:10
    has nothing at 12:30, and collapsing the two into 12:00-13:10 makes the
    CLI accept a window VIOS then rejects -- validating against a range that
    does not exist.

    Stricter than :func:`get_timelines_map`, which serves best-effort
    screenshot enrichment and treats an unreadable payload as "unmapped".
    That is right there and wrong here: a delete that cannot read the timeline
    must not conclude there was nothing to reclaim.
    """
    base = vst_internal_url.rstrip("/")
    if base.endswith("/vst"):
        base = base[:-4]
    payload = await _get_json(f"{base}/vst/api/v1/storage/timelines", timeout_seconds, "timelines")
    if not isinstance(payload, dict):
        raise VSTError(f"Unexpected timelines response shape: {type(payload).__name__}")

    # Absent and present-but-null are different answers. A stream VIOS does not
    # list has nothing recorded; a stream it lists with a null timeline is one
    # it knows about and could not describe, and concluding "nothing to
    # reclaim" from that is how a delete reports success over live recordings.
    if stream_id not in payload:
        return []
    segments = payload[stream_id]
    if segments is None:
        raise VSTError(
            f"VIOS lists {stream_id} with a null timeline, which is not the same as having no "
            f"recordings; refusing to report a clean delete"
        )
    if not isinstance(segments, list):
        raise VSTError(f"Unexpected timeline shape for {stream_id}: {type(segments).__name__}")

    out: list[tuple[datetime.datetime, datetime.datetime]] = []
    for seg in segments:
        if not (isinstance(seg, dict) and seg.get("startTime") and seg.get("endTime")):
            raise VSTError(
                f"VIOS listed a recorded segment for {stream_id} without a usable start and end time; "
                f"refusing to report a clean delete"
            )
        # Parsed, not compared as strings: an unparseable value is truthy and
        # would otherwise reach the delete query, and ordering raw strings is
        # only correct while every value happens to be uniform ISO-8601 UTC.
        try:
            first = iso8601_to_datetime(str(seg["startTime"]))
            last = iso8601_to_datetime(str(seg["endTime"]))
        except Exception as exc:
            raise VSTError(
                f"VIOS listed a segment for {stream_id} with an unparseable time "
                f"({seg.get('startTime')!r}..{seg.get('endTime')!r}); refusing to report a clean delete"
            ) from exc
        if first > last:
            raise VSTError(
                f"VIOS listed a segment for {stream_id} ending before it starts "
                f"({seg['startTime']}..{seg['endTime']}); refusing to report a clean delete"
            )
        out.append((first, last))

    out.sort()
    return [(_isoformat(a), _isoformat(b)) for a, b in out]


async def recorded_span(
    vst_internal_url: str,
    stream_id: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> tuple[str, str] | None:
    """The envelope across every segment, or None if nothing is recorded.

    For `delete`, which reclaims the whole range and does not care about gaps
    inside it. Anything choosing a *window* wants
    :func:`recorded_segments` -- an envelope hides the gaps.
    """
    segments = await recorded_segments(vst_internal_url, stream_id, timeout_seconds)
    if not segments:
        return None
    # max over ends, not the last segment's: sorting by start says nothing
    # about which segment finishes last, and a nested one would truncate the
    # envelope a delete then reclaims.
    return segments[0][0], max(end for _, end in segments)


async def confirm_absent(
    vst_internal_url: str,
    name: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Re-list and fail if `name` is still registered.

    Matching on **name** rather than sensorId is the point: an auto-discovered
    file's `sensorId` may carry a `_N` suffix, so an id-keyed absence check
    passes while VIOS still lists the source under its canonical name -- and
    a delete that answered non-200 then reports success.
    """
    remaining = [s for s in await list_sensors(vst_internal_url, timeout_seconds) if s.get("name") == name]
    if remaining:
        ids = ", ".join(str(s.get("sensorId", "?")) for s in remaining)
        raise VSTError(f"VIOS still lists {name!r} after delete (sensorId: {ids})")


class VIOSTimeoutError(LibraryError):
    """A bounded wait expired (exit 7)."""


#: An upload is accepted before VIOS has indexed it. The skill this replaced
#: waited 15 x 2s; doubled here because that was tuned on small sample clips
#: and a large file takes longer to index.
_TIMELINE_WAIT_SECONDS = 60.0
_TIMELINE_POLL_SECONDS = 2.0

#: Shorter than the upload wait: a delete should not hang for a minute, but a
#: few polls cover the gap between an upload landing and its timeline.
_DELETE_TIMELINE_WAIT_SECONDS = 15.0

#: Enough to make VIOS start rendering; the bytes are discarded.
_WARM_CHUNK_BYTES = 64 * 1024


async def await_timeline(
    vst_internal_url: str,
    stream_id: str,
    timeout_seconds: float = _TIMELINE_WAIT_SECONDS,
    interval_seconds: float = _TIMELINE_POLL_SECONDS,
) -> tuple[str, str]:
    """Wait until VIOS has indexed a stream's recording, or give up saying so.

    VIOS accepts an upload before its timeline exists, so a clip taken
    immediately afterwards finds nothing recorded. Only "not indexed yet" is
    retried: a backend error propagates on the first attempt rather than being
    mistaken for slow indexing and retried to the bound.

    Deliberately not folded into :func:`recorded_span`, which `delete` also
    calls -- waiting a minute for recordings we are about to remove would be
    the wrong behaviour in the one place it matters most.
    """
    deadline = _monotonic() + timeout_seconds
    while True:
        span = await recorded_span(vst_internal_url, stream_id, timeout_seconds=_DEFAULT_TIMEOUT_SECONDS)
        if span is not None:
            return span
        if _monotonic() >= deadline:
            raise VIOSTimeoutError(
                f"VIOS accepted the upload but had not indexed {stream_id} after "
                f"{timeout_seconds:.0f}s; it may still appear -- check `vss vios timeline`"
            )
        await asyncio.sleep(interval_seconds)


async def delete_media(
    vst_internal_url: str,
    ref: SensorRef,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    keep_recordings: bool = False,
) -> dict[str, object]:
    """Remove a sensor and its recordings, by the flow its provenance needs.

    An uploaded file is removed through storage (which takes the on-disk file
    with it). An RTSP sensor needs both: the sensor delete stops the
    recording, the storage delete reclaims what it already wrote. Either way
    absence is then confirmed by name, never inferred from the status code.

    `keep_recordings` stops the camera without reclaiming what it recorded --
    for a stream whose footage is already indexed, where search hits still need
    their clips to resolve after the camera is gone. It is refused for an
    uploaded file, where the storage delete *is* the deregistration: keeping
    the bytes there would leave a file with no sensor pointing at it.
    """
    if keep_recordings and ref.kind != "stream":
        raise VIOSInvalidInputError(
            f"--keep-recordings applies to a stream; {ref.name!r} is a {ref.kind}, whose stored file "
            f"is what deregisters it, so keeping it would orphan the file"
        )
    base = vst_internal_url.rstrip("/")
    steps: list[str] = []

    # Read the recorded span BEFORE deleting the sensor: for an RTSP source the
    # sensor delete can take the timeline with it, and then there is no way left
    # to name the range whose recordings still occupy disk.
    #
    # The whole envelope, not one segment: a stream with several recorded
    # segments would otherwise keep everything after the first while this
    # function reported a confirmed cleanup.
    span = await recorded_span(vst_internal_url, ref.stream_id, timeout_seconds)

    # An uploaded file is on disk whether or not its timeline has been indexed,
    # and the storage delete below -- the thing that reclaims the bytes -- is
    # keyed on that timeline. Indexing is asynchronous, so a delete issued soon
    # after an upload sees no timeline, skips storage, deregisters the sensor,
    # and confirms clean while the file remains. Wait briefly rather than
    # orphaning it. A stream with nothing recorded is a different case and is
    # left alone: there is no stored file to reclaim.
    if span is None and ref.kind == "video":
        with contextlib.suppress(VIOSTimeoutError):
            span = await await_timeline(vst_internal_url, ref.stream_id, timeout_seconds=_DELETE_TIMELINE_WAIT_SECONDS)

    sensor_url = f"{base}/vst/api/v1/sensor/{quote_path_segment(ref.sensor_id)}"

    if ref.kind == "stream":
        await _delete(sensor_url, timeout_seconds, "sensor delete")
        steps.append("sensor")

    if span is not None and not keep_recordings:
        window = urllib.parse.urlencode({"startTime": span[0], "endTime": span[1]})
        await _delete(
            f"{base}/vst/api/v1/storage/file/{quote_path_segment(ref.stream_id)}?{window}",
            timeout_seconds,
            "storage delete",
        )
        steps.append("storage")

    # For an uploaded file the storage delete is what deregisters the sensor --
    # so a file with nothing recorded yet (timelines populate asynchronously
    # after an upload) would otherwise have had nothing issued against it at
    # all, and the confirmation below would fail on a sensor we never tried to
    # remove. Deregister whatever is still listed before confirming.
    if "sensor" not in steps and any(
        s.get("name") == ref.name for s in await list_sensors(vst_internal_url, timeout_seconds)
    ):
        await _delete(sensor_url, timeout_seconds, "sensor delete")
        steps.append("sensor")

    await confirm_absent(vst_internal_url, ref.name, timeout_seconds)
    return {
        "name": ref.name,
        "sensor_id": ref.sensor_id,
        "type": ref.kind,
        "deleted": steps,
        # Three states, kept apart. "removed": storage was reclaimed. "none": a
        # stream VIOS listed no recordings for -- nothing to reclaim. For an
        # uploaded file whose timeline never appeared, neither is true: the
        # sensor is gone but the bytes may not be, so say so rather than let it
        # read as a clean delete. A failure to read the timelines raises.
        "recordings": (
            "kept" if keep_recordings else ("removed" if span else ("none" if ref.kind == "stream" else "unconfirmed"))
        ),
        "confirmed": keep_recordings or span is not None or ref.kind == "stream",
    }
