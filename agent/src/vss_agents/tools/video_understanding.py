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
from datetime import timedelta
import logging
import tempfile
from typing import Any
from typing import Literal

import aiohttp
import boto3
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.prompts import MessagesPlaceholder
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from openai import APIConnectionError
from openai import APITimeoutError
from openai import InternalServerError
from openai import RateLimitError
from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator

from vss_agents.tools.vst.timeline import get_timeline
from vss_agents.tools.vst.utils import get_stream_id
from vss_agents.utils.frame_select import frame_select
from vss_agents.utils.reasoning_parsing import parse_content_blocks
from vss_agents.utils.retry import create_retry_strategy
from vss_agents.utils.url_translation import translate_url

logger = logging.getLogger(__name__)

# Transient errors worth retrying for VLM calls.
# Excludes client errors (4xx) like BadRequestError which are not retryable.
_VLM_RETRYABLE_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,  # 5xx
    RateLimitError,  # 429
    aiohttp.ClientConnectorError,
    aiohttp.ConnectionTimeoutError,
    aiohttp.ServerTimeoutError,
    aiohttp.ServerDisconnectedError,
)


def _parse_thinking_from_content(content: str) -> tuple[str | None, str]:
    """
    Parse thinking content from VLM responses that use <think></think> and <answer></answer> tags.

    Args:
        content: The VLM response content

    Returns:
        tuple[str | None, str]: (thinking_content, answer_content)
    """
    if not content:
        return None, content

    # Check for <think></think> tags
    if "<think>" in content and "</think>" in content:
        think_start = content.find("<think>")
        think_end = content.find("</think>")

        if think_start != -1 and think_end != -1 and think_start < think_end:
            thinking = content[think_start + len("<think>") : think_end].strip()
            # Extract answer part after </think>
            after_think = content[think_end + len("</think>") :].strip()

            # Check if there's an <answer> tag
            if "<answer>" in after_think and "</answer>" in after_think:
                answer_start = after_think.find("<answer>")
                answer_end = after_think.find("</answer>")
                answer = after_think[answer_start + len("<answer>") : answer_end].strip()
            else:
                # No <answer> tag, use everything after </think>
                answer = after_think

            return thinking, answer

    # No thinking tags found, return original content
    return None, content


class VideoUnderstandingConfig(FunctionBaseConfig, name="video_understanding"):
    """Configuration for the Video Understanding tool."""

    vlm_name: LLMRef = Field(
        ...,
        description="The name of the LLM to use for the image caption tool.",
    )
    minio_url: str = Field(
        "http://localhost:9000",
        description="The endpoint URL of the MinIO server",
    )
    access_key: str = Field(
        "minioadmin",
        description="The access key of the S3 bucket",
    )
    secret_key: str = Field(
        "minioadmin",
        description="The secret key of the S3 bucket",
    )
    bucket_name: str = Field(
        "my-bucket",
        description="The name of the S3 bucket to use for video storage",
    )
    max_frames: int = Field(
        24,
        description="The maximum number of frames to sample from the video",
    )
    max_fps: int = Field(
        default=2,
        description="Maximum frames per second to sample. num_frames = min(video_length * max_fps, max_frames)",
    )
    min_pixels: int = Field(
        1568,
        description="The minimum number of pixels for 2 frames from the video, 28x28=784 will be converted to one video token",
    )
    max_pixels: int = Field(
        345600,
        description="The maximum number of pixels for 2 frames from the video, 28x28=784 will be converted to one video token",
    )
    reasoning: bool = Field(
        False,
        description="Only for cosmos reason models, turn on reasoning when you want to let the VLM reason before returning the answer.",
    )
    filter_thinking: bool = Field(
        False,
        description="Whether to filter out thinking traces from the VLM response. When enabled, only the answer portion is returned.",
    )
    use_vst: bool = Field(
        True,
        description="Whether to use VST service to get the video URL. If False, it will use the MinIO service to get the video URL.",
    )
    time_format: Literal["iso", "offset"] = Field(
        "iso",
        description="Timestamp input format: 'iso' for ISO 8601 UTC strings (e.g. '2025-08-25T03:05:55Z'), "
        "'offset' for seconds since stream start. "
        "Must match across video_understanding, vst.video_clip, vst.snapshot, and critic_agent configs.",
    )
    video_url_tool: str | None = Field(
        None,
        description="A tool to be used to get the video URL by sensor ID and timestamp(default to use VST service)",
    )
    use_base64: bool = Field(
        False,
        description="Whether to use base64 encoding to send the video to the VLM. If True, the video will be encoded to base64 and sent to the VLM.",
    )
    system_prompt: str | None = Field(
        default=None,
        description="Optional custom system prompt for the VLM. If not provided, uses default reasoning prompt when reasoning=True, or no system prompt when reasoning=False.",
    )
    # URL translation configuration for VLM
    vlm_mode: str | None = Field(
        default="local",
        description="VLM mode: 'remote' (VLM is external, needs public URLs), 'local' or 'local_shared' (VLM is local, needs internal URLs)",
    )
    internal_ip: str | None = Field(
        default="",
        description="Internal IP / docker host IP for URL translation",
    )
    external_ip: str | None = Field(
        default="",
        description="Public IP accessible from the internet for URL translation",
    )
    vst_internal_url: str | None = Field(
        default=None,
        description="Internal VST base URL (e.g., 'http://HOST_IP:30888'). "
        "Used for URL translation when behind a reverse proxy.",
    )


