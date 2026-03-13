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

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from enum import Enum
import json
import logging
from typing import Literal

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from vss_agents.tools.vst.timeline import get_timeline
from vss_agents.tools.vst.utils import get_stream_id
from vss_agents.utils.time_convert import iso8601_to_datetime

logger = logging.getLogger(__name__)

CRITIC_AGENT_PROMPT = """
You are a helpful assistant that will evaluate a video against the original prompt
and evaluate whether the requested parameters are met.

user_prompt: {user_prompt}

Your task is to break down the user prompt into a list of parameters that are requested
and evaluate whether the video meets the requested parameter.

Example 1:
user_prompt: "Find the man wearing a blue shirt, dark pants, and carrying a backpack"

Return the output in the following format:
```json
{{
    "man": true,
    "blue shirt": true,
    "dark pants": true,
    "backpack": true
}}

Example 2:
user_prompt: "Find the woman picking up a box"

Return the output in the following format:
```json
{{
    "woman": true,
    "picking up a box": false
}}

Example 3:
user_prompt: "Find the running person in a green jacket"

Return the output in the following format:
```json
{{
    "person": true,
    "running": false,
    "green jacket": true
}}


```
"""


class CriticAgentConfig(FunctionBaseConfig, name="critic_agent"):
    """Config for the Critic Agent."""

    critic_prompt: str = Field(
        default=CRITIC_AGENT_PROMPT,
        description="The prompt that is used to evaluate the video against the user prompt.",
    )
    max_concurrent_verifications: int = Field(
        default=5,
        description="Maximum number of concurrent VLM calls",
        ge=1,
    )
    video_analysis_tool: FunctionRef | None = Field(
        default=None,
        description="Video analysis tool to use for video analysis.",
    )
    time_format: Literal["iso", "offset"] = Field(
        default="iso",
        description="Timestamp input format: 'iso' for ISO 8601 UTC strings (e.g. '2025-08-25T03:05:55Z'), "
        "'offset' for seconds since stream start. "
        "Must match across video_understanding, vst.video_clip, vst.snapshot, and critic_agent configs.",
    )


class VideoInfo(BaseModel):
    """Information about a video."""

    # Make this type hashable so it can be used as a key in a dictionary
    model_config = ConfigDict(frozen=True)
    sensor_id: str = Field(description="The sensor ID of the video.")
    start_timestamp: str = Field(
        description="The start timestamp in UTC ISO 8601 format (e.g., '2025-08-25T03:05:55.752Z')"
    )
    end_timestamp: str = Field(
        description="The end timestamp in UTC ISO 8601 format (e.g., '2025-08-25T03:06:15.752Z')"
    )


class CriticAgentInput(BaseModel):
    """Input for the Critic Agent."""

    query: str = Field(description="The user query that was used to generate the search results.")
    videos: list[VideoInfo] = Field(description="The list of video information to evaluate.")
    evaluation_count: int | None = Field(
        default=None,
        description="The number of videos to evaluate. If None, all videos will be evaluated.",
        ge=1,
    )


class CriticAgentResult(Enum):
    """Result for a single video evaluation."""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNVERIFIED = "unverified"


# Kind of a roundabout way to compose the output since `FunctionInfo.create` must return a BaseModel.
class VideoResult(BaseModel):
    """Result for a single video evaluation."""

    video_info: VideoInfo = Field(description="The URL of the video that was evaluated.")
    result: CriticAgentResult = Field(description="The result of the video evaluation.")
    criteria_met: dict[str, bool] | None = Field(
        default=None,
        description="A dictionary of the user prompt's criteria for each parameter and whether the video meets it or not.",
    )


class CriticAgentOutput(BaseModel):
    """Output for the Critic Agent."""

    video_results: list[VideoResult] = Field(description="The list of video results.")


def get_json_from_string(string: str) -> str:
    """Strip the JSON from the string."""
    if "```json" in string:
        return string.split("```json")[1].split("```")[0].strip()
    else:
        return string


def _convert_to_seconds(timestamp: str, video_start_dt: datetime) -> float:
    """Convert timestamp to seconds since video start timestamp."""
    timestamp_dt = iso8601_to_datetime(timestamp)
    return (timestamp_dt - video_start_dt).total_seconds()


