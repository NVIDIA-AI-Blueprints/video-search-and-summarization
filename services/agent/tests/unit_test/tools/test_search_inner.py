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

from vss_agents.tools.attribute_search import AttributeSearchMetadata
from vss_agents.tools.attribute_search import AttributeSearchResult
from vss_agents.tools.embed_search import EmbedSearchOutput
from vss_agents.tools.embed_search import EmbedSearchResultItem
from vss_agents.tools.fusion import FusionConfig
from vss_agents.tools.fusion import FusionInput
from vss_agents.tools.fusion import _merge_config_defaults
from vss_agents.tools.fusion import run_fusion
from vss_agents.tools.search import RankingSpaceConfig
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
    async def test_non_agent_embed_search_passes_min_cosine_similarity(self, config, mock_builder):
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "camera1.mp4",
                    "similarity_score": 0.95,
                    "start_time": "2025-01-15T10:00:00Z",
                    "end_time": "2025-01-15T10:30:00Z",
                }
            ]
        )
        inner_fn = await self._get_inner_fn(config, mock_builder, embed_output)

        inp = SearchInput(query="find cars", source_type="video_file", agent_mode=False, min_cosine_similarity=0.7)
        result = await inner_fn(inp)

        assert isinstance(result, SearchOutput)
        embed_input = json.loads(mock_builder.get_function.return_value.ainvoke.call_args.args[0])
        assert embed_input["params"]["min_cosine_similarity"] == "0.7"

    @pytest.mark.asyncio
    async def test_agent_mode_request_min_cosine_similarity_not_forwarded(self, config, mock_builder):
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "camera1.mp4",
                    "similarity_score": 0.95,
                    "start_time": "2025-01-15T10:00:00Z",
                    "end_time": "2025-01-15T10:30:00Z",
                }
            ]
        )
        inner_fn = await self._get_inner_fn(config, mock_builder, embed_output)

        inp = SearchInput(query="find cars", source_type="video_file", agent_mode=True, min_cosine_similarity=0.7)
        result = await inner_fn(inp)

        assert isinstance(result, SearchOutput)
        embed_input = json.loads(mock_builder.get_function.return_value.ainvoke.call_args.args[0])
        assert "min_cosine_similarity" not in embed_input["params"]

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
    async def test_search_agent_mode_rtsp_keeps_video_source_name_for_attribute_search(self, mock_builder, monkeypatch):
        """RTSP agent-mode search must preserve camera names for attribute_search filters."""
        from vss_agents.tools import search as search_module

        config = SearchConfig(
            embed_search_tool="embed_search",
            attribute_search_tool="attribute_search",
            agent_mode_llm="gpt-4o",
            vst_internal_url="http://localhost:30888",
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = EmbedSearchOutput(query_embedding=[], results=[])

        mock_attribute_search = AsyncMock()
        mock_attribute_search.ainvoke.return_value = []

        async def _get_function(tool_name):
            if tool_name == "embed_search":
                return mock_embed
            if tool_name == "attribute_search":
                return mock_attribute_search
            raise AssertionError(f"Unexpected tool lookup: {tool_name}")

        mock_builder.get_function.side_effect = _get_function

        mock_llm = AsyncMock()
        mock_llm_response = MagicMock()
        mock_llm_response.content = json.dumps(
            {
                "query": "room with glass door",
                "video_sources": ["video1"],
                "attributes": ["room with glass door"],
                "has_action": False,
            }
        )
        mock_llm.ainvoke.return_value = mock_llm_response
        mock_builder.get_llm.return_value = mock_llm

        async def _fake_get_streams_info(_vst_url):
            return {
                "7f8fcbf4-9e1b-41b9-bf52-1e6ce1ca9f6c": {
                    "name": "video1",
                    "url": "rtsp://example.com/live/7f8fcbf4-9e1b-41b9-bf52-1e6ce1ca9f6c",
                }
            }

        monkeypatch.setattr(search_module, "get_streams_info", _fake_get_streams_info)

        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = SearchInput(
            query="a room with glass door in video1",
            source_type="rtsp",
            agent_mode=True,
        )
        result = await inner_fn(inp)

        assert isinstance(result, SearchOutput)
        mock_attribute_search.ainvoke.assert_awaited_once()
        assert mock_attribute_search.ainvoke.await_args.args[0]["video_sources"] == ["video1"]

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
        embed_input = json.loads(mock_embed.ainvoke.call_args.args[0])
        assert "min_cosine_similarity" not in embed_input["params"]

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
    async def test_search_agent_mode_ignores_deprecated_min_cosine_similarity(self, config, mock_builder):
        """Test agent mode ignores deprecated min_cosine_similarity."""
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


def _make_attribute_results(*, video_name="warehouse_01.mp4", frame_score=0.7):
    """Minimal valid `list[AttributeSearchResult]` for fusion-path tests."""
    return [
        AttributeSearchResult(
            screenshot_url="http://attr.example/overlay.jpg",
            metadata=AttributeSearchMetadata(
                sensor_id="warehouse_01",
                object_id="42",
                object_type="person",
                frame_timestamp="2025-01-15T10:00:02Z",
                start_time="2025-01-15T10:00:00Z",
                end_time="2025-01-15T10:00:05Z",
                behavior_score=0.6,
                frame_score=frame_score,
                video_name=video_name,
            ),
        )
    ]


def _make_llm_mock(*, attributes, query="person in red shirt"):
    """Build an LLM mock whose decomposed-query response carries `attributes`."""
    mock_llm = AsyncMock()
    llm_response = MagicMock()
    llm_response.content = json.dumps({"query": query, "attributes": attributes, "has_action": True})
    mock_llm.ainvoke.return_value = llm_response
    return mock_llm


class _RealFusionStub:
    """Test stub exposing the same surface as the registered fusion NAT tool,
    routed through the real fusion math from ``fusion.py``.
    """

    def __init__(self):
        self.config = FusionConfig()

    async def ainvoke(self, fusion_input):
        if isinstance(fusion_input, dict):
            fusion_input = FusionInput.model_validate(fusion_input)
        merged = _merge_config_defaults(fusion_input, self.config)
        for rl in merged.lists:
            merged.space_weights.setdefault(rl.space, self.config.space_weights_default)
        return run_fusion(merged)


class TestSearchInnerFusionPath:
    """Behavioral fusion-path tests that pass on BOTH the legacy and the
    new generalized fusion path.

    Parametrization: every test runs twice - once with ``enable_generalized_fusion=False`` (legacy)
    and once with ``True`` (new path, delegates to the fusion NAT tool).

    Tests pass only if the behavioral contract is met by both implementations for sanity testing
    and avoiding regressions.
    """

    @pytest.fixture(params=[False, True], ids=["legacy", "generalized"])
    def fusion_flag(self, request):
        return request.param

    @pytest.fixture
    def config(self, fusion_flag):
        kwargs = {
            "embed_search_tool": "embed_search",
            "attribute_search_tool": "attribute_search",
            "agent_mode_llm": "gpt-4o",
            "vst_internal_url": "http://localhost:30888",
            "use_attribute_search": True,
            "fusion_method": "rrf",
            "enable_generalized_fusion": fusion_flag,
        }
        if fusion_flag:
            # Generalized path requires fusion_tool + non-empty ranking_spaces with 'embed' present
            kwargs.update(
                fusion_tool="fusion",
                ranking_spaces=[
                    RankingSpaceConfig(space="embed", tool="embed_search", weight=1.0),
                    RankingSpaceConfig(space="attribute", tool="attribute_search", weight=0.5),
                ],
            )
        return SearchConfig(**kwargs)

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.fixture
    def mock_attribute(self):
        m = AsyncMock()
        m.ainvoke.return_value = _make_attribute_results()
        return m

    @pytest.fixture
    def mock_fusion(self):
        return _RealFusionStub()

    def _wire_builder(self, mock_builder, mock_embed, mock_attribute, mock_llm, mock_fusion):
        """Wire `builder.get_function` to dispatch by NAT tool name.

        The "fusion" entry is harmless on the legacy path (never looked up
        since `enable_generalized_fusion=False` skips the startup resolve)
        and required on the new path. Wiring it unconditionally keeps the
        helper signature uniform across both parametrizations.
        """

        def _get_function(name):
            return {
                "embed_search": mock_embed,
                "attribute_search": mock_attribute,
                "fusion": mock_fusion,
            }[name]

        mock_builder.get_function.side_effect = _get_function
        mock_builder.get_llm.return_value = mock_llm

    async def _drive_search(self, config, mock_builder, search_input):
        gen = search.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        return await function_info.single_fn(search_input)

    @pytest.mark.asyncio
    async def test_fusion_path_returns_all_embed_videos(self, config, mock_builder, mock_attribute, mock_fusion):
        """Happy path: fusion runs and every embed-input video is in the output.

        Embed-anchored contract: fusion may rerank/filter on score but never
        invents new videos and never silently drops one. Both legacy and
        the new path must preserve this invariant.
        """
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "warehouse_01.mp4",
                    "similarity_score": 0.85,
                    "sensor_id": "",
                    "start_time": "2025-01-15T10:00:00Z",
                    "end_time": "2025-01-15T10:00:05Z",
                },
                {
                    "video_name": "dock_03.mp4",
                    "similarity_score": 0.55,
                    "sensor_id": "",
                    "start_time": "2025-01-15T11:00:00Z",
                    "end_time": "2025-01-15T11:00:05Z",
                },
            ]
        )
        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output

        self._wire_builder(
            mock_builder, mock_embed, mock_attribute, _make_llm_mock(attributes=["red shirt"]), mock_fusion
        )

        result = await self._drive_search(
            config,
            mock_builder,
            SearchInput(query="person in red shirt", source_type="video_file", agent_mode=True),
        )

        assert isinstance(result, SearchOutput)
        assert {r.video_name for r in result.data} >= {"warehouse_01.mp4", "dock_03.mp4"}

        # Every surfaced result must report a meaningful, bounded match strength
        assert all(0.0 < r.similarity <= 1.0 for r in result.data)

        # Presentation order matches the agent's claim of quality (descending)
        sims = [r.similarity for r in result.data]
        assert sims == sorted(sims, reverse=True)

        # similarity tracks fusion's ranking
        if all(r.fused_score is not None for r in result.data):
            by_sim = [r.video_name for r in sorted(result.data, key=lambda r: -r.similarity)]
            by_fused = [r.video_name for r in sorted(result.data, key=lambda r: -r.fused_score)]
            assert by_sim == by_fused

    @pytest.mark.asyncio
    async def test_fusion_path_respects_top_k(self, config, mock_builder, mock_attribute, mock_fusion):
        """Happy path: ``top_k`` caps the output after fusion, never before.

        Both paths must apply ``top_k`` to the fused result list, not to
        one of the input ranking spaces.
        """
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": f"clip_{i:02d}.mp4",
                    "similarity_score": 0.9 - 0.05 * i,
                    "sensor_id": "",
                    "start_time": f"2025-01-15T10:{i:02d}:00Z",
                    "end_time": f"2025-01-15T10:{i:02d}:05Z",
                }
                for i in range(5)
            ]
        )
        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output

        self._wire_builder(
            mock_builder, mock_embed, mock_attribute, _make_llm_mock(attributes=["red shirt"]), mock_fusion
        )

        result = await self._drive_search(
            config,
            mock_builder,
            SearchInput(query="person in red shirt", source_type="video_file", agent_mode=True, top_k=2),
        )

        assert isinstance(result, SearchOutput)
        assert len(result.data) <= 2

    @pytest.mark.asyncio
    async def test_fusion_path_skipped_when_llm_returns_no_attributes(
        self, config, mock_builder, mock_attribute, mock_fusion
    ):
        """Failure path (gate): empty ``attributes`` from LLM -> attribute_search not called."""
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "warehouse_01.mp4",
                    "similarity_score": 0.85,
                    "sensor_id": "",
                    "start_time": "2025-01-15T10:00:00Z",
                    "end_time": "2025-01-15T10:00:05Z",
                },
            ]
        )
        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output

        self._wire_builder(mock_builder, mock_embed, mock_attribute, _make_llm_mock(attributes=[]), mock_fusion)

        result = await self._drive_search(
            config,
            mock_builder,
            SearchInput(query="anything", source_type="video_file", agent_mode=True),
        )

        assert isinstance(result, SearchOutput)
        assert {r.video_name for r in result.data} == {"warehouse_01.mp4"}
        assert mock_attribute.ainvoke.call_count == 0

    @pytest.mark.asyncio
    async def test_fusion_path_returns_empty_when_embed_returns_no_results(
        self, config, mock_builder, mock_attribute, mock_fusion
    ):
        """Failure path (empty embed): no embed results -> empty output, no fusion.

        Embed-anchored semantics: if embed produces nothing, the new path
        must NOT surface attribute-only chunks. Both paths short-circuit
        and return an empty ``SearchOutput``.
        """
        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = _make_embed_output_with_results([])

        self._wire_builder(
            mock_builder, mock_embed, mock_attribute, _make_llm_mock(attributes=["red shirt"]), mock_fusion
        )

        result = await self._drive_search(
            config,
            mock_builder,
            SearchInput(query="person in red shirt", source_type="video_file", agent_mode=True),
        )

        assert isinstance(result, SearchOutput)
        assert result.data == []
        assert mock_attribute.ainvoke.call_count == 0

    @pytest.mark.asyncio
    async def test_attribute_dominant_segment_still_reports_meaningful_similarity(
        self, config, mock_builder, mock_fusion
    ):
        """A fused segment whose member chunks come only from a non-embed space
        (no ``raw_embed_cosine`` in payload) must still report a meaningful similarity.

        Scenario: embed contributes one chunk at 10:00 (passes the embed-anchored
        gate). Attribute hits a DIFFERENT chunk at 10:01 - so the new fusion path
        produces two segments, the second of which has only attribute contribution.
        Ensure the second segment does not leak ``similarity=0.0`` to end user's chat output
        and report the normalized fused score.
        """
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "warehouse_01.mp4",
                    "similarity_score": 0.85,
                    "sensor_id": "warehouse_01",
                    "start_time": "2025-01-15T10:00:00Z",
                    "end_time": "2025-01-15T10:00:05Z",
                },
            ]
        )
        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output

        # Attribute hit at a different chunk than embed -> attribute-only segment.
        mock_attribute_local = AsyncMock()
        mock_attribute_local.ainvoke.return_value = [
            AttributeSearchResult(
                screenshot_url="http://attr.example/overlay.jpg",
                metadata=AttributeSearchMetadata(
                    sensor_id="warehouse_01",
                    object_id="42",
                    object_type="person",
                    frame_timestamp="2025-01-15T10:01:02Z",
                    start_time="2025-01-15T10:01:00Z",
                    end_time="2025-01-15T10:01:05Z",
                    behavior_score=0.6,
                    frame_score=0.95,
                    video_name="warehouse_01.mp4",
                ),
            ),
        ]

        self._wire_builder(
            mock_builder,
            mock_embed,
            mock_attribute_local,
            _make_llm_mock(attributes=["red shirt"]),
            mock_fusion,
        )

        result = await self._drive_search(
            config,
            mock_builder,
            SearchInput(query="person in red shirt", source_type="video_file", agent_mode=True),
        )

        # Every surfaced result reports a meaningful match strength,
        # including any segment that came purely from the attribute space.
        assert result.data
        assert all(r.similarity > 0 for r in result.data)

    @pytest.mark.asyncio
    async def test_single_space_run_still_reports_meaningful_similarity(self, config, mock_builder, mock_fusion):
        """Single-non-empty-space scenario: attribute returns nothing, embed has hits."""
        embed_output = _make_embed_output_with_results(
            [
                {
                    "video_name": "warehouse_01.mp4",
                    "similarity_score": 0.85,
                    "sensor_id": "warehouse_01",
                    "start_time": "2025-01-15T10:00:00Z",
                    "end_time": "2025-01-15T10:00:05Z",
                },
                {
                    "video_name": "dock_03.mp4",
                    "similarity_score": 0.55,
                    "sensor_id": "dock_03",
                    "start_time": "2025-01-15T11:00:00Z",
                    "end_time": "2025-01-15T11:00:05Z",
                },
            ]
        )
        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output

        # Attribute returns nothing - only embed contributes.
        mock_attribute_empty = AsyncMock()
        mock_attribute_empty.ainvoke.return_value = []

        self._wire_builder(
            mock_builder,
            mock_embed,
            mock_attribute_empty,
            _make_llm_mock(attributes=["red shirt"]),
            mock_fusion,
        )

        result = await self._drive_search(
            config,
            mock_builder,
            SearchInput(query="person in red shirt", source_type="video_file", agent_mode=True),
        )

        # Single-space run still produces meaningful, bounded similarities in
        # descending order - the orchestrator must not degrade when one space
        # has nothing to say
        assert result.data
        assert all(0.0 < r.similarity <= 1.0 for r in result.data)
        sims = [r.similarity for r in result.data]
        assert sims == sorted(sims, reverse=True)


# ---------------------------------------------------------------------------
# SearchConfig defaults (pin contracts that the orchestrator depends on)
# ---------------------------------------------------------------------------


class TestSearchConfigDefaults:
    """Pin defaults that govern orchestrator behavior on the generalized path."""

    def test_default_payload_merge_priority(self):
        """attribute outranks embed; unlisted spaces (e.g. caption) silently default to 99."""
        config = SearchConfig(
            embed_search_tool="embed_search",
            agent_mode_llm="gpt-4o",
            vst_internal_url="http://localhost:30888",
        )
        prio = config.payload_merge_priority

        assert prio == {"attribute": 0, "embed": 1}
        assert prio["attribute"] < prio["embed"]
        assert "caption" not in prio