class VideoUnderstandingInput(BaseModel):
    """Input for the Video Caption tool"""

    sensor_id: str = Field(
        ...,
        description="The sensor ID or the name of the video file in VST to understand",
        min_length=1,
    )
    start_timestamp: str = Field(
        ...,
        description="The start timestamp in UTC ISO 8601 format (e.g., '2025-08-25T03:05:55.752Z')",
    )
    end_timestamp: str = Field(
        ...,
        description="The end timestamp in UTC ISO 8601 format (e.g., '2025-08-25T03:06:15.752Z')",
    )
    user_prompt: str = Field(
        ...,
        description="The prompt that is used to query the VLM to understand the video, mention all search entities in the prompt that is related to the user's query.",
        min_length=1,
    )
    object_ids: list[str] | None = Field(
        None,
        description="Optional list of object IDs to display as overlays in the video (e.g., from incident objectIds or info.primaryObjectId)",
    )
    vlm_reasoning: bool | None = Field(
        default=None,
        description="Enable VLM reasoning mode. If None, uses config.reasoning default.",
    )
    model_config = {
        "extra": "forbid",
    }


class VideoUnderstandingOffsetInput(BaseModel):
    """Input for the Video Understanding tool (offset mode).

    start_timestamp and end_timestamp are floats representing seconds since the beginning of the stream.
    """

    sensor_id: str = Field(
        ...,
        description="The sensor ID or the name of the video file in VST to understand",
        min_length=1,
    )
    start_timestamp: float | None = Field(
        None,
        description="Optional start time offsets (in seconds since beginning of the stream), if None, then the entire stream is returned",
    )
    end_timestamp: float | None = Field(
        None,
        description="Optional end time offsets (in seconds since beginning of the stream), if None, then the entire stream is returned",
    )
    user_prompt: str = Field(
        ...,
        description="The prompt that is used to query the VLM to understand the video, mention all search entities in the prompt that is related to the user's query.",
        min_length=1,
    )
    vlm_reasoning: bool | None = Field(
        default=None,
        description="Enable VLM reasoning mode. If None, uses config.reasoning default.",
    )
    model_config = {
        "extra": "forbid",
    }

    @model_validator(mode="before")
    @classmethod
    def validate_start_and_end_time(cls, info: dict) -> dict:
        start = info.get("start_timestamp")
        end = info.get("end_timestamp")

        if start is not None:
            start = float(start)
            if start < 0:
                raise ValueError("Start time offset must be non-negative")
            info["start_timestamp"] = start

        if end is not None:
            end = float(end)
            if end < 0:
                raise ValueError("End time offset must be non-negative")
            info["end_timestamp"] = end

        if start is not None and end is not None and start >= end:
            raise ValueError("Start time offset must be before end time offset")

        return info


def extend_timestamp(start_time: str, end_time: str) -> str:
    start_time_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    end_time_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    video_duration = (end_time_dt - start_time_dt).total_seconds()
    # Ensure at least 1 second duration
    if video_duration < 1.0:
        end_time_dt = start_time_dt + timedelta(seconds=1.0)
    # Always return ISO format string
    return end_time_dt.isoformat().replace("+00:00", "Z")


