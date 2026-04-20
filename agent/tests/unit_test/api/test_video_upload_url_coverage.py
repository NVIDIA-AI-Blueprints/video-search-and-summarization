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
"""Additional unit tests for video_upload_url module to improve coverage."""

from pydantic import ValidationError
import pytest
from vss_agents.api.video_upload_url import VideoUploadURLConfig
from vss_agents.api.video_upload_url import VideoUploadURLInput
from vss_agents.api.video_upload_url import VideoUploadURLOutput


class TestVideoUploadURLConfig:
    """Test VideoUploadURLConfig model."""

    def test_required_fields(self):
        config = VideoUploadURLConfig(
            vst_external_url="http://1.2.3.4:30888",
            agent_base_url="http://10.0.0.1:8000",
        )
        assert config.vst_external_url == "http://1.2.3.4:30888"
        assert config.agent_base_url == "http://10.0.0.1:8000"

    def test_missing_vst_url_raises(self):
        with pytest.raises(ValidationError):
            VideoUploadURLConfig(agent_base_url="http://10.0.0.1:8000")

    def test_missing_agent_url_raises(self):
        with pytest.raises(ValidationError):
            VideoUploadURLConfig(vst_external_url="http://1.2.3.4:30888")


class TestVideoUploadURLInput:
    """Test VideoUploadURLInput model."""

    def test_basic(self):
        inp = VideoUploadURLInput(filename="video.mp4")
        assert inp.filename == "video.mp4"
        assert inp.embedding is False

    def test_with_embedding(self):
        inp = VideoUploadURLInput(filename="video.mp4", embedding=True)
        assert inp.embedding is True

    def test_empty_filename_raises(self):
        with pytest.raises(ValidationError):
            VideoUploadURLInput(filename="")

    def test_missing_filename_raises(self):
        with pytest.raises(ValidationError):
            VideoUploadURLInput()


class TestVideoUploadURLOutput:
    """Test VideoUploadURLOutput model."""

    def test_basic(self):
        output = VideoUploadURLOutput(url="http://example.com/upload")
        assert output.url == "http://example.com/upload"

    def test_missing_url_raises(self):
        with pytest.raises(ValidationError):
            VideoUploadURLOutput()