@register_function(config_type=CriticAgentConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def critic_agent(config: CriticAgentConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    async def _execute_critic(
        critic_input: CriticAgentInput,
    ) -> CriticAgentOutput:
        """
        Critic Agent to critique the search results generated by the user query.

        Args:
            critic_input (CriticAgentInput): The input to the critic agent.

        Returns:
            CriticAgentOutput: A CriticAgentOutput containing a list of VideoResult objects, one for each video.
        """
        video_count = min(critic_input.evaluation_count or len(critic_input.videos), len(critic_input.videos))
        semaphore = asyncio.Semaphore(config.max_concurrent_verifications)
        results: CriticAgentOutput = CriticAgentOutput(video_results=[])

        async def evaluate_video(video: VideoInfo) -> VideoResult | None:
            if not config.video_analysis_tool:
                logger.warning(f"[Critic Agent] No video analysis tool configured, skipping video {video.sensor_id}")
                return None
            video_analysis_tool = await builder.get_function(config.video_analysis_tool)
            async with semaphore:
                formatted_prompt = config.critic_prompt.format(user_prompt=critic_input.query)
                logger.debug(f"Formatted prompt: {formatted_prompt}")

                try:
                    # The critic agent always receives ISO 8601 timestamps from its callers.
                    # When time_format is "iso", pass them through directly to the video analysis tool.
                    # When time_format is "offset", convert ISO timestamps to seconds-since-start
                    # because the video analysis tool expects float offsets (for uploaded video files).
                    if config.time_format == "iso":
                        video_analysis_input = {
                            "sensor_id": video.sensor_id,
                            "start_timestamp": video.start_timestamp,
                            "end_timestamp": video.end_timestamp,
                            "user_prompt": formatted_prompt,
                            "vlm_reasoning": True,
                        }
                    else:
                        stream_id = await get_stream_id(video.sensor_id)
                        start_iso, end_iso = await get_timeline(stream_id)
                        video_start_dt = iso8601_to_datetime(start_iso)
                        # Sometimes the end timestamp is after the video end timestamp, so we need to clip the end offset.
                        start_offset = _convert_to_seconds(video.start_timestamp, video_start_dt)
                        end_offset = _convert_to_seconds(video.end_timestamp, video_start_dt)
                        clip_end_offset = _convert_to_seconds(end_iso, video_start_dt)
                        if end_offset > clip_end_offset:
                            end_offset = clip_end_offset
                        video_analysis_input = {
                            "sensor_id": video.sensor_id,
                            "start_timestamp": start_offset,
                            "end_timestamp": end_offset,
                            "user_prompt": formatted_prompt,
                            "vlm_reasoning": True,
                        }
                    vlm_response = await video_analysis_tool.ainvoke(video_analysis_input)
                    logger.info(f"VLM response: {vlm_response}")
                except Exception as e:
                    # Failing one video analysis call is not a critical error, so we return None.
                    logger.error(f"Error calling video analysis tool: {e}")
                    return None

                try:
                    criteria_dict: dict[str, bool] = json.loads(get_json_from_string(vlm_response))
                    # For now, we assume the video fails if any of the parameters are not met
                    result = CriticAgentResult.CONFIRMED
                    for value in criteria_dict.values():
                        if not value:
                            result = CriticAgentResult.REJECTED
                            break
                    logger.debug(f"Video {video} criteria dict: {criteria_dict}")
                    return VideoResult(video_info=video, result=result, criteria_met=criteria_dict)
                except Exception as e:
                    # Failing one video analysis call is not a critical error, so we return None.
                    logger.error(f"Error parsing VLM response: {e}")
                    return VideoResult(video_info=video, result=CriticAgentResult.UNVERIFIED, criteria_met={})

        tasks = [evaluate_video(video) for video in critic_input.videos[:video_count] if video.sensor_id]
        video_results = await asyncio.gather(*tasks)
        results.video_results = [result for result in video_results if result is not None]
        logger.info(f"Critic agent results: {results.model_dump_json(indent=2)}")
        return results

    yield FunctionInfo.create(
        single_fn=_execute_critic,
        description=_execute_critic.__doc__,
        input_schema=CriticAgentInput,
        single_output_schema=CriticAgentOutput,
    )
