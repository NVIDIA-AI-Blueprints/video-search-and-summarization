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
"""Additional unit tests for embed_search module to improve coverage."""

import json

from vss_agents.tools.embed_search import BASE_2025
from vss_agents.tools.embed_search import EmbedSearchOutput
from vss_agents.tools.embed_search import _sanitize_for_logging
from vss_agents.tools.embed_search import _str_input_converter


class TestSanitizeForLogging:
    """Test _sanitize_for_logging function."""

    def test_dict_with_vector_field(self):
        obj = {"vector": [0.1, 0.2, 0.3], "other": "data"}
        result = _sanitize_for_logging(obj)
        assert result["vector"] == "<embedding_vector(length=3)>"
        assert result["other"] == "data"

    def test_dict_with_empty_vector(self):
        obj = {"vector": []}
        result = _sanitize_for_logging(obj)
        assert result["vector"] == "<embedding_vector>"

    def test_dict_with_query_vector(self):
        obj = {"query_vector": [0.1, 0.2]}
        result = _sanitize_for_logging(obj)
        assert result["query_vector"] == "<embedding_vector(length=2)>"

    def test_dict_with_embeddings_list(self):
        obj = {"embeddings": [[0.1], [0.2], [0.3]]}
        result = _sanitize_for_logging(obj)
        assert result["embeddings"] == "<embeddings_list(length=3)>"

    def test_nested_dict(self):
        obj = {"outer": {"vector": [0.1, 0.2]}}
        result = _sanitize_for_logging(obj)
        assert result["outer"]["vector"] == "<embedding_vector(length=2)>"

    def test_list_of_dicts(self):
        obj = [{"vector": [0.1]}, {"name": "test"}]
        result = _sanitize_for_logging(obj)
        assert result[0]["vector"] == "<embedding_vector(length=1)>"
        assert result[1]["name"] == "test"

    def test_plain_value(self):
        assert _sanitize_for_logging("hello") == "hello"
        assert _sanitize_for_logging(42) == 42
        assert _sanitize_for_logging(None) is None

    def test_vector_non_list(self):
        obj = {"vector": "not_a_list"}
        result = _sanitize_for_logging(obj)
        assert result["vector"] == "<embedding_vector>"


class TestStrInputConverterEdgeCases:
    """Test _str_input_converter edge cases."""

    def test_json_with_both_params_and_prompts_and_source_type(self):
        input_str = '{"params": {"query": "test"}, "prompts": {"sys": "hello"}, "source_type": "video_file"}'
        result = _str_input_converter(input_str)
        assert result.params["query"] == "test"
        assert result.prompts["sys"] == "hello"

    def test_json_missing_source_type_falls_back(self):
        """When source_type is missing, QueryInput validation fails and fallback is used."""
        input_str = '{"params": {"query": "test"}, "prompts": {"sys": "hello"}}'
        result = _str_input_converter(input_str)
        # Falls back to default with source_type="video_file"
        assert result.source_type == "video_file"


class TestBase2025Constant:
    """Test BASE_2025 constant."""

    def test_base_2025_is_utc(self):
        from datetime import UTC

        assert BASE_2025.tzinfo is UTC

    def test_base_2025_is_jan_1(self):
        assert BASE_2025.year == 2025
        assert BASE_2025.month == 1
        assert BASE_2025.day == 1


class TestEmbedSearchOutputEdgeCases:
    """Test EmbedSearchOutput edge cases."""

    def test_with_query_embedding(self):
        output = EmbedSearchOutput(query_embedding=[0.1, 0.2, 0.3], results=[])
        assert len(output.query_embedding) == 3

    def test_empty_results_serialization(self):
        output = EmbedSearchOutput()
        json_str = output.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["results"] == []
        assert parsed["query_embedding"] == []
