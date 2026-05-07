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

"""VST Video Clip tool - video URL tool with bounding box overlay support.

Supports two timestamp formats controlled by config:
- 'offset' format: start_time/end_time are floats (seconds since beginning of stream)
- 'iso' format: start_time/end_time are ISO 8601 UTC timestamp strings
"""

import asyncio
from collections.abc import AsyncGenerator
import datetime
import json
import logging
import os
from typing import Literal
import urllib.parse

import aiohttp
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator

from vss_agents.tools.vst.timeline import get_timeline
from vss_agents.tools.vst.utils import VSTError
from vss_agents.tools.vst.utils import build_overlay_config
from vss_agents.tools.vst.utils import get_stream_id
from vss_agents.tools.vst.utils import validate_video_url
from vss_agents.utils.retry import create_retry_strategy

logger = logging.getLogger(__name__)


class VSTVideoClipConfig(FunctionBaseConfig, name="vst.video_clip"):
    """Configuration for the VST Video Clip tool."""

    vst_internal_url: str = Field(
        ...,
        description="The internal VST URL for making API requests (e.g., http://${INTERNAL_IP}:30888)",
    )
    vst_external_url: str = Field(
        ...,
        description="The external VST URL for client-facing URLs (e.g., http://${EXTERNAL_IP}:30888)",
    )
    overlay_config: bool = Field(
        False,
        description="Whether to enable overlay configuration for object detection bounding box overlays",
    )
    time_format: Literal["offset", "iso"] = Field(
        "offset",
        description="Timestamp input format: 'iso' for ISO 8601 UTC strings (e.g. '2025-08-25T03:05:55Z'), "
        "'offset' for seconds since stream start. "
        "Must match across video_understanding, vst.video_clip, vst.snapshot, and critic_agent configs.",
    )
    disable_audio: bool = Field(
        True,
        description="When True, VST clip requests pass disableAudio=true (audio stripped; CR2-compatible). "
        "Set False for VLMs that accept audio (e.g. Nemotron Omni NIM) to passthrough audio from VIOS/VST.",
    )


class VSTVideoClipOffsetInput(BaseModel):
    """Input for the VST Video Clip tool (offset mode).

    start_time and end_time are floats representing seconds since the beginning of the stream.
    """

    sensor_id: str = Field(
        ...,
        description="The name or the stream ID of the video file uploaded",
        min_length=1,
    )
    start_time: float | None = Field(
        None,
        description="Start time in seconds since the beginning of the stream, or None for entire video",
    )
    end_time: float | None = Field(
        None,
        description="End time in seconds since the beginning of the stream, or None for entire video",
    )
    object_ids: list[str] | None = Field(
        None,
        description="Optional list of object IDs to display as overlays in the video",
    )

    @model_validator(mode="before")
    @classmethod
    def validate_start_and_end_time(cls, info: dict) -> dict:
        start = info.get("start_time")
        end = info.get("end_time")

        if start is not None:
            start = float(start)
            if start < 0:
                raise ValueError("Start time must be non-negative")
            info["start_time"] = start

        if end is not None:
            end = float(end)
            if end < 0:
                raise ValueError("End time must be non-negative")
            info["end_time"] = end

        if start is not None and end is not None and start >= end:
            raise ValueError("Start time must be before end time")

        return info


class VSTVideoClipISOInput(BaseModel):
    """Input for the VST Video Clip tool (ISO timestamp mode).

    start_time and end_time are ISO 8601 UTC timestamp strings.
    """

    sensor_id: str = Field(
        ...,
        description="The name or the stream ID of the video file uploaded",
        min_length=1,
    )
    start_time: str | None = Field(
        None,
        description="Start time as ISO 8601 UTC timestamp (e.g., '2025-08-25T03:05:55.752Z'), or None for entire video",
    )
    end_time: str | None = Field(
        None,
        description="End time as ISO 8601 UTC timestamp (e.g., '2025-08-25T03:06:15.752Z'), or None for entire video",
    )
    object_ids: list[str] | None = Field(
        None,
        description="Optional list of object IDs to display as overlays in the video",
    )


