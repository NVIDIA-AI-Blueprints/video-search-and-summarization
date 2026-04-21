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

    def test_config_creation(self):
        config = VideoUploadURLConfig(
            vst_external_url="http://localhost:30888",
            agent_base_url="http://localhost:8000",
        )
        assert config.vst_external_url == "http://localhost:30888"
        assert config.agent_base_url == "http://localhost:8000"

    def test_config_missing_vst_base_url(self):
        with pytest.raises(ValidationError):
            VideoUploadURLConfig(
                agent_base_url="http://localhost:8000",
            )

    def test_config_missing_agent_base_url(self):
        with pytest.raises(ValidationError):
            VideoUploadURLConfig(
                vst_external_url="http://localhost:30888",
            )


class TestVideoUploadURLInput:
    """Test VideoUploadURLInput model."""

    def test_input_basic(self):
        input_data = VideoUploadURLInput(filename="video.mp4")
        assert input_data.filename == "video.mp4"
        assert input_data.embedding is False

    def test_input_with_embedding(self):
        input_data = VideoUploadURLInput(filename="video.mp4", embedding=True)
        assert input_data.embedding is True

    def test_input_empty_filename_fails(self):
        with pytest.raises(ValidationError):
            VideoUploadURLInput(filename="")

    def test_input_with_extension(self):
        input_data = VideoUploadURLInput(filename="my_video.mp4")
        assert input_data.filename == "my_video.mp4"

    def test_input_without_extension(self):
        input_data = VideoUploadURLInput(filename="my_video")
        assert input_data.filename == "my_video"


class TestVideoUploadURLOutput:
    """Test VideoUploadURLOutput model."""

    def test_output_creation(self):
        output = VideoUploadURLOutput(
            url="http://localhost:30888/vst/api/v1/storage/file/video/2025-01-01T00:00:00.000Z"
        )
        assert "video" in output.url

    def test_output_embedding_url(self):
        output = VideoUploadURLOutput(url="http://localhost:8000/api/v1/videos-for-search/my_video")
        assert "videos-for-search" in output.url

    def test_output_serialization(self):
        output = VideoUploadURLOutput(url="http://test.com/video")
        json_str = output.model_dump_json()
        assert "url" in json_str
        assert "http://test.com/video" in json_str
