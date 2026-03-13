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
from typing import Any

import httpx
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class VSTFilesConfig(FunctionBaseConfig, name="vst_files"):
    """Configuration for the VST Files tool."""

    vst_backend_url: str = Field(..., description="The URL of the VST backend server")
    timeout: int = Field(default=30, description="Request timeout in seconds")
    use_mock: bool = Field(True, description="Use mock data instead of real VST API for development")
    offset: int = Field(0, description="Start offset to fetch the records from VST API")
    limit: int = Field(100, description="Maximum number of records to fetch from VST API")
    mock_video_list: dict = Field(
        default={
            "b7a1c1f2-9c0e-4d8d-8a6a-2e5f7d2e3c1b": [
                {
                    "mediaFilePath": "/home/vst/vst_release/streamer_videos/assault_camera_1.mp4",
                    "metadataFilePath": "./media/events/20240115_103000.json",
                    "metadata": {
                        "eventInfo": "Parking lot surveillance camera",
                        "timestamp": 1752045606222,
                        "id": "a09612ec-f64e-404f-ac74-0ecf1175980a",  # change this id as needed
                        "streamName": "assault_camera_1",
                        "sensorId": "b7a1c1f2-9c0e-4d8d-8a6a-2e5f7d2e3c1b",
                        "duration": 14,  # Duration in seconds
                    },
                }
            ]
        },
        description="Mock VST data matching real API response format with nested sensor structure",
    )


class VSTFilesInput(BaseModel):
    """Input for the VST Files tool"""

    question: str = Field(..., description="The user's query to find relevant video files")


@register_function(config_type=VSTFilesConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def vst_files(config: VSTFilesConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    Query the VST backend for a list of files matching the user's query
    Returns a dictionary mapping VST video IDs to their metadata.
    """

    async def _vst_files(_vst_files_input: VSTFilesInput) -> dict[str, dict[str, Any]]:
        """
        Query the VST backend to get available video files and their metadata.

        Input:
            vst_files_input: VSTFilesInput containing the user query

        Returns:
            Dict[str, Dict[str, Any]]: Dictionary mapping VST video IDs to metadata
            Example: {
                "vst_id_123": {
                    "filename": "assault_camera_1.mp4",
                    "duration": 120.5,
                    "sensor_id": "cam1",
                    "timestamp": 1234567890,
                }
            }
        """
        if config.use_mock:
            logger.info("Using mock VST data for development")
            vst_response = config.mock_video_list
        else:
            try:
                async with httpx.AsyncClient(timeout=config.timeout) as client:
                    # Query VST backend for available videos
                    response = await client.get(
                        f"{config.vst_backend_url}/api/v1/storage/file/list",
                        params={"offset": config.offset, "limit": config.limit},
                    )
                    response.raise_for_status()

                    vst_response = response.json()
                    logger.info(f"VST backend returned {len(vst_response)} videos")

            except httpx.TimeoutException:
                logger.error(f"VST backend timeout after {config.timeout} seconds")
                return {}
            except httpx.HTTPStatusError as e:
                # Reading response in a safe manner
                try:
                    error_text = e.response.text
                except Exception:
                    error_text = "Unable to read response content"
                logger.error(f"VST backend HTTP error: {e.response.status_code} - {error_text}")
                return {}
            except Exception as e:
                logger.error(f"Error querying VST backend: {e}")
                return {}

        # Transform VST response to expected format
        available_videos: list[dict[str, str]] = []
        for sensor_id, clips in vst_response.items():
            for clip in clips:
                # Extract filename from mediaFilePath and clean it up
                media_file_path = clip.get("mediaFilePath", "")
                if media_file_path:
                    raw_filename = media_file_path.split("/")[-1]
                    # remove extra dots from filename, keep only the last extension
                    if "." in raw_filename:
                        name_part = raw_filename.rsplit(".", 1)[0]
                        ext_part = raw_filename.rsplit(".", 1)[1]
                        # Replace any remaining dots in name with underscores
                        clean_name = name_part.replace(".", "_")
                        filename = f"{clean_name}.{ext_part}"
                    else:
                        filename = f"{raw_filename}.mp4"  # Add .mp4 if no extension
                else:
                    filename = "unknown.mp4"

                # Use metadata.id as video_id, fallback to generating one if missing
                metadata = clip.get("metadata", {})
                video_id = metadata.get("id", f"{sensor_id}_{len(available_videos)}")

                available_videos.append(
                    {
                        "vst_id": video_id,
                        "filename": filename,
                        "sensor_id": sensor_id,
                        "timestamp": metadata.get("timestamp", 0),
                        "duration": metadata.get("duration", 0.0),
                    }
                )

        logger.info(f"Processed {len(available_videos)} video clips from {len(vst_response)} sensors")

        # Create files_ids dict with full metadata
        files_metadata = {
            f["vst_id"]: {
                "filename": f["filename"],
                "sensor_id": f["sensor_id"],
                "duration": f["duration"],
                "timestamp": f["timestamp"],
            }
            for f in available_videos
        }

        logger.info(f"!!!All files metadata: {files_metadata}")

        return files_metadata

    yield FunctionInfo.create(
        single_fn=_vst_files,
        description=_vst_files.__doc__,
        input_schema=VSTFilesInput,
        single_output_schema=dict[str, dict[str, Any]],
    )
