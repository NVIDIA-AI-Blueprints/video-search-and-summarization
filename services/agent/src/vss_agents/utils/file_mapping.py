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

from dataclasses import dataclass
from enum import Enum
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)


class StorageType(Enum):
    """Types of video storage backends"""

    VST = "vst"
    VSS = "vss"
    LOCAL = "local"


@dataclass
class VideoFileInfo:
    """Information about a video file from storage backend"""

    filename: str
    storage_type: StorageType
    storage_id: str  # VST video_id or VSS file_id
    duration: float
    sensor_id: str | None = None
    timestamp: int | None = None
    local_path: str | None = None  # Full path for LOCAL storage type


class FileMapping:
    """
    Central service for mapping filenames to storage backend IDs.
    Provides abstraction so tools only need to know filenames.
    """

    def __init__(self) -> None:
        self._filename_to_info: dict[str, VideoFileInfo] = {}
        self._vss_filename_to_id: dict[str, str] = {}
        self._vst_filename_to_id: dict[str, str] = {}

    def add_vst_files(self, vst_files_data: dict[str, dict]) -> None:
        """
        Add VST file mappings from vst_files tool response.

        Args:
            vst_files_data: Response from vst_files tool
                Format: {
                    "video_id_123": {
                        "filename": "camera1.mp4",
                        "duration": 120.5,
                        "sensor_id": "sensor_001",
                        "timestamp": 1234567890
                    }
                }
        """
        for vst_id, file_data in vst_files_data.items():
            filename = file_data["filename"]

            video_info = VideoFileInfo(
                filename=filename,
                storage_type=StorageType.VST,
                storage_id=vst_id,
                duration=file_data.get("duration", 0.0),
                sensor_id=file_data.get("sensor_id"),
                timestamp=file_data.get("timestamp"),
            )

            self._filename_to_info[filename] = video_info
            self._vst_filename_to_id[filename] = vst_id

            logger.info(f"Added VST mapping: {filename} -> {vst_id}")

    def add_vss_files(self, vss_files_data: dict[str, str]) -> None:
        """
        Add VSS file mappings from vss_files tool response.

        Args:
            vss_files_data: Response from vss_files tool
                Format: {"vss_id_123": "filename.mp4", ...}
        """
        for vss_id, filename in vss_files_data.items():
            if filename not in self._filename_to_info:
                video_info = VideoFileInfo(
                    filename=filename,
                    storage_type=StorageType.VSS,
                    storage_id=vss_id,
                    duration=0.0,  # default duration
                )
                self._filename_to_info[filename] = video_info

            # VSS mapping for chat tool
            self._vss_filename_to_id[filename] = vss_id
            logger.info(f"Added VSS mapping: {filename} -> {vss_id}")

    def get_file_info(self, filename: str) -> VideoFileInfo | None:
        """Get complete file information by filename"""
        return self._filename_to_info.get(filename)

    def get_vst_id(self, filename: str) -> str | None:
        """Get VST ID for filename"""
        return self._vst_filename_to_id.get(filename)

    def get_vss_id(self, filename: str) -> str | None:
        """Get VSS ID for filename"""
        return self._vss_filename_to_id.get(filename)

    def get_storage_type(self, filename: str) -> StorageType | None:
        """Get primary storage type for filename"""
        info = self._filename_to_info.get(filename)
        return info.storage_type if info else None

    def has_vst_file(self, filename: str) -> bool:
        """Check if filename is available in VST"""
        return filename in self._vst_filename_to_id

    def has_vss_file(self, filename: str) -> bool:
        """Check if filename is available in VSS"""
        return filename in self._vss_filename_to_id

    def get_all_filenames(self) -> list[str]:
        """Get all available filenames"""
        return list(self._filename_to_info.keys())

    def add_local_files(self, local_files_data: dict[str, dict]) -> None:
        """
        Add local file mappings from local file scan.

        Args:
            local_files_data: Dictionary of local files
                Format: {
                    "filename.mp4": {
                        "filename": "filename.mp4",
                        "duration": 120.5,
                        "full_path": "/path/to/filename.mp4"
                    }
                }
        """
        for filename, file_data in local_files_data.items():
            video_info = VideoFileInfo(
                filename=filename,
                storage_type=StorageType.LOCAL,
                storage_id=filename,  # Use filename as ID for local files
                duration=file_data.get("duration", 0.0),
                local_path=file_data["full_path"],
            )

            self._filename_to_info[filename] = video_info
            logger.info(f"Added local mapping: {filename} -> {file_data['full_path']}")

    def get_files_by_storage_type(self, storage_type: StorageType) -> dict[str, VideoFileInfo]:
        """Get all files of a specific storage type"""
        return {
            filename: info for filename, info in self._filename_to_info.items() if info.storage_type == storage_type
        }

    def clear(self) -> None:
        """Clear all mappings"""
        self._filename_to_info.clear()
        self._vss_filename_to_id.clear()
        self._vst_filename_to_id.clear()
        logger.info("Cleared all file mappings")


# Global instance for use across tools
file_mapping = FileMapping()


async def resolve_video_file(
    filename: str, start_timestamp: float, end_timestamp: float, vst_download_tool: Any = None
) -> tuple[str, bool]:
    """
    Resolves filename to actual file path for video processing.
    Uses global file mapping to determine storage backend and download if needed.

    Args:
        filename: Video filename (e.g., 'camera1.mp4')
        start_timestamp: Start time in seconds
        end_timestamp: End time in seconds
        vst_download_tool: VST download tool (if available)

    Returns:
        Tuple of (actual_file_path, needs_cleanup)
        - actual_file_path: Local file path to use for processing
        - needs_cleanup: Whether the file should be deleted after processing
    """

    # Get file information from global mapping
    file_info = file_mapping.get_file_info(filename)
    if not file_info:
        raise ValueError(f"File '{filename}' not found in available video files")

    logger.info(f"Resolving file: {filename} (storage: {file_info.storage_type.value})")

    if file_info.storage_type == StorageType.VST:
        # download VST file clip to temporary location
        if not vst_download_tool:
            raise ValueError("VST download tool not available but VST file requested")

        # Create temporary file for the clip
        temp_dir = tempfile.mkdtemp(prefix="vst_clip_")
        start_timestamp_ms = int(start_timestamp * 1000)

        end_timestamp_ms = int(end_timestamp * 1000)

        temp_filename = f"clip_{file_info.storage_id}_{start_timestamp_ms}_{end_timestamp_ms}.mp4"

        logger.info(f"Downloading VST clip: {file_info.storage_id} ({start_timestamp}s-{end_timestamp}s)")

        # Download clip from VST
        download_result = await vst_download_tool.ainvoke(
            input={
                "video_id": file_info.storage_id,
                "filename": temp_filename,
                "start_time": start_timestamp_ms,
                "end_time": end_timestamp_ms,
                "container": "mp4",
                "asset_path": temp_dir,
            }
        )

        actual_file_path = download_result.local_file_path
        logger.info(f"Downloaded VST clip to: {actual_file_path}")
        return actual_file_path, True  # need to cleanup

    elif file_info.storage_type == StorageType.LOCAL:
        # For Local file return direct local path
        local_path = file_info.local_path
        if not local_path or not os.path.exists(local_path):
            raise ValueError(f"Local file not found: {local_path}")
        logger.info(f"Using local file: {filename} -> {local_path}")
        return local_path, False  # No cleanup needed
    elif file_info.storage_type == StorageType.VSS:
        raise NotImplementedError("VSS storage type not yet supported for video file resolution")
    else:
        raise ValueError(f"Unknown storage type: {file_info.storage_type}")
