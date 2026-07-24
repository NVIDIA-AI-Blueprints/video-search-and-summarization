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
import logging
import os
import shutil
from typing import Any
import uuid

import httpx
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator

from vss_agents.utils.file_mapping import resolve_video_file
from vss_agents.utils.time_measure import TimeMeasure

logger = logging.getLogger(__name__)

VLM_PROMPT = """
You are an expert at video understanding and description. Your task is to capture, in as much detail as possible, the events from the video, which are related to the user's query.
Be sure to capture as much description as possible about the environment, people, objects, and actions performed in the video.
For example, describe the attire of the people, the make and model of the vehicles, the color of the objects, etc.
Those images are samples from the video with fps {fps} frames per second.
User's query: {user_prompt}.
Video start timestamp: {start_timestamp}.
You must begin each caption with a timestamp in pts format, and add the start_timestamp to the timestamp from each caption.
The timestamp should be rounded to 2 decimal places.
for example:
start_timestamp: 10.0
[10.45] This is a caption.
[11.24] This is another caption.
should be
[20.45] This is a caption.
[21.24] This is another caption.
"""


class VideoCaptionConfig(FunctionBaseConfig, name="video_caption"):
    """Configuration for the Video Caption tool."""

    llm_name: LLMRef = Field(
        ...,
        description="The name of the LLM to use for the image caption tool.",
    )

    prompt: str = Field(
        VLM_PROMPT,
        description="The prompt that is used to query the VLM to understand the video",
    )
    max_retries: int = Field(
        3,
        description="The maximum number of retries to attempt when the VLM returns an error message.",
    )
    max_frames_per_request: int = Field(
        10,
        description="The maximum number of frames to request from the VLM at once. gpt4o: 10",
    )
    use_vss: bool = Field(
        True,
        description="Whether to use VLM for video caption. If False, it will directly use the VLM(llm_name) to caption the video.",
    )
    vss_summarize_tool: FunctionRef = Field(
        "vss_summarize",
        description="The name of the VSS summarize tool to use for video caption. If use_vss is True, it will use the VSS backend to caption the video.",
    )
    vss_file_upload_tool: FunctionRef = Field(
        "vss_upload",
        description="The name of the VSS file upload tool to use for uploading the video file to VSS backend.",
    )
    vss_backend_url: str = Field(
        "http://localhost:31000",
        description="The URL of the VSS backend.",
    )
    vst_download_tool: FunctionRef = Field(
        default="vst_download", description="The VST tool to use for downloading video clips from VST backend"
    )


class VideoCaptionInput(BaseModel):
    """Input for the Video Caption tool"""

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
    fps: float = Field(
        1.0,
        description="The fps to sample the video. Usually VLM works the best with fps around 1 fps",
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


# possible error messages from VLM, denied to help
error_messages = [
    "I'm sorry, I can't help with that",
    "I'm unable to",
]


async def call_vlm_partition(
    llm: Any,
    base64_frames: list[str],
    template_prompt: str,
    user_prompt: str,
    start_timestamp: float,
    fps: float,
    max_retries: int,
) -> tuple[float, str]:
    text_prompt = template_prompt.format(
        fps=fps,
        user_prompt=user_prompt,
        start_timestamp=start_timestamp,
    )
    messages = [
        HumanMessage(
            content=[
                {"type": "text", "text": text_prompt},
                *[
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame}"}}
                    for frame in base64_frames
                ],
            ]
        )
    ]
    caption_str: str = ""
    for retry_idx in range(max_retries):
        captions = await llm.ainvoke(messages)
        caption_str = str(captions.content)

        if any(caption_str.startswith(error_msg) for error_msg in error_messages) and len(caption_str.strip()) < 80:
            logger.warning("VLM is unable to help %s, retry %d out of %d", caption_str, retry_idx, max_retries)
            new_text_prompt = await llm.ainvoke(
                [
                    SystemMessage(
                        content="The following is a prompt that is used to caption a video, but the VLM denied to help and returned an error message. Please modify the prompt to make it more specific and easier for the VLM to understand. Only return the modified prompt, do not include any other text."
                    ),
                    HumanMessage(content=[{"type": "text", "text": "original prompt: " + text_prompt}]),
                    HumanMessage(content=[{"type": "text", "text": "VLM error message: " + caption_str}]),
                ]
            )
            text_prompt = new_text_prompt.content
            continue
        else:
            break

    return start_timestamp, caption_str


