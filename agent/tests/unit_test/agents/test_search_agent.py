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

"""
Unit tests for search_agent.py - focusing on data models, configuration, and presentation converters
"""

import json

from pydantic import ValidationError
import pytest

from vss_agents.agents.search_agent import SearchAgentConfig
from vss_agents.agents.search_agent import SearchAgentInput
from vss_agents.agents.search_agent import _helper_markdown_bullet_list
from vss_agents.agents.search_agent import _to_chat_response
from vss_agents.agents.search_agent import _to_chat_response_chunk
from vss_agents.agents.search_agent import _to_incidents_output
from vss_agents.tools.search import SearchOutput
from vss_agents.tools.search import SearchResult


class TestSearchAgentConfig:
    """Test SearchAgentConfig model."""

    def test_required_fields(self):
        """Test that required fields are enforced."""
        config = SearchAgentConfig(
            embed_search_tool="embed_search",
            vst_internal_url="http://localhost:30888",
        )
        assert config.embed_search_tool == "embed_search"
        assert config.attribute_search_tool is None
        assert config.agent_mode_llm is None
        assert config.vst_internal_url == "http://localhost:30888"

    def test_all_fields(self):
        """Test configuration with all fields."""
        config = SearchAgentConfig(
            embed_search_tool="embed_search",
            attribute_search_tool="attribute_search",
            agent_mode_llm="nim_llm",
            use_attribute_search=True,
            vst_internal_url="http://localhost:30888",
        )
        assert config.embed_search_tool == "embed_search"
        assert config.attribute_search_tool == "attribute_search"
        assert config.agent_mode_llm == "nim_llm"
        assert config.use_attribute_search is True
        assert config.vst_internal_url == "http://localhost:30888"

    def test_defaults(self):
        """Test default values."""
        config = SearchAgentConfig(
            embed_search_tool="embed_search",
            vst_internal_url="http://localhost:30888",
        )
        assert config.use_attribute_search is False
        assert config.attribute_search_tool is None
        assert config.agent_mode_llm is None
        assert config.vst_internal_url == "http://localhost:30888"

    def test_custom_use_attribute_search(self):
        """Test custom use_attribute_search."""
        config = SearchAgentConfig(
            embed_search_tool="embed_search",
            use_attribute_search=True,
            vst_internal_url="http://localhost:30888",
        )
        assert config.use_attribute_search is True
        assert config.vst_internal_url == "http://localhost:30888"


class TestSearchAgentInput:
    """Test SearchAgentInput model."""

    def test_required_query(self):
        """Test that query is required."""
        input_data = SearchAgentInput(query="find person in red shirt")
        assert input_data.query == "find person in red shirt"

    def test_missing_query_raises(self):
        """Test that missing query raises validation error."""
        with pytest.raises(ValidationError):
            SearchAgentInput()

    def test_defaults(self):
        """Test default values."""
        input_data = SearchAgentInput(query="test query")
        assert input_data.agent_mode is True
        assert input_data.use_attribute_search is None
        assert input_data.max_results == 5
        assert input_data.top_k is None
        assert input_data.start_time is None
        assert input_data.end_time is None

    def test_all_fields(self):
        """Test input with all fields."""
        input_data = SearchAgentInput(
            query="find delivery truck",
            agent_mode=False,
            use_attribute_search=False,
            max_results=10,
            top_k=20,
            start_time="2025-01-01T14:00:00Z",
            end_time="2025-01-01T16:00:00Z",
        )
        assert input_data.query == "find delivery truck"
        assert input_data.agent_mode is False
        assert input_data.use_attribute_search is False
        assert input_data.max_results == 10
        assert input_data.top_k == 20
        assert input_data.start_time == "2025-01-01T14:00:00Z"
        assert input_data.end_time == "2025-01-01T16:00:00Z"

    def test_agent_mode_disabled(self):
        """Test with agent_mode disabled."""
        input_data = SearchAgentInput(
            query="simple search",
            agent_mode=False,
        )
        assert input_data.agent_mode is False

    def test_fusion_disabled(self):
        """Test with fusion reranking disabled."""
        input_data = SearchAgentInput(
            query="simple search",
            use_attribute_search=False,
        )
        assert input_data.use_attribute_search is False

    def test_custom_max_results(self):
        """Test with custom max_results."""
        input_data = SearchAgentInput(
            query="test query",
            max_results=15,
        )
        assert input_data.max_results == 15

    def test_top_k_override(self):
        """Test with top_k override."""
        input_data = SearchAgentInput(
            query="test query",
            max_results=5,
            top_k=50,
        )
        assert input_data.top_k == 50
        assert input_data.max_results == 5

    def test_time_filters(self):
        """Test with time filters."""
        input_data = SearchAgentInput(
            query="time-based search",
            start_time="2025-01-01T10:00:00Z",
            end_time="2025-01-01T12:00:00Z",
        )
        assert input_data.start_time == "2025-01-01T10:00:00Z"
        assert input_data.end_time == "2025-01-01T12:00:00Z"

    def test_only_start_time(self):
        """Test with only start_time."""
        input_data = SearchAgentInput(
            query="test query",
            start_time="2025-01-01T10:00:00Z",
        )
        assert input_data.start_time == "2025-01-01T10:00:00Z"
        assert input_data.end_time is None

    def test_only_end_time(self):
        """Test with only end_time."""
        input_data = SearchAgentInput(
            query="test query",
            end_time="2025-01-01T12:00:00Z",
        )
        assert input_data.start_time is None
        assert input_data.end_time == "2025-01-01T12:00:00Z"


