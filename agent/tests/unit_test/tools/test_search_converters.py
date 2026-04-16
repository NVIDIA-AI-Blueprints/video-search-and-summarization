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
"""Tests for search converters and remaining inner function edge cases."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from vss_agents.tools.embed_search import EmbedSearchOutput
from vss_agents.tools.embed_search import EmbedSearchResultItem
from vss_agents.tools.search import SearchConfig
from vss_agents.tools.search import SearchInput
from vss_agents.tools.search import SearchOutput
from vss_agents.tools.search import SearchResult
from vss_agents.tools.search import search


def _make_embed_output(results):
    """Helper to build an EmbedSearchOutput."""
    items = []
    for r in results:
        items.append(
            EmbedSearchResultItem(
                video_name=r.get("video_name", ""),
                description=r.get("description", ""),
                start_time=r.get("start_time", ""),
                end_time=r.get("end_time", ""),
                sensor_id=r.get("sensor_id", "s1"),
                screenshot_url=r.get("screenshot_url", ""),
                similarity_score=float(r.get("similarity_score", 0.0)),
            )
        )
    return EmbedSearchOutput(query_embedding=[0.1, 0.2], results=items)


class TestSearchConverters:
    """Test search converter functions via registered converters."""

    @pytest.fixture
    def config(self):
        return SearchConfig(
            embed_search_tool="embed_search", agent_mode_llm="gpt-4o", vst_internal_url="http://localhost:30888"
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_str_input_converter(self, config, mock_builder):
        mock_builder.get_function.return_value = AsyncMock()
        mock_builder.get_llm.return_value = AsyncMock()
        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        # Find converters (they're registered functions)
        converters = fi.converters
        assert len(converters) >= 4

        # Test str converter (first in list)
        str_converter = converters[0]
        result = str_converter('{"query": "test", "source_type": "video_file", "agent_mode": true}')
        assert isinstance(result, SearchInput)
        assert result.query == "test"

    @pytest.mark.asyncio
    async def test_chat_request_converter(self, config, mock_builder):
        mock_builder.get_function.return_value = AsyncMock()
        mock_builder.get_llm.return_value = AsyncMock()
        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        converters = fi.converters
        chat_request_converter = converters[1]

        mock_message = MagicMock()
        mock_message.content = '{"query": "find car", "source_type": "video_file", "agent_mode": false}'
        mock_request = MagicMock()
        mock_request.messages = [mock_message]

        result = chat_request_converter(mock_request)
        assert isinstance(result, SearchInput)
        assert result.query == "find car"

    @pytest.mark.asyncio
    async def test_chat_request_converter_error(self, config, mock_builder):
        mock_builder.get_function.return_value = AsyncMock()
        mock_builder.get_llm.return_value = AsyncMock()
        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        converters = fi.converters
        chat_request_converter = converters[1]

        mock_message = MagicMock()
        mock_message.content = "not valid json"
        mock_request = MagicMock()
        mock_request.messages = [mock_message]

        with pytest.raises(Exception):
            chat_request_converter(mock_request)

    @pytest.mark.asyncio
    async def test_output_converter(self, config, mock_builder):
        mock_builder.get_function.return_value = AsyncMock()
        mock_builder.get_llm.return_value = AsyncMock()
        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        converters = fi.converters
        output_converter = converters[2]

        output = SearchOutput(
            data=[
                SearchResult(
                    video_name="v.mp4",
                    description="d",
                    start_time="t1",
                    end_time="t2",
                    sensor_id="s1",
                    screenshot_url="s",
                    similarity=0.9,
                )
            ]
        )
        result = output_converter(output)
        assert isinstance(result, str)
        assert "v.mp4" in result

    @pytest.mark.asyncio
    async def test_chat_response_converter(self, config, mock_builder):
        mock_builder.get_function.return_value = AsyncMock()
        mock_builder.get_llm.return_value = AsyncMock()
        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        converters = fi.converters
        chat_response_converter = converters[3]

        output = SearchOutput(data=[])
        result = chat_response_converter(output)
        assert result is not None

    @pytest.mark.asyncio
    async def test_chat_response_chunk_converter(self, config, mock_builder):
        mock_builder.get_function.return_value = AsyncMock()
        mock_builder.get_llm.return_value = AsyncMock()
        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        converters = fi.converters
        chat_chunk_converter = converters[4]

        output = SearchOutput(data=[])
        result = chat_chunk_converter(output)
        assert result is not None

    @pytest.mark.asyncio
    async def test_search_dict_output(self, config, mock_builder):
        """Test when embed_search returns a dict."""
        embed_output = _make_embed_output([])
        embed_dict = embed_output.model_dump()
        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_dict
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        inner_fn = fi.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_embed_error_with_meta(self, config, mock_builder):
        """Test error with meta.status attribute."""
        from fastapi import HTTPException

        err = RuntimeError("ES error")
        err.meta = MagicMock()
        err.meta.status = 429
        mock_embed = AsyncMock()
        mock_embed.ainvoke.side_effect = err
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        inner_fn = fi.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        with pytest.raises(HTTPException) as exc_info:
            await inner_fn(inp)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_search_embed_error_with_int_arg(self, config, mock_builder):
        """Test error with int first arg."""
        from fastapi import HTTPException

        err = RuntimeError(502)
        mock_embed = AsyncMock()
        mock_embed.ainvoke.side_effect = err
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        inner_fn = fi.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        with pytest.raises(HTTPException) as exc_info:
            await inner_fn(inp)
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_search_sensor_description_fallback(self, config, mock_builder):
        """Test that description from EmbedSearchResultItem is used."""
        embed_output = _make_embed_output(
            [
                {
                    "video_name": "cam.mp4",
                    "description": "Front entrance",
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T01:00:00Z",
                    "similarity_score": 0.9,
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        inner_fn = fi.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        result = await inner_fn(inp)
        assert result.data[0].description == "Front entrance"

    @pytest.mark.asyncio
    async def test_search_invalid_end_time_iso(self, config, mock_builder):
        """Test handling of result with end_time."""
        embed_output = _make_embed_output(
            [
                {
                    "video_name": "cam.mp4",
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T01:00:00Z",
                    "similarity_score": 0.9,
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        inner_fn = fi.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        result = await inner_fn(inp)
        assert len(result.data) == 1

    @pytest.mark.asyncio
    async def test_search_no_base_timestamp(self, config, mock_builder):
        """Test when no base timestamp is available."""
        embed_output = _make_embed_output(
            [
                {
                    "video_name": "cam.mp4",
                    "start_time": "",
                    "end_time": "",
                    "similarity_score": 0.9,
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        inner_fn = fi.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)
        assert len(result.data) == 1
