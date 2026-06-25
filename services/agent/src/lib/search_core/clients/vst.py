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
"""VSTClient — VST surface used by primitives.

Includes the VST helpers (get_name_to_stream_id_map, get_stream_id, get_timeline)
ported from services/agent/src/vss_agents/tools/vst/{utils,timeline}.py with
two adjustments: no env reads (callers must pass internal URL explicitly),
and the retry exception tuple is widened to `Exception` to match the originals'
intent (they wrap `RuntimeError`/`VSTError` which aren't aiohttp types).

build_screenshot_url stays a free function for callers that don't need the
OO wrapper.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import TYPE_CHECKING
from typing import Literal
import urllib.parse

import aiohttp

from .._internal.retry import create_retry_strategy
from .._internal.time_convert import iso8601_to_datetime

if TYPE_CHECKING:
    from ..runtime import SearchRuntime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- types


class VSTError(Exception):
    """Base exception for VST API errors. Mirrors tools/vst/utils.py:64."""


# ----------------------------------------------------------------- free helpers


def build_screenshot_url(vst_external_url: str, stream_id: str, timestamp: str) -> str:
    """Build a client-facing screenshot URL.

    Mirrors tools/vst/snapshot.py:49. Pure string composition.
    """
    vst_external_url = vst_external_url.rstrip("/")
    return f"{vst_external_url}/vst/api/v1/replay/stream/{stream_id}/picture?startTime={timestamp}"


async def get_video_clip_url(
    *,
    stream_id: str,
    start_time: float | str | None = None,
    end_time: float | str | None = None,
    vst_internal_url: str,
    disable_audio: bool = True,
) -> str:
    """Return a temporary VST clip URL for a stream and optional time range.

    NAT's ``vst.video_clip`` tool owns this in the agent path. The search_core
    copy keeps critic/VLM verification usable without importing NAT or invoking
    the agent. ``start_time`` / ``end_time`` may be ISO strings or second offsets
    from the stream timeline.
    """
    if isinstance(start_time, str) != isinstance(end_time, str):
        raise VSTError("start_time and end_time must both be ISO strings or both be second offsets")

    if isinstance(start_time, str) and isinstance(end_time, str):
        start_time_iso = start_time
        end_time_iso = end_time
    else:
        start_timestamp, end_timestamp = await get_timeline(stream_id, vst_internal_url)
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
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/storage/file/{stream_id}/url?{query_params}"

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async for retry in create_retry_strategy(retries=3, exceptions=(Exception,)):
            with retry:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise VSTError(f"Failed to get video clip URL: HTTP {response.status}")
                    text = await response.text()
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError as e:
                        raise VSTError(f"Invalid JSON in VST clip response: {e}") from e
                    video_url = payload.get("videoUrl")
                    if not video_url:
                        raise VSTError("No videoUrl in VST clip response")
                    return str(video_url)

    raise VSTError("Failed to get video clip URL")


async def get_name_to_stream_id_map(vst_internal_url: str) -> dict[str, str]:
    """Fetch `/api/v1/sensor/streams` and return `{sensor_name: stream_id}`.

    Mirrors tools/vst/utils.py:70-97 with the env-fallback removed.
    """
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/sensor/streams"
    async with aiohttp.ClientSession() as session:
        async for retry in create_retry_strategy(retries=3, exceptions=(Exception,)):
            with retry:
                try:
                    async with session.get(url) as response:
                        if response.status != 200:
                            raise RuntimeError(f"VST streams API returned status {response.status}")
                        text = await response.text()
                        payload = json.loads(text)
                        mapping: dict[str, str] = {}
                        for file in payload:
                            stream_id = next(iter(file))
                            if isinstance(file[stream_id], list) and len(file[stream_id]) > 0:
                                name = file[stream_id][0]["name"]
                                mapping[name] = stream_id
                            else:
                                logger.warning(f"Stream ID {stream_id} is empty, skipping")
                        return mapping
                except Exception as e:
                    logger.error(f"Error getting name to stream ID map: {e}")
                    raise
    return {}  # unreachable; satisfies mypy


async def get_streams_info(vst_internal_url: str) -> dict[str, dict[str, str]]:
    """Return `{stream_id: {"name": name, "url": rtsp_url}}` from VST.

    Mirrors tools/vst/utils.py:420-453. Used by the Search orchestrator to
    resolve video_sources by name when source_type='rtsp'.
    """
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/sensor/streams"
    async with aiohttp.ClientSession() as session:
        async for retry in create_retry_strategy(retries=3, exceptions=(Exception,)):
            with retry:
                try:
                    async with session.get(url) as response:
                        if response.status != 200:
                            raise VSTError(f"VST streams API returned status {response.status}")
                        text = await response.text()
                        payload = json.loads(text)
                        result: dict[str, dict[str, str]] = {}
                        for entry in payload:
                            stream_id = next(iter(entry))
                            stream_list = entry[stream_id]
                            if stream_list and len(stream_list) > 0:
                                result[stream_id] = {
                                    "name": stream_list[0].get("name", ""),
                                    "url": stream_list[0].get("url", ""),
                                }
                        return result
                except Exception as e:
                    logger.error(f"Error getting streams info: {e}")
                    raise
    return {}  # unreachable; satisfies mypy


async def get_stream_id(sensor_id: str, vst_internal_url: str) -> str:
    """Resolve sensor_id → stream_id via VST. Mirrors tools/vst/utils.py:99-117.

    ``sensor_id`` may already be a stream_id (UUID); the function tolerates that.
    """
    stream_id_map = await get_name_to_stream_id_map(vst_internal_url)
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


async def get_sensor_id_from_stream_id(stream_id: str, vst_internal_url: str) -> str:
    """Reverse lookup: stream_id (UUID) → sensor_id (camera name).

    Mirrors tools/vst/utils.py:119-153. If ``stream_id`` is already a sensor
    name (and present in the VST map), returns it as-is. Raises VSTError on miss.
    """
    name_to_stream_id_map = await get_name_to_stream_id_map(vst_internal_url)
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


async def get_timeline(stream_id: str, vst_internal_url: str) -> tuple[str, str]:
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

    async with aiohttp.ClientSession() as session:
        async for retry in create_retry_strategy(retries=3, exceptions=(Exception,)):
            with retry:
                try:
                    async with session.get(timelines_url) as response:
                        if response.status != 200:
                            raise RuntimeError(f"VST timelines API returned status {response.status}")
                        text = await response.text()
                        timelines_data = json.loads(text)
                        timeline_list = timelines_data.get(stream_id, [])
                        if not timeline_list:
                            logger.info("no timeline for input; trying to resolve as sensor name")
                            stream_id = await get_stream_id(stream_id, vst_internal_url)
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
                except Exception as e:
                    raise VSTError(f"Error getting timeline for stream {stream_id}: {e}") from e
    return "", ""  # unreachable; satisfies mypy


# ---------------------------------------------------------------------- client


class VSTClient:
    """Implements the VSTSnapshot protocol.

    All methods accept the URL via constructor (typically from SearchRuntime);
    no env reads. resolve_stream_id and get_timeline forward to the free
    helpers above.
    """

    def __init__(self, *, internal_url: str, external_url: str) -> None:
        self._internal_url = internal_url
        self._external_url = external_url

    @classmethod
    def from_runtime(cls, rt: SearchRuntime) -> VSTClient:
        return cls(internal_url=rt.vst_internal_url, external_url=rt.vst_external_url)

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
        )
        if internal:
            return video_url

        external_base = self._external_url.rstrip("/")
        parsed = urllib.parse.urlparse(video_url)
        suffix = parsed.path
        if parsed.query:
            suffix = f"{suffix}?{parsed.query}"
        if parsed.fragment:
            suffix = f"{suffix}#{parsed.fragment}"
        return f"{external_base}{suffix}"

    async def resolve_stream_id(self, sensor_id: str) -> str:
        """Resolve sensor_id → stream_id via the VST API. Raises VSTError on miss."""
        return await get_stream_id(sensor_id, self._internal_url)

    async def get_timeline(self, sensor_id: str) -> tuple[str, str]:
        """Return (start_iso, end_iso) for a sensor/stream's replay range."""
        # The free helper handles sensor-name → stream_id fallback internally.
        return await get_timeline(sensor_id, self._internal_url)

    async def aclose(self) -> None:
        return None
