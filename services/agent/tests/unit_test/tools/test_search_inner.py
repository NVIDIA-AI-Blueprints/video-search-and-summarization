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
"""Tests for search inner function via generator invocation."""

import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from vss_agents.tools.embed_search import EmbedSearchOutput
from vss_agents.tools.embed_search import EmbedSearchResultItem
from vss_agents.tools.search import SearchConfig
from vss_agents.tools.search import SearchInput
from vss_agents.tools.search import SearchOutput
from vss_agents.tools.search import search


def _make_embed_output_with_results(results):
    """Helper to build an EmbedSearchOutput with search results."""
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
    return EmbedSearchOutput(query_embedding=[0.1, 0.2, 0.3], results=items)


class TestSearchInner:
    """Test the inner _search function."""

    @pytest.fixture
    def config(self):
        return SearchConfig(
            embed_search_tool="embed_search",
            agent_mode_llm="gpt-4o",
            vst_internal_url="http://localhost:30888",
        )

    @pytest.fixture
    def mock_builder(self):
        builder = AsyncMock()
        return builder

    async def _get_inner_fn(self, config, mock_builder, embed_output):
        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed

        mock_llm = AsyncMock()
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        return function_info.single_fn

    @pytest.mark.asyncio
    async def test_basic_search_no_agent_mode(self, config, mock_builder):
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "camera1.mp4",
                    "description": "Test",
                    "start_time": "2025-01-15T10:00:00Z",
                    "end_time": "2025-01-15T10:30:00Z",
                    "screenshot_url": "http://example.com/screenshot.jpg",
                    "similarity_score": 0.95,
                }
            ]
        )
        inner_fn = await self._get_inner_fn(config, mock_builder, embed_output)

        inp = SearchInput(query="find cars", source_type="video_file", agent_mode=False)
        result = await inner_fn(inp)

        assert isinstance(result, SearchOutput)
        assert len(result.data) == 1
        assert result.data[0].video_name == "camera1.mp4"
        assert result.data[0].similarity == 0.95

    @pytest.mark.asyncio
    async def test_search_with_video_sources(self, config, mock_builder):
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam1.mp4",
                    "similarity_score": 0.8,
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T01:00:00Z",
                    "screenshot_url": "",
                }
            ]
        )
        inner_fn = await self._get_inner_fn(config, mock_builder, embed_output)

        inp = SearchInput(
            query="find person",
            source_type="video_file",
            agent_mode=False,
            video_sources=["cam1.mp4"],
            top_k=5,
        )
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_with_timestamps(self, config, mock_builder):
        from datetime import UTC
        from datetime import datetime

        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam.mp4",
                    "similarity_score": 0.9,
                    "start_time": "2025-01-15T10:00:00Z",
                    "end_time": "2025-01-15T10:30:00Z",
                }
            ]
        )
        inner_fn = await self._get_inner_fn(config, mock_builder, embed_output)

        inp = SearchInput(
            query="find car",
            source_type="video_file",
            agent_mode=False,
            timestamp_start=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            timestamp_end=datetime(2025, 1, 15, 11, 0, 0, tzinfo=UTC),
            description="parking lot",
        )
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_no_results(self, config, mock_builder):
        embed_output = EmbedSearchOutput(query_embedding=[], results=[])
        inner_fn = await self._get_inner_fn(config, mock_builder, embed_output)

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)
        assert len(result.data) == 0

    @pytest.mark.asyncio
    async def test_search_empty_video_name_skipped(self, config, mock_builder):
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "",
                    "similarity_score": 0.9,
                }
            ]
        )
        inner_fn = await self._get_inner_fn(config, mock_builder, embed_output)

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        result = await inner_fn(inp)
        assert len(result.data) == 0

    @pytest.mark.asyncio
    async def test_search_string_output(self, config, mock_builder):
        """Test when embed_search returns a JSON string."""
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam.mp4",
                    "similarity_score": 0.9,
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T01:00:00Z",
                }
            ]
        )
        json_str = embed_output.model_dump_json()

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = json_str
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_with_agent_mode(self, config, mock_builder):
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam.mp4",
                    "similarity_score": 0.85,
                    "start_time": "2025-01-01T13:00:00Z",
                    "end_time": "2025-01-01T14:00:00Z",
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed

        mock_llm = AsyncMock()
        mock_llm_response = MagicMock()
        mock_llm_response.content = json.dumps(
            {
                "query": "person pushing cart",
                "description": "endeavor heart",
                "timestamp_start": "2025-01-01T13:00:00Z",
                "timestamp_end": "2025-01-01T14:00:00Z",
                "top_k": 5,
            }
        )
        mock_llm.ainvoke.return_value = mock_llm_response
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="person pushing a cart in endeavor heart", source_type="video_file", agent_mode=True)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_agent_mode_json_code_block(self, config, mock_builder):
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam.mp4",
                    "similarity_score": 0.85,
                    "start_time": "2025-01-01T13:00:00Z",
                    "end_time": "2025-01-01T14:00:00Z",
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed

        mock_llm = AsyncMock()
        mock_llm_response = MagicMock()
        mock_llm_response.content = '```json\n{"query": "test", "video_sources": ["cam1"]}\n```'
        mock_llm.ainvoke.return_value = mock_llm_response
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="test in cam1", source_type="video_file", agent_mode=True)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_agent_mode_code_block_no_json(self, config, mock_builder):
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam.mp4",
                    "similarity_score": 0.8,
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T01:00:00Z",
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed

        mock_llm = AsyncMock()
        mock_llm_response = MagicMock()
        mock_llm_response.content = '```\n{"query": "test"}\n```'
        mock_llm.ainvoke.return_value = mock_llm_response
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=True)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_agent_mode_invalid_json(self, config, mock_builder):
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam.mp4",
                    "similarity_score": 0.8,
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T01:00:00Z",
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed

        mock_llm = AsyncMock()
        mock_llm_response = MagicMock()
        mock_llm_response.content = "not valid json at all"
        mock_llm.ainvoke.return_value = mock_llm_response
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=True)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_agent_mode_llm_error(self, config, mock_builder):
        """Test agent_mode when LLM raises error."""
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam.mp4",
                    "similarity_score": 0.8,
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T01:00:00Z",
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = RuntimeError("LLM error")
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=True)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_embed_value_error(self, config, mock_builder):
        """Test handling ValueError from embed_search."""
        from fastapi import HTTPException

        mock_embed = AsyncMock()
        mock_embed.ainvoke.side_effect = ValueError("Index not found")
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        with pytest.raises(HTTPException) as exc_info:
            await inner_fn(inp)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_search_embed_generic_error(self, config, mock_builder):
        """Test handling generic error from embed_search."""
        from fastapi import HTTPException

        mock_embed = AsyncMock()
        mock_embed.ainvoke.side_effect = RuntimeError("Something went wrong")
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        with pytest.raises(HTTPException) as exc_info:
            await inner_fn(inp)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_search_embed_error_with_status_code(self, config, mock_builder):
        """Test handling error with status_code attribute."""
        from fastapi import HTTPException

        err = RuntimeError("ES error")
        err.status_code = 503
        mock_embed = AsyncMock()
        mock_embed.ainvoke.side_effect = err
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        with pytest.raises(HTTPException) as exc_info:
            await inner_fn(inp)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_search_with_description_in_results(self, config, mock_builder):
        """Test that description is passed through from embed results."""
        embed_output = _make_embed_output_with_results(
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
        inner_fn = await self._get_inner_fn(config, mock_builder, embed_output)

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        result = await inner_fn(inp)
        assert result.data[0].description == "Front entrance"

    @pytest.mark.asyncio
    async def test_search_with_float_timestamps_in_response(self, config, mock_builder):
        """Test handling float start_time and end_time in response."""
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam.mp4",
                    "similarity_score": 0.9,
                    "start_time": "2025-01-01T00:01:40Z",
                    "end_time": "2025-01-01T00:03:20Z",
                }
            ]
        )
        inner_fn = await self._get_inner_fn(config, mock_builder, embed_output)

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)
        assert len(result.data) == 1

    @pytest.mark.asyncio
    async def test_search_agent_mode_with_min_cosine_similarity(self, config, mock_builder):
        """Test agent mode extracting min_cosine_similarity."""
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam.mp4",
                    "similarity_score": 0.9,
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T01:00:00Z",
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed

        mock_llm = AsyncMock()
        mock_llm_response = MagicMock()
        mock_llm_response.content = json.dumps(
            {
                "query": "test",
                "min_cosine_similarity": 0.5,
                "top_k": "invalid",
                "video_sources": "single_video",
            }
        )
        mock_llm.ainvoke.return_value = mock_llm_response
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=True)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_agent_mode_invalid_timestamps(self, config, mock_builder):
        """Test agent mode with invalid extracted timestamps."""
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam.mp4",
                    "similarity_score": 0.9,
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T01:00:00Z",
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed

        mock_llm = AsyncMock()
        mock_llm_response = MagicMock()
        mock_llm_response.content = json.dumps(
            {
                "query": "test",
                "timestamp_start": "invalid-date",
                "timestamp_end": "also-invalid",
                "min_cosine_similarity": "not-a-number",
            }
        )
        mock_llm.ainvoke.return_value = mock_llm_response
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=True)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_agent_mode_json_block_no_closing(self, config, mock_builder):
        """Test agent mode with json block without closing markers."""
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "cam.mp4",
                    "similarity_score": 0.9,
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T01:00:00Z",
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed

        mock_llm = AsyncMock()
        mock_llm_response = MagicMock()
        mock_llm_response.content = '```json\n{"query": "test"}'
        mock_llm.ainvoke.return_value = mock_llm_response
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(query="test", source_type="video_file", agent_mode=True)
        result = await inner_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_search_converters(self, config, mock_builder):
        """Test that converters are registered."""
        mock_embed = AsyncMock()
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        assert function_info.converters is not None
        assert len(function_info.converters) >= 4
