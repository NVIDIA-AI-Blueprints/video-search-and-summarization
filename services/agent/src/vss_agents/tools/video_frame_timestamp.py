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

import base64
from collections.abc import AsyncGenerator
from datetime import datetime
import logging

import cv2
from langchain_core.prompts import ChatPromptTemplate
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from vss_agents.prompt import VIDEO_FRAME_TIMESTAMP_PROMPT

logger = logging.getLogger(__name__)


class VideoFrameTimestampConfig(FunctionBaseConfig, name="video_frame_timestamp"):
    """Configuration for the Video Frame Timestamp tool."""

    llm_name: str = Field(
        "openai_llm",
        description="The name of the LLM to use.",
    )
    prompt: str = Field(
        VIDEO_FRAME_TIMESTAMP_PROMPT,
        description="Prompt for video frame timestamp",
    )


class VideoFrameTimestampInput(BaseModel):
    """Input for the Video Frame Timestamp tool"""

    asset_file_path: str = Field(
        ...,
        description="The path to the asset to summarize",
    )
    frame_offset_seconds: float = Field(
        ...,
        description="The offset in seconds from the start of the video to get the timestamp",
    )


@register_function(config_type=VideoFrameTimestampConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def video_frame_timestamp(config: VideoFrameTimestampConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    async def _video_frame_timestamp(video_frame_timestamp_input: VideoFrameTimestampInput) -> datetime:
        """
        Given an offset in seconds from the start of the video, return the timestamp of the video frame.
        Using a VLM to extract it from the image.

        Returns:
            str: The timestamp of the video frame.
        """
        # extract the frame from the video given the offset
        video_capture = cv2.VideoCapture(video_frame_timestamp_input.asset_file_path)
        video_capture.set(cv2.CAP_PROP_POS_MSEC, video_frame_timestamp_input.frame_offset_seconds * 1000)
        _, frame = video_capture.read()
        video_capture.release()
        _, buffer = cv2.imencode(".jpg", frame)
        base64_frame = base64.b64encode(buffer.tobytes()).decode("utf-8")
        llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        prompt = ChatPromptTemplate(
            [
                {
                    "role": "system",
                    "content": config.prompt,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_frame}", "detail": "auto"},
                        },
                    ],
                },
            ]
        )
        chain = prompt | llm
        result = await chain.ainvoke({"base64_frame": base64_frame})
        # 2024-05-30T01:41:25.000Z
        return datetime.strptime(result.content, "%Y-%m-%dT%H:%M:%S.%fZ")

    yield FunctionInfo.create(
        single_fn=_video_frame_timestamp,
        description=_video_frame_timestamp.__doc__,
        input_schema=VideoFrameTimestampInput,
        single_output_schema=datetime,
    )
