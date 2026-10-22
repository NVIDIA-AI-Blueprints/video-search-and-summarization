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

from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from elasticsearch import ConnectionError

from src.lib.integration.es_client import (
    ES_COLLECTION_NAME,
    ES_EMBEDDINGS_DATA_COLLECTION_NAME,
    ES_EMBEDDINGS_INDEX_SETTINGS,
    ES_INDEX_SETTINGS,
    ES_URL,
    close_es_client,
    delete_embeddings_index,
    ensure_embeddings_index,
    get_es_client,
    index_embeddings_to_es,
    search_similar_embeddings,
)


class TestEmbeddingsFunctions:
    """Test cases for embedding-related functions."""

    def setup_method(self):
        """Set up a mock Elasticsearch client before each test."""
        self.mock_es_client = MagicMock()

    def test_ensure_embeddings_index_creates_index(self):
        """Test that the embeddings index is created if it does not exist."""
        self.mock_es_client.indices.exists.return_value = False
        index_name = "test_embeddings_index"
        ensure_embeddings_index(self.mock_es_client, index_name)
        # Just verify that create was called with the right index name
        # The exact body schema may change over time
        self.mock_es_client.indices.create.assert_called_once()
        call_args = self.mock_es_client.indices.create.call_args
        assert call_args.kwargs["index"] == index_name
        assert "body" in call_args.kwargs

    def test_ensure_embeddings_index_does_not_create_existing_index(self):
        """Test that the embeddings index is not created if it already exists."""
        self.mock_es_client.indices.exists.return_value = True
        index_name = "test_embeddings_index"
        ensure_embeddings_index(self.mock_es_client, index_name)
        self.mock_es_client.indices.create.assert_not_called()

    @patch("src.lib.integration.es_client.bulk")
    @patch("src.lib.integration.es_client.ensure_embeddings_index")
    @patch("src.lib.integration.es_client.extract_user_query")
    def test_index_embeddings_to_es(self, mock_extract_user_query, mock_ensure_index, mock_bulk):
        """Test the creation of the index and bulk indexing of documents."""
        mock_extract_user_query.side_effect = ["query1", "query2"]
        mock_bulk.return_value = (2, 0)
        binned_data = {
            "tool1": [
                (
                    [0.1, 0.2],
                    {
                        "workload_id": "w1",
                        "timestamp": 123,
                        "request": {"messages": [{"content": "query1"}]},
                    },
                ),
                (
                    [0.3, 0.4],
                    {
                        "workload_id": "w2",
                        "timestamp": 456,
                        "request": {"messages": [{"content": "query2"}]},
                    },
                ),
            ]
        }
        workload_id = "test_workload"
        client_id = "test_client"

        index_name = index_embeddings_to_es(
            self.mock_es_client, binned_data, workload_id, client_id
        )

        mock_ensure_index.assert_called_once()
        assert f"flywheel_icl_embeddings_index_{workload_id}_{client_id}" in index_name

        mock_bulk.assert_called_once()
        actions = mock_bulk.call_args.args[1]
        assert len(actions) == 2
        assert actions[0]["_source"]["tool_name"] == "tool1"
        assert actions[0]["_source"]["query_text"] == "query1"

    def test_search_similar_embeddings(self):
        """Test the k-NN vector search query."""
        self.mock_es_client.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_score": 0.9,
                        "_source": {
                            "tool_name": "test_tool",
                            "record": {"data": "test_data"},
                        },
                    }
                ]
            }
        }

        results = search_similar_embeddings(
            self.mock_es_client, [0.1, 0.2], "test_index", max_candidates=1
        )
        assert len(results) == 1
        score, tool_name, record = results[0]
        assert score == 0.9
        assert tool_name == "test_tool"
        assert record == {"data": "test_data"}

    def test_search_similar_embeddings_handles_exception(self):
        """Test that an empty list is returned when a search exception occurs."""
        self.mock_es_client.search.side_effect = Exception("Search failed")
        results = search_similar_embeddings(
            self.mock_es_client, [0.1, 0.2], "test_index", max_candidates=1
        )
        assert results == []

    def test_delete_embeddings_index(self):
        """Test the deletion of the embedding index."""
        self.mock_es_client.indices.exists.return_value = True
        delete_embeddings_index(self.mock_es_client, "test_index")
        self.mock_es_client.indices.delete.assert_called_with(index="test_index")

    def test_delete_embeddings_index_does_not_exist(self):
        """Test that delete is not called if the index does not exist."""
        self.mock_es_client.indices.exists.return_value = False
        delete_embeddings_index(self.mock_es_client, "test_index")
        self.mock_es_client.indices.delete.assert_not_called()

    def test_delete_embeddings_index_handles_exception(self):
        """Test that delete handles exceptions gracefully."""
        self.mock_es_client.indices.exists.return_value = True
        self.mock_es_client.indices.delete.side_effect = Exception("Delete failed")
        # Lines 364-365 coverage: exception handling in delete
        delete_embeddings_index(self.mock_es_client, "test_index")
        self.mock_es_client.indices.delete.assert_called_once()

    @patch("src.lib.integration.es_client.bulk")
    @patch("src.lib.integration.es_client.ensure_embeddings_index")
    @patch("src.lib.integration.es_client.extract_user_query")
    def test_index_embeddings_bulk_failures(
        self, mock_extract_user_query, mock_ensure_index, mock_bulk
    ):
        """Test handling of bulk indexing failures."""
        mock_extract_user_query.return_value = "query1"
        # Line 311 coverage: failed bulk indexing
        mock_bulk.return_value = (0, [{"error": "failed"}])
        binned_data = {
            "tool1": [
                (
                    [0.1, 0.2],
                    {
                        "workload_id": "w1",
                        "timestamp": 123,
                        "request": {"messages": [{"content": "query1"}]},
                    },
                )
            ]
        }
        index_name = index_embeddings_to_es(
            self.mock_es_client, binned_data, "workload_id", "client_id"
        )
        assert index_name is not None


