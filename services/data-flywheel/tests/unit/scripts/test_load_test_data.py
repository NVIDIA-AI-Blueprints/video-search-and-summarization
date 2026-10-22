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
import os

# Mock ES client completely to prevent any connection attempts
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest

# Create a comprehensive mock for the ES client
mock_client = MagicMock()
mock_client.index = MagicMock()
mock_client.indices.flush = MagicMock()
mock_client.indices.refresh = MagicMock()

# Mock the entire es_client module
mock_es_client_module = MagicMock()
mock_es_client_module.get_es_client = MagicMock(return_value=mock_client)
mock_es_client_module.ES_COLLECTION_NAME = "flywheel"

# Mock elasticsearch.helpers.bulk
mock_bulk = Mock(return_value=(1, []))
sys.modules["elasticsearch"] = MagicMock()
sys.modules["elasticsearch.helpers"] = MagicMock()
sys.modules["elasticsearch.helpers"].bulk = mock_bulk

# Patch sys.modules to include our mock before any imports
sys.modules["lib"] = MagicMock()
sys.modules["lib.integration"] = MagicMock()
sys.modules["lib.integration.es_client"] = mock_es_client_module

# Now we can safely import
# Replace the ES_CLIENT that was created during import with our mock
import src.scripts.load_test_data  # noqa: E402
from src.scripts.load_test_data import (  # noqa: E402
    create_openai_request_response,
    load_data_to_elasticsearch,
)

src.scripts.load_test_data.ES_CLIENT = mock_client


@pytest.fixture
def mock_es_client():
    """Fixture to create a mock Elasticsearch client."""
    mock_client = MagicMock()
    mock_client.index = MagicMock()
    mock_client.indices.flush = MagicMock()
    mock_client.indices.refresh = MagicMock()
    return mock_client


@pytest.fixture(autouse=True)
def mock_es_imports():
    """Auto-use fixture to mock all ES-related imports."""
    with (
        patch("src.scripts.load_test_data.ES_CLIENT") as mock_es_client,
        patch("src.scripts.load_test_data.bulk") as mock_bulk_func,
    ):
        # Configure the mock client
        mock_client = MagicMock()
        mock_client.index = MagicMock()
        mock_client.indices.flush = MagicMock()
        mock_client.indices.refresh = MagicMock()

        mock_es_client.return_value = mock_client
        # Also patch the actual ES_CLIENT variable
        mock_es_client.index = MagicMock()
        mock_es_client.indices.flush = MagicMock()
        mock_es_client.indices.refresh = MagicMock()

        # Configure the bulk mock to return success
        mock_bulk_func.return_value = (1, [])

        yield mock_es_client, mock_bulk_func


@pytest.fixture
def sample_conversation_data():
    """Fixture to provide sample conversation data."""
    return {
        "messages": [
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "I'm doing well, thank you!"},
        ],
        "tools": [{"name": "test_tool", "description": "A test tool"}],
    }


@pytest.fixture
def sample_log_format_data():
    """Fixture to provide sample data in log format."""
    return [
        {
            "workload_id": "test_workload",
            "client_id": "test_client",
            "timestamp": 1234567890,
            "request": {"model": "test-model"},
            "response": {"id": "test-response"},
        }
    ]


@pytest.fixture
def test_data_dir():
    """Fixture to create and clean up test data directory."""
    from src.scripts.utils import get_project_root

    # Create test data directory in project root
    project_root = get_project_root()
    test_dir = os.path.join(project_root, "data", "test_files")
    os.makedirs(test_dir, exist_ok=True)

    yield test_dir

    # Clean up test files after test
    import shutil

    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)


