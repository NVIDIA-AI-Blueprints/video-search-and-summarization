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
from requests.exceptions import ConnectionError, HTTPError, RequestException, Timeout

from src.backends.nmp.embedding import Embedding
from tests.unit.backends.nmp.conftest import assert_request_payload


class TestEmbedding:
    """Test Embedding class functionality."""

    @pytest.mark.parametrize(
        "endpoint_url,model_name,api_key,expected_endpoint",
        [
            (
                "http://custom/v1/embeddings",
                "custom-model",
                "custom-key",
                "http://custom/v1/embeddings",
            ),
            (None, "test-model", "test-key", "http://test-nim-base/v1/embeddings"),  # Uses settings
            (None, None, None, "http://test-nim-base/v1/embeddings"),  # All defaults
        ],
    )
    def test_initialization(
        self, mock_settings, endpoint_url, model_name, api_key, expected_endpoint
    ):
        """Test Embedding initialization with different parameters."""
        kwargs = {}
        if endpoint_url is not None:
            kwargs["endpoint_url"] = endpoint_url
        if model_name is not None:
            kwargs["model_name"] = model_name
        if api_key is not None:
            kwargs["api_key"] = api_key

        client = Embedding(**kwargs)

        assert client.endpoint_url == expected_endpoint
        expected_model = model_name if model_name else "nvidia/llama-3.2-nv-embedqa-1b-v2"
        assert client.model_name == expected_model
        assert client.api_key == api_key

    @pytest.mark.parametrize(
        "text_input,input_type,expected_response",
        [
            ("single text", "query", [[0.1, 0.2, 0.3]]),
            (["text1", "text2"], "passage", [[0.1, 0.2], [0.3, 0.4]]),
            ("empty response", "query", []),
        ],
    )
    def test_get_embedding_success(
        self, embedding_client, mock_requests_post, text_input, input_type, expected_response
    ):
        """Test successful embedding generation scenarios."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [{"embedding": emb} for emb in expected_response]
        }
        mock_requests_post.return_value = mock_response

        result = embedding_client.get_embedding(text_input, input_type=input_type)

        assert result == expected_response
        expected_input = text_input if isinstance(text_input, list) else [text_input]
        assert_request_payload(mock_requests_post, "test-model", expected_input, input_type)

    @pytest.mark.parametrize(
        "text_input,has_auth",
        [
            ("test with auth", True),
            (["batch", "without", "auth"], False),
        ],
    )
    def test_get_embedding_auth_scenarios(
        self, mock_requests_post, mock_single_embedding_response, text_input, has_auth
    ):
        """Test embedding generation with and without authentication."""
        client = Embedding(
            endpoint_url="http://test-endpoint/v1/embeddings",
            model_name="test-model",
            api_key="test-api-key" if has_auth else None,
        )
        mock_requests_post.return_value = mock_single_embedding_response

        result = client.get_embedding(text_input)

        assert result == [[0.1, 0.2, 0.3]]
        expected_input = text_input if isinstance(text_input, list) else [text_input]
        assert_request_payload(mock_requests_post, "test-model", expected_input, has_auth=has_auth)

    @pytest.mark.parametrize(
        "exception_type,expect_none",
        [
            (ConnectionError("Connection failed"), True),
            (Timeout("Request timeout"), True),
            (HTTPError("HTTP error"), True),
            (RequestException("General request error"), True),
            (json.JSONDecodeError("Invalid JSON", "", 0), False),  # Not caught, should raise
        ],
    )
    def test_get_embedding_failures(
        self, embedding_client, mock_requests_post, sample_single_text, exception_type, expect_none
    ):
        """Test various failure scenarios."""
        if isinstance(exception_type, json.JSONDecodeError):
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.raise_for_status.return_value = None
            mock_response.json.side_effect = exception_type
            mock_requests_post.return_value = mock_response

            with pytest.raises(json.JSONDecodeError):
                embedding_client.get_embedding(sample_single_text)
        else:
            mock_requests_post.side_effect = exception_type
            result = embedding_client.get_embedding(sample_single_text)
            assert result is None if expect_none else result

    def test_get_embedding_http_error_and_malformed_response(
        self, embedding_client, mock_requests_post, sample_single_text
    ):
        """Test HTTP error status and malformed response handling."""
        # HTTP error
        mock_http_error = Mock()
        mock_http_error.status_code = 400
        mock_http_error.raise_for_status.side_effect = HTTPError("Bad Request")
        mock_requests_post.return_value = mock_http_error

        result = embedding_client.get_embedding(sample_single_text)
        assert result is None

        # Malformed response
        mock_malformed = Mock()
        mock_malformed.status_code = 200
        mock_malformed.raise_for_status.return_value = None
        mock_malformed.json.return_value = {"error": "malformed response"}
        mock_requests_post.return_value = mock_malformed

        with pytest.raises(KeyError):
            embedding_client.get_embedding(sample_single_text)

    @pytest.mark.parametrize(
        "texts,batch_size,expected_calls",
        [
            (["text1"], 32, 1),  # Single text
            ([], 32, 0),  # Empty input
            (["t1", "t2", "t3"], 2, 2),  # Multiple batches: [t1,t2], [t3]
            ([f"text{i}" for i in range(10)], 5, 2),  # Exact batch divisions: [0-4], [5-9]
            ([f"text{i}" for i in range(100)], 32, 4),  # Large dataset: ceil(100/32) = 4
        ],
    )
    def test_get_embeddings_batch_call_counts(
        self, embedding_client, texts, batch_size, expected_calls
    ):
        """Test that batching makes correct number of calls to get_embedding."""
        with patch.object(embedding_client, "get_embedding") as mock_get_embedding:
            # Mock each call to return embeddings for the actual batch size it receives
            def side_effect(batch_texts, input_type="query"):
                return [[0.1, 0.2]] * len(batch_texts)

            mock_get_embedding.side_effect = side_effect

            result = embedding_client.get_embeddings_batch(texts, batch_size=batch_size)

            assert mock_get_embedding.call_count == expected_calls
            if texts:
                assert len(result) == len(texts)

    @pytest.mark.parametrize(
        "batch_responses,expected_result,batch_size",
        [
            # All success with batch_size=2
            ([[[0.1, 0.2], [0.3, 0.4]], [[0.5, 0.6]]], [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]], 2),
            # Mixed success/failure with batch_size=1
            ([[[0.1, 0.2]], None, [[0.5, 0.6]]], [[0.1, 0.2], None, [0.5, 0.6]], 1),
            # All failure with batch_size=1
            ([None, None], [None, None], 1),
        ],
    )
    def test_get_embeddings_batch_failure_handling(
        self, embedding_client, batch_responses, expected_result, batch_size
    ):
        """Test batch processing with various success/failure combinations."""
        texts = [f"text{i}" for i in range(len(expected_result))]

        with patch.object(embedding_client, "get_embedding") as mock_get_embedding:
            mock_get_embedding.side_effect = batch_responses

            result = embedding_client.get_embeddings_batch(texts, batch_size=batch_size)

            assert result == expected_result
            assert len(result) == len(texts)

    @pytest.mark.parametrize("input_type", ["query", "passage"])
    def test_get_embeddings_batch_input_type_propagation(self, embedding_client, input_type):
        """Test that input_type parameter is passed correctly."""
        texts = ["text1", "text2"]

        with patch.object(embedding_client, "get_embedding") as mock_get_embedding:
            mock_get_embedding.return_value = [[0.1, 0.2], [0.3, 0.4]]

            embedding_client.get_embeddings_batch(texts, input_type=input_type)

            mock_get_embedding.assert_called_once_with(texts, input_type=input_type)

    def test_batch_partial_failure_maintains_array_size(self, embedding_client):
        """Test that partial batch failures maintain correct array sizes."""
        texts = [f"text{i}" for i in range(5)]  # 5 texts, batch_size=3 -> 2 batches

        with patch.object(embedding_client, "get_embedding") as mock_get_embedding:
            # First batch (3 texts) succeeds, second batch (2 texts) fails
            mock_get_embedding.side_effect = [
                [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],  # 3 successes
                None,  # 2 failures
            ]

            result = embedding_client.get_embeddings_batch(texts, batch_size=3)

            expected = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6], None, None]
            assert result == expected
            assert len(result) == len(texts)

    def test_embedding_request_structure_and_logging(self, embedding_client, mock_requests_post):
        """Test request structure and error logging."""
        # Test request structure
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
        mock_requests_post.return_value = mock_response

        embedding_client.get_embedding("test text", input_type="passage")

        call_args = mock_requests_post.call_args
        payload = json.loads(call_args[1]["data"])
        assert all(key in payload for key in ["model", "input", "input_type"])
        assert payload["input_type"] == "passage"

        # Test error logging
        mock_requests_post.side_effect = ConnectionError("Network error")
        with patch("src.backends.nmp.embedding.logger") as mock_logger:
            result = embedding_client.get_embedding("test")
            assert result is None
            mock_logger.error.assert_called_once()
            assert "Error calling embedding API" in mock_logger.error.call_args[0][0]