class TestGetESClient:
    """Test cases for get_es_client function."""

    def setup_method(self):
        """Reset the global ES client before each test."""
        close_es_client()

    def teardown_method(self):
        """Clean up the global ES client after each test."""
        close_es_client()

    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_success_immediate(self, mock_sleep, mock_elasticsearch):
        """Test successful connection on first attempt."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.cluster.health.return_value = {"status": "green"}
        mock_client.indices.exists.return_value = False
        mock_client.indices.refresh.return_value = None
        mock_client.indices.create.return_value = None
        mock_elasticsearch.return_value = mock_client

        # Call function
        result = get_es_client()

        # Assertions
        assert result == mock_client
        mock_elasticsearch.assert_called_once_with(hosts=[ES_URL])
        mock_client.ping.assert_called_once()
        mock_client.cluster.health.assert_called_once()
        mock_client.indices.refresh.assert_called_once()
        # Check that both indices are checked for existence
        mock_client.indices.exists.assert_has_calls(
            [
                mock.call(index=ES_COLLECTION_NAME),
                mock.call(index=ES_EMBEDDINGS_DATA_COLLECTION_NAME),
            ]
        )
        # Check that both indices are created
        mock_client.indices.create.assert_any_call(index=ES_COLLECTION_NAME, body=ES_INDEX_SETTINGS)
        mock_client.indices.create.assert_any_call(
            index=ES_EMBEDDINGS_DATA_COLLECTION_NAME, body=ES_EMBEDDINGS_INDEX_SETTINGS
        )
        mock_sleep.assert_not_called()

    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_success_with_existing_index(self, mock_sleep, mock_elasticsearch):
        """Test successful connection when index already exists."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.cluster.health.return_value = {"status": "yellow"}
        mock_client.indices.exists.return_value = True
        mock_client.indices.refresh.return_value = None
        mock_elasticsearch.return_value = mock_client

        # Call function
        result = get_es_client()

        # Assertions
        assert result == mock_client
        # Check that both indices are checked for existence
        mock_client.indices.exists.assert_has_calls(
            [
                mock.call(index=ES_COLLECTION_NAME),
                mock.call(index=ES_EMBEDDINGS_DATA_COLLECTION_NAME),
            ]
        )
        mock_client.indices.create.assert_not_called()  # Should not create if exists
        mock_sleep.assert_not_called()

    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_retry_on_ping_failure(self, mock_sleep, mock_elasticsearch):
        """Test retry logic when ping fails initially."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.ping.side_effect = [False, False, True]  # Fail twice, succeed third time
        mock_client.cluster.health.return_value = {"status": "green"}
        mock_client.indices.exists.return_value = True
        mock_client.indices.refresh.return_value = None
        mock_elasticsearch.return_value = mock_client

        # Call function
        result = get_es_client()

        # Assertions
        assert result == mock_client
        assert mock_client.ping.call_count == 3
        assert mock_sleep.call_count == 2  # Sleep called twice before success

    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_retry_on_unhealthy_status(self, mock_sleep, mock_elasticsearch):
        """Test retry logic when cluster status is not healthy."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.cluster.health.side_effect = [
            {"status": "red"},  # Unhealthy
            {"status": "red"},  # Still unhealthy
            {"status": "green"},  # Finally healthy
        ]
        mock_client.indices.exists.return_value = True
        mock_client.indices.refresh.return_value = None
        mock_elasticsearch.return_value = mock_client

        # Call function
        result = get_es_client()

        # Assertions
        assert result == mock_client
        assert mock_client.cluster.health.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_connection_error_then_success(self, mock_sleep, mock_elasticsearch):
        """Test recovery from connection errors."""
        # Setup mocks - first call raises ConnectionError, second succeeds
        mock_client_success = MagicMock()
        mock_client_success.ping.return_value = True
        mock_client_success.cluster.health.return_value = {"status": "green"}
        mock_client_success.indices.exists.return_value = True
        mock_client_success.indices.refresh.return_value = None

        # First call raises ConnectionError, second call succeeds
        call_count = 0

        def side_effect_func(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Connection failed")
            return mock_client_success

        mock_elasticsearch.side_effect = side_effect_func

        # Call function
        result = get_es_client()

        # Assertions
        assert result == mock_client_success
        assert mock_elasticsearch.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_connection_error_max_retries(self, mock_sleep, mock_elasticsearch):
        """Test that RuntimeError is raised after max connection error retries."""

        # Setup mocks - always raise ConnectionError
        def connection_error_side_effect(*args, **kwargs):
            raise ConnectionError("Persistent connection error")

        mock_elasticsearch.side_effect = connection_error_side_effect

        # Call function and expect exception
        with pytest.raises(RuntimeError, match="Could not connect to Elasticsearch"):
            get_es_client()

    @patch("src.lib.integration.es_client.Elasticsearch")
    def test_get_es_client_reuses_healthy_connection(self, mock_elasticsearch):
        """Test that get_es_client reuses an existing healthy connection."""
        # First call - create connection
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.cluster.health.return_value = {"status": "green"}
        mock_client.indices.exists.return_value = True
        mock_client.indices.refresh.return_value = None
        mock_elasticsearch.return_value = mock_client

        first_client = get_es_client()

        # Second call - should reuse connection without creating new one
        second_client = get_es_client()

        assert first_client == second_client
        assert mock_elasticsearch.call_count == 1  # Only created once


class TestCloseESClient:
    """Test cases for close_es_client function."""

    def test_close_es_client_with_exception(self):
        """Test that close_es_client handles exceptions gracefully."""
        # Lines 258-259 coverage: exception handling in close
        mock_client = MagicMock()
        mock_client.close.side_effect = Exception("Close failed")

        import src.lib.integration.es_client as es_module

        es_module._es_client = mock_client

        # Should not raise exception
        close_es_client()
        mock_client.close.assert_called_once()


class TestGetESClientEdgeCases:
    """Additional edge case tests for get_es_client."""

    def setup_method(self):
        """Reset the global ES client before each test."""
        close_es_client()

    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_timeout_on_unhealthy_cluster(self, mock_sleep, mock_elasticsearch):
        """Test timeout when cluster never becomes healthy."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.cluster.health.return_value = {"status": "red"}  # Always unhealthy
        mock_elasticsearch.return_value = mock_client

        # Call function and expect exception
        with pytest.raises(RuntimeError, match="Elasticsearch did not become healthy in time"):
            get_es_client()

        # Assertions
        assert mock_client.ping.call_count == 30
        assert mock_client.cluster.health.call_count == 30
        assert mock_sleep.call_count == 30

    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_timeout_on_ping_failure(self, mock_sleep, mock_elasticsearch):
        """Test timeout when ping always fails."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.ping.return_value = False  # Always fails
        mock_elasticsearch.return_value = mock_client

        # Call function and expect exception
        with pytest.raises(RuntimeError, match="Elasticsearch did not become healthy in time"):
            get_es_client()

        # Assertions
        assert mock_client.ping.call_count == 30
        mock_client.cluster.health.assert_not_called()  # Should not be called if ping fails
        assert mock_sleep.call_count == 30

    @patch("src.lib.integration.es_client.logger")
    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_logging(self, mock_sleep, mock_elasticsearch, mock_logger):
        """Test that appropriate logging messages are generated."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.cluster.health.return_value = {"status": "green"}
        mock_client.indices.exists.return_value = False
        mock_client.indices.refresh.return_value = None
        mock_client.indices.create.return_value = None
        mock_elasticsearch.return_value = mock_client

        # Call function
        get_es_client()

        # Check logging calls
        mock_logger.info.assert_any_call("Elasticsearch is ready! Status: green")
        mock_logger.info.assert_any_call(f"Creating primary index: {ES_COLLECTION_NAME}...")

    @patch("src.lib.integration.es_client.logger")
    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_logging_existing_index(
        self, mock_sleep, mock_elasticsearch, mock_logger
    ):
        """Test logging when index already exists."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.cluster.health.return_value = {"status": "yellow"}
        mock_client.indices.exists.return_value = True
        mock_client.indices.refresh.return_value = None
        mock_elasticsearch.return_value = mock_client

        # Call function
        get_es_client()

        # Check logging calls
        mock_logger.info.assert_any_call("Elasticsearch is ready! Status: yellow")
        mock_logger.info.assert_any_call(f"Primary index '{ES_COLLECTION_NAME}' already exists.")

    @patch("src.lib.integration.es_client.logger")
    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_logging_waiting_for_health(
        self, mock_sleep, mock_elasticsearch, mock_logger
    ):
        """Test logging when waiting for cluster to become healthy."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.cluster.health.side_effect = [{"status": "red"}, {"status": "green"}]
        mock_client.indices.exists.return_value = True
        mock_client.indices.refresh.return_value = None
        mock_elasticsearch.return_value = mock_client

        # Call function
        get_es_client()

        # Check logging calls
        mock_logger.info.assert_any_call("Waiting for Elasticsearch to be healthy (status: red)...")
        mock_logger.info.assert_any_call("Elasticsearch is ready! Status: green")

    @patch("src.lib.integration.es_client.logger")
    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_logging_connection_error(
        self, mock_sleep, mock_elasticsearch, mock_logger
    ):
        """Test error logging on connection failure."""

        # Setup mocks
        def connection_error_side_effect(*args, **kwargs):
            raise ConnectionError("Test connection error")

        mock_elasticsearch.side_effect = connection_error_side_effect

        # Call function and expect exception
        with pytest.raises(RuntimeError):
            get_es_client()

        # Check error logging
        mock_logger.error.assert_any_call("Could not connect to Elasticsearch")

    @patch("src.lib.integration.es_client.logger")
    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_get_es_client_logging_timeout_error(self, mock_sleep, mock_elasticsearch, mock_logger):
        """Test error logging on timeout."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.ping.return_value = False
        mock_elasticsearch.return_value = mock_client

        # Call function and expect exception
        with pytest.raises(RuntimeError):
            get_es_client()

        # Check error logging
        mock_logger.error.assert_any_call("Elasticsearch did not become healthy in time")


class TestIntegration:
    """Integration tests for ES client functionality."""

    def setup_method(self):
        """Reset the global ES client before each test."""
        close_es_client()

    def teardown_method(self):
        """Clean up the global ES client after each test."""
        close_es_client()

    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_full_successful_workflow(self, mock_sleep, mock_elasticsearch):
        """Test the complete successful workflow."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.cluster.health.return_value = {"status": "green"}
        mock_client.indices.exists.return_value = False
        mock_client.indices.refresh.return_value = None
        mock_client.indices.create.return_value = None
        mock_elasticsearch.return_value = mock_client

        # Call function
        result = get_es_client()

        # Verify complete workflow
        assert result == mock_client

        # Check the sequence of operations
        mock_elasticsearch.assert_called_once_with(hosts=[ES_URL])
        mock_client.ping.assert_called_once()
        mock_client.cluster.health.assert_called_once()
        mock_client.indices.refresh.assert_called_once()
        # Check that both indices are checked for existence
        mock_client.indices.exists.assert_has_calls(
            [
                mock.call(index=ES_COLLECTION_NAME),
                mock.call(index=ES_EMBEDDINGS_DATA_COLLECTION_NAME),
            ]
        )
        # Check that both indices are created
        mock_client.indices.create.assert_any_call(index=ES_COLLECTION_NAME, body=ES_INDEX_SETTINGS)
        mock_client.indices.create.assert_any_call(
            index=ES_EMBEDDINGS_DATA_COLLECTION_NAME, body=ES_EMBEDDINGS_INDEX_SETTINGS
        )

    @patch("src.lib.integration.es_client.Elasticsearch")
    @patch("src.lib.integration.es_client.sleep")
    def test_retry_mechanism_with_mixed_failures(self, mock_sleep, mock_elasticsearch):
        """Test retry mechanism with different types of failures."""
        # Setup mocks for a complex scenario
        mock_client = MagicMock()

        # First attempt: ConnectionError (no ping call)
        # Second attempt: ping fails
        # Third attempt: ping succeeds, unhealthy status
        # Fourth attempt: ping succeeds, healthy status, success
        ping_side_effect = [False, True, True]
        health_side_effect = [{"status": "red"}, {"status": "green"}, {"status": "green"}]

        # Complex scenario with multiple failures and eventual success
        call_count = 0

        def side_effect_func(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Connection failed")
            return mock_client

        mock_elasticsearch.side_effect = side_effect_func

        mock_client.ping.side_effect = ping_side_effect
        mock_client.cluster.health.side_effect = health_side_effect
        mock_client.indices.exists.return_value = True
        mock_client.indices.refresh.return_value = None

        # Call function
        result = get_es_client()

        # Assertions
        assert result == mock_client
        assert (
            mock_elasticsearch.call_count >= 3
        )  # At least 3 calls (first fails, subsequent succeed)
        assert mock_client.ping.call_count == 3  # Called after each successful connection
        # Health is called on attempts where ping succeeds, then once more on the retry after red status
        assert mock_client.cluster.health.call_count >= 2
        # Sleep is called after: ConnectionError, ping failure, and red health status
        assert mock_sleep.call_count >= 3
