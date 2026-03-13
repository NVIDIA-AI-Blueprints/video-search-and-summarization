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
import math
from typing import Annotated
from typing import Any
import uuid

from vss_agents.data_models.vss import MediaInfoOffset
from vss_agents.prompt import INIT_SUMMARIZE_PROMPT
from vss_agents.prompt import VLM_FORMAT_INSTRUCTION
from vss_agents.prompt import VLM_PROMPT_EXAMPLES

from nat.builder.builder import Builder
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

logger = logging.getLogger(__name__)


class VSSSummarizeConfig(FunctionBaseConfig, name="vss_summarize"):
    """Configuration for the VSS Summarize tool."""

    backend_url: str = Field(
        ...,
        description="The URL of the VSS backend.",
    )
    vss_version: str = Field(
        "2.3.0",
        description="The version of the VSS backend.",
    )
    conn_timeout_ms: int = Field(
        default=5000,
        description="The connection timeout in milliseconds.",
    )
    read_timeout_ms: int = Field(
        default=360000,
        description="The read timeout in milliseconds.",
    )

    max_concurrency: int = Field(
        default=4,
        description="The maximum number of concurrent requests to the VSS backend",
    )
    model_config = ConfigDict(extra="forbid")
    max_num_frames_per_chunk: int = Field(
        default=8,
        description="The maximum number of frames to summarize in each chunk, default is 10",
    )


class VSSSummarizeInput(BaseModel):
    """Input for the VSS Summarize tool"""

    id: uuid.UUID | list[uuid.UUID] = Field(
        description="Unique ID or list of IDs of the file(s)/live-stream(s) to summarize",
    )

    prompt: str = Field(
        ...,
        max_length=5000,
        description="Prompt for summary generation, include objects and events that user's query is about, this will instruct the VLM to generate a dense caption for each frame",
        examples=VLM_PROMPT_EXAMPLES,
    )
    # chunk_duration: int = Field(
    #     default=60,
    #     examples=[60, 30, 20, 10, 5],
    #     description=(
    #         "Chunk videos into `chunkDuration` seconds, examples are 5, 10, 30, 60. smaller chunks will give more detailed captions, "
    #         "however it will slow down the processing, choose a bigger chunk at the beginning then use a smaller chunk on a "
    #         "second pass and limiting the video's start and end time by setting the media_info parameter"
    #     ),
    #     ge=0,
    #     le=3600,
    #     json_schema_extra={"format": "int32"},
    # )
    step_size: float | None = Field(
        default=None,
        ge=0.1,
        le=10,
        description="The step size for the sampling of frames, VLM usually works best with a step size around 1 second. Smaller step size will give more detailed captions, however it will slow down the processing.",
    )
    video_duration: float = Field(
        ...,
        description="The duration of the entire video",
    )

    media_info: Annotated[
        MediaInfoOffset,
        Field(
            ...,
            description=("The offset of the video clip to summarize"),
        ),
    ]

    summary_aggregation_prompt: str = Field(
        INIT_SUMMARIZE_PROMPT["summary_aggregation_prompt"],
        description="The prompt for aggregating the summaries from batches of video chunks",
    )
    caption_summarization_prompt: str = Field(
        INIT_SUMMARIZE_PROMPT["caption_summarization_prompt"],
        description="The prompt for summarizing a batch of video captions from video chunks",
    )

    @model_validator(mode="before")
    @classmethod
    def validate_all(cls, data: dict) -> Any:
        """Validate the entire VSSSummarizeInput object"""
        if data.get("media_info") is None:
            data["media_info"] = MediaInfoOffset(start_offset=0, end_offset=int(data["video_duration"]))
        elif data["media_info"].end_offset > data["video_duration"]:
            data["media_info"].end_offset = int(data["video_duration"])
        return data

    model_config = {
        "extra": "forbid",
    }


class VSSSummarizeOutput(BaseModel):
    """Output for the VSS Summarize tool"""

    media_info: MediaInfoOffset = Field(..., description="The media info of the video")
    summary: str = Field(..., description="The summary of the video")
    step_size: float | None = Field(None, description="The step size of the sampling of frames, in seconds")

    def __str__(self) -> str:
        # return as a list item in a markdown list
        media_info_str = f"{self.media_info.start_offset} - {self.media_info.end_offset}"
        ret = f"- timestamp: {media_info_str}\n"
        ret += f"- step size: {self.step_size}\n"
        ret += f"- summary: {self.summary}\n"
        return ret


