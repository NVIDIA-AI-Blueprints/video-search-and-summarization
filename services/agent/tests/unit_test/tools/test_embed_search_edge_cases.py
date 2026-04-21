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
"""Edge case tests for embed_search to cover remaining lines."""

import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.tools.embed_search import EmbedSearchConfig
from vss_agents.tools.embed_search import EmbedSearchOutput
from vss_agents.tools.embed_search import QueryInput
from vss_agents.tools.embed_search import embed_search


def _make_es_response(hits):
    response = MagicMock()
    response.body = {"hits": {"hits": hits, "total": {"value": len(hits)}}}
    response.__getitem__ = lambda self, key: self.body[key]
    return response


class TestEmbedSearchEdgeCases:
    """Cover remaining edge case lines in embed_search."""

    @pytest.fixture
    def config(self):
        return EmbedSearchConfig(
            cosmos_embed_endpoint="http://localhost:8080",
            es_endpoint="http://localhost:9200",
            vst_external_url="http://vst-external:8080",
            vst_internal_url="http://vst-internal:8080",
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.fixture
    def mock_es_client(self):
        client = AsyncMock()
        client.indices.exists.return_value = True
        return client

    @pytest.fixture
    def mock_embed_client(self):
        client = AsyncMock()
        client.get_text_embedding.return_value = [0.1, 0.2, 0.3]
        return client

    async def _get_inner_fn(self, config, mock_builder, mock_es_client, mock_embed_client):
        with patch("vss_agents.tools.embed_search.AsyncElasticsearch", return_value=mock_es_client):
            with patch("vss_agents.tools.embed_search.CosmosEmbedClient", return_value=mock_embed_client):
                gen = embed_search.__wrapped__(config, mock_builder)
                fi = await gen.__anext__()
                return fi.single_fn

    @pytest.mark.asyncio
    async def test_top_k_limits_results(self, config, mock_builder, mock_es_client, mock_embed_client):
        """Test that top_k limits the number of results."""
        hits = []
        for i in range(10):
            hits.append(
                {
                    "_id": f"hit{i}",
                    "_score": 0.95 - i * 0.01,
                    "_source": {
                        "timestamp": "2025-01-01T00:00:00Z",
                        "end": "2025-01-01T01:00:00Z",
                        "sensor": {"id": f"s{i}", "info": {"url": f"v{i}.mp4"}},
                        "llm": {
                            "queries": [
                                {
                                    "id": f"q{i}",
                                    "response": json.dumps({"video_name": f"v{i}.mp4"}),
                                    "params": {},
                                    "prompts": {},
                                    "embeddings": [],
                                }
                            ],
                            "visionEmbeddings": [{"vector": [0.1, 0.2]}],
                        },
                    },
                }
            )
        mock_es_client.search.return_value = _make_es_response(hits)

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es_client, mock_embed_client)
        query_input = QueryInput(params={"query": "test", "top_k": "3"}, source_type="video_file")
        result = await inner_fn(query_input)

        # Should only have 3 results due to top_k
        assert isinstance(result, EmbedSearchOutput)
        assert len(result.results) == 3

    @pytest.mark.asyncio
    async def test_empty_response_field(self, config, mock_builder, mock_es_client, mock_embed_client):
        """Test when response field is empty string."""
        source = {
            "timestamp": "2025-01-01T00:00:00Z",
            "sensor": {"id": "s1", "info": {"url": "v.mp4"}},
            "llm": {
                "queries": [{"id": "q1", "response": ""}],
                "visionEmbeddings": [{"vector": [0.1, 0.2]}],
            },
        }
        mock_es_client.search.return_value = _make_es_response([{"_id": "h1", "_score": 0.95, "_source": source}])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es_client, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="video_file")
        result = await inner_fn(query_input)

        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_no_sensor_description(self, config, mock_builder, mock_es_client, mock_embed_client):
        """Test when no sensor description is available."""
        source = {
            "timestamp": "2025-01-01T00:00:00Z",
            "end": "",
            "sensor": {"id": "s1", "info": {"url": "v.mp4"}, "description": ""},
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
                "visionEmbeddings": [{"vector": [0.1, 0.2]}],
            },
        }
        mock_es_client.search.return_value = _make_es_response([{"_id": "h1", "_score": 0.95, "_source": source}])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es_client, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="video_file")
        result = await inner_fn(query_input)

        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_response_data_not_dict(self, config, mock_builder, mock_es_client, mock_embed_client):
        """Test when response data is not a dict."""
        source = {
            "timestamp": "2025-01-01T00:00:00Z",
            "sensor": {"id": "s1", "info": {"url": "v.mp4"}},
            "llm": {
                "queries": [{"id": "q1", "response": '"just a string"', "params": {}, "prompts": {}, "embeddings": []}],
                "visionEmbeddings": [{"vector": [0.1, 0.2]}],
            },
        }
        mock_es_client.search.return_value = _make_es_response([{"_id": "h1", "_score": 0.95, "_source": source}])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es_client, mock_embed_client)
        query_input = QueryInput(params={"query": "test"}, source_type="video_file")
        result = await inner_fn(query_input)

        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_with_filters_and_timestamps(self, config, mock_builder, mock_es_client, mock_embed_client):
        """Test with multiple filters applied."""
        mock_es_client.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es_client, mock_embed_client)
        query_input = QueryInput(
            params={
                "query": "test",
                "video_sources": '["v1.mp4"]',
                "description": "parking",
                "timestamp_start": "2025-01-15T10:00:00Z",
                "timestamp_end": "2025-01-15T11:00:00Z",
                "top_k": "5",
                "min_cosine_similarity": "0.3",
            },
            source_type="video_file",
        )
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)

    @pytest.mark.asyncio
    async def test_timestamp_without_tz(self, config, mock_builder, mock_es_client, mock_embed_client):
        """Test timestamps without timezone info."""
        mock_es_client.search.return_value = _make_es_response([])

        inner_fn = await self._get_inner_fn(config, mock_builder, mock_es_client, mock_embed_client)
        query_input = QueryInput(
            params={
                "query": "test",
                "timestamp_start": "2025-01-15T10:00:00",
                "timestamp_end": "2025-01-15T11:00:00",
            },
            source_type="video_file",
        )
        result = await inner_fn(query_input)
        assert isinstance(result, EmbedSearchOutput)
