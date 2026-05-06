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
"""Unit tests for search module."""

from datetime import UTC
from datetime import datetime
import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from pydantic import ValidationError
import pytest

from vss_agents.data_models.ranking import ChunkKey
from vss_agents.data_models.ranking import RankedChunk
from vss_agents.data_models.ranking import RankedList
from vss_agents.tools.embed_search import EmbedSearchConfig
from vss_agents.tools.embed_search import QueryInput
from vss_agents.tools.embed_search import _str_input_converter
from vss_agents.tools.search import QUERY_DECOMPOSITION_PROMPT
from vss_agents.tools.search import DecomposedQuery
from vss_agents.tools.search import RankingSpaceConfig
from vss_agents.tools.search import SearchConfig
from vss_agents.tools.search import SearchInput
from vss_agents.tools.search import SearchOutput
from vss_agents.tools.search import SearchResult
from vss_agents.tools.search import _resolve_video_sources_for_search
from vss_agents.tools.search import decompose_query
from vss_agents.tools.search import representative_member


class TestResolveVideoSourcesForSearch:
    """Test source-name resolution for search filters."""

    def test_rtsp_keeps_sensor_name_when_uuid_known(self):
        stream_id = "7f8fcbf4-9e1b-41b9-bf52-1e6ce1ca9f6c"

        result = _resolve_video_sources_for_search(
            video_sources=["video1"],
            name_to_uuid={"video1": stream_id},
            source_type="rtsp",
        )

        assert result == ["video1"]

    def test_rtsp_resolves_uuid_back_to_sensor_name(self):
        stream_id = "7f8fcbf4-9e1b-41b9-bf52-1e6ce1ca9f6c"

        result = _resolve_video_sources_for_search(
            video_sources=[stream_id],
            name_to_uuid={"video1": stream_id},
            source_type="rtsp",
        )

        assert result == ["video1"]

    def test_video_file_resolves_name_to_uuid(self):
        stream_id = "7f8fcbf4-9e1b-41b9-bf52-1e6ce1ca9f6c"

        result = _resolve_video_sources_for_search(
            video_sources=["video1.mp4"],
            name_to_uuid={"video1.mp4": stream_id},
            source_type="video_file",
        )

        assert result == [stream_id]

    def test_unresolved_video_source_keeps_original_name(self):
        result = _resolve_video_sources_for_search(
            video_sources=["missing-camera"],
            name_to_uuid={},
            source_type="video_file",
        )

        assert result == ["missing-camera"]