async def _build_vlm_messages(
    video_url: str,
    user_prompt: str,
    *,
    use_frame_images: bool,
    use_base64: bool,
    video_length_seconds: float,
    num_frames: int,
    max_fps: int,
) -> list[HumanMessage]:
    """Download/transform video and build VLM messages for the appropriate backend."""
    if use_frame_images:
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(video_url) as resp:
            resp.raise_for_status()
            video_data = await resp.read()

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tmp:
            tmp.write(video_data)
            tmp.flush()
            step_size = max(video_length_seconds / num_frames, 1.0 / max_fps)
            base64_frames = frame_select(tmp.name, 0.0, video_length_seconds, step_size)

        return [
            HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": f"The following images are a sequence of frames from a video. Answer the user's question based on the video: {user_prompt}",
                    },
                    *[
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame}"}}
                        for frame in base64_frames
                    ],
                ]
            )
        ]

    if use_base64:
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(video_url) as resp:
            resp.raise_for_status()
            video_data = await resp.read()
            video_base64 = base64.b64encode(video_data).decode("utf-8")
            video_url = f"data:video/mp4;base64,{video_base64}"

    return [
        HumanMessage(
            content=[
                {"type": "text", "text": user_prompt},
                {"type": "video_url", "video_url": {"url": video_url}},
            ]
        )
    ]


