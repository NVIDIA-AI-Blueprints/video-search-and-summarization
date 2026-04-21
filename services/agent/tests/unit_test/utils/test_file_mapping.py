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
"""Tests for vss_agents/utils/file_mapping.py."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.utils.file_mapping import FileMapping
from vss_agents.utils.file_mapping import StorageType
from vss_agents.utils.file_mapping import VideoFileInfo
from vss_agents.utils.file_mapping import resolve_video_file


class TestStorageType:
    """Tests for StorageType enum."""

    def test_storage_type_values(self):
        """Test StorageType enum values."""
        assert StorageType.VST.value == "vst"
        assert StorageType.VSS.value == "vss"
        assert StorageType.LOCAL.value == "local"


class TestVideoFileInfo:
    """Tests for VideoFileInfo dataclass."""

    def test_create_video_file_info(self):
        """Test creating VideoFileInfo."""
        info = VideoFileInfo(
            filename="test.mp4",
            storage_type=StorageType.VST,
            storage_id="vst-123",
            duration=120.5,
            sensor_id="sensor-001",
            timestamp=1234567890,
            local_path=None,
        )
        assert info.filename == "test.mp4"
        assert info.storage_type == StorageType.VST
        assert info.storage_id == "vst-123"
        assert info.duration == 120.5
        assert info.sensor_id == "sensor-001"
        assert info.timestamp == 1234567890

    def test_video_file_info_defaults(self):
        """Test VideoFileInfo with default values."""
        info = VideoFileInfo(
            filename="test.mp4",
            storage_type=StorageType.LOCAL,
            storage_id="local-id",
            duration=60.0,
        )
        assert info.sensor_id is None
        assert info.timestamp is None
        assert info.local_path is None


class TestFileMapping:
    """Tests for FileMapping class."""

    def test_init(self):
        """Test FileMapping initialization."""
        fm = FileMapping()
        assert fm._filename_to_info == {}
        assert fm._vss_filename_to_id == {}
        assert fm._vst_filename_to_id == {}

    def test_add_vst_files(self):
        """Test adding VST file mappings."""
        fm = FileMapping()
        vst_data = {
            "vst-123": {
                "filename": "camera1.mp4",
                "duration": 120.0,
                "sensor_id": "sensor-001",
                "timestamp": 1234567890,
            },
            "vst-456": {
                "filename": "camera2.mp4",
                "duration": 180.0,
            },
        }
        fm.add_vst_files(vst_data)

        assert fm.has_vst_file("camera1.mp4")
        assert fm.has_vst_file("camera2.mp4")
        assert fm.get_vst_id("camera1.mp4") == "vst-123"
        assert fm.get_vst_id("camera2.mp4") == "vst-456"

    def test_add_vss_files(self):
        """Test adding VSS file mappings."""
        fm = FileMapping()
        vss_data = {
            "vss-123": "video1.mp4",
            "vss-456": "video2.mp4",
        }
        fm.add_vss_files(vss_data)

        assert fm.has_vss_file("video1.mp4")
        assert fm.has_vss_file("video2.mp4")
        assert fm.get_vss_id("video1.mp4") == "vss-123"
        assert fm.get_vss_id("video2.mp4") == "vss-456"

    def test_add_local_files(self):
        """Test adding local file mappings."""
        fm = FileMapping()
        local_data = {
            "local1.mp4": {
                "filename": "local1.mp4",
                "duration": 60.0,
                "full_path": "/videos/local1.mp4",
            },
        }
        fm.add_local_files(local_data)

        info = fm.get_file_info("local1.mp4")
        assert info is not None
        assert info.storage_type == StorageType.LOCAL
        assert info.local_path == "/videos/local1.mp4"

    def test_get_file_info(self):
        """Test getting file info."""
        fm = FileMapping()
        fm.add_vst_files(
            {
                "vst-123": {
                    "filename": "test.mp4",
                    "duration": 100.0,
                }
            }
        )

        info = fm.get_file_info("test.mp4")
        assert info is not None
        assert info.filename == "test.mp4"
        assert info.storage_type == StorageType.VST

    def test_get_file_info_not_found(self):
        """Test getting file info for nonexistent file."""
        fm = FileMapping()
        info = fm.get_file_info("nonexistent.mp4")
        assert info is None

    def test_get_storage_type(self):
        """Test getting storage type."""
        fm = FileMapping()
        fm.add_vst_files(
            {
                "vst-123": {
                    "filename": "vst-file.mp4",
                    "duration": 100.0,
                }
            }
        )

        assert fm.get_storage_type("vst-file.mp4") == StorageType.VST
        assert fm.get_storage_type("nonexistent.mp4") is None

    def test_get_all_filenames(self):
        """Test getting all filenames."""
        fm = FileMapping()
        fm.add_vst_files(
            {
                "vst-1": {"filename": "a.mp4", "duration": 60.0},
                "vst-2": {"filename": "b.mp4", "duration": 60.0},
            }
        )

        filenames = fm.get_all_filenames()
        assert "a.mp4" in filenames
        assert "b.mp4" in filenames

    def test_get_files_by_storage_type(self):
        """Test getting files by storage type."""
        fm = FileMapping()
        fm.add_vst_files(
            {
                "vst-1": {"filename": "vst.mp4", "duration": 60.0},
            }
        )
        fm.add_local_files(
            {
                "local.mp4": {"filename": "local.mp4", "duration": 60.0, "full_path": "/local.mp4"},
            }
        )

        vst_files = fm.get_files_by_storage_type(StorageType.VST)
        local_files = fm.get_files_by_storage_type(StorageType.LOCAL)

        assert "vst.mp4" in vst_files
        assert "local.mp4" in local_files
        assert len(vst_files) == 1
        assert len(local_files) == 1

    def test_clear(self):
        """Test clearing all mappings."""
        fm = FileMapping()
        fm.add_vst_files({"vst-1": {"filename": "test.mp4", "duration": 60.0}})
        fm.clear()

        assert fm.get_all_filenames() == []
        assert not fm.has_vst_file("test.mp4")

    def test_has_vst_file_false(self):
        """Test has_vst_file returns False for nonexistent file."""
        fm = FileMapping()
        assert not fm.has_vst_file("nonexistent.mp4")

    def test_has_vss_file_false(self):
        """Test has_vss_file returns False for nonexistent file."""
        fm = FileMapping()
        assert not fm.has_vss_file("nonexistent.mp4")


class TestResolveVideoFile:
    """Tests for resolve_video_file function."""

    @pytest.mark.asyncio
    async def test_resolve_local_file(self, tmp_path):
        """Test resolving a local video file."""
        # Create a temp file
        video_file = tmp_path / "test.mp4"
        video_file.write_text("fake video content")

        # Add to file mapping
        test_mapping = FileMapping()
        test_mapping.add_local_files(
            {
                "test.mp4": {
                    "filename": "test.mp4",
                    "duration": 60.0,
                    "full_path": str(video_file),
                }
            }
        )

        with patch("vss_agents.utils.file_mapping.file_mapping", test_mapping):
            path, needs_cleanup = await resolve_video_file("test.mp4", 0.0, 10.0)

        assert path == str(video_file)
        assert not needs_cleanup

    @pytest.mark.asyncio
    async def test_resolve_file_not_found(self):
        """Test resolving nonexistent file."""
        test_mapping = FileMapping()

        with patch("vss_agents.utils.file_mapping.file_mapping", test_mapping):
            with pytest.raises(ValueError, match="not found"):
                await resolve_video_file("nonexistent.mp4", 0.0, 10.0)

    @pytest.mark.asyncio
    async def test_resolve_vst_file_no_tool(self):
        """Test resolving VST file without download tool raises error."""
        test_mapping = FileMapping()
        test_mapping.add_vst_files(
            {
                "vst-123": {
                    "filename": "vst-file.mp4",
                    "duration": 60.0,
                }
            }
        )

        with patch("vss_agents.utils.file_mapping.file_mapping", test_mapping):
            with pytest.raises(ValueError, match="VST download tool not available"):
                await resolve_video_file("vst-file.mp4", 0.0, 10.0, vst_download_tool=None)

    @pytest.mark.asyncio
    async def test_resolve_local_file_not_exists(self):
        """Test resolving local file that doesn't exist on disk."""
        test_mapping = FileMapping()
        test_mapping.add_local_files(
            {
                "missing.mp4": {
                    "filename": "missing.mp4",
                    "duration": 60.0,
                    "full_path": "/nonexistent/path/missing.mp4",
                }
            }
        )

        with patch("vss_agents.utils.file_mapping.file_mapping", test_mapping):
            with pytest.raises(ValueError, match="Local file not found"):
                await resolve_video_file("missing.mp4", 0.0, 10.0)

    @pytest.mark.asyncio
    async def test_resolve_vst_file_with_tool(self):
        """Test resolving VST file with download tool (covers lines 216-239)."""
        test_mapping = FileMapping()
        test_mapping.add_vst_files(
            {
                "vst-123": {
                    "filename": "vst-video.mp4",
                    "duration": 60.0,
                }
            }
        )

        # Mock the download tool
        mock_download_tool = AsyncMock()
        mock_result = MagicMock()
        mock_result.local_file_path = "/tmp/downloaded_clip.mp4"
        mock_download_tool.ainvoke = AsyncMock(return_value=mock_result)

        with patch("vss_agents.utils.file_mapping.file_mapping", test_mapping):
            with patch("tempfile.mkdtemp", return_value="/tmp/vst_clip_test"):
                path, needs_cleanup = await resolve_video_file(
                    "vst-video.mp4", 0.0, 10.0, vst_download_tool=mock_download_tool
                )

        assert path == "/tmp/downloaded_clip.mp4"
        assert needs_cleanup is True

        # Verify the download was called with correct parameters
        mock_download_tool.ainvoke.assert_called_once()
        call_input = mock_download_tool.ainvoke.call_args[1]["input"]
        assert call_input["video_id"] == "vst-123"
        assert call_input["start_time"] == 0  # 0.0 * 1000
        assert call_input["end_time"] == 10000  # 10.0 * 1000

    @pytest.mark.asyncio
    async def test_resolve_vss_file_not_implemented(self):
        """Test resolving VSS file raises NotImplementedError (covers lines 248-249)."""
        test_mapping = FileMapping()
        test_mapping.add_vss_files(
            {
                "vss-123": "vss-video.mp4",
            }
        )

        with patch("vss_agents.utils.file_mapping.file_mapping", test_mapping):
            with pytest.raises(NotImplementedError, match="VSS storage type not yet supported"):
                await resolve_video_file("vss-video.mp4", 0.0, 10.0)
