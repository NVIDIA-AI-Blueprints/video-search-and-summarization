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
"""Unit tests for embed_search module."""

import json
from unittest.mock import MagicMock

from pydantic import ValidationError
import pytest

from vss_agents.tools.embed_search import EmbedSearchConfig
from vss_agents.tools.embed_search import EmbedSearchOutput
from vss_agents.tools.embed_search import EmbedSearchResultItem
from vss_agents.tools.embed_search import QueryInput
from vss_agents.tools.embed_search import _chat_request_input_converter


class TestChatRequestInputConverter:
    """Test _chat_request_input_converter function."""

    def test_valid_json_params(self):
        mock_message = MagicMock()
        mock_message.content = '{"params": {"query": "find cars"}, "source_type": "video_file"}'
        mock_request = MagicMock()
        mock_request.messages = [mock_message]

        result = _chat_request_input_converter(mock_request)
        assert result.params["query"] == "find cars"
        assert result.source_type == "video_file"

    def test_valid_json_prompts(self):
        mock_message = MagicMock()
        mock_message.content = '{"prompts": {"system": "analyze video"}, "source_type": "rtsp"}'
        mock_request = MagicMock()
        mock_request.messages = [mock_message]

        result = _chat_request_input_converter(mock_request)
        assert result.prompts["system"] == "analyze video"
        assert result.source_type == "rtsp"

    def test_invalid_json_uses_content_as_query(self):
        mock_message = MagicMock()
        mock_message.content = "plain text query"
        mock_request = MagicMock()
        mock_request.messages = [mock_message]

        result = _chat_request_input_converter(mock_request)
        assert result.params["query"] == "plain text query"
        assert result.source_type == "video_file"  # fallback when not in Query format

    def test_json_without_params_or_prompts(self):
        mock_message = MagicMock()
        mock_message.content = '{"other_field": "value"}'
        mock_request = MagicMock()
        mock_request.messages = [mock_message]

        result = _chat_request_input_converter(mock_request)
        assert result.params["query"] == '{"other_field": "value"}'
        assert result.source_type == "video_file"  # fallback when not in Query format


class TestEmbedSearchConfigValidation:
    """Test EmbedSearchConfig validation."""

    def test_missing_cosmos_endpoint_raises(self):
        with pytest.raises(ValidationError):
            EmbedSearchConfig(
                es_endpoint="http://localhost:9200",
                vst_external_url="http://localhost:8081",
            )

    def test_missing_es_endpoint_raises(self):
        with pytest.raises(ValidationError):
            EmbedSearchConfig(
                cosmos_embed_endpoint="http://localhost:8080",
                vst_external_url="http://localhost:8081",
            )

    def test_missing_vst_base_url_raises(self):
        with pytest.raises(ValidationError):
            EmbedSearchConfig(
                cosmos_embed_endpoint="http://localhost:8080",
                es_endpoint="http://localhost:9200",
            )


class TestQueryInputValidation:
    """Test QueryInput edge cases."""

    def test_empty_embeddings_list(self):
        qi = QueryInput(embeddings=[], source_type="video_file")
        assert qi.embeddings == []

    def test_embeddings_with_nested_dict(self):
        qi = QueryInput(
            embeddings=[{"vector": [0.1, 0.2], "info": {"model": "test"}}],
            source_type="rtsp",
        )
        assert len(qi.embeddings) == 1
        assert qi.embeddings[0]["vector"] == [0.1, 0.2]


class TestEmbedSearchResultItem:
    """Test EmbedSearchResultItem model."""

    def test_defaults(self):
        item = EmbedSearchResultItem()
        assert item.video_name == ""
        assert item.description == ""
        assert item.start_time == ""
        assert item.end_time == ""
        assert item.sensor_id == ""
        assert item.screenshot_url == ""
        assert item.similarity_score == 0.0

    def test_with_values(self):
        item = EmbedSearchResultItem(
            video_name="video1.mp4",
            description="A parking lot video",
            start_time="2025-01-15T10:00:00Z",
            end_time="2025-01-15T10:01:00Z",
            sensor_id="21908c9a-bd40-4941-8a2e-79bc0880fb5a",
            screenshot_url="http://example.com/screenshot.jpg",
            similarity_score=0.95,
        )
        assert item.video_name == "video1.mp4"
        assert item.description == "A parking lot video"
        assert item.start_time == "2025-01-15T10:00:00Z"
        assert item.end_time == "2025-01-15T10:01:00Z"
        assert item.sensor_id == "21908c9a-bd40-4941-8a2e-79bc0880fb5a"
        assert item.screenshot_url == "http://example.com/screenshot.jpg"
        assert item.similarity_score == 0.95

    def test_serialization(self):
        item = EmbedSearchResultItem(
            video_name="test.mp4",
            similarity_score=0.85,
        )
        json_str = item.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["video_name"] == "test.mp4"
        assert parsed["similarity_score"] == 0.85


class TestEmbedSearchOutput:
    """Test EmbedSearchOutput model."""

    def test_defaults(self):
        output = EmbedSearchOutput()
        assert output.query_embedding == []
        assert output.results == []

    def test_with_results(self):
        item1 = EmbedSearchResultItem(video_name="video1.mp4", similarity_score=0.9)
        item2 = EmbedSearchResultItem(video_name="video2.mp4", similarity_score=0.8)
        output = EmbedSearchOutput(
            query_embedding=[0.1, 0.2, 0.3],
            results=[item1, item2],
        )
        assert len(output.query_embedding) == 3
        assert len(output.results) == 2
        assert output.results[0].video_name == "video1.mp4"
        assert output.results[1].video_name == "video2.mp4"

    def test_serialization(self):
        item = EmbedSearchResultItem(video_name="test.mp4", similarity_score=0.9)
        output = EmbedSearchOutput(
            query_embedding=[0.1, 0.2],
            results=[item],
        )
        json_str = output.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["query_embedding"] == [0.1, 0.2]
        assert len(parsed["results"]) == 1
        assert parsed["results"][0]["video_name"] == "test.mp4"