@register_function(config_type=VideoUnderstandingConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def video_understanding(config: VideoUnderstandingConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    base_vlm = await builder.get_llm(config.vlm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    is_nim = config.vlm_name.startswith("nim_")
    model_name = getattr(base_vlm, "model_name", "") or getattr(base_vlm, "model", "")
    is_cosmos_model = is_nim and "cosmos" in model_name
    is_cosmos_reason2 = is_nim and model_name == "nvidia/cosmos-reason2-8b"

    # Dynamically determine if we extract frames for this model (only needed for official OpenAI endpoints for now)
    # Supposes any vlm_name prefixed with "openai_" is from an official OpenAI endpoint
    use_frame_images = str(config.vlm_name).startswith("openai_")
    logger.info(
        f"Using VLM profile: {config.vlm_name}, use_frame_images: {use_frame_images}, use_base64: {config.use_base64}"
    )

    if not config.use_vst:
        s3_client = boto3.client(
            "s3",
            endpoint_url=config.minio_url,
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
            region_name="us-east-1",
            verify=True,
        )
    else:
        s3_client = None

    # VLM prompt templates setup
    if config.system_prompt:
        # Use custom system prompt from config
        logger.info(f"Using custom system prompt: {config.system_prompt[:100]}...")
        reasoning_prompt_template = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    f"{config.system_prompt}\n\nWrap your response in the following format:\n<think>\nyour reasoning\n</think>\n\n<answer>\nyour answer following the observation format above\n</answer>",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )
        non_reasoning_prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", config.system_prompt),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )
    else:
        # Use default prompts
        reasoning_prompt_template = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "Answer the question in the following format: <think>\nyour reasoning\n</think>\n\n<answer>\nyour answer\n</answer>.",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )
        non_reasoning_prompt_template = ChatPromptTemplate.from_messages(
            [
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

    # For cosmos-reason2-8b: override to use no system prompt for the reasoning instructions (reasoning instructions are appended to user message)
    if is_cosmos_reason2:
        reasoning_prompt_template = non_reasoning_prompt_template

    async def _video_understanding(
        video_understanding_input: VideoUnderstandingInput | VideoUnderstandingOffsetInput,
    ) -> str:
        """
        This tool uses the VLM to understand a video clip from start_timestamp to end_timestamp.

        IMPORTANT:
            - start_timestamp MUST be smaller than end_timestamp
        Returns:
            str: The caption for the video.
        """
        # Calculate video length and dynamic num_frames
        if config.video_url_tool:
            vst_video_url = await builder.get_tool(config.video_url_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        else:
            vst_video_url = None
            if config.use_vst:
                raise ValueError("video_url_tool is not configured and use_vst is True")
        if (
            video_understanding_input.start_timestamp is not None
            and video_understanding_input.end_timestamp is not None
        ):
            if config.time_format == "iso":
                # "iso" mode: timestamps are already ISO 8601 strings — parse directly.
                start_ts = str(video_understanding_input.start_timestamp)
                end_ts = str(video_understanding_input.end_timestamp)
                start_dt = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
            else:
                # "offset" mode: timestamps are seconds since start of stream.
                # Fetch the stream timeline and add the offset to compute absolute datetimes.
                stream_id = await get_stream_id(video_understanding_input.sensor_id)
                start_iso, end_iso = await get_timeline(stream_id)
                start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")) + timedelta(
                    seconds=float(video_understanding_input.start_timestamp)
                )
                end_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")) + timedelta(
                    seconds=float(video_understanding_input.end_timestamp)
                )
        else:
            # use entire video
            stream_id = await get_stream_id(video_understanding_input.sensor_id)
            start_iso, end_iso = await get_timeline(stream_id)
            start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        video_length_seconds = (end_dt - start_dt).total_seconds()
        num_frames = min(int(video_length_seconds) * config.max_fps, config.max_frames)
        # Ensure at least 1 frame···
        num_frames = max(num_frames, 1)
        logger.info(
            f"Video length: {video_length_seconds:.1f}s, num_frames: {num_frames} (max_fps={config.max_fps}, max_frames={config.max_frames})"
        )

        # Bind VLM with dynamic num_frames
        if is_cosmos_model:
            media_io_kwargs = {"video": {"num_frames": num_frames}}
            if is_cosmos_reason2:
                mm_processor_kwargs = {"size": {"shortest_edge": config.min_pixels, "longest_edge": config.max_pixels}}
            else:
                mm_processor_kwargs = {
                    "videos_kwargs": {"min_pixels": config.min_pixels, "max_pixels": config.max_pixels}
                }
            vlm = base_vlm.bind(
                mm_processor_kwargs=mm_processor_kwargs,
                media_io_kwargs=media_io_kwargs,
            )
        else:
            vlm = base_vlm

        # Select reasoning mode: default to config.reasoning if not specified in the input
        use_reasoning = (
            video_understanding_input.vlm_reasoning
            if video_understanding_input.vlm_reasoning is not None
            else config.reasoning
        )

        if use_frame_images:  # OpenAI models (reasoning configuration through parameters)
            if use_reasoning:
                vlm = vlm.bind(reasoning={"effort": "medium", "summary": "auto"})
            prompt_template = non_reasoning_prompt_template
        else:
            prompt_template = reasoning_prompt_template if use_reasoning else non_reasoning_prompt_template

        vlm_chain = prompt_template | vlm
        logger.info(f"VLM reasoning mode: {use_reasoning}, use_frame_images: {use_frame_images}")

        # Step 1: Get the video URL (different paths for S3 vs VST)
        if not config.use_vst:
            # get the video URL from S3
            if not s3_client:
                raise ValueError("S3 client is not configured correctly")
            video_url = s3_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": config.bucket_name,
                    "Key": video_understanding_input.sensor_id + ".mp4",
                },
                ExpiresIn=3600,
            )
            logger.info(f"Video URL from S3: {video_url}")
        else:
            if config.time_format == "iso":
                # "iso" mode: pass ISO 8601 timestamps directly to the video URL tool.
                logger.info(
                    f"Using {config.video_url_tool} to get video URL for file {video_understanding_input.sensor_id} from {video_understanding_input.start_timestamp} to {video_understanding_input.end_timestamp}"
                )

                video_understanding_input.end_timestamp = extend_timestamp(
                    str(video_understanding_input.start_timestamp), str(video_understanding_input.end_timestamp)
                )

                vst_video_url_args: dict[str, Any] = {
                    "sensor_id": video_understanding_input.sensor_id,
                    "start_time": video_understanding_input.start_timestamp,
                    "end_time": video_understanding_input.end_timestamp,
                }

                if hasattr(video_understanding_input, "object_ids") and video_understanding_input.object_ids:
                    vst_video_url_args["object_ids"] = video_understanding_input.object_ids
                    logger.info(f"Passing object IDs to VST video URL: {video_understanding_input.object_ids}")

                logger.debug(f"VST video URL arguments: {vst_video_url_args}")

                vst_video_url_result = await vst_video_url.ainvoke(input=vst_video_url_args)

                video_url = vst_video_url_result.video_url
            else:
                # "offset" mode: pass second-based offsets to the video URL tool.
                vst_video_url_result = await vst_video_url.ainvoke(
                    input={
                        "sensor_id": video_understanding_input.sensor_id,
                        "start_time": video_understanding_input.start_timestamp,
                        "end_time": video_understanding_input.end_timestamp,
                    }
                )
                video_url = vst_video_url_result.video_url
                logger.debug(f"Video URL from VST: {video_url}")

            # Translate URL for VLM based on vlm_mode:
            # - remote: INTERNAL_IP -> EXTERNAL_IP (VLM needs public URLs)
            # - local/local_shared: EXTERNAL_IP -> INTERNAL_IP (VLM needs internal URLs)
            video_url = translate_url(
                video_url,
                config.vlm_mode,
                config.internal_ip,
                config.external_ip,
                config.vst_internal_url,
            )

        logger.info(f"[Video Understanding] VIDEO URL FOR VLM ANALYSIS: {video_url}")

        user_prompt = video_understanding_input.user_prompt
        if is_cosmos_reason2 and use_reasoning:
            user_prompt = user_prompt + (
                "\n\nAnswer the question using the following format:\n\n"
                "<think>\nYour reasoning.\n</think>\n\n"
                "Write your final answer immediately after the </think> tag."
            )

        messages = await _build_vlm_messages(
            video_url,
            user_prompt,
            use_frame_images=use_frame_images,
            use_base64=config.use_base64,
            video_length_seconds=video_length_seconds,
            num_frames=num_frames,
            max_fps=config.max_fps,
        )

        # Retry logic for VLM call — only retry transient errors (connection/timeout/5xx).
        # Client errors like 400 (e.g. unsupported video format) are not retryable.
        async for retry in create_retry_strategy(retries=3, exceptions=_VLM_RETRYABLE_ERRORS):
            with retry:
                try:
                    response = await vlm_chain.ainvoke({"messages": messages})
                    logger.debug(f"Response: {response}")
                    break
                except Exception as e:
                    logger.error(f"Error understanding video {video_understanding_input.sensor_id}: {e}")
                    raise e

        if use_frame_images:  # OpenAI models (output reasoning in content_blocks)
            reasoning, answer = parse_content_blocks(response)
            if reasoning or answer:
                content = f"<think>{reasoning}</think>{answer or ''}" if reasoning else (answer or "")
            else:
                content = str(response.content) if response.content is not None else ""
        else:
            content = str(response.content) if response.content is not None else ""
        # Filter thinking traces
        if config.filter_thinking:
            thinking, answer = _parse_thinking_from_content(content)
            if thinking:
                logger.info(
                    f"Filtered out thinking trace ({len(thinking)} chars), returning answer ({len(answer)} chars)"
                )
                return answer
            else:
                logger.info("No thinking traces found in response")

        return content

    # Register the tool with the appropriate input schema based on time_format:
    #   - "offset": accepts float offsets (seconds since start of stream).
    #     Use for uploaded video files where only relative position matters.
    #   - "iso": accepts ISO 8601 UTC timestamp strings.
    #     Use for RTSP live streams where events have real-world wall-clock times.
    # This must match the time_format of the video_url_tool (e.g. vst.video_clip)
    # and any caller such as critic_agent.
    if config.time_format == "offset":

        async def _video_understanding_offset(video_understanding_input: VideoUnderstandingOffsetInput) -> str:
            return await _video_understanding(video_understanding_input)

        input_desc = """
        Input:
            sensor_id: The sensor ID or the name of the video file in VST to understand
            start_timestamp: The start timestamp in offset seconds since beginning of the stream
            end_timestamp: The end timestamp in offset seconds since beginning of the stream
            user_prompt: The prompt that is used to query the VLM to understand the video, mention all search entities in the prompt that is related to the user's query.
            vlm_reasoning: Enable VLM reasoning mode. If None, uses config.reasoning default.
            Note: start_timestamp and end_timestamp are optional. If None, then the entire stream is returned.
        """

        yield FunctionInfo.create(
            single_fn=_video_understanding_offset,
            description=(_video_understanding.__doc__ or "") + input_desc,
            input_schema=VideoUnderstandingOffsetInput,
            single_output_schema=str,
        )
    else:

        async def _video_understanding_iso(video_understanding_input: VideoUnderstandingInput) -> str:
            return await _video_understanding(video_understanding_input)

        input_desc = """
        Input:
            sensor_id: The sensor ID or the name of the video file in VST to understand
            start_timestamp: The start timestamp in UTC ISO 8601 format (e.g., '2025-08-25T03:05:55.752Z')
            end_timestamp: The end timestamp in UTC ISO 8601 format (e.g., '2025-08-25T03:06:15.752Z')
            user_prompt: The prompt that is used to query the VLM to understand the video, mention all search entities in the prompt that is related to the user's query.
            vlm_reasoning: Enable VLM reasoning mode. If None, uses config.reasoning default.
        """

        yield FunctionInfo.create(
            single_fn=_video_understanding_iso,
            description=(_video_understanding.__doc__ or "") + input_desc,
            input_schema=VideoUnderstandingInput,
            single_output_schema=str,
        )
