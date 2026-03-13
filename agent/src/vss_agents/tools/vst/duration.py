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
import datetime
import logging

from vss_agents.tools.vst.timeline import get_timeline
from vss_agents.tools.vst.utils import get_stream_id

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class VSTDurationConfig(FunctionBaseConfig, name="vst.duration"):
    """Configuration for the VST Duration tool."""

    vst_internal_url: str = Field(
        ...,
        description="The internal VST URL for API calls (e.g., http://${INTERNAL_IP}:30888)",
    )


class VSTDurationInput(BaseModel):
    """Input for the VST Video URL tool"""

    sensor_id: str = Field(
        ...,
        description="The name or the stream ID of the video file uploaded",
        min_length=1,
    )


class VSTDurationOutput(BaseModel):
    """Output for the VST Duration tool"""

    duration: float = Field(
        ...,
        description="The duration of the video in seconds",
    )


@register_function(config_type=VSTDurationConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def vst_duration(config: VSTDurationConfig, _: Builder) -> AsyncGenerator[FunctionInfo]:
    async def _vst_duration(vst_duration_input: VSTDurationInput) -> VSTDurationOutput:
        """Get the duration of the video for `video_name`.

        Args:
            vst_duration_input: VSTDurationInput containing sensor_id

        Returns:
            VSTDurationOutput containing duration of the video
        """
        stream_id = await get_stream_id(vst_duration_input.sensor_id, config.vst_internal_url)
        start_timestamp, end_timestamp = await get_timeline(stream_id, config.vst_internal_url)
        duration = (
            datetime.datetime.fromisoformat(end_timestamp.replace("Z", "+00:00"))
            - datetime.datetime.fromisoformat(start_timestamp.replace("Z", "+00:00"))
        ).total_seconds()
        return VSTDurationOutput(duration=duration)

    yield FunctionInfo.create(
        single_fn=_vst_duration,
        description=_vst_duration.__doc__,
        input_schema=VSTDurationInput,
        single_output_schema=VSTDurationOutput,
    )
