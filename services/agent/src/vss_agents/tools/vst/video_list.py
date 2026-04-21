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

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from vss_agents.tools.vst.timeline import get_timeline
from vss_agents.tools.vst.utils import get_name_to_stream_id_map

logger = logging.getLogger(__name__)


class VSTVideoListConfig(FunctionBaseConfig, name="vst.video_list"):
    """Configuration for the VST Video List tool."""

    vst_internal_url: str = Field(
        ...,
        description="The internal VST URL for API calls (e.g., http://${INTERNAL_IP}:30888)",
    )


class VSTVideoListInput(BaseModel):
    """Input for the VST Video List tool"""

    pass


class VSTVideoListOutput(BaseModel):
    """Output for the VST Video List tool."""

    video_list: list[dict[str, str | float]] = Field(
        ...,
        description="List of available video names and their durations",
    )


@register_function(config_type=VSTVideoListConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def _vst_video_list(config: VSTVideoListConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    async def _vst_video_list(vst_video_list_input: VSTVideoListInput) -> VSTVideoListOutput:  # noqa: ARG001
        """Get the list of available video names from VST."""
        name_to_stream_id = await get_name_to_stream_id_map(config.vst_internal_url)
        output: list[dict[str, str | float]] = []
        for name, stream_id in name_to_stream_id.items():
            start_timestamp, end_timestamp = await get_timeline(stream_id, config.vst_internal_url)
            duration = (
                datetime.datetime.fromisoformat(end_timestamp.replace("Z", "+00:00"))
                - datetime.datetime.fromisoformat(start_timestamp.replace("Z", "+00:00"))
            ).total_seconds()
            output.append({"name": name, "duration": duration})
        return VSTVideoListOutput(video_list=output)

    yield FunctionInfo.create(
        single_fn=_vst_video_list,
        description=_vst_video_list.__doc__,
        single_output_schema=VSTVideoListOutput,
        input_schema=VSTVideoListInput,
    )
