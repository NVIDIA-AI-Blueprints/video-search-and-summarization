# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import json
from unittest.mock import Mock, patch

import pytest

from src.backends.nmp.embedding import Embedding


@pytest.fixture
def mock_settings():
    """Mock settings with embedding configuration."""
    with patch("src.config.settings") as mock_settings:
        mock_settings.system_config.nmp_config.nim_base_url = "http://test-nim-base"
        yield mock_settings


@pytest.fixture
def embedding_client(mock_settings):
    """Create an Embedding client instance with test configuration."""
    return Embedding(
        endpoint_url="http://test-endpoint/v1/embeddings",
        model_name="test-model",
        api_key="test-api-key",
    )


@pytest.fixture
def mock_single_embedding_response():
    """Mock a successful single embedding API response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    return mock_response


@pytest.fixture
def sample_single_text():
    """Single text sample for testing."""
    return "Single test text"


@pytest.fixture
def mock_requests_post():
    """Mock requests.post for testing."""
    with patch("src.backends.nmp.embedding.requests.post") as mock_post:
        yield mock_post


def assert_request_payload(
    mock_post, expected_model, expected_input, expected_input_type="query", has_auth=True
):
    """Helper function to assert the request payload is correct."""
    assert mock_post.call_count >= 1
    call_args = mock_post.call_args

    # Check URL
    assert call_args[0][0] == "http://test-endpoint/v1/embeddings"

    # Check headers
    headers = call_args[1]["headers"]
    assert headers["accept"] == "application/json"
    assert headers["Content-Type"] == "application/json"
    if has_auth:
        assert headers["Authorization"] == "Bearer test-api-key"
    else:
        assert "Authorization" not in headers

    # Check payload
    payload = json.loads(call_args[1]["data"])
    assert payload["model"] == expected_model
    assert payload["input"] == expected_input
    assert payload["input_type"] == expected_input_type