class TestRepresentativeMember:
    """Unit tests for ``representative_member`` using unit-less score agnostic to different embedding spaces
    (payload picker for fused segments).
    """

    @staticmethod
    def _key(second: int) -> ChunkKey:
        return ChunkKey(sensor_id="cam-1", start=datetime(2025, 1, 1, 0, 0, second, tzinfo=UTC))

    def test_tied_best_rank_resolves_by_member_keys_order(self):
        """Both keys rank #1 in some space -> tie -> stable: first ``member_keys`` entry wins."""
        k1, k2 = self._key(0), self._key(5)
        embed = RankedList(
            space="embed",
            chunks=[
                RankedChunk(key=k1, rank=2, score=0.50),
                RankedChunk(key=k2, rank=1, score=0.40),  # k2 ranks 1 in embed
            ],
        )
        attr = RankedList(
            space="attribute",
            chunks=[
                # k1 ranks 1 in attribute (top score is irrelevant - unit-incompatible with embed)
                RankedChunk(key=k1, rank=1, score=0.99),
                RankedChunk(key=k2, rank=3, score=0.30),
            ],
        )
        # Both k1 and k2 have best_rank=1 (in different spaces) -> tied.
        # Stable selection: first in member_keys wins.
        assert representative_member([k1, k2], [embed, attr]) == k1
        assert representative_member([k2, k1], [embed, attr]) == k2

    def test_picks_lowest_rank_when_not_tied(self):
        """Asymmetric case: k1 best_rank=1, k2 best_rank=3 -> k1 wins by rank."""
        k1, k2 = self._key(0), self._key(5)
        embed = RankedList(
            space="embed",
            chunks=[
                RankedChunk(key=k1, rank=2, score=0.50),
                RankedChunk(key=k2, rank=5, score=0.40),  # k2 only ranks 5 in embed
            ],
        )
        attr = RankedList(
            space="attribute",
            chunks=[
                RankedChunk(key=k1, rank=1, score=0.99),
                RankedChunk(key=k2, rank=3, score=0.30),
            ],
        )
        # k1 best_rank = min(2, 1) = 1; k2 best_rank = min(5, 3) = 3 -> k1 wins.
        assert representative_member([k1, k2], [embed, attr]) == k1
        # Order independent: even when k2 is listed first, k1 still wins on rank.
        assert representative_member([k2, k1], [embed, attr]) == k1

    def test_uses_rank_not_raw_score_to_avoid_apples_to_oranges(self):
        """Regression for the apples-to-oranges bug: rank, not raw score, decides.

        k2 has the best rank (1 in embed) but a low raw score there.
        k1 has rank 2 in both spaces but a high raw score in attribute.
        Pre-fix raw-score picker would always pick k1 (top raw score 0.99).
        Post-fix rank picker picks k2 (best_rank=1 < k1's best_rank=2).
        """
        k1, k2 = self._key(0), self._key(5)
        embed = RankedList(
            space="embed",
            chunks=[
                RankedChunk(key=k1, rank=2, score=0.50),
                RankedChunk(key=k2, rank=1, score=0.10),  # k2's top embed rank, low raw
            ],
        )
        attr = RankedList(
            space="attribute",
            chunks=[
                RankedChunk(key=k1, rank=2, score=0.99),  # k1's high raw, but rank 2
                RankedChunk(key=k2, rank=3, score=0.30),
            ],
        )
        # Rank picker: k2 wins (best_rank=1 < k1's best_rank=2).
        # Raw-score picker would pick k1 (max raw 0.99) -> different answer.
        assert representative_member([k1, k2], [embed, attr]) == k2

    def test_keys_absent_from_lists_lose_via_inf_sentinel(self):
        """Defensive fallback: keys never appearing in any list (sentinel = inf) lose to anything seen."""
        k_seen, k_unseen = self._key(0), self._key(5)
        embed = RankedList(
            space="embed",
            chunks=[RankedChunk(key=k_seen, rank=4, score=0.10)],
        )
        # k_unseen has no rank anywhere -> sentinel inf -> loses to k_seen even at rank 4.
        assert representative_member([k_unseen, k_seen], [embed]) == k_seen
        assert representative_member([k_seen, k_unseen], [embed]) == k_seen