@register_function(config_type=VideoCaptionConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def video_caption(config: VideoCaptionConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    # Get VST download tool if available
    vst_download_tool = None
    try:
        vst_download_tool = await builder.get_tool("vst_download", wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        logger.info("VST download tool available")
    except Exception:
        logger.info("VST download tool not available")

    if config.use_vss:
        vss_summarize_tool = await builder.get_tool(config.vss_summarize_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        vss_file_upload_tool = await builder.get_tool(
            config.vss_file_upload_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN
        )

        async def _video_caption_vss(video_caption_input: VideoCaptionInput) -> str:
            """
            This tool uses the VLM(through VSS backend) to understand a video clip from start_timestamp to end_timestamp.
            video clip is sampled at fps frames per second.

            Input:
                video_caption_input: VideoCaptionInput

            Returns:
                str: The caption for the video.
            """

            # Resolve filename to actual file path and determine cleanup needs
            resolved_file_path, needs_cleanup = await resolve_video_file(
                video_caption_input.filename,
                video_caption_input.start_timestamp,
                video_caption_input.end_timestamp,
                vst_download_tool,
            )

            logger.info(f"Resolved file path: {resolved_file_path}")

            temp_dir_to_cleanup = None
            try:
                # Handle different storage types

                # For VST file upload downloaded clip to VSS
                vss_upload_output = await vss_file_upload_tool.ainvoke(
                    input={
                        "file_path": resolved_file_path,
                        "start_timestamp": video_caption_input.start_timestamp,
                        "end_timestamp": video_caption_input.end_timestamp,
                    },
                )
                file_id = vss_upload_output.file_id
                logger.info(f"Uploaded VST clip to VSS: {file_id}")

                # Mark temp directory for cleanup
                if needs_cleanup:
                    temp_dir_to_cleanup = os.path.dirname(resolved_file_path)

                # summarize the video clip
                vss_summarize_output = await vss_summarize_tool.ainvoke(
                    input={
                        "id": uuid.UUID(file_id),  # Convert string to UUID
                        "prompt": video_caption_input.user_prompt,
                        "video_duration": video_caption_input.end_timestamp - video_caption_input.start_timestamp,
                        "caption_summarization_prompt": "Copy all captions together with timestamps, no other text.",
                        "summary_aggregation_prompt": f"Copy all captions to the output. Add start timestamp to the timestamp from each caption. start_timestamp is {video_caption_input.start_timestamp}",
                    },
                )

                # delete from VSS if we uploaded it
                if not (resolved_file_path.startswith("vss_") or resolved_file_path.startswith("file_")):
                    try:
                        async with httpx.AsyncClient() as client:
                            await client.delete(f"{config.vss_backend_url}/files/{file_id}")
                        logger.info(f"Cleaned up VSS upload: {file_id}")
                    except Exception as e:
                        logger.warning(f"Failed to clean up VSS file: {e}")

                ret_str = (
                    "Video captions for "
                    + video_caption_input.filename
                    + " from "
                    + str(video_caption_input.start_timestamp)
                    + " to "
                    + str(video_caption_input.end_timestamp)
                    + ":\\n\\n"
                    + str(vss_summarize_output.summary)
                )
                return str(ret_str)

            finally:
                # Cleanup temporary VST download directory if needed
                if temp_dir_to_cleanup and os.path.exists(temp_dir_to_cleanup):
                    logger.info(f"Cleaning up temporary directory: {temp_dir_to_cleanup}")
                    shutil.rmtree(temp_dir_to_cleanup, ignore_errors=True)

        yield FunctionInfo.create(
            single_fn=_video_caption_vss,
            description=_video_caption_vss.__doc__,
            input_schema=VideoCaptionInput,
            single_output_schema=str,
        )
    else:
        logger.info("Using VLM for video caption")
        from vss_agents.utils.frame_select import frame_select

        llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

        loop = asyncio.get_event_loop()

        async def _video_caption(video_caption_input: VideoCaptionInput) -> str:
            """
            This tool uses the VLM to understand a video clip from start_timestamp to end_timestamp.
            video clip is sampled at fps frames per second.

            IMPORTANT:
                - A good video clip should be 5 - 300 seconds long.
                - This tool is slow and expensive, only use it when necessary.
                - In the prompt, don't add timestamp, instead, use the start_timestamp and end_timestamp to indicate the time range of the video clip.
                - In the prompt, don't ask to **identify** an individual or any PII type of query, instead ask to create general descriptions about the people(attire, gender, location, etc), objects, and actions.
            Input:
                video_caption_input: VideoCaptionInput

            Returns:
                str: The caption for the video.
            """

            # Resolve filename to actual file path and determine cleanup needs
            resolved_file_path, needs_cleanup = await resolve_video_file(
                video_caption_input.filename,
                video_caption_input.start_timestamp,
                video_caption_input.end_timestamp,
                vst_download_tool,
            )

            temp_dir_to_cleanup = None
            try:
                # Mark temp directory for cleanup if needed
                if needs_cleanup:
                    temp_dir_to_cleanup = os.path.dirname(resolved_file_path)

                step_size = 1 / video_caption_input.fps
                with TimeMeasure(
                    f"frame_select-{resolved_file_path}, {video_caption_input.start_timestamp}, {
                        video_caption_input.end_timestamp
                    }, {video_caption_input.fps}"
                ):
                    base64_frames = await loop.run_in_executor(
                        None,
                        frame_select,
                        resolved_file_path,
                        video_caption_input.start_timestamp,
                        video_caption_input.end_timestamp,
                        step_size,
                    )
                tasks = []
                for i in range(0, len(base64_frames), config.max_frames_per_request):
                    start_timestamp = video_caption_input.start_timestamp + i * step_size
                    tasks.append(
                        call_vlm_partition(
                            llm,
                            base64_frames[i : i + config.max_frames_per_request],
                            config.prompt,
                            video_caption_input.user_prompt,
                            start_timestamp,
                            video_caption_input.fps,
                            config.max_retries,
                        )
                    )
                results = await asyncio.gather(*tasks)
                results.sort(key=lambda x: x[0])

                ret_str = (
                    "Video captions for "
                    + video_caption_input.filename
                    + " from "
                    + str(video_caption_input.start_timestamp)
                    + " to "
                    + str(video_caption_input.end_timestamp)
                    + ":\n\n"
                    + "\n".join([result[1] for result in results])
                )

                return ret_str

            except Exception as e:
                logger.error(f"Error captioning video {video_caption_input.filename}: {e}")
                raise e

            finally:
                # Cleanup temporary VST download directory if needed
                if temp_dir_to_cleanup and os.path.exists(temp_dir_to_cleanup):
                    logger.info(f"Cleaning up temporary directory: {temp_dir_to_cleanup}")
                    shutil.rmtree(temp_dir_to_cleanup, ignore_errors=True)

        yield FunctionInfo.create(
            single_fn=_video_caption,
            description=_video_caption.__doc__,
            input_schema=VideoCaptionInput,
            single_output_schema=str,
        )
