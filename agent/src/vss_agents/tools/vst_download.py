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
from pathlib import Path

import httpx
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class VSTDownloadConfig(FunctionBaseConfig, name="vst_download"):
    """Configuration for the VST Download tool."""

    vst_backend_url: str = Field(..., description="The URL of the VST backend server")
    download_timeout: int = Field(default=300, description="Download timeout in seconds")
    chunk_size: int = Field(default=8192, description="Chunk size for streaming download")


class VSTDownloadInput(BaseModel):
    """Input for the VST Download tool"""

    video_id: str = Field(..., description="The VST video ID to download")
    filename: str = Field(..., description="The filename to save the downloaded video as")
    start_time: int = Field(..., description="Start time in milliseconds")
    end_time: int = Field(..., description="End time in milliseconds")
    container: str = Field(default="mp4", description="Video container format (mp4, mkv, etc.)")
    asset_path: str = Field(..., description="Directory path where the video will be saved")


class VSTDownloadOutput(BaseModel):
    """Output for the VST Download tool"""

    local_file_path: str = Field(..., description="The local path where the video was saved")
    file_size_bytes: int = Field(..., description="Size of the downloaded file in bytes")
    duration_ms: int = Field(..., description="Duration of the downloaded clip in milliseconds")

    cleanup_required: bool = Field(default=True, description="Whether file needs cleanup after use")


@register_function(config_type=VSTDownloadConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def vst_download(config: VSTDownloadConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    Tool to download video clips from the VST backend.
    Downloads a specific time range from a VST video to local storage.
    """

    async def _vst_download(vst_download_input: VSTDownloadInput) -> VSTDownloadOutput:
        """
        Download a video clip from VST backend for the specified time range.

        Input:
            vst_download_input: VSTDownloadInput with download parameters

        Returns:
            VSTDownloadOutput: Information about the downloaded file
        """

        # Ensure asset path exists
        asset_path = Path(vst_download_input.asset_path)
        asset_path.mkdir(parents=True, exist_ok=True)

        # Construct local file path
        local_file_path = asset_path / vst_download_input.filename

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=config.download_timeout,
                    write=60.0,
                    pool=60.0,
                )
            ) as client:
                # Request video clip from VST backend
                download_params: dict[str, str | int] = {
                    "id": vst_download_input.video_id,
                    "startTime": vst_download_input.start_time,
                    "endTime": vst_download_input.end_time,
                    "container": vst_download_input.container,
                }

                logger.info(
                    f"Downloading VST video clip: {vst_download_input.video_id} "
                    f"({vst_download_input.start_time}ms-{vst_download_input.end_time}ms)"
                )

                # Stream download from VST backend
                async with client.stream(
                    "GET", f"{config.vst_backend_url}/api/v1/storage/file", params=download_params
                ) as response:
                    response.raise_for_status()

                    # Get file size from headers if available
                    content_length = response.headers.get("content-length")
                    expected_size = int(content_length) if content_length else None

                    # Download file in chunks
                    file_size = 0
                    with open(local_file_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=config.chunk_size):
                            f.write(chunk)
                            file_size += len(chunk)

                    # Verify download
                    if expected_size and file_size != expected_size:
                        logger.warning(f"Downloaded size ({file_size}) doesn't match expected size ({expected_size})")

                    # Calculate duration of the clip
                    duration_ms = vst_download_input.end_time - vst_download_input.start_time

                    logger.info(
                        f"Successfully downloaded VST video clip to: {local_file_path} "
                        f"(size: {file_size} bytes, duration: {duration_ms}ms)"
                    )

                    return VSTDownloadOutput(
                        local_file_path=str(local_file_path), file_size_bytes=file_size, duration_ms=duration_ms
                    )

        except httpx.TimeoutException:
            logger.error(f"VST download timeout after {config.download_timeout} seconds")
            # Clean up partial file
            if local_file_path.exists():
                local_file_path.unlink()
            raise RuntimeError(f"VST download timeout for video {vst_download_input.video_id}") from None

        except httpx.HTTPStatusError as e:
            # Reading response in a safe manner
            try:
                response_text = await e.response.aread()
                error_text = response_text.decode("utf-8", errors="ignore")
            except Exception:
                error_text = "Unable to read response content"

            logger.error(f"VST download HTTP error: {e.response.status_code} - {error_text}")
            # Clean up partial file
            if local_file_path.exists():
                local_file_path.unlink()
            raise RuntimeError(
                f"VST download failed for video {vst_download_input.video_id}: HTTP {e.response.status_code}"
            ) from e

        except Exception as e:
            logger.error(f"Error downloading from VST: {e}")
            # Clean up partial file
            if local_file_path.exists():
                local_file_path.unlink()
            raise RuntimeError(f"VST download failed for video {vst_download_input.video_id}: {e}") from e

    yield FunctionInfo.create(
        single_fn=_vst_download,
        description=_vst_download.__doc__,
        input_schema=VSTDownloadInput,
        single_output_schema=VSTDownloadOutput,
    )
