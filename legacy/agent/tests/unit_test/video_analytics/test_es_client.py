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
"""Tests for vss_agents/video_analytics/es_client.py."""

from copy import deepcopy

import pytest

from vss_agents.video_analytics.es_client import BASE_QUERY_TEMPLATE
from vss_agents.video_analytics.es_client import ESClient


class TestBaseQueryTemplate:
    """Tests for BASE_QUERY_TEMPLATE constant."""

    def test_template_structure(self):
        """Test that BASE_QUERY_TEMPLATE has correct structure."""
        assert "query" in BASE_QUERY_TEMPLATE
        assert "bool" in BASE_QUERY_TEMPLATE["query"]
        assert "must" in BASE_QUERY_TEMPLATE["query"]["bool"]

    def test_template_is_dict(self):
        """Test that template is a dictionary."""
        assert isinstance(BASE_QUERY_TEMPLATE, dict)

    def test_template_deepcopy_independence(self):
        """Test that deepcopy creates independent copy."""
        copy1 = deepcopy(BASE_QUERY_TEMPLATE)
        copy2 = deepcopy(BASE_QUERY_TEMPLATE)

        copy1["query"]["bool"]["must"].append({"test": "value"})

        # Original should be unchanged
        assert len(BASE_QUERY_TEMPLATE["query"]["bool"]["must"]) == 0
        # Copy2 should be unchanged
        assert len(copy2["query"]["bool"]["must"]) == 0

    def test_template_must_is_list(self):
        """Test that must clause is a list."""
        assert isinstance(BASE_QUERY_TEMPLATE["query"]["bool"]["must"], list)


class TestESClient:
    """Tests for ESClient class."""

    def test_client_initialization(self):
        """Test ESClient initialization."""
        client = ESClient("http://localhost:9200")
        assert client.index_prefix == ""
        assert client.client is not None

    def test_client_with_prefix(self):
        """Test ESClient with index prefix."""
        client = ESClient("http://localhost:9200", index_prefix="test-")
        assert client.index_prefix == "test-"

    def test_get_index_valid_key(self):
        """Test get_index with valid key."""
        client = ESClient("http://localhost:9200")
        index = client.get_index("incidents")
        assert index == "incidents-*"

    def test_get_index_with_prefix(self):
        """Test get_index with prefix."""
        client = ESClient("http://localhost:9200", index_prefix="prod-")
        index = client.get_index("incidents")
        assert index == "prod-incidents-*"

    def test_get_index_invalid_key(self):
        """Test get_index with invalid key raises ValueError."""
        client = ESClient("http://localhost:9200")
        with pytest.raises(ValueError, match="Invalid index key"):
            client.get_index("invalid_index")

    def test_indexes_whitelist(self):
        """Test INDEXES class variable contains expected keys."""
        expected_keys = ["incidents", "vlm_incidents", "behavior", "frames", "calibration"]
        for key in expected_keys:
            assert key in ESClient.INDEXES

    def test_all_indexes_are_strings(self):
        """Test all index values are strings."""
        for key, value in ESClient.INDEXES.items():
            assert isinstance(key, str)
            assert isinstance(value, str)

    def test_incidents_index_pattern(self):
        """Test incidents index has wildcard pattern."""
        assert "*" in ESClient.INDEXES["incidents"]

    def test_calibration_index_no_wildcard(self):
        """Test calibration index has no wildcard."""
        assert "*" not in ESClient.INDEXES["calibration"]
