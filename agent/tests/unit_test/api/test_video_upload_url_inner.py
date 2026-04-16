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
"""Tests for video_upload_url inner function via generator invocation."""

from unittest.mock import MagicMock

import pytest

from vss_agents.api.video_upload_url import VideoUploadURLConfig
from vss_agents.api.video_upload_url import VideoUploadURLInput
from vss_agents.api.video_upload_url import VideoUploadURLOutput
from vss_agents.api.video_upload_url import video_upload_url


class TestVideoUploadUrlInner:
    """Test the inner _video_upload_url function."""

    @pytest.fixture
    def config(self):
        return VideoUploadURLConfig(
            vst_external_url="http://1.2.3.4:30888",
            agent_base_url="http://10.0.0.1:8000",
        )

    @pytest.fixture
    def mock_builder(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_upload_url_normal(self, config, mock_builder):
        gen = video_upload_url.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = VideoUploadURLInput(filename="test_video.mp4")
        result = await inner_fn(inp)

        assert isinstance(result, VideoUploadURLOutput)
        assert "1.2.3.4:30888" in result.url
        assert "test_video" in result.url
        assert "2025-01-01T00:00:00.000Z" in result.url

    @pytest.mark.asyncio
    async def test_upload_url_embedding(self, config, mock_builder):
        gen = video_upload_url.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = VideoUploadURLInput(filename="test_video.mp4", embedding=True)
        result = await inner_fn(inp)

        assert isinstance(result, VideoUploadURLOutput)
        assert "10.0.0.1:8000" in result.url
        assert "videos-for-search" in result.url
        assert "test_video" in result.url

    @pytest.mark.asyncio
    async def test_upload_url_filename_without_extension(self, config, mock_builder):
        gen = video_upload_url.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = VideoUploadURLInput(filename="my_video")
        result = await inner_fn(inp)

        assert isinstance(result, VideoUploadURLOutput)
        assert "my_video" in result.url

    @pytest.mark.asyncio
    async def test_upload_url_trailing_slash_stripped(self, mock_builder):
        config = VideoUploadURLConfig(
            vst_external_url="http://1.2.3.4:30888/",
            agent_base_url="http://10.0.0.1:8000/",
        )
        gen = video_upload_url.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = VideoUploadURLInput(filename="test.mp4")
        result = await inner_fn(inp)
        assert "//" not in result.url.replace("http://", "")

    @pytest.mark.asyncio
    async def test_converters_registered(self, config, mock_builder):
        gen = video_upload_url.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        assert function_info is not None
        assert function_info.converters is not None
        assert len(function_info.converters) > 0
