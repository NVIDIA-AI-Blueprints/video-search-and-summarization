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
"""Tests for embed_search inner function via generator invocation."""

import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.tools.embed_search import EmbedSearchConfig
from vss_agents.tools.embed_search import EmbedSearchOutput
from vss_agents.tools.embed_search import QueryInput
from vss_agents.tools.embed_search import embed_search
from vss_agents.utils.es_client import VSSESClient


def _make_es_hit(source, score=0.9):
    """Create a mock ES hit."""
    return {"_id": "hit1", "_score": score, "_source": source}


def _make_es_response(hits):
    """Create a mock ES response."""
    response = MagicMock()
    response.body = {"hits": {"hits": hits, "total": {"value": len(hits)}}}
    response.__getitem__ = lambda self, key: self.body[key]
    return response


class TestEmbedSearchInner:
    """Test the inner _embed_search function."""

    @pytest.fixture
    def config(self):
        return EmbedSearchConfig(
            cosmos_embed_endpoint="http://localhost:8080",
            es_endpoint="http://localhost:9200",
            vst_external_url="http://vst-external:8080",
            default_max_results=100,
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.fixture
    def mock_es(self, monkeypatch):
        client = AsyncMock()
        client.indices.exists.return_value = True
        # Key MUST match config.es_endpoint — VSSESClient.get_es_client looks up
        # the cache by exact endpoint string. Mismatched key would fall through
        # to creating a real AsyncElasticsearch and trying to dial live ES.
        monkeypatch.setattr(VSSESClient, "_clients", {"http://localhost:9200": client})
        monkeypatch.setattr(VSSESClient, "close_all", AsyncMock())
        return client

    @pytest.fixture
    def mock_embed_client(self):
        client = AsyncMock()
        client.get_text_embedding.return_value = [0.1, 0.2, 0.3]
        client.get_image_embedding.return_value = [0.4, 0.5, 0.6]
        client.get_video_embedding.return_value = [0.7, 0.8, 0.9]
        return client

    async def _get_inner_fn(self, config, mock_builder, mock_es, mock_embed_client):
        with patch("vss_agents.tools.embed_search.CosmosEmbedClient", return_value=mock_embed_client):
            gen = embed_search.__wrapped__(config, mock_builder)
            function_info = await gen.__anext__()
            return function_info.single_fn

    @pytest.mark.asyncio
    async def test_text_query(self, config, mock_builder, mock_es, mock_embed_client):
        source = {
            "timestamp": "2025-01-15T10:00:00Z",
            "end": "2025-01-15T10:30:00Z",
            "sensor": {
                "id": "stream1",
                "type": "camera",
                "description": "Front cam",
                "info": {"url": "video1.mp4"},
            },
            "llm": {
                "queries": [
                    {
                        "id": "q1",
                        "response": json.dumps({"video_name": "v1.mp4"}),
                        "params": {},
                        "prompts": {},
                        "embeddings": [],
                    }
                ],
                "visionEmbeddings": [{"vector": [0.1, 0.2]}],
            },
        }
        mock_es.search.return_value = _make_es_response([_make_es_hit(source, 0.95)])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "find cars"}, source_type="video_file")
        result = await inner_fn(query_input)

        assert isinstance(result, EmbedSearchOutput)
        assert len(result.results) > 0

    @pytest.mark.asyncio
    async def test_image_url_query(self, config, mock_builder, mock_es, mock_embed_client):
        source = {
            "timestamp": "2025-01-01T00:00:00Z",
            "end": "2025-01-01T01:00:00Z",
            "sensor": {"id": "s1", "info": {"url": "v.mp4"}},
            "llm": {
                "queries": [{"id": "q1", "response": "{}"}],
                "visionEmbeddings": [{"vector": [0.1, 0.2]}],
            },
        }
        mock_es.search.return_value = _make_es_response([_make_es_hit(source)])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"image_url": "http://example.com/img.jpg"}, source_type="video_file")
        result = await inner_fn(query_input)

        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_video_url_query(self, config, mock_builder, mock_es, mock_embed_client):
        mock_es.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"video_url": "http://example.com/video.mp4"}, source_type="video_file")
        result = await inner_fn(query_input)

        assert isinstance(result, EmbedSearchOutput)
        assert len(result.results) == 0

    @pytest.mark.asyncio
    async def test_precomputed_embeddings(self, config, mock_builder, mock_es, mock_embed_client):
        mock_es.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(embeddings=[{"vector": [0.1, 0.2, 0.3]}], source_type="video_file")
        result = await inner_fn(query_input)

        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_no_query_raises(self, config, mock_builder, mock_es, mock_embed_client):
        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={}, source_type="video_file")
        with pytest.raises(ValueError, match="Either query"):
            await inner_fn(query_input)

    @pytest.mark.asyncio
    async def test_with_video_sources_filter(self, config, mock_builder, mock_es, mock_embed_client):
        mock_es.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(
            params={"query": "test", "video_sources": '["video1.mp4", "video2.mp4"]'}, source_type="video_file"
        )
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_with_comma_separated_video_sources(self, config, mock_builder, mock_es, mock_embed_client):
        mock_es.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(
            params={"query": "test", "video_sources": "video1.mp4, video2.mp4"}, source_type="video_file"
        )
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_with_description_filter(self, config, mock_builder, mock_es, mock_embed_client):
        mock_es.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test", "description": "parking lot"}, source_type="video_file")
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_with_timestamp_filters(self, config, mock_builder, mock_es, mock_embed_client):
        mock_es.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(
            params={
                "query": "test",
                "timestamp_start": "2025-01-15T10:00:00Z",
                "timestamp_end": "2025-01-15T11:00:00Z",
            },
            source_type="video_file",
        )
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_with_top_k(self, config, mock_builder, mock_es, mock_embed_client):
        mock_es.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test", "top_k": "5"}, source_type="video_file")
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_with_min_cosine_similarity(self, config, mock_builder, mock_es, mock_embed_client):
        source = {
            "timestamp": "2025-01-01T00:00:00Z",
            "end": "2025-01-01T01:00:00Z",
            "sensor": {"id": "s1", "info": {"url": "v.mp4"}},
            "llm": {
                "queries": [{"id": "q1", "response": "{}"}],
                "visionEmbeddings": [{"vector": [0.1, 0.2]}],
            },
        }
        mock_es.search.return_value = _make_es_response([_make_es_hit(source, 0.3)])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test", "min_cosine_similarity": "0.5"}, source_type="video_file")
        result = await inner_fn(query_input)

        # Score 0.3 -> cosine = 2*0.3-1 = -0.4 < 0.5 -> filtered out
        assert len(result.results) == 0

    @pytest.mark.asyncio
    async def test_es_index_not_found(self, config, mock_builder, mock_es, mock_embed_client):
        from elasticsearch import NotFoundError as ESNotFoundError

        # Create proper ESNotFoundError with ApiResponseMeta
        mock_meta = MagicMock()
        mock_meta.status = 404
        mock_es.search.side_effect = ESNotFoundError(message="index not found", meta=mock_meta, body={})

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="video_file")

        with pytest.raises(ValueError, match="does not exist"):
            await inner_fn(query_input)

    @pytest.mark.asyncio
    async def test_hit_without_llm_skipped(self, config, mock_builder, mock_es, mock_embed_client):
        source = {
            "timestamp": "2025-01-01T00:00:00Z",
            "end": "2025-01-01T01:00:00Z",
            "sensor": {"id": "s1", "info": {}},
            # No "llm" field
        }
        mock_es.search.return_value = _make_es_response([_make_es_hit(source)])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="video_file")
        result = await inner_fn(query_input)

        assert len(result.results) == 0

    @pytest.mark.asyncio
    async def test_hit_with_location_and_coordinate(self, config, mock_builder, mock_es, mock_embed_client):
        source = {
            "timestamp": "2025-01-01T00:00:00Z",
            "end": "2025-01-01T01:00:00Z",
            "sensor": {
                "id": "s1",
                "type": "camera",
                "description": "cam",
                "location": {"lat": 37.0, "lon": -122.0, "alt": 10.0},
                "coordinate": {"x": 1.0, "y": 2.0, "z": 3.0},
                "info": {"url": "video.mp4"},
            },
            "info": {"key": "value"},
            "llm": {
                "queries": [
                    {
                        "id": "q1",
                        "response": json.dumps({"video_name": "v.mp4"}),
                        "params": {},
                        "prompts": {},
                        "embeddings": [{"vector": [0.1], "info": {"m": "c"}}],
                    }
                ],
            },
        }
        mock_es.search.return_value = _make_es_response([_make_es_hit(source, 0.95)])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="video_file")
        result = await inner_fn(query_input)

        assert isinstance(result, EmbedSearchOutput)
        assert len(result.results) == 1
        assert result.results[0].video_name == "v.mp4"

    @pytest.mark.asyncio
    async def test_hit_with_no_queries(self, config, mock_builder, mock_es, mock_embed_client):
        source = {
            "timestamp": "2025-01-01T00:00:00Z",
            "sensor": {"id": "s1", "info": {}},
            "llm": {
                "queries": [],
                "visionEmbeddings": [{"vector": [0.1, 0.2]}],
            },
        }
        mock_es.search.return_value = _make_es_response([_make_es_hit(source, 0.95)])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="video_file")
        result = await inner_fn(query_input)

        assert isinstance(result, EmbedSearchOutput)
        assert len(result.results) == 1

    @pytest.mark.asyncio
    async def test_invalid_timestamp_in_response(self, config, mock_builder, mock_es, mock_embed_client):
        source = {
            "timestamp": "invalid-timestamp",
            "end": "also-invalid",
            "sensor": {"id": "s1", "info": {"url": "v.mp4"}},
            "llm": {
                "queries": [
                    {
                        "id": "q1",
                        "response": json.dumps({"video_name": "v.mp4"}),
                        "params": {},
                        "prompts": {},
                        "embeddings": [],
                    }
                ],
            },
        }
        mock_es.search.return_value = _make_es_response([_make_es_hit(source, 0.95)])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="video_file")
        result = await inner_fn(query_input)

        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_timestamp_start_only(self, config, mock_builder, mock_es, mock_embed_client):
        mock_es.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(
            params={"query": "test", "timestamp_start": "2025-01-15T10:00:00Z"}, source_type="video_file"
        )
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_invalid_timestamp_in_params(self, config, mock_builder, mock_es, mock_embed_client):
        mock_es.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(
            params={"query": "test", "timestamp_start": "not-a-date", "timestamp_end": "also-invalid"},
            source_type="video_file",
        )
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_with_vst_internal_url(self, mock_builder, mock_es, mock_embed_client):
        config = EmbedSearchConfig(
            cosmos_embed_endpoint="http://localhost:8080",
            es_endpoint="http://localhost:9200",
            vst_external_url="http://vst-external:8080",
            vst_internal_url="http://vst-internal:8080",
        )
        mock_es.search.return_value = _make_es_response([])

        with patch("vss_agents.tools.embed_search.CosmosEmbedClient", return_value=mock_embed_client):
            gen = embed_search.__wrapped__(config, mock_builder)
            function_info = await gen.__anext__()
            inner_fn = function_info.single_fn

        query_input = QueryInput(params={"query": "test"}, source_type="video_file")
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_single_video_source_not_list(self, config, mock_builder, mock_es, mock_embed_client):
        mock_es.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test", "video_sources": '"single_video"'}, source_type="video_file")
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_queries_data_not_list(self, config, mock_builder, mock_es, mock_embed_client):
        source = {
            "timestamp": "2025-01-01T00:00:00Z",
            "sensor": {"id": "s1", "info": {}},
            "llm": {
                "queries": "not_a_list",
                "visionEmbeddings": [{"vector": [0.1, 0.2]}],
            },
        }
        mock_es.search.return_value = _make_es_response([_make_es_hit(source, 0.95)])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="video_file")
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_response_not_json(self, config, mock_builder, mock_es, mock_embed_client):
        """Test when stored query response is not valid JSON."""
        source = {
            "timestamp": "2025-01-01T00:00:00Z",
            "sensor": {"id": "s1", "info": {"url": "v.mp4"}},
            "llm": {
                "queries": [{"id": "q1", "response": "not json"}],
                "visionEmbeddings": [{"vector": [0.1, 0.2]}],
            },
        }
        mock_es.search.return_value = _make_es_response([_make_es_hit(source, 0.95)])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="video_file")
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)
        assert len(result.results) == 1

    @pytest.mark.asyncio
    async def test_rtsp_source_type(self, config, mock_builder, mock_es, mock_embed_client):
        """Test with rtsp source_type uses different search indices."""
        mock_es.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="rtsp")
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_video_file_index_not_exists(self, config, mock_builder, mock_es, mock_embed_client):
        """Test video_file source_type raises when index doesn't exist."""
        from elasticsearch import NotFoundError as ESNotFoundError

        mock_es.search.side_effect = ESNotFoundError(message="index_not_found_exception", meta=MagicMock(), body={})

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="video_file")

        with pytest.raises(ValueError, match="does not exist"):
            await inner_fn(query_input)
