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
"""Unit tests for VST tools modules (vst_download, vst_files)."""

from vss_agents.tools.vst_download import VSTDownloadConfig
from vss_agents.tools.vst_download import VSTDownloadInput
from vss_agents.tools.vst_download import VSTDownloadOutput
from vss_agents.tools.vst_files import VSTFilesConfig
from vss_agents.tools.vst_files import VSTFilesInput


class TestVSTDownloadConfig:
    """Test VSTDownloadConfig model."""

    def test_with_required_field(self):
        config = VSTDownloadConfig(vst_backend_url="http://vst.example.com")
        assert config.vst_backend_url == "http://vst.example.com"
        assert config.download_timeout == 300
        assert config.chunk_size == 8192

    def test_custom_values(self):
        config = VSTDownloadConfig(
            vst_backend_url="http://vst.example.com",
            download_timeout=600,
            chunk_size=16384,
        )
        assert config.download_timeout == 600
        assert config.chunk_size == 16384


class TestVSTDownloadInput:
    """Test VSTDownloadInput model."""

    def test_basic_input(self):
        input_data = VSTDownloadInput(
            video_id="video-123",
            filename="test.mp4",
            start_time=0,
            end_time=10000,
            asset_path="/tmp/videos",
        )
        assert input_data.video_id == "video-123"
        assert input_data.filename == "test.mp4"
        assert input_data.start_time == 0
        assert input_data.end_time == 10000
        assert input_data.container == "mp4"
        assert input_data.asset_path == "/tmp/videos"

    def test_with_custom_container(self):
        input_data = VSTDownloadInput(
            video_id="video-123",
            filename="test.mkv",
            start_time=5000,
            end_time=15000,
            asset_path="/tmp/videos",
            container="mkv",
        )
        assert input_data.container == "mkv"


class TestVSTDownloadOutput:
    """Test VSTDownloadOutput model."""

    def test_output_creation(self):
        output = VSTDownloadOutput(
            local_file_path="/tmp/videos/test.mp4",
            file_size_bytes=1024000,
            duration_ms=10000,
        )
        assert output.local_file_path == "/tmp/videos/test.mp4"
        assert output.file_size_bytes == 1024000
        assert output.duration_ms == 10000
        assert output.cleanup_required is True

    def test_output_no_cleanup(self):
        output = VSTDownloadOutput(
            local_file_path="/tmp/videos/test.mp4",
            file_size_bytes=1024000,
            duration_ms=10000,
            cleanup_required=False,
        )
        assert output.cleanup_required is False


class TestVSTFilesConfig:
    """Test VSTFilesConfig model."""

    def test_with_required_field(self):
        config = VSTFilesConfig(vst_backend_url="http://vst.example.com")
        assert config.vst_backend_url == "http://vst.example.com"
        assert config.timeout == 30
        assert config.use_mock is True
        assert config.offset == 0
        assert config.limit == 100
        assert "b7a1c1f2-9c0e-4d8d-8a6a-2e5f7d2e3c1b" in config.mock_video_list

    def test_custom_values(self):
        config = VSTFilesConfig(
            vst_backend_url="http://vst.example.com",
            timeout=60,
            use_mock=False,
            offset=10,
            limit=50,
        )
        assert config.timeout == 60
        assert config.use_mock is False
        assert config.offset == 10
        assert config.limit == 50


class TestVSTFilesInput:
    """Test VSTFilesInput model."""

    def test_basic_input(self):
        input_data = VSTFilesInput(question="Show me all videos from today")
        assert input_data.question == "Show me all videos from today"