@register_function(config_type=VSSSummarizeConfig)
async def vss_summarize(config: VSSSummarizeConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    from aiohttp import ClientSession
    from aiohttp import ClientTimeout
    import requests

    try:
        response = requests.get(config.backend_url + "/models", timeout=10)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to get model from VSS backend: {response.status_code} {response.text}")
        vss_internal_model = response.json()["data"][0]["id"]
    except Exception as e:
        logger.error("Error getting model from VSS backend: %s, backend_url: %s", e, config.backend_url)
        raise e

    conn_timeout = config.conn_timeout_ms / 1000
    read_timeout = config.read_timeout_ms / 1000
    session = ClientSession(timeout=ClientTimeout(connect=conn_timeout, total=read_timeout))

    async def _vss_summarize(vss_summarize_input: VSSSummarizeInput) -> VSSSummarizeOutput:
        """
        Use vss backend with the vision language model to understand and summarize a video clip.
        In the input, you should provide the prompt, make sure to include the objects and events that user's query is about, this will instruct the VLM to generate a dense caption for each frame.
        Note: this tool is slow and expensive, please use it only when necessary for more detailed information.
        Input:
            vss_summarize_input: VSSSummarizeInput

        Returns:
            str: The summary of the video.
        """

        step_size = vss_summarize_input.step_size
        # adjust step size based on the max_concurrency
        media_info = vss_summarize_input.media_info

        voi_video_duration = media_info.end_offset - media_info.start_offset
        if step_size is None:
            # initial summary should use step size based on max_concurrency
            chunk_duration = math.ceil(voi_video_duration / config.max_concurrency)
            # minimum step size at the first pass is 1.0 second
            num_frames_per_chunk = min(config.max_num_frames_per_chunk, int(chunk_duration))
            step_size = chunk_duration / num_frames_per_chunk
        else:
            num_frames_per_chunk = int(max(min(voi_video_duration / step_size, config.max_num_frames_per_chunk), 1))
            chunk_duration = min(max(1, math.ceil(num_frames_per_chunk * step_size)), voi_video_duration)

        req_obj: dict[str, Any] = {}

        req_obj["id"] = str(vss_summarize_input.id)
        fps = 1 / step_size
        req_obj["prompt"] = (
            vss_summarize_input.prompt
            + "\n"
            + VLM_FORMAT_INSTRUCTION
            + "\n"
            + f"Below are frames sampled from the same video clip at fps {fps}"
        )
        req_obj["summarize"] = True
        req_obj["enable_chat"] = True
        # get model from vss backend list-models and use it for summarization
        req_obj["model"] = vss_internal_model
        req_obj["caption_summarization_prompt"] = vss_summarize_input.caption_summarization_prompt
        req_obj["summary_aggregation_prompt"] = vss_summarize_input.summary_aggregation_prompt
        # add padding instruction to the prompt

        req_obj["chunk_duration"] = chunk_duration
        req_obj["media_info"] = {
            "type": "offset",
            "start_offset": media_info.start_offset,
            "end_offset": media_info.end_offset,
        }
        req_obj["num_frames_per_chunk"] = num_frames_per_chunk

        summary = ""
        logger.info("Summarizing video with request: %s", req_obj)

        try:
            async with session.post(config.backend_url + "/summarize", json=req_obj) as response:
                if response.status != 200:
                    raise RuntimeError(f"Failed to summarize: {response.status} {response.text}")
                response_json = await response.json()
                choices = response_json.get("choices", [])
                if choices:
                    summary = choices[0].get("message", {}).get("content", "")
                    logger.info("Summary: %s", summary)
                else:
                    raise RuntimeError("No choices found in the response, response: %s", response_json)
        except RuntimeError as e:
            logger.exception("Summarization pipeline failed, error: %s", e)
        except Exception as e:
            logger.exception("Error calling vss summarize: %s", e)
        logger.info("Summary: %s", summary)
        return VSSSummarizeOutput(summary=summary, step_size=step_size, media_info=media_info)

    yield FunctionInfo.create(
        single_fn=_vss_summarize,
        description=_vss_summarize.__doc__,
        input_schema=VSSSummarizeInput,
        single_output_schema=VSSSummarizeOutput,
    )