class TestSearchConfig:
    """Test SearchConfig model."""

    def test_required_fields(self):
        config = SearchConfig(
            embed_search_tool="embed_search",
            agent_mode_llm="gpt-4o",
            vst_internal_url="http://localhost:30888",
            embed_weight=1.0,
        )
        assert config.embed_search_tool == "embed_search"
        assert config.agent_mode_llm == "gpt-4o"
        assert config.vst_internal_url == "http://localhost:30888"
        assert config.embed_weight == 1.0
        assert "query" in config.agent_mode_prompt

    def test_embed_weight_is_mandatory(self):
        """``embed_weight`` has no default - SearchConfig refuses to construct without it.

        Pinning this prevents silent regressions and explicit config for an important
        space that is the anchor.
        """
        with pytest.raises(ValidationError) as exc_info:
            SearchConfig(
                embed_search_tool="embed_search",
                agent_mode_llm="gpt-4o",
                vst_internal_url="http://localhost:30888",
            )
        assert "embed_weight" in str(exc_info.value)

    def test_custom_prompt(self):
        config = SearchConfig(
            embed_search_tool="embed_search",
            agent_mode_llm="gpt-4o",
            vst_internal_url="http://localhost:30888",
            embed_weight=1.0,
            agent_mode_prompt="Custom prompt for analysis",
        )
        assert config.agent_mode_prompt == "Custom prompt for analysis"

    def test_fusion_method_defaults(self):
        """Test that fusion method defaults are set correctly."""
        config = SearchConfig(
            embed_search_tool="embed_search",
            agent_mode_llm="gpt-4o",
            vst_internal_url="http://localhost:30888",
            embed_weight=1.0,
        )
        assert config.fusion_method == "rrf"
        assert config.w_attribute == 0.55
        assert config.w_embed == 0.35
        assert config.rrf_k == 60
        assert config.rrf_w == 0.5

    def test_fusion_method_weighted_linear(self):
        """Test weighted linear fusion configuration."""
        config = SearchConfig(
            embed_search_tool="embed_search",
            agent_mode_llm="gpt-4o",
            vst_internal_url="http://localhost:30888",
            embed_weight=1.0,
            fusion_method="weighted_linear",
            w_attribute=0.6,
            w_embed=0.4,
        )
        assert config.fusion_method == "weighted_linear"
        assert config.w_attribute == 0.6
        assert config.w_embed == 0.4

    def test_fusion_method_rrf_custom(self):
        """Test RRF fusion with custom parameters."""
        config = SearchConfig(
            embed_search_tool="embed_search",
            agent_mode_llm="gpt-4o",
            vst_internal_url="http://localhost:30888",
            embed_weight=1.0,
            fusion_method="rrf",
            rrf_k=100,
            rrf_w=0.7,
        )
        assert config.fusion_method == "rrf"
        assert config.rrf_k == 100
        assert config.rrf_w == 0.7

    def test_anchor_in_ranking_spaces_rejected(self):
        """``embed`` declared in ``ranking_spaces`` is rejected with a clear message.

        Anchor handling is separate: the anchor space is dispatched outside the
        registry and weighted via ``embed_weight``.
        """
        with pytest.raises(ValidationError) as exc_info:
            SearchConfig(
                embed_search_tool="embed_search",
                agent_mode_llm="gpt-4o",
                vst_internal_url="http://localhost:30888",
                embed_weight=1.0,
                enable_generalized_fusion=True,
                fusion_tool="fusion",
                ranking_spaces=[
                    RankingSpaceConfig(space="embed", tool="embed_search", weight=1.0),
                    RankingSpaceConfig(space="attribute", tool="attribute_search", weight=0.5),
                ],
            )
        msg = str(exc_info.value)
        assert "anchor" in msg
        assert "embed_weight" in msg

    def test_generalized_fusion_with_only_anchor_rejected(self):
        """Generalized path requires at least one non-anchor space.

        The anchor participates implicitly, an empty ``ranking_spaces`` is
        not allowed for now i.e. there are no other spaces to fuse with, and
        usual setups include at least one embedding space like attributes.
        """
        with pytest.raises(ValidationError) as exc_info:
            SearchConfig(
                embed_search_tool="embed_search",
                agent_mode_llm="gpt-4o",
                vst_internal_url="http://localhost:30888",
                embed_weight=1.0,
                enable_generalized_fusion=True,
                fusion_tool="fusion",
            )
        assert "non-empty ranking_spaces" in str(exc_info.value)


