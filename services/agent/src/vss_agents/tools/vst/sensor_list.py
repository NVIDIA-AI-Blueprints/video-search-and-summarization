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

"""VST Sensor List tool - Direct API access to list available sensors."""

from collections.abc import AsyncGenerator
import logging

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from vss_agents.tools.vst.utils import get_name_to_stream_id_map

logger = logging.getLogger(__name__)


class VSTSensorListConfig(FunctionBaseConfig, name="vst.sensor_list"):
    """Configuration for the VST Sensor List tool."""

    vst_internal_url: str = Field(
        ...,
        description="The internal VST URL for making API requests (e.g., http://${INTERNAL_IP}:30888)",
    )


class VSTSensorListInput(BaseModel):
    """Input for the VST Sensor List tool (no parameters needed)."""

    pass


class VSTSensorListOutput(BaseModel):
    """Output for the VST Sensor List tool."""

    sensor_names: list[str] = Field(
        ...,
        description="List of available sensor names",
    )


@register_function(config_type=VSTSensorListConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def vst_sensor_list(config: VSTSensorListConfig, _: Builder) -> AsyncGenerator[FunctionInfo]:
    """VST Sensor List tool that returns available sensor names using direct VST API."""

    async def _vst_sensor_list(input_data: VSTSensorListInput) -> VSTSensorListOutput:  # noqa: ARG001
        """
        Get a list of available sensor names from VST.

        Returns:
            VSTSensorListOutput containing list of sensor names
        """
        logger.info("Fetching sensor list from VST")

        name_to_stream_id = await get_name_to_stream_id_map(config.vst_internal_url)
        sensor_names = sorted(name_to_stream_id.keys())

        logger.info(f"Found {len(sensor_names)} sensors: {sensor_names}")

        return VSTSensorListOutput(sensor_names=sensor_names)

    yield FunctionInfo.create(
        single_fn=_vst_sensor_list,
        description=_vst_sensor_list.__doc__,
        input_schema=VSTSensorListInput,
        single_output_schema=VSTSensorListOutput,
    )
