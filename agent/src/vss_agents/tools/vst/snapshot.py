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

"""VST Snapshot tool - snapshot/picture URL tool with bounding box overlay support.

Supports two timestamp formats controlled by config:
- 'offset' format: start_time is a float (seconds since beginning of stream)
- 'iso' format: start_time is an ISO 8601 UTC timestamp string
"""

from collections.abc import AsyncGenerator
from datetime import datetime
from datetime import timedelta
import json
import logging
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

from vss_agents.tools.vst.timeline import get_timeline
from vss_agents.tools.vst.utils import VSTError
from vss_agents.tools.vst.utils import build_overlay_config
from vss_agents.tools.vst.utils import get_stream_id
from vss_agents.utils.retry import create_retry_strategy

logger = logging.getLogger(__name__)


def build_screenshot_url(vst_external_url: str, stream_id: str, timestamp: str) -> str:
    """Build an external screenshot URL for client-facing URLs directly, without any validation.

    Args:
        vst_external_url: External VST URL for client-facing URLs
        stream_id: The stream ID
        timestamp: The timestamp for the screenshot

    Returns:
        External screenshot URL string
    """
    vst_external_url = vst_external_url.rstrip("/")
    return f"{vst_external_url}/vst/api/v1/replay/stream/{stream_id}/picture?startTime={timestamp}"


class VSTSnapshotConfig(FunctionBaseConfig, name="vst.snapshot"):
    """Configuration for the VST Snapshot tool."""

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


class VSTSnapshotOffsetInput(BaseModel):
    """Input for the VST Snapshot tool (offset mode).

    start_time is a float representing seconds since the beginning of the stream.
    """

    sensor_id: str = Field(
        ...,
        description="The name of the video file uploaded or the stream ID from VST",
        min_length=1,
    )
    start_time: float = Field(
        ...,
        description="Seconds since the beginning of the stream (e.g., 30.0 for 30 seconds in)",
    )


class VSTSnapshotISOInput(BaseModel):
    """Input for the VST Snapshot tool (ISO timestamp mode).

    start_time is an ISO 8601 UTC timestamp string.
    """

    sensor_id: str = Field(
        ...,
        description="The name of the video file uploaded or the stream ID from VST",
        min_length=1,
    )
    start_time: str = Field(
        ...,
        description="ISO 8601 UTC timestamp (e.g., '2025-08-25T03:05:55.752Z')",
        min_length=1,
    )


# Union type for backward compatibility in internal APIs
VSTSnapshotInput = VSTSnapshotOffsetInput | VSTSnapshotISOInput


class VSTSnapshotOutput(BaseModel):
    """Output for the VST Snapshot tool"""

    image_url: str = Field(
        ...,
        description="Direct URL to access the snapshot",
    )
    stream_id: str = Field(
        ...,
        description="The stream ID that is mapped from the sensor ID",
    )


async def get_snapshot_url(
    stream_id: str,
    start_time: float | str,
    vst_internal_url: str,
    overlay_enabled: bool = False,
) -> str:
    """Get the snapshot URL for a given stream ID.

    Args:
        stream_id: The VST stream ID.
        start_time: Seconds offset (float) or ISO 8601 timestamp (str).
        vst_internal_url: Internal VST URL.
        overlay_enabled: Whether to add bounding box overlay.

    Returns:
        The snapshot image URL from VST.
    """
    if isinstance(start_time, str):
        # ISO 8601 timestamp - use directly
        timestamp_iso = start_time
    else:
        # Seconds offset - compute from timeline
        timeline_start, timeline_end = await get_timeline(stream_id, vst_internal_url)
        picture_time = datetime.fromisoformat(timeline_start) + timedelta(seconds=start_time)
        if picture_time < datetime.fromisoformat(timeline_start) or picture_time > datetime.fromisoformat(timeline_end):
            raise ValueError(f"Picture time is out of the video timeline {timeline_start} to {timeline_end}")
        timestamp_iso = picture_time.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    query_params = urllib.parse.urlencode({"startTime": timestamp_iso})
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/replay/stream/{stream_id}/picture/url?{query_params}"

    # Add overlay configuration for bounding boxes
    overlay_param = build_overlay_config(overlay_enabled)
    if overlay_param:
        url += f"&overlay={overlay_param}"

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async for attempt in create_retry_strategy(retries=3):
            with attempt:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise VSTError(f"Failed to get snapshot URL: HTTP {response.status}")
                    text = await response.text()
                    image_url = json.loads(text).get("imageUrl")
                    if not image_url:
                        raise VSTError("Failed to get snapshot URL: no imageUrl in response")

    return str(image_url)


@register_function(config_type=VSTSnapshotConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def vst_snapshot(config: VSTSnapshotConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    async def _vst_snapshot(vst_snapshot_input: VSTSnapshotOffsetInput | VSTSnapshotISOInput) -> VSTSnapshotOutput:
        """Get a temporary VST picture URL for `sensor_id` at `start_time`.

        Returns:
            VSTSnapshotOutput containing image URL and stream ID
        """
        stream_id = await get_stream_id(vst_snapshot_input.sensor_id, config.vst_internal_url)

        image_url = await get_snapshot_url(
            stream_id,
            vst_snapshot_input.start_time,
            config.vst_internal_url,
            overlay_enabled=config.overlay_config,
        )

        # Replace internal URL with external URL for client access
        image_url = f"{config.vst_external_url}{urllib.parse.urlparse(image_url).path}"

        return VSTSnapshotOutput(image_url=image_url, stream_id=stream_id)

    # Register the tool with the appropriate input schema based on time_format:
    #   - "iso": accepts ISO 8601 UTC timestamp strings (e.g. "2025-08-25T03:05:55Z").
    #     Use for RTSP live streams where events have real-world wall-clock times.
    #   - "offset": accepts floats representing seconds since start of stream (e.g. 30.0).
    #     Use for uploaded video files where only relative position matters.
    # This must match the time_format of any tool calling this one (e.g. video_understanding).
    #
    # NAT's _convert_input checks `input_type == input_schema` to decide whether to pass
    # the full Pydantic model or extract its first field. A Union annotation would mismatch.
    if config.time_format == "iso":

        async def _vst_snapshot_iso(vst_snapshot_input: VSTSnapshotISOInput) -> VSTSnapshotOutput:
            return await _vst_snapshot(vst_snapshot_input)

        input_desc = """
        \n\nInput:
        - sensor_id: Required. The name of the sensor or video file.
        - start_time: Required. ISO 8601 UTC timestamp (e.g., '2025-08-25T03:05:55.752Z').
        """
        func_desc = _vst_snapshot.__doc__ or ""

        yield FunctionInfo.create(
            single_fn=_vst_snapshot_iso,
            description=func_desc + input_desc,
            input_schema=VSTSnapshotISOInput,
            single_output_schema=VSTSnapshotOutput,
        )
    else:

        async def _vst_snapshot_offset(vst_snapshot_input: VSTSnapshotOffsetInput) -> VSTSnapshotOutput:
            return await _vst_snapshot(vst_snapshot_input)

        input_desc = """
        \n\nInput:
        - sensor_id: Required. The name of the sensor or video file.
        - start_time: Required. Seconds since the beginning of the stream (e.g., 30.0 for 30 seconds from the start of the video).
        """
        func_desc = _vst_snapshot.__doc__ or ""
        yield FunctionInfo.create(
            single_fn=_vst_snapshot_offset,
            description=func_desc + input_desc,
            input_schema=VSTSnapshotOffsetInput,
            single_output_schema=VSTSnapshotOutput,
        )