class TestRankingSpaceConfigValidation:
    """Validate the (space, tool) pair against the registry at config-load time."""

    def test_valid_pair_passes(self):
        """Registered (space, tool) pair constructs successfully."""
        cfg = RankingSpaceConfig(space="attribute", tool="attribute_search")
        assert cfg.space == "attribute"
        assert cfg.tool == "attribute_search"

    def test_mismatched_pair_raises(self):
        """A tool not in ``allowed_tools`` for the declared space is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            RankingSpaceConfig(space="attribute", tool="caption_search")
        msg = str(exc_info.value)
        assert "attribute" in msg
        assert "caption_search" in msg
        assert "registered tools for this space" in msg

    def test_error_message_lists_allowed_tools(self):
        """Failure message names the allowed tools so users can self-correct."""
        with pytest.raises(ValidationError) as exc_info:
            RankingSpaceConfig(space="attribute", tool="not_a_real_tool")
        # The validator surfaces sorted allowed_tools so YAML authors see the
        # exact alternatives to choose from.
        assert "attribute_search" in str(exc_info.value)


class TestSearchInput:
    """Test SearchInput model."""

    def test_required_fields(self):
        input_data = SearchInput(
            query="find a person",
            source_type="video_file",
            agent_mode=True,
        )
        assert input_data.query == "find a person"
        assert input_data.agent_mode is True

    def test_all_fields(self):
        input_data = SearchInput(
            query="find cars",
            source_type="rtsp",
            video_sources=["video1", "video2"],
            description="parking lot",
            timestamp_start=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            timestamp_end=datetime(2025, 1, 15, 11, 0, 0, tzinfo=UTC),
            top_k=10,
            min_cosine_similarity=0.5,
            agent_mode=False,
        )
        assert input_data.query == "find cars"
        assert input_data.video_sources == ["video1", "video2"]
        assert input_data.description == "parking lot"
        assert input_data.top_k == 10
        assert input_data.min_cosine_similarity == 0.5
        assert input_data.agent_mode is False

    def test_defaults(self):
        input_data = SearchInput(
            query="test query",
            source_type="video_file",
            agent_mode=True,
        )
        assert input_data.video_sources is None
        assert input_data.description is None
        assert input_data.timestamp_start is None
        assert input_data.timestamp_end is None
        assert input_data.top_k is None  # return all mathing results
        assert input_data.min_cosine_similarity == 0.0

    def test_missing_query_raises(self):
        with pytest.raises(ValidationError):
            SearchInput(source_type="video_file", agent_mode=True)

    def test_missing_agent_mode_raises(self):
        with pytest.raises(ValidationError):
            SearchInput(query="test", source_type="video_file")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            SearchInput(
                query="test",
                source_type="video_file",
                agent_mode=True,
                extra_field="not allowed",
            )


class TestSearchResult:
    """Test SearchResult model."""

    def test_valid_result(self):
        result = SearchResult(
            video_name="video1.mp4",
            description="A video of a parking lot",
            start_time="2025-01-15T10:00:00Z",
            end_time="2025-01-15T10:01:00Z",
            sensor_id="21908c9a-bd40-4941-8a2e-79bc0880fb5a",
            screenshot_url="http://example.com/screenshot1.jpg",
            similarity=0.95,
        )
        assert result.video_name == "video1.mp4"
        assert result.description == "A video of a parking lot"
        assert result.start_time == "2025-01-15T10:00:00Z"
        assert result.end_time == "2025-01-15T10:01:00Z"
        assert result.sensor_id == "21908c9a-bd40-4941-8a2e-79bc0880fb5a"
        assert result.screenshot_url == "http://example.com/screenshot1.jpg"
        assert result.similarity == 0.95

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            SearchResult(
                video_name="video1.mp4",
                # Missing other required fields
            )


class TestSearchOutput:
    """Test SearchOutput model."""

    def test_empty_data(self):
        output = SearchOutput()
        assert output.data == []

    def test_with_results(self):
        result1 = SearchResult(
            video_name="video1.mp4",
            description="Description 1",
            start_time="2025-01-15T10:00:00Z",
            end_time="2025-01-15T10:01:00Z",
            sensor_id="sensor-1",
            screenshot_url="http://example.com/screenshot1.jpg",
            similarity=0.95,
        )
        result2 = SearchResult(
            video_name="video2.mp4",
            description="Description 2",
            start_time="2025-01-15T11:00:00Z",
            end_time="2025-01-15T11:01:00Z",
            sensor_id="sensor-2",
            screenshot_url="http://example.com/screenshot2.jpg",
            similarity=0.85,
        )
        output = SearchOutput(data=[result1, result2])
        assert len(output.data) == 2
        assert output.data[0].video_name == "video1.mp4"
        assert output.data[1].video_name == "video2.mp4"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            SearchOutput(
                data=[],
                extra_field="not allowed",
            )

    def test_serialization(self):
        result = SearchResult(
            video_name="video1.mp4",
            description="Test",
            start_time="2025-01-15T10:00:00Z",
            end_time="2025-01-15T10:01:00Z",
            sensor_id="sensor-1",
            screenshot_url="http://example.com/screenshot1.jpg",
            similarity=0.9,
        )
        output = SearchOutput(data=[result])
        json_str = output.model_dump_json()
        assert "video1.mp4" in json_str
        assert "0.9" in json_str


class TestQueryInput:
    """Test QueryInput model."""

    def test_defaults(self):
        qi = QueryInput(source_type="video_file")
        assert qi.id == ""
        assert qi.params == {}
        assert qi.prompts == {}
        assert qi.response == ""
        assert qi.embeddings == []
        assert qi.source_type == "video_file"

    def test_with_values(self):
        qi = QueryInput(
            id="input1",
            params={"query": "find person"},
            prompts={"system": "analyze"},
            response="result",
            embeddings=[{"vector": [0.1, 0.2]}],
            source_type="rtsp",
        )
        assert qi.id == "input1"
        assert qi.params["query"] == "find person"
        assert qi.source_type == "rtsp"


class TestEmbedSearchConfig:
    """Test EmbedSearchConfig model."""

    def test_required_fields(self):
        config = EmbedSearchConfig(
            cosmos_embed_endpoint="http://localhost:8080",
            es_endpoint="http://localhost:9200",
            vst_external_url="http://localhost:8081",
        )
        assert config.cosmos_embed_endpoint == "http://localhost:8080"
        assert config.es_endpoint == "http://localhost:9200"
        assert config.es_index == "video_embeddings"
        assert config.vst_external_url == "http://localhost:8081"

    def test_custom_index(self):
        config = EmbedSearchConfig(
            cosmos_embed_endpoint="http://localhost:8080",
            es_endpoint="http://localhost:9200",
            vst_external_url="http://localhost:8081",
            es_index="custom_index",
        )
        assert config.es_index == "custom_index"


class TestStrInputConverter:
    """Test _str_input_converter function."""

    def test_json_with_params(self):
        input_str = '{"params": {"query": "find cars"}, "source_type": "video_file"}'
        result = _str_input_converter(input_str)
        assert result.params["query"] == "find cars"
        assert result.source_type == "video_file"

    def test_json_with_prompts(self):
        input_str = '{"prompts": {"system": "analyze"}, "source_type": "rtsp"}'
        result = _str_input_converter(input_str)
        assert result.prompts["system"] == "analyze"
        assert result.source_type == "rtsp"

    def test_invalid_json_format(self):
        input_str = "not valid json"
        result = _str_input_converter(input_str)
        assert result.params["query"] == "not valid json"

    def test_json_without_params_or_prompts(self):
        input_str = '{"other_field": "value"}'
        result = _str_input_converter(input_str)
        # Should treat entire input as query string
        assert result.params["query"] == '{"other_field": "value"}'


class TestDecomposedQuery:
    """Test DecomposedQuery model."""

    def test_defaults(self):
        dq = DecomposedQuery()
        assert dq.query == ""
        assert dq.video_sources == []
        assert dq.source_type == "video_file"
        assert dq.timestamp_start is None
        assert dq.timestamp_end is None
        assert dq.attributes == []
        assert dq.top_k is None

    def test_with_values(self):
        dq = DecomposedQuery(
            query="man pushing cart",
            video_sources=["Endeavor heart"],
            source_type="stream",
            timestamp_start="2025-01-01T13:00:00Z",
            timestamp_end="2025-01-01T14:00:00Z",
            attributes=["man", "beige shirt"],
            top_k=10,
        )
        assert dq.query == "man pushing cart"
        assert dq.video_sources == ["Endeavor heart"]
        assert dq.source_type == "stream"
        assert dq.timestamp_start == "2025-01-01T13:00:00Z"
        assert dq.timestamp_end == "2025-01-01T14:00:00Z"
        assert dq.attributes == ["man", "beige shirt"]
        assert dq.top_k == 10


class TestQueryDecompositionPrompt:
    """Test QUERY_DECOMPOSITION_PROMPT constant."""

    def test_prompt_has_placeholders(self):
        assert "{video_sources}" in QUERY_DECOMPOSITION_PROMPT
        assert "{few_shot_examples}" in QUERY_DECOMPOSITION_PROMPT
        assert "{user_query}" in QUERY_DECOMPOSITION_PROMPT

    def test_prompt_contains_instructions(self):
        assert "query" in QUERY_DECOMPOSITION_PROMPT.lower()
        assert "video_sources" in QUERY_DECOMPOSITION_PROMPT
        assert "source_type" in QUERY_DECOMPOSITION_PROMPT
        assert "timestamp_start" in QUERY_DECOMPOSITION_PROMPT
        assert "timestamp_end" in QUERY_DECOMPOSITION_PROMPT
        assert "attributes" in QUERY_DECOMPOSITION_PROMPT
        assert "top_k" in QUERY_DECOMPOSITION_PROMPT
        assert "min_cosine_similarity" not in QUERY_DECOMPOSITION_PROMPT


class TestDecomposeQuery:
    """Test decompose_query function."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM for testing."""
        llm = MagicMock()
        llm.ainvoke = AsyncMock()
        return llm

    @pytest.mark.asyncio
    async def test_simple_query(self, mock_llm):
        """Test decomposition of a simple search query."""
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"query": "red car", "video_sources": [], "source_type": "video_file", "attributes": ["red", "car"]}'
        )

        result = await decompose_query("find a red car", mock_llm)

        assert result.query == "red car"
        assert result.video_sources == []
        assert result.source_type == "video_file"
        assert result.attributes == ["red", "car"]

    @pytest.mark.asyncio
    async def test_query_with_time_range(self, mock_llm):
        """Test decomposition with time range extraction."""
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"query": "person walking", "timestamp_start": "2025-01-01T09:00:00Z", "timestamp_end": "2025-01-01T10:00:00Z"}'
        )

        result = await decompose_query("find person walking between 9am and 10am", mock_llm)

        assert result.query == "person walking"
        assert result.timestamp_start == "2025-01-01T09:00:00Z"
        assert result.timestamp_end == "2025-01-01T10:00:00Z"

    @pytest.mark.asyncio
    async def test_query_with_video_sources(self, mock_llm):
        """Test decomposition with video source extraction."""
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"query": "delivery truck", "video_sources": ["warehouse entrance", "parking lot"], "source_type": "stream"}'
        )

        result = await decompose_query(
            "find delivery truck at warehouse entrance or parking lot camera",
            mock_llm,
            video_stream_names=["warehouse entrance", "parking lot", "main gate"],
        )

        assert result.query == "delivery truck"
        assert result.video_sources == ["warehouse entrance", "parking lot"]
        assert result.source_type == "stream"

    @pytest.mark.asyncio
    async def test_complex_query_all_parameters(self, mock_llm):
        """Test decomposition of complex query with all parameters."""
        mock_llm.ainvoke.return_value = MagicMock(
            content="""{
                "query": "man pushing cart",
                "video_sources": ["Endeavor heart"],
                "source_type": "stream",
                "timestamp_start": "2025-01-01T13:00:00Z",
                "timestamp_end": "2025-01-01T14:00:00Z",
                "attributes": ["man", "beige shirt"]
            }"""
        )

        result = await decompose_query(
            "Find a man pushing a cart wearing a beige shirt between 1 pm and 2 pm at Endeavor heart",
            mock_llm,
            video_stream_names=["Endeavor heart", "Building A"],
        )

        assert result.query == "man pushing cart"
        assert result.video_sources == ["Endeavor heart"]
        assert result.source_type == "stream"
        assert result.timestamp_start == "2025-01-01T13:00:00Z"
        assert result.timestamp_end == "2025-01-01T14:00:00Z"
        assert result.attributes == ["man", "beige shirt"]

    @pytest.mark.asyncio
    async def test_query_with_json_code_block(self, mock_llm):
        """Test parsing JSON wrapped in markdown code blocks."""
        mock_llm.ainvoke.return_value = MagicMock(
            content='```json\n{"query": "blue car", "attributes": ["blue", "car"]}\n```'
        )

        result = await decompose_query("find blue car", mock_llm)

        assert result.query == "blue car"
        assert result.attributes == ["blue", "car"]

    @pytest.mark.asyncio
    async def test_query_with_plain_code_block(self, mock_llm):
        """Test parsing JSON wrapped in plain code blocks."""
        mock_llm.ainvoke.return_value = MagicMock(
            content='```\n{"query": "person running", "source_type": "video_file"}\n```'
        )

        result = await decompose_query("find person running", mock_llm)

        assert result.query == "person running"
        assert result.source_type == "video_file"

    @pytest.mark.asyncio
    async def test_fallback_on_invalid_json(self, mock_llm):
        """Test fallback to original query when LLM returns invalid JSON."""
        mock_llm.ainvoke.return_value = MagicMock(content="This is not valid JSON")

        result = await decompose_query("find a dog", mock_llm)

        assert result.query == "find a dog"
        assert result.video_sources == []
        assert result.source_type == "video_file"

    @pytest.mark.asyncio
    async def test_fallback_on_llm_exception(self, mock_llm):
        """Test fallback when LLM raises an exception."""
        mock_llm.ainvoke.side_effect = Exception("LLM service unavailable")

        result = await decompose_query("find a cat", mock_llm)

        assert result.query == "find a cat"
        assert result.video_sources == []

    @pytest.mark.asyncio
    async def test_with_video_file_names(self, mock_llm):
        """Test providing video file names as context."""
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"query": "accident scene", "video_sources": ["highway_cam.mp4"], "source_type": "video_file"}'
        )

        result = await decompose_query(
            "find accident in highway_cam video",
            mock_llm,
            video_file_names=["highway_cam.mp4", "parking_lot.mp4"],
        )

        assert result.query == "accident scene"
        assert result.video_sources == ["highway_cam.mp4"]
        assert result.source_type == "video_file"

    @pytest.mark.asyncio
    async def test_empty_response_fields(self, mock_llm):
        """Test handling of null/empty fields in response."""
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"query": "test", "video_sources": null, "attributes": null, "source_type": null}'
        )

        result = await decompose_query("test query", mock_llm)

        assert result.query == "test"
        assert result.video_sources == []
        assert result.attributes == []
        assert result.source_type == "video_file"

    @pytest.mark.asyncio
    async def test_custom_few_shot_examples(self, mock_llm):
        """Test using custom few-shot examples."""
        mock_llm.ainvoke.return_value = MagicMock(content='{"query": "forklift", "source_type": "stream"}')

        custom_examples = """Example:
User query: "Find forklift"
Output: {"query": "forklift", "source_type": "stream"}"""

        result = await decompose_query(
            "find forklift",
            mock_llm,
            few_shot_examples=custom_examples,
        )

        assert result.query == "forklift"

    @pytest.mark.asyncio
    async def test_query_with_only_attributes(self, mock_llm):
        """Test query that extracts only attributes."""
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"query": "person with backpack", "attributes": ["person", "blue backpack", "hat"]}'
        )

        result = await decompose_query("find a person with a blue backpack and hat", mock_llm)

        assert result.query == "person with backpack"
        assert "blue backpack" in result.attributes
        assert "hat" in result.attributes

    @pytest.mark.asyncio
    async def test_partial_time_range(self, mock_llm):
        """Test query with only start time specified."""
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"query": "security guard", "timestamp_start": "2025-01-01T08:00:00Z"}'
        )

        result = await decompose_query("find security guard after 8am", mock_llm)

        assert result.query == "security guard"
        assert result.timestamp_start == "2025-01-01T08:00:00Z"
        assert result.timestamp_end is None

    @pytest.mark.asyncio
    async def test_query_with_top_k(self, mock_llm):
        """Test extraction of top_k from query."""
        mock_llm.ainvoke.return_value = MagicMock(content='{"query": "red car", "top_k": 5}')

        result = await decompose_query("find top 5 red cars", mock_llm)

        assert result.query == "red car"
        assert result.top_k == 5

    @pytest.mark.asyncio
    async def test_query_with_all_filtering_params(self, mock_llm):
        """Test extraction of filtering params."""
        mock_llm.ainvoke.return_value = MagicMock(content='{"query": "blue truck", "top_k": 10}')

        result = await decompose_query("find top 10 highly similar blue trucks", mock_llm)

        assert result.query == "blue truck"
        assert result.top_k == 10

    @pytest.mark.asyncio
    async def test_invalid_top_k_ignored(self, mock_llm):
        """Test that invalid top_k values are ignored."""
        mock_llm.ainvoke.return_value = MagicMock(content='{"query": "car", "top_k": "invalid"}')

        result = await decompose_query("find cars", mock_llm)

        assert result.query == "car"
        assert result.top_k is None


class TestQueryInputSourceType:
    """Test QueryInput source_type field."""

    def test_source_type_required(self):
        with pytest.raises(ValidationError):
            QueryInput()

    def test_source_type_rtsp(self):
        qi = QueryInput(source_type="rtsp")
        assert qi.source_type == "rtsp"

    def test_source_type_video_file(self):
        qi = QueryInput(source_type="video_file")
        assert qi.source_type == "video_file"

    def test_source_type_in_serialization(self):
        qi = QueryInput(
            id="test",
            params={"query": "test"},
            source_type="rtsp",
        )
        json_str = qi.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["source_type"] == "rtsp"