class TestDecomposedQueryObjectIds:
    """Test DecomposedQuery object_ids field."""

    def test_object_ids_field_exists(self):
        """Test that DecomposedQuery accepts object_ids."""
        from vss_agents.tools.search import DecomposedQuery

        dq = DecomposedQuery(object_ids=[5, 6])
        assert dq.object_ids == [5, 6]

    def test_object_ids_default_none(self):
        """Test that object_ids defaults to None."""
        from vss_agents.tools.search import DecomposedQuery

        dq = DecomposedQuery()
        assert dq.object_ids is None

    def test_single_object_id(self):
        """Test single object ID in list."""
        from vss_agents.tools.search import DecomposedQuery

        dq = DecomposedQuery(object_ids=[42])
        assert dq.object_ids == [42]


class TestFetchObjectEmbedding:
    """Test _fetch_object_embedding edge cases."""

    @pytest.mark.asyncio
    async def test_missing_object_raises(self):
        """Test that missing object_id raises ValueError."""
        from unittest.mock import AsyncMock

        from vss_agents.tools.attribute_search import _fetch_object_embedding

        mock_client = AsyncMock()
        mock_client.search.return_value = {"hits": {"hits": []}}

        with pytest.raises(ValueError, match="not found"):
            await _fetch_object_embedding("999", mock_client, "test-index")

    @pytest.mark.asyncio
    async def test_missing_embedding_raises(self):
        """Test that object with no embedding vector raises ValueError."""
        from unittest.mock import AsyncMock

        from vss_agents.tools.attribute_search import _fetch_object_embedding

        mock_client = AsyncMock()
        mock_client.search.return_value = {"hits": {"hits": [{"_source": {"embeddings": {}}}]}}

        with pytest.raises(ValueError, match="no embedding vector"):
            await _fetch_object_embedding("5", mock_client, "test-index")

    @pytest.mark.asyncio
    async def test_dict_embedding_shape(self):
        """Test embedding extraction from dict shape: {"vector": [...]}."""
        from unittest.mock import AsyncMock

        from vss_agents.tools.attribute_search import _fetch_object_embedding

        mock_client = AsyncMock()
        mock_client.search.return_value = {"hits": {"hits": [{"_source": {"embeddings": {"vector": [1.0, 2.0, 3.0]}}}]}}

        result = await _fetch_object_embedding("5", mock_client, "test-index")
        assert result == [1.0, 2.0, 3.0]

    @pytest.mark.asyncio
    async def test_list_embedding_shape(self):
        """Test embedding extraction from list shape: [{"vector": [...]}]."""
        from unittest.mock import AsyncMock

        from vss_agents.tools.attribute_search import _fetch_object_embedding

        mock_client = AsyncMock()
        mock_client.search.return_value = {"hits": {"hits": [{"_source": {"embeddings": [{"vector": [4.0, 5.0]}]}}]}}

        result = await _fetch_object_embedding("5", mock_client, "test-index")
        assert result == [4.0, 5.0]

    @pytest.mark.asyncio
    async def test_list_index_with_str(self):
        """Test behavior_index as list gets joined."""
        from unittest.mock import AsyncMock

        from vss_agents.tools.attribute_search import _fetch_object_embedding

        mock_client = AsyncMock()
        mock_client.search.return_value = {"hits": {"hits": [{"_source": {"embeddings": {"vector": [1.0]}}}]}}

        await _fetch_object_embedding("5", mock_client, ["idx-a", "idx-b"])
        mock_client.search.assert_called_once()
        call_kwargs = mock_client.search.call_args
        assert call_kwargs.kwargs["index"] == "idx-a,idx-b"