# Union type for backward compatibility in internal APIs
VSTVideoClipInput = VSTVideoClipOffsetInput | VSTVideoClipISOInput


class VSTVideoClipOutput(BaseModel):
    """Output for the VST Video Clip tool"""

    video_url: str = Field(
        ...,
        description="Direct URL to access the video file",
    )
    stream_id: str = Field(
        ...,
        description="The stream ID that is mapped from the sensor ID",
    )


async def get_video_url(
    stream_id: str,
    start_time: float | str | None = None,
    end_time: float | str | None = None,
    vst_internal_url: str | None = None,
    overlay_enabled: bool = False,
    object_ids: list[str] | None = None,
    disable_audio: bool = True,
) -> str:
    """Get the video URL for a given stream ID.

    Args:
        stream_id: The VST stream ID.
        start_time: Seconds offset (float), ISO 8601 timestamp (str), or None for full video.
        end_time: Seconds offset (float), ISO 8601 timestamp (str), or None for full video.
        vst_internal_url: Internal VST URL.
        overlay_enabled: Whether to add bounding box overlay configuration.
        object_ids: Optional list of object IDs for overlay filtering.
        disable_audio: When True (default), sets VST query disableAudio=true. When False, sets disableAudio=false
            so audio can reach audio-capable VLMs (e.g. Nemotron Omni).

    Returns:
        The video URL from VST.
    """
    if vst_internal_url is None:
        vst_internal_url = os.getenv("VST_INTERNAL_URL", "http://localhost:30888")

    # Determine if we're using ISO timestamps or seconds offsets
    if isinstance(start_time, str) and isinstance(end_time, str):
        # ISO timestamps - use directly
        start_time_iso = start_time
        end_time_iso = end_time
    else:
        # Seconds offsets - compute from timeline
        start_timestamp, end_timestamp = await get_timeline(stream_id, vst_internal_url)

        # Normalize to timezone-aware UTC datetimes
        start_dt = datetime.datetime.fromisoformat(start_timestamp.replace("Z", "+00:00"))
        end_dt = datetime.datetime.fromisoformat(end_timestamp.replace("Z", "+00:00"))
        start_time_pts = start_dt.timestamp() * 1000
        end_time_pts = end_dt.timestamp() * 1000

        if start_time is not None and not isinstance(start_time, str):
            clip_start_time_pts = min(start_time * 1000 + start_time_pts, end_time_pts)
        else:
            clip_start_time_pts = start_time_pts

        if end_time is not None and not isinstance(end_time, str):
            clip_end_time_pts = min(end_time * 1000 + start_time_pts, end_time_pts)
        else:
            clip_end_time_pts = end_time_pts

        # Strengthened validation
        if (
            clip_start_time_pts < start_time_pts
            or clip_end_time_pts > end_time_pts
            or clip_end_time_pts < clip_start_time_pts
        ):
            raise ValueError(
                f"Clip times must be within the stream timeline {start_timestamp}..{end_timestamp} and start <= end, got {clip_start_time_pts}..{clip_end_time_pts}"
            )

        start_time_iso = (
            datetime.datetime.fromtimestamp(clip_start_time_pts / 1000, tz=datetime.UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        end_time_iso = (
            datetime.datetime.fromtimestamp(clip_end_time_pts / 1000, tz=datetime.UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    # Build the VST API URL
    query_params = urllib.parse.urlencode(
        {
            "startTime": start_time_iso,
            "endTime": end_time_iso,
            "blocking": "true",
            "disableAudio": "true" if disable_audio else "false",
        }
    )
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/storage/file/{stream_id}/url?{query_params}"

    # Add overlay configuration for bounding boxes
    overlay_param = build_overlay_config(overlay_enabled, object_ids)
    if overlay_param:
        url += f"&configuration={overlay_param}"

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async for retry in create_retry_strategy(retries=3, exceptions=(aiohttp.ClientError, asyncio.TimeoutError)):
            with retry:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise VSTError(f"Failed to get video clip URL: HTTP {response.status}")
                    text = await response.text()
                    try:
                        result = json.loads(text)
                    except json.JSONDecodeError as e:
                        raise VSTError(f"Invalid JSON in VST response: {e}") from e
                    video_clip_url = result.get("videoUrl")
                    if not video_clip_url:
                        raise VSTError("No videoUrl in response")

    return str(video_clip_url)


@register_function(config_type=VSTVideoClipConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def vst_video_clip(config: VSTVideoClipConfig, _: Builder) -> AsyncGenerator[FunctionInfo]:
    async def _vst_video_clip(
        vst_video_clip_input: VSTVideoClipOffsetInput | VSTVideoClipISOInput,
    ) -> VSTVideoClipOutput:
        """Get a temporary VST video URL for `video_name` over an optional time range.

        Args:
            Note: start_time MUST be smaller than end_time

        Returns:
            VSTVideoClipOutput containing video URL and stream ID
        """
        stream_id = await get_stream_id(vst_video_clip_input.sensor_id, config.vst_internal_url)

        video_clip_url = await get_video_url(
            stream_id,
            vst_video_clip_input.start_time,
            vst_video_clip_input.end_time,
            config.vst_internal_url,
            overlay_enabled=config.overlay_config,
            object_ids=vst_video_clip_input.object_ids,
            disable_audio=config.disable_audio,
        )
        await validate_video_url(video_clip_url)
        # Replace internal URL with external URL for client access
        video_clip_url = f"{config.vst_external_url}{urllib.parse.urlparse(video_clip_url).path}"
        return VSTVideoClipOutput(video_url=video_clip_url, stream_id=stream_id)

    # Register the tool with the appropriate input schema based on time_format:
    #   - "iso": accepts ISO 8601 UTC timestamp strings (e.g. "2025-08-25T03:05:55Z").
    #     Use for RTSP live streams where events have real-world wall-clock times.
    #   - "offset": accepts floats representing seconds since start of stream (e.g. 30.0).
    #     Use for uploaded video files where only relative position matters.
    # This must match the time_format of any tool calling this one (e.g. video_understanding, critic_agent).
    #
    # NAT's _convert_input checks `input_type == input_schema` to decide whether to pass
    # the full Pydantic model or extract its first field. A Union annotation would mismatch.
    if config.time_format == "iso":

        async def _vst_video_clip_iso(vst_video_clip_input: VSTVideoClipISOInput) -> VSTVideoClipOutput:
            return await _vst_video_clip(vst_video_clip_input)

        input_desc = """
        \n\nInput:
        - sensor_id: Required. The name of the sensor or video file.
        - start_time: Optional. ISO 8601 UTC timestamp (e.g., '2025-08-25T03:05:55.752Z'), if not provided, the entire video will be returned.
        - end_time: Optional. ISO 8601 UTC timestamp (e.g., '2025-08-25T03:06:15.752Z'), if not provided, the entire video will be returned.
        """
        func_desc = vst_video_clip.__doc__ or ""
        yield FunctionInfo.create(
            single_fn=_vst_video_clip_iso,
            description=func_desc + input_desc,
            input_schema=VSTVideoClipISOInput,
            single_output_schema=VSTVideoClipOutput,
        )
    else:

        async def _vst_video_clip_offset(vst_video_clip_input: VSTVideoClipOffsetInput) -> VSTVideoClipOutput:
            return await _vst_video_clip(vst_video_clip_input)

        input_desc = """
        \n\nInput:
        - sensor_id: Required. The name of the sensor or video file.
        - start_time: Optional. Seconds since the beginning of the stream, if not provided, the entire video will be returned.
        - end_time: Optional. Seconds since the beginning of the stream, if not provided, the entire video will be returned.
        """
        func_desc = _vst_video_clip.__doc__ or ""
        yield FunctionInfo.create(
            single_fn=_vst_video_clip_offset,
            description=func_desc + input_desc,
            input_schema=VSTVideoClipOffsetInput,
            single_output_schema=VSTVideoClipOutput,
        )