def test_create_openai_request_response(sample_conversation_data):
    """Test the creation of OpenAI-style request/response pairs."""
    result = create_openai_request_response(sample_conversation_data)

    # Verify the structure of the result
    assert "timestamp" in result
    assert "request" in result
    assert "response" in result

    # Verify request structure
    request = result["request"]
    assert request["model"] == "not-a-model"
    assert request["temperature"] == 0.7
    assert request["max_tokens"] == 1000
    assert request["messages"] == sample_conversation_data["messages"][:-1]
    assert request["tools"] == sample_conversation_data["tools"]

    # Verify response structure
    response = result["response"]
    assert response["object"] == "chat.completion"
    assert response["model"] == "not-a-model"
    assert len(response["choices"]) == 1
    assert response["choices"][0]["message"] == sample_conversation_data["messages"][-1]
    assert "usage" in response


def test_load_data_to_elasticsearch_with_log_format(
    mock_es_imports, sample_log_format_data, test_data_dir
):
    """Test loading data that's already in log format."""
    mock_es_client, mock_bulk_func = mock_es_imports

    # Create a test JSONL file in the project data directory
    test_file = os.path.join(test_data_dir, "test_data.jsonl")
    with open(test_file, "w") as f:
        for item in sample_log_format_data:
            f.write(json.dumps(item) + "\n")

    # Test loading with overrides
    load_data_to_elasticsearch(
        workload_id="new_workload",
        client_id="new_client",
        file_path=test_file,
    )

    # Verify ES client calls
    mock_bulk_func.assert_called_once()
    mock_es_client.indices.flush.assert_called_once()
    mock_es_client.indices.refresh.assert_called_once()

    # Verify the bulk actions
    call_args = mock_bulk_func.call_args[0]  # Get positional args
    bulk_actions = call_args[1]  # Second arg is the list of actions
    assert len(bulk_actions) == 1
    assert bulk_actions[0]["_index"] == "flywheel"
    assert bulk_actions[0]["_source"]["workload_id"] == "new_workload"
    assert bulk_actions[0]["_source"]["client_id"] == "new_client"


def test_load_data_to_elasticsearch_with_conversation_format(
    mock_es_imports, sample_conversation_data, test_data_dir
):
    """Test loading data that needs to be transformed into log format."""
    mock_es_client, mock_bulk_func = mock_es_imports

    # Create a test JSONL file in the project data directory
    test_file = os.path.join(test_data_dir, "test_data.jsonl")
    with open(test_file, "w") as f:
        f.write(json.dumps(sample_conversation_data) + "\n")

    # Test loading
    load_data_to_elasticsearch(
        workload_id="test_workload",
        client_id="test_client",
        file_path=test_file,
    )

    # Verify ES client calls
    mock_bulk_func.assert_called_once()
    mock_es_client.indices.flush.assert_called_once()
    mock_es_client.indices.refresh.assert_called_once()

    # Verify the bulk actions
    call_args = mock_bulk_func.call_args[0]  # Get positional args
    bulk_actions = call_args[1]  # Second arg is the list of actions
    assert len(bulk_actions) == 1
    assert bulk_actions[0]["_index"] == "flywheel"
    assert bulk_actions[0]["_source"]["workload_id"] == "test_workload"
    assert bulk_actions[0]["_source"]["client_id"] == "test_client"
    assert "request" in bulk_actions[0]["_source"]
    assert "response" in bulk_actions[0]["_source"]


def test_load_data_to_elasticsearch_with_invalid_file(mock_es_imports):
    """Test loading data with an invalid file path."""
    with pytest.raises(SystemExit):
        load_data_to_elasticsearch(file_path="nonexistent.jsonl")


def test_load_data_to_elasticsearch_with_empty_file(mock_es_imports, test_data_dir):
    """Test loading data from an empty file."""
    mock_es_client, mock_bulk_func = mock_es_imports

    # Create an empty file in the project data directory
    test_file = os.path.join(test_data_dir, "empty.jsonl")
    with open(test_file, "w") as _:
        pass  # Create empty file

    # Test loading
    load_data_to_elasticsearch(file_path=test_file)

    # Verify ES client calls were still made (flush and refresh happen regardless)
    mock_bulk_func.assert_not_called()
    mock_es_client.indices.flush.assert_called_once()
    mock_es_client.indices.refresh.assert_called_once()
