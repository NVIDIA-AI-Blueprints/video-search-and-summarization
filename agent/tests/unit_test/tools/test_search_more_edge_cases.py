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
"""Additional edge case tests for search module."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from vss_agents.tools.embed_search import EmbedSearchOutput
from vss_agents.tools.embed_search import EmbedSearchResultItem
from vss_agents.tools.search import SearchConfig
from vss_agents.tools.search import SearchInput
from vss_agents.tools.search import SearchOutput
from vss_agents.tools.search import search


def _make_embed_output(results):
    """Helper to build an EmbedSearchOutput with results."""
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


class TestSearchMoreEdgeCases:
    """Cover remaining uncovered lines in search.py."""

    @pytest.fixture
    def config(self):
        return SearchConfig(
            embed_search_tool="embed_search", agent_mode_llm="gpt-4o", vst_internal_url="http://localhost:30888"
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_agent_mode_code_block_no_closing(self, config, mock_builder):
        """Test agent mode with code block without closing backticks."""
        embed_output = _make_embed_output(
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
        mock_llm_resp = MagicMock()
        mock_llm_resp.content = '```\n{"query": "test"}'  # No closing ```
        mock_llm.ainvoke.return_value = mock_llm_resp
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        inp = SearchInput(query="test", source_type="video_file", agent_mode=True)
        result = await fi.single_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_query_exception_skipped(self, config, mock_builder):
        """Test that exceptions in individual query processing are caught."""
        # An empty video_name should be skipped
        embed_output = _make_embed_output(
            [
                {
                    "video_name": "",
                    "similarity_score": 0.9,
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T01:00:00Z",
                }
            ]
        )

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed
        mock_builder.get_llm.return_value = AsyncMock()

        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        result = await fi.single_fn(inp)
        assert isinstance(result, SearchOutput)
        assert len(result.data) == 0

    @pytest.mark.asyncio
    async def test_agent_mode_not_dict_extracted(self, config, mock_builder):
        """Test agent mode when LLM returns non-dict JSON."""
        embed_output = _make_embed_output([])

        mock_embed = AsyncMock()
        mock_embed.ainvoke.return_value = embed_output
        mock_builder.get_function.return_value = mock_embed

        mock_llm = AsyncMock()
        mock_llm_resp = MagicMock()
        mock_llm_resp.content = '"just a string"'
        mock_llm.ainvoke.return_value = mock_llm_resp
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        inp = SearchInput(query="test", source_type="video_file", agent_mode=True)
        result = await fi.single_fn(inp)
        assert isinstance(result, SearchOutput)

    @pytest.mark.asyncio
    async def test_no_timestamp_no_base(self, config, mock_builder):
        """Test when no start_time is provided in result."""
        embed_output = _make_embed_output(
            [
                {
                    "video_name": "cam.mp4",
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

        inp = SearchInput(query="test", source_type="video_file", agent_mode=False)
        result = await fi.single_fn(inp)
        assert isinstance(result, SearchOutput)
        assert len(result.data) == 1

    @pytest.mark.asyncio
    async def test_agent_mode_response_without_content_attr(self, config, mock_builder):
        """Test agent mode LLM response without content attribute."""
        embed_output = _make_embed_output(
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
        # LLM response that is just a string (no .content attribute)
        mock_llm.ainvoke.return_value = '{"query": "test"}'
        mock_builder.get_llm.return_value = mock_llm

        gen = search.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        inp = SearchInput(query="test", source_type="video_file", agent_mode=True)
        result = await fi.single_fn(inp)
        assert isinstance(result, SearchOutput)
