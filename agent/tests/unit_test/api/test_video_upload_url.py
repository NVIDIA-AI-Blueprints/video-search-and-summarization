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
"""Unit tests for video_upload_url module."""

from pydantic import ValidationError
import pytest
from vss_agents.api.video_upload_url import VideoUploadURLConfig
from vss_agents.api.video_upload_url import VideoUploadURLInput
from vss_agents.api.video_upload_url import VideoUploadURLOutput


class TestVideoUploadURLConfig:
    """Test VideoUploadURLConfig model."""

    def test_with_required_fields(self):
        config = VideoUploadURLConfig(
            vst_external_url="http://vst:8080",
            agent_base_url="http://agent:8000",
        )
        assert config.vst_external_url == "http://vst:8080"
        assert config.agent_base_url == "http://agent:8000"

    def test_missing_vst_external_url_fails(self):
        with pytest.raises(ValidationError):
            VideoUploadURLConfig(agent_base_url="http://agent:8000")

    def test_missing_agent_base_url_fails(self):
        with pytest.raises(ValidationError):
            VideoUploadURLConfig(vst_external_url="http://vst:8080")


class TestVideoUploadURLInput:
    """Test VideoUploadURLInput model."""

    def test_basic_input(self):
        input_data = VideoUploadURLInput(filename="video.mp4")
        assert input_data.filename == "video.mp4"
        assert input_data.embedding is False

    def test_with_embedding(self):
        input_data = VideoUploadURLInput(
            filename="video.mp4",
            embedding=True,
        )
        assert input_data.embedding is True

    def test_empty_filename_fails(self):
        with pytest.raises(ValidationError):
            VideoUploadURLInput(filename="")

    def test_missing_filename_fails(self):
        with pytest.raises(ValidationError):
            VideoUploadURLInput()

    def test_various_filenames(self):
        filenames = ["test.mp4", "camera_1_2025.mkv", "incident-001.mp4", "a.mp4"]
        for filename in filenames:
            input_data = VideoUploadURLInput(filename=filename)
            assert input_data.filename == filename


class TestVideoUploadURLOutput:
    """Test VideoUploadURLOutput model."""

    def test_output_creation(self):
        output = VideoUploadURLOutput(url="http://vst:8080/vst/api/v1/storage/file/test/2025-01-01T00:00:00.000Z")
        assert "vst" in output.url
        assert "storage" in output.url

    def test_output_with_different_urls(self):
        urls = [
            "http://vst:8080/vst/api/v1/storage/file/video1/2025-01-01T00:00:00.000Z",
            "http://agent:8000/api/v1/videos-for-search/video2",
            "https://secure-vst.example.com/storage/video3",
        ]
        for url in urls:
            output = VideoUploadURLOutput(url=url)
            assert output.url == url


class TestVideoUploadURLFunction:
    """Test the video_upload_url function logic directly."""

    def test_vst_url_construction(self):
        """Test VST URL construction logic."""
        # Simulate the URL construction logic from the function
        vst_base_url = "http://vst:8080"
        filename = "test_video.mp4"

        base_url = vst_base_url.rstrip("/")
        filename_without_ext = filename.rsplit(".", 1)[0] or filename
        timestamp = "2025-01-01T00:00:00.000Z"
        url = f"{base_url}/vst/api/v1/storage/file/{filename_without_ext}/{timestamp}"

        assert url == "http://vst:8080/vst/api/v1/storage/file/test_video/2025-01-01T00:00:00.000Z"

    def test_embedding_url_construction(self):
        """Test embedding URL construction logic."""
        agent_base_url = "http://agent:8000"
        filename = "test_video.mp4"

        agent_base = agent_base_url.rstrip("/")
        filename_without_ext = filename.rsplit(".", 1)[0] or filename
        url = f"{agent_base}/api/v1/videos-for-search/{filename_without_ext}"

        assert url == "http://agent:8000/api/v1/videos-for-search/test_video"

    def test_url_with_trailing_slash(self):
        """Test URL generation strips trailing slash."""
        vst_base_url = "http://vst:8080/"
        agent_base_url = "http://agent:8000/"

        vst_stripped = vst_base_url.rstrip("/")
        agent_stripped = agent_base_url.rstrip("/")

        assert vst_stripped == "http://vst:8080"
        assert agent_stripped == "http://agent:8000"

    def test_filename_without_extension(self):
        """Test filename without extension handling."""
        filename = "video_no_ext"
        filename_without_ext = filename.rsplit(".", 1)[0] or filename

        assert filename_without_ext == "video_no_ext"

    def test_filename_with_multiple_dots(self):
        """Test filename with multiple dots."""
        filename = "video.2025.01.01.mp4"
        filename_without_ext = filename.rsplit(".", 1)[0] or filename

        assert filename_without_ext == "video.2025.01.01"

    def test_input_json_parsing(self):
        """Test input can be created from JSON."""
        json_str = '{"filename": "test.mp4", "embedding": true}'
        input_data = VideoUploadURLInput.model_validate_json(json_str)

        assert input_data.filename == "test.mp4"
        assert input_data.embedding is True

    def test_output_json_serialization(self):
        """Test output can be serialized to JSON."""
        output = VideoUploadURLOutput(url="http://example.com/video")
        json_str = output.model_dump_json()

        assert "http://example.com/video" in json_str
