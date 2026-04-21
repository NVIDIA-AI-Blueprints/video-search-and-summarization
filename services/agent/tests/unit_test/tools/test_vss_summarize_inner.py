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
"""Tests for vss_summarize inner function via generator invocation."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import uuid

import pytest

from vss_agents.tools.vss_summarize import VSSSummarizeConfig
from vss_agents.tools.vss_summarize import VSSSummarizeInput
from vss_agents.tools.vss_summarize import VSSSummarizeOutput
from vss_agents.tools.vss_summarize import vss_summarize


class TestVSSSummarizeInner:
    """Test vss_summarize inner function."""

    @pytest.fixture
    def config(self):
        return VSSSummarizeConfig(
            backend_url="http://localhost:31000",
            max_concurrency=4,
            max_num_frames_per_chunk=8,
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_summarize_success(self, config, mock_builder):
        # Mock requests.get for model list
        mock_requests_response = MagicMock()
        mock_requests_response.status_code = 200
        mock_requests_response.json.return_value = {"data": [{"id": "cosmos-vlm"}]}

        # Mock aiohttp session for summarize call
        mock_aiohttp_response = MagicMock()
        mock_aiohttp_response.status = 200
        mock_aiohttp_response.json = AsyncMock(
            return_value={"choices": [{"message": {"content": "Summary: A person walks across the lot."}}]}
        )
        mock_aiohttp_cm = AsyncMock()
        mock_aiohttp_cm.__aenter__ = AsyncMock(return_value=mock_aiohttp_response)
        mock_aiohttp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_aiohttp_cm

        with patch("requests.get", return_value=mock_requests_response):
            with patch("aiohttp.ClientSession", return_value=mock_session):
                gen = vss_summarize.__wrapped__(config, mock_builder)
                fi = await gen.__anext__()
                inner_fn = fi.single_fn

        file_id = uuid.uuid4()
        inp = VSSSummarizeInput(
            id=file_id,
            prompt="Describe the scene",
            video_duration=60.0,
        )
        result = await inner_fn(inp)

        assert isinstance(result, VSSSummarizeOutput)
        assert "person" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_summarize_with_step_size(self, config, mock_builder):
        mock_requests_response = MagicMock()
        mock_requests_response.status_code = 200
        mock_requests_response.json.return_value = {"data": [{"id": "vlm-model"}]}

        mock_aiohttp_response = MagicMock()
        mock_aiohttp_response.status = 200
        mock_aiohttp_response.json = AsyncMock(return_value={"choices": [{"message": {"content": "Detailed summary"}}]})
        mock_aiohttp_cm = AsyncMock()
        mock_aiohttp_cm.__aenter__ = AsyncMock(return_value=mock_aiohttp_response)
        mock_aiohttp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_aiohttp_cm

        with patch("requests.get", return_value=mock_requests_response):
            with patch("aiohttp.ClientSession", return_value=mock_session):
                gen = vss_summarize.__wrapped__(config, mock_builder)
                fi = await gen.__anext__()
                inner_fn = fi.single_fn

        file_id = uuid.uuid4()
        inp = VSSSummarizeInput(
            id=file_id,
            prompt="Describe",
            video_duration=60.0,
            step_size=1.0,
        )
        result = await inner_fn(inp)
        assert isinstance(result, VSSSummarizeOutput)
        assert result.step_size == 1.0

    @pytest.mark.asyncio
    async def test_summarize_api_error(self, config, mock_builder):
        mock_requests_response = MagicMock()
        mock_requests_response.status_code = 200
        mock_requests_response.json.return_value = {"data": [{"id": "vlm"}]}

        mock_aiohttp_response = MagicMock()
        mock_aiohttp_response.status = 500
        mock_aiohttp_response.text = "Internal server error"
        mock_aiohttp_cm = AsyncMock()
        mock_aiohttp_cm.__aenter__ = AsyncMock(return_value=mock_aiohttp_response)
        mock_aiohttp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_aiohttp_cm

        with patch("requests.get", return_value=mock_requests_response):
            with patch("aiohttp.ClientSession", return_value=mock_session):
                gen = vss_summarize.__wrapped__(config, mock_builder)
                fi = await gen.__anext__()
                inner_fn = fi.single_fn

        file_id = uuid.uuid4()
        inp = VSSSummarizeInput(id=file_id, prompt="test", video_duration=60.0)
        result = await inner_fn(inp)
        assert result.summary == ""

    @pytest.mark.asyncio
    async def test_summarize_empty_choices(self, config, mock_builder):
        mock_requests_response = MagicMock()
        mock_requests_response.status_code = 200
        mock_requests_response.json.return_value = {"data": [{"id": "vlm"}]}

        mock_aiohttp_response = MagicMock()
        mock_aiohttp_response.status = 200
        mock_aiohttp_response.json = AsyncMock(return_value={"choices": []})
        mock_aiohttp_cm = AsyncMock()
        mock_aiohttp_cm.__aenter__ = AsyncMock(return_value=mock_aiohttp_response)
        mock_aiohttp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_aiohttp_cm

        with patch("requests.get", return_value=mock_requests_response):
            with patch("aiohttp.ClientSession", return_value=mock_session):
                gen = vss_summarize.__wrapped__(config, mock_builder)
                fi = await gen.__anext__()
                inner_fn = fi.single_fn

        file_id = uuid.uuid4()
        inp = VSSSummarizeInput(id=file_id, prompt="test", video_duration=60.0)
        result = await inner_fn(inp)
        assert result.summary == ""

    @pytest.mark.asyncio
    async def test_summarize_connection_error(self, config, mock_builder):
        mock_requests_response = MagicMock()
        mock_requests_response.status_code = 200
        mock_requests_response.json.return_value = {"data": [{"id": "vlm"}]}

        mock_session = MagicMock()
        mock_aiohttp_cm = AsyncMock()
        mock_aiohttp_cm.__aenter__ = AsyncMock(side_effect=ConnectionError("cannot connect"))
        mock_aiohttp_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.post.return_value = mock_aiohttp_cm

        with patch("requests.get", return_value=mock_requests_response):
            with patch("aiohttp.ClientSession", return_value=mock_session):
                gen = vss_summarize.__wrapped__(config, mock_builder)
                fi = await gen.__anext__()
                inner_fn = fi.single_fn

        file_id = uuid.uuid4()
        inp = VSSSummarizeInput(id=file_id, prompt="test", video_duration=60.0)
        result = await inner_fn(inp)
        assert result.summary == ""

    @pytest.mark.asyncio
    async def test_init_model_error(self, config, mock_builder):
        mock_requests_response = MagicMock()
        mock_requests_response.status_code = 500
        mock_requests_response.text = "Error"

        with patch("requests.get", return_value=mock_requests_response):
            with pytest.raises(RuntimeError, match="Failed to get model"):
                gen = vss_summarize.__wrapped__(config, mock_builder)
                await gen.__anext__()
