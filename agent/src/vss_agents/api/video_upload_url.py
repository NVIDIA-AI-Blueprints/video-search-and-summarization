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
import re

from fastapi import HTTPException
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.api_server import ChatRequest
from nat.data_models.api_server import ChatResponse
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class VideoUploadURLConfig(FunctionBaseConfig, name="video_upload_url"):
    """Configuration for the Video Upload URL tool."""

    vst_external_url: str = Field(
        ...,
        description="The external VST URL for client-facing upload URLs",
    )
    agent_base_url: str = Field(
        ...,
        description="The base URL of the agent service (e.g., http://localhost:8000)",
    )


class VideoUploadURLInput(BaseModel):
    """Input for the Video Upload URL tool."""

    filename: str = Field(
        ...,
        description="The name of the video file to be uploaded",
        min_length=1,
    )
    embedding: bool = Field(
        default=False,
        description="Whether to generate URL for video embedding/search ingestion",
    )


class VideoUploadURLOutput(BaseModel):
    """Output for the Video Upload URL tool."""

    url: str = Field(
        ...,
        description="The VST upload URL for the video file with timestamp",
    )


@register_function(config_type=VideoUploadURLConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def video_upload_url(config: VideoUploadURLConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    Video Upload URL tool that provides a VST upload URL for a video file.

    This tool constructs a URL for uploading a video file to VST storage.
    """

    async def _video_upload_url(video_upload_url_input: VideoUploadURLInput) -> VideoUploadURLOutput:
        """
        Get a VST upload URL for a video file.

        Args:
            video_upload_url_input: VideoUploadURLInput containing the filename and embedding flag.

        Returns:
            VideoUploadURLOutput containing the upload URL with timestamp or embedding URL.
        """
        try:
            filename = video_upload_url_input.filename
            if not filename:
                raise HTTPException(status_code=400, detail="Filename is required")

            # Check for any whitespace character in filename
            if re.search(r"\s", filename):
                raise HTTPException(
                    status_code=400, detail="Filename cannot contain whitespace. Please rename the file and try again."
                )

            # Remove file extension if present
            filename_without_ext = filename.rsplit(".", 1)[0] or filename

            embedding = video_upload_url_input.embedding

            # If embedding is requested, return the agent URL for video search
            if embedding:
                agent_base_url = config.agent_base_url.rstrip("/")
                url = f"{agent_base_url}/api/v1/videos-for-search/{filename_without_ext}"
                logger.info(f"Generated video embedding URL: {url}")

            # ELSE return the VST upload URL
            else:
                # Remove trailing slash from base url if present
                base_url = config.vst_external_url.rstrip("/")

                # Return fixed timestamp
                timestamp = "2025-01-01T00:00:00.000Z"

                # TODO: remove the temp url and use the vst base url from the config
                # temp_base_url = "http://localhost:30888"

                # Construct the upload URL
                url = f"{base_url}/vst/api/v1/storage/file/{filename_without_ext}/{timestamp}"

                logger.info(f"Generated video upload URL: {url}")

            return VideoUploadURLOutput(url=url)

        except Exception as e:
            logger.error(f"Error generating video upload URL: {e}")
            raise

    def _str_input_converter(input: str) -> VideoUploadURLInput:
        """Convert string input (JSON) to VideoUploadURLInput."""
        return VideoUploadURLInput.model_validate_json(input)

    def _chat_request_input_converter(request: ChatRequest) -> VideoUploadURLInput:
        """Convert ChatRequest to VideoUploadURLInput from the last message content."""
        try:
            return VideoUploadURLInput.model_validate_json(request.messages[-1].content)
        except Exception:
            logger.exception("Error in chat request input converter.")
            raise

    def _output_converter(output: VideoUploadURLOutput) -> str:
        """Convert output to string JSON."""
        return output.model_dump_json()

    def _chat_response_output_converter(response: VideoUploadURLOutput) -> ChatResponse:
        """Convert output to ChatResponse."""
        return ChatResponse.from_string(_output_converter(response))

    yield FunctionInfo.create(
        single_fn=_video_upload_url,
        description=_video_upload_url.__doc__,
        input_schema=VideoUploadURLInput,
        single_output_schema=VideoUploadURLOutput,
        converters=[
            _str_input_converter,
            _chat_request_input_converter,
            _output_converter,
            _chat_response_output_converter,
        ],
    )
