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

from collections.abc import AsyncGenerator
import json
import logging
import os

import aiohttp
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from vss_agents.tools.vst.utils import VSTError
from vss_agents.tools.vst.utils import get_stream_id
from vss_agents.utils.retry import create_retry_strategy
from vss_agents.utils.time_convert import iso8601_to_datetime

logger = logging.getLogger(__name__)


class VSTTimelineConfig(FunctionBaseConfig, name="vst.timeline"):
    """Configuration for the VST Timeline tool."""

    vst_internal_url: str = Field(
        ...,
        description="The internal VST URL for API calls (e.g., http://${INTERNAL_IP}:30888)",
    )


class VSTTimelineInput(BaseModel):
    """Input for the VST Timeline tool"""

    sensor_id: str = Field(
        ...,
        description="The name of the sensor/video (e.g., 'warehouse_01') OR the stream ID",
    )


class VSTTimelineOutput(BaseModel):
    """Output for the VST Timeline tool"""

    start_timestamp: str = Field(
        ...,
        description="The start timestamp of the video",
    )
    end_timestamp: str = Field(
        ...,
        description="The end timestamp of the video",
    )


async def get_timeline(stream_id: str, vst_internal_url: str | None = None) -> tuple[str, str]:
    """
    Get the start and end timestamps for a video from VST API.

    This function:
    1. Calls VST streams API to find the stream ID for the given sensor name
    2. Calls VST timelines API to get the timeline information
    3. Extracts and returns the endTime converted to ISO format

    Args:
        stream_id: The stream ID of the sensor/video, note it also works with sensor name(sensor id), internally it will be converted to stream id.
        vst_internal_url: Internal VST URL for API calls (defaults to VST_INTERNAL_URL env var or http://localhost:30888)

    Returns:
        ISO timestamp string (e.g., "2025-01-01T00:10:28.000Z")

    Raises:
        RuntimeError: If the video is not found or API calls fail
    """
    if vst_internal_url is None:
        vst_internal_url = os.getenv("VST_INTERNAL_URL", "http://localhost:30888")

    # Remove /vst suffix if present
    if vst_internal_url.endswith("/vst"):
        vst_internal_url = vst_internal_url[:-4]
    timelines_url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/storage/timelines"

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
                            logger.info("probabaly input is sensor id or video name, trying to get stream id")
                            stream_id = await get_stream_id(stream_id, vst_internal_url)
                            timeline_list = timelines_data.get(stream_id, [])
                            if not timeline_list:
                                raise VSTError(f"No timeline found for stream {stream_id}")
                        logger.info("Timeline for stream %s: %s", stream_id, timeline_list)
                        start, end = timeline_list[0].get("startTime"), timeline_list[0].get("endTime")
                        # check duration if too short, throw error
                        start_dt = iso8601_to_datetime(start)
                        end_dt = iso8601_to_datetime(end)
                        duration = end_dt - start_dt
                        if duration.total_seconds() < 1:
                            raise VSTError(f"Timeline duration is too short for stream {stream_id}")
                        return start, end
                except Exception as e:
                    raise VSTError(f"Error getting timeline for stream {stream_id}: {e}") from e
    return "", ""  # unreachable, but satisfies mypy


@register_function(config_type=VSTTimelineConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def vst_timeline(config: VSTTimelineConfig, _: Builder) -> AsyncGenerator[FunctionInfo]:
    async def _vst_timeline(vst_timeline_input: VSTTimelineInput) -> VSTTimelineOutput:
        """Get the start and end timestamps for a video from VST."""

        stream_id = await get_stream_id(vst_timeline_input.sensor_id, config.vst_internal_url)
        start_timestamp, end_timestamp = await get_timeline(stream_id, config.vst_internal_url)
        return VSTTimelineOutput(start_timestamp=start_timestamp, end_timestamp=end_timestamp)

    yield FunctionInfo.create(
        single_fn=_vst_timeline,
        description=_vst_timeline.__doc__,
        input_schema=VSTTimelineInput,
        single_output_schema=VSTTimelineOutput,
    )