class TestDecomposeQueryObjectIds:
    """Test that decompose_query correctly passes object_ids."""

    @pytest.mark.asyncio
    async def test_object_ids_extracted(self):
        """Test that object_ids from LLM response are passed to DecomposedQuery."""
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock

        from vss_agents.tools.search import decompose_query

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = '{"query": "object ids 5, 6", "object_ids": [5, 6], "has_action": false}'
        mock_llm.ainvoke.return_value = mock_response

        result = await decompose_query("search for object ids 5, 6", mock_llm)
        assert result.object_ids == [5, 6]

    @pytest.mark.asyncio
    async def test_object_ids_none_when_absent(self):
        """Test that object_ids is None when LLM doesn't extract any."""
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock

        from vss_agents.tools.search import decompose_query

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = '{"query": "person in red shirt", "has_action": false}'
        mock_llm.ainvoke.return_value = mock_response

        result = await decompose_query("person in red shirt", mock_llm)
        assert result.object_ids is None

    @pytest.mark.asyncio
    async def test_object_ids_invalid_type_ignored(self):
        """Test that non-integer object_ids are gracefully ignored."""
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock

        from vss_agents.tools.search import decompose_query

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = '{"query": "test", "object_ids": ["abc", "def"], "has_action": false}'
        mock_llm.ainvoke.return_value = mock_response

        result = await decompose_query("test", mock_llm)
        assert result.object_ids is None

    @pytest.mark.asyncio
    async def test_negative_object_ids_filtered(self):
        """Test that non-positive object_ids are filtered out."""
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock

        from vss_agents.tools.search import decompose_query

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = '{"query": "test", "object_ids": [-1, 0, 5, 10], "has_action": false}'
        mock_llm.ainvoke.return_value = mock_response

        result = await decompose_query("test", mock_llm)
        assert result.object_ids == [5, 10]

    @pytest.mark.asyncio
    async def test_all_non_positive_ids_yields_none(self):
        """Test that all non-positive IDs results in None."""
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock

        from vss_agents.tools.search import decompose_query

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = '{"query": "test", "object_ids": [-1, 0], "has_action": false}'
        mock_llm.ainvoke.return_value = mock_response

        result = await decompose_query("test", mock_llm)
        assert result.object_ids is None


# ===== Tests for presentation converters (moved from embed_search) =====


def _make_search_output(num_results=1):
    """Helper to create a SearchOutput with test data."""
    results = []
    for i in range(num_results):
        results.append(
            SearchResult(
                video_name=f"video{i + 1}.mp4",
                description=f"Test video {i + 1}",
                start_time=f"2025-01-15T{10 + i}:00:00Z",
                end_time=f"2025-01-15T{10 + i}:01:00Z",
                sensor_id=f"sensor-{i + 1}",
                screenshot_url=f"http://example.com/screenshot{i + 1}.jpg",
                similarity=0.95 - (i * 0.1),
            )
        )
    return SearchOutput(data=results)


class TestToIncidentsOutput:
    """Test _to_incidents_output function (moved from embed_search)."""

    def test_empty_search_output(self):
        output = SearchOutput()
        result = _to_incidents_output(output)
        assert "<incidents>" in result
        assert "</incidents>" in result
        assert '"incidents": []' in result

    def test_with_results(self):
        output = _make_search_output(2)
        result = _to_incidents_output(output)
        assert "<incidents>" in result
        assert "video1.mp4" in result
        assert "video2.mp4" in result
        assert "0.95" in result

    def test_incidents_json_structure(self):
        output = _make_search_output(1)
        result = _to_incidents_output(output)
        # Extract JSON between tags
        json_start = result.index("\n") + 1
        json_end = result.rindex("\n</incidents>")
        incidents_json = json.loads(result[json_start:json_end])
        assert "incidents" in incidents_json
        assert len(incidents_json["incidents"]) == 1
        incident = incidents_json["incidents"][0]
        assert "Alert Details" in incident
        assert "Clip Information" in incident
        assert incident["Alert Details"]["Alert Triggered"] == "video1.mp4"


class TestToChatResponse:
    """Test _to_chat_response function (moved from embed_search)."""

    def test_empty_search_output(self):
        output = SearchOutput()
        result = _to_chat_response(output)
        assert result is not None
        assert hasattr(result, "choices") or hasattr(result, "content")

    def test_with_results(self):
        output = _make_search_output(1)
        result = _to_chat_response(output)
        assert result is not None


class TestToChatResponseChunk:
    """Test _to_chat_response_chunk function (moved from embed_search)."""

    def test_empty_search_output(self):
        output = SearchOutput()
        result = _to_chat_response_chunk(output)
        assert result is not None

    def test_with_results(self):
        output = _make_search_output(1)
        result = _to_chat_response_chunk(output)
        assert result is not None


class TestHelperMarkdownBulletList:
    """Test _helper_markdown_bullet_list function (moved from embed_search)."""

    def test_empty_search_output(self):
        output = SearchOutput()
        result = _helper_markdown_bullet_list(output)
        assert "```markdown" in result
        assert "```" in result

    def test_with_results(self):
        output = _make_search_output(2)
        result = _helper_markdown_bullet_list(output)
        assert "video1.mp4" in result
        assert "video2.mp4" in result
        assert "0.95" in result
        assert "0.85" in result
        assert "Similarity Score" in result
