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
import logging

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator

logger = logging.getLogger(__name__)


class VideoDetailedCaptionConfig(FunctionBaseConfig, name="video_detailed_caption"):
    """Configuration for the Video Detailed Caption tool."""

    detailed_fps: float = Field(
        2.0,
        description="The fixed fps to sample the video when detailed captioning short videos.",
    )
    max_video_duration: float = Field(
        60,
        description="The maximum duration of the video for captioning in seconds. If the video duration is longer than this value, a message will be returned to agent to caption a shorter video or use skimming.",
    )


class VideoDetailedCaptionInput(BaseModel):
    """Input for the Video Detailed Caption tool"""

    filename: str = Field(
        ...,
        description="The filename of the video to caption (e.g., 'camera1.mp4').",
    )
    start_timestamp: float = Field(
        ...,
        description="The start timestamp in pts of the video to understand",
    )
    end_timestamp: float = Field(
        ...,
        description="The end timestamp in pts of the video to understand",
    )
    user_prompt: str = Field(
        ...,
        description="The prompt that is used to query the VLM to understand the video, mention all search entities in the prompt that is related to the user's query.",
    )
    video_duration: float = Field(
        ...,
        description="The duration of the video in seconds",
    )
    model_config = {
        "extra": "forbid",
    }

    @model_validator(mode="before")
    @classmethod
    def validate_end_timestamp(cls, info: dict) -> dict:
        if info["video_duration"] <= 0:
            raise ValueError(f"Video duration must be positive, got {info['video_duration']}")
        if info["end_timestamp"] is None or info["end_timestamp"] > info["video_duration"]:
            # Subtract small epsilon to avoid MoviePy precision issues when end_timestamp equals video_duration
            info["end_timestamp"] = info["video_duration"] - 0.01
        return info


@register_function(config_type=VideoDetailedCaptionConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def video_detailed_caption(config: VideoDetailedCaptionConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    async def _video_detailed_caption(video_detailed_caption_input: VideoDetailedCaptionInput) -> str:
        """
        This tool uses the VLM to understand a shorter video clip in detail from start_timestamp to end_timestamp.
        video clip is sampled at a higher fps - frames per second.

        IMPORTANT:
            - This tool is slow and expensive, only use it when necessary.
            - In the prompt, don't add timestamp, instead, use the start_timestamp and end_timestamp to indicate the time range of the video clip.
            - In the prompt, don't ask to **identify** an individual or any PII type of query, instead ask to create general descriptions about the people(attire, gender, location, etc), objects, and actions.
        Input:
            video_detailed_caption_input: VideoDetailedCaptionInput

        Returns:
            str: The caption for the video.
        """

        captioning_duration = video_detailed_caption_input.end_timestamp - video_detailed_caption_input.start_timestamp
        if captioning_duration > config.max_video_duration:
            return (
                "Video duration is too long for detailed captioning, please caption a shorter video of less than "
                + str(config.max_video_duration)
                + " seconds or use video_skim_caption tool."
            )

        # Create a VideoCaptionInput object and call video caption tool
        video_caption_input = {
            "filename": video_detailed_caption_input.filename,
            "start_timestamp": video_detailed_caption_input.start_timestamp,
            "end_timestamp": video_detailed_caption_input.end_timestamp,
            "user_prompt": video_detailed_caption_input.user_prompt,
            "fps": config.detailed_fps,
            "video_duration": video_detailed_caption_input.video_duration,
        }

        # Call video caption tool
        video_caption_tool = await builder.get_tool("video_caption", wrapper_type=LLMFrameworkEnum.LANGCHAIN)

        try:
            ret_str: str = await video_caption_tool.ainvoke(video_caption_input)
        except Exception as e:
            logger.error(f"Error calling video_caption_tool: {e}")
            logger.error(f"Error type: {type(e)}")
            raise e

        return str(ret_str)

    yield FunctionInfo.create(
        single_fn=_video_detailed_caption,
        description=_video_detailed_caption.__doc__,
        input_schema=VideoDetailedCaptionInput,
        single_output_schema=str,
    )
