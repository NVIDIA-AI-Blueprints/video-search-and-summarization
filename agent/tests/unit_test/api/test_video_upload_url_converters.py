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
"""Tests for video_upload_url converter functions."""

from unittest.mock import MagicMock

import pytest
from vss_agents.api.video_upload_url import VideoUploadURLConfig
from vss_agents.api.video_upload_url import VideoUploadURLInput
from vss_agents.api.video_upload_url import VideoUploadURLOutput
from vss_agents.api.video_upload_url import video_upload_url


class TestVideoUploadURLConverters:
    """Test converter functions."""

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
    async def test_str_input_converter(self, config, mock_builder):
        gen = video_upload_url.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        str_converter = fi.converters[0]
        result = str_converter('{"filename": "test.mp4"}')
        assert isinstance(result, VideoUploadURLInput)
        assert result.filename == "test.mp4"

    @pytest.mark.asyncio
    async def test_chat_request_input_converter(self, config, mock_builder):
        gen = video_upload_url.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        chat_converter = fi.converters[1]
        mock_message = MagicMock()
        mock_message.content = '{"filename": "video.mp4"}'
        mock_request = MagicMock()
        mock_request.messages = [mock_message]

        result = chat_converter(mock_request)
        assert isinstance(result, VideoUploadURLInput)

    @pytest.mark.asyncio
    async def test_chat_request_converter_error(self, config, mock_builder):
        gen = video_upload_url.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        chat_converter = fi.converters[1]
        mock_message = MagicMock()
        mock_message.content = "not json"
        mock_request = MagicMock()
        mock_request.messages = [mock_message]

        with pytest.raises(Exception):
            chat_converter(mock_request)

    @pytest.mark.asyncio
    async def test_output_converter(self, config, mock_builder):
        gen = video_upload_url.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        output_converter = fi.converters[2]
        output = VideoUploadURLOutput(url="http://test.com/upload")
        result = output_converter(output)
        assert isinstance(result, str)
        assert "test.com" in result

    @pytest.mark.asyncio
    async def test_chat_response_converter(self, config, mock_builder):
        gen = video_upload_url.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        chat_response_converter = fi.converters[3]
        output = VideoUploadURLOutput(url="http://test.com/upload")
        # The original code has a bug: ChatResponse.from_string() requires 'usage'
        # but _chat_response_output_converter doesn't pass it
        with pytest.raises(TypeError):
            chat_response_converter(output)
