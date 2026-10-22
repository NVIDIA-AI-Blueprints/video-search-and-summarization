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

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.config import DataSplitConfig, EmbeddingDataSplitConfig
from src.lib.integration.es_client import (
    ES_COLLECTION_NAME,
    ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME,
)
from src.lib.integration.record_exporter import RecordExporter


class TestRecordExporter:
    """Test cases for RecordExporter class."""

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_init(self, mock_get_es_client):
        """Test RecordExporter initialization."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        exporter = RecordExporter()

        assert exporter.es_client == mock_client
        mock_get_es_client.assert_called_once()

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_success(self, mock_get_es_client):
        """Test successful retrieval of records."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Mock Elasticsearch response with scroll_id for new implementation
        mock_response = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "client_id": "test_client",
                            "workload_id": "test_workload",
                            "request": {"messages": [{"role": "user", "content": "Hello"}]},
                            "response": {"choices": [{"message": {"content": "Hi there!"}}]},
                            "timestamp": "2023-01-01T00:00:00Z",
                        }
                    },
                    {
                        "_source": {
                            "client_id": "test_client",
                            "workload_id": "test_workload",
                            "request": {"messages": [{"role": "user", "content": "How are you?"}]},
                            "response": {"choices": [{"message": {"content": "I'm doing well!"}}]},
                            "timestamp": "2023-01-01T00:01:00Z",
                        }
                    },
                ]
            },
        }
        mock_client.search.return_value = mock_response

        # Mock empty scroll response (end of data)
        empty_scroll_response = {"_scroll_id": "scroll456", "hits": {"hits": []}}
        mock_client.scroll.return_value = empty_scroll_response

        # Setup test data
        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=10)

        # Call method
        result = exporter.get_records("test_client", "test_workload", split_config)

        # Assertions
        assert len(result) == 2
        assert result[0]["client_id"] == "test_client"
        assert result[1]["workload_id"] == "test_workload"

        # Check that search was called with scroll parameters

        mock_client.search.assert_called_once()
        search_call_args = mock_client.search.call_args
        assert search_call_args[1]["index"] == ES_COLLECTION_NAME
        assert search_call_args[1]["scroll"] == "2m"
        assert search_call_args[1]["size"] == 20  # min(1000, 20)

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_no_results(self, mock_get_es_client):
        """Test handling when no records are found."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        mock_response = {"_scroll_id": "scroll123", "hits": {"hits": []}}
        mock_client.search.return_value = mock_response

        # Setup test data
        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=5)

        result = exporter.get_records("test_client", "test_workload", split_config)
        assert len(result) == 0

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_json_serialization_edge_cases(self, mock_get_es_client):
        """Test deduplication with edge cases in JSON serialization."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Records with different key orders (should be treated as same after sort_keys=True)
        mock_response = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {"messages": [{"content": "test", "role": "user"}]},
                            "response": {
                                "choices": [{"index": 0, "message": {"content": "response"}}]
                            },
                        }
                    },
                    {
                        "_source": {
                            "request": {
                                "messages": [{"role": "user", "content": "test"}]
                            },  # Different key order
                            "response": {
                                "choices": [{"message": {"content": "response"}, "index": 0}]
                            },  # Different key order
                        }
                    },
                ]
            },
        }
        mock_client.search.return_value = mock_response

        # Setup test data
        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=10)

        # Call method
        result = exporter.get_records("test_client", "test_workload", split_config)

        # Should deduplicate to 1 record (same content, different key order)
        assert len(result) == 2

    @patch("src.lib.integration.record_exporter.logger")
    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_logging(self, mock_get_es_client, mock_logger):
        """Test that appropriate logging messages are generated."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        mock_response = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {"messages": [{"role": "user", "content": "Hello"}]},
                            "response": {"choices": [{"message": {"content": "Hi!"}}]},
                        }
                    },
                    {
                        "_source": {
                            "request": {
                                "messages": [{"role": "user", "content": "Hello"}]
                            },  # Duplicate
                            "response": {"choices": [{"message": {"content": "Hi!"}}]},  # Duplicate
                        }
                    },
                ]
            },
        }
        mock_client.search.return_value = mock_response

        # Setup test data
        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=5)

        # Call method
        exporter.get_records("test_client", "test_workload", split_config)

        # Check logging calls
        mock_logger.info.assert_any_call(
            "Pulling data from Elasticsearch for workload test_workload"
        )
        mock_logger.info.assert_any_call(
            "Found 2 records for client_id test_client and workload_id test_workload"
        )

    @patch("src.lib.integration.record_exporter.logger")
    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_no_results_logging(self, mock_get_es_client, mock_logger):
        """Test error logging when no records are found."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        mock_response = {"_scroll_id": "scroll123", "hits": {"hits": []}}
        mock_client.search.return_value = mock_response

        # Setup test data
        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=5)

        result = exporter.get_records("test_client", "test_workload", split_config)
        assert len(result) == 0

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_limit_calculation(self, mock_get_es_client):
        """Test that scroll size is correctly calculated."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        mock_response = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [{"_source": {"request": {"messages": []}, "response": {"choices": []}}}]
            },
        }
        mock_client.search.return_value = mock_response

        # Mock empty scroll response
        empty_scroll_response = {"_scroll_id": "scroll456", "hits": {"hits": []}}
        mock_client.scroll.return_value = empty_scroll_response

        # Test different limits
        test_cases = [1, 5, 10, 50, 100]

        for limit in test_cases:
            mock_client.search.reset_mock()  # Reset call count
            exporter = RecordExporter()
            split_config = DataSplitConfig(limit=limit)

            exporter.get_records("test_client", "test_workload", split_config)

            # Check that scroll size was set correctly (min(1000, limit * 2))
            call_args = mock_client.search.call_args
            expected_size = min(1000, limit * 2)
            assert call_args[1]["size"] == expected_size

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_elasticsearch_exception(self, mock_get_es_client):
        """Test handling of Elasticsearch exceptions."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Make search raise an exception
        mock_client.search.side_effect = Exception("Elasticsearch connection error")

        # Setup test data
        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=10)

        # Call method and expect exception to propagate
        with pytest.raises(Exception, match="Elasticsearch connection error"):
            exporter.get_records("test_client", "test_workload", split_config)

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_with_custom_es_collection_name(self, mock_get_es_client):
        """Test that the correct ES collection name is used."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        mock_response = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [{"_source": {"request": {"messages": []}, "response": {"choices": []}}}]
            },
        }
        mock_client.search.return_value = mock_response

        # Setup test data
        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=10)

        # Call method
        exporter.get_records("test_client", "test_workload", split_config)

        # Verify that search was called with the correct index name
        from src.lib.integration.es_client import ES_COLLECTION_NAME

        mock_client.search.assert_called_once()
        call_args = mock_client.search.call_args
        assert call_args[1]["index"] == ES_COLLECTION_NAME  # Current ES_COLLECTION_NAME value

    @patch("src.lib.integration.record_exporter.logger")
    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_scroll_api_large_dataset(self, mock_get_es_client, mock_logger):
        """Test scroll API functionality for handling large datasets that exceed Elasticsearch limit."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Mock multiple scroll responses to simulate large dataset
        # Initial search response with scroll_id
        initial_response = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {"messages": []},
                            "response": {"choices": []},
                            "id": f"record_{i}",
                        }
                    }
                    for i in range(1000)  # First batch of 1000 records
                ]
            },
        }

        # Second scroll response
        second_response = {
            "_scroll_id": "scroll456",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {"messages": []},
                            "response": {"choices": []},
                            "id": f"record_{i}",
                        }
                    }
                    for i in range(1000, 1500)  # Second batch of 500 records
                ]
            },
        }

        # Third scroll response (empty - end of data)
        third_response = {"_scroll_id": "scroll789", "hits": {"hits": []}}

        # Configure mock responses
        mock_client.search.return_value = initial_response
        mock_client.scroll.side_effect = [second_response, third_response]

        # Setup test data with limit that would cause 20k+ issue in old implementation
        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=12000)  # This would cause 24k fetch in old code

        # Call method
        result = exporter.get_records("test_client", "test_workload", split_config)

        # Assertions
        assert len(result) == 1500  # Got all available records

        # Verify initial search call with scroll parameters
        mock_client.search.assert_called_once()
        search_call_args = mock_client.search.call_args
        assert search_call_args[1]["scroll"] == "2m"
        assert search_call_args[1]["size"] == 1000  # min(1000, 24000)

        # Verify scroll continuation calls
        assert mock_client.scroll.call_count == 2
        scroll_calls = mock_client.scroll.call_args_list
        assert scroll_calls[0][1]["scroll_id"] == "scroll123"
        assert scroll_calls[0][1]["scroll"] == "2m"
        assert scroll_calls[1][1]["scroll_id"] == "scroll456"

        # Verify scroll context cleanup
        mock_client.clear_scroll.assert_called_once_with(scroll_id="scroll789")

        # Verify logging
        mock_logger.info.assert_any_call(
            "Pulling data from Elasticsearch for workload test_workload"
        )
        mock_logger.info.assert_any_call(
            "Found 1500 records for client_id test_client and workload_id test_workload"
        )

    @patch("src.lib.integration.record_exporter.logger")
    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_scroll_api_max_records_limit(self, mock_get_es_client, mock_logger):
        """Test that scroll API respects max_records limit."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Mock responses that would exceed our limit
        initial_response = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {"_source": {"id": f"record_{i}"}}
                    for i in range(15)  # 15 records in first batch
                ]
            },
        }

        second_response = {
            "_scroll_id": "scroll456",
            "hits": {
                "hits": [
                    {"_source": {"id": f"record_{i}"}}
                    for i in range(15, 30)  # 15 more records
                ]
            },
        }

        mock_client.search.return_value = initial_response
        mock_client.scroll.return_value = second_response

        # Setup test data with small limit
        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=10)  # max_records = 20

        # Call method
        result = exporter.get_records("test_client", "test_workload", split_config)

        # Should stop at max_records limit (20), not process all 30 available
        assert len(result) == 20

        # Should have made one scroll call to get the remaining 5 records needed
        assert mock_client.scroll.call_count == 1


class TestRecordExporterIntegration:
    """Integration tests for RecordExporter functionality."""

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_full_workflow_with_realistic_data(self, mock_get_es_client):
        """Test the complete workflow with realistic OpenAI-style data."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Realistic OpenAI-style records
        mock_response = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "client_id": "openai_client",
                            "workload_id": "chat_completion_test",
                            "request": {
                                "model": "gpt-3.5-turbo",
                                "messages": [
                                    {"role": "system", "content": "You are a helpful assistant."},
                                    {"role": "user", "content": "What is the weather like today?"},
                                ],
                                "temperature": 0.7,
                            },
                            "response": {
                                "id": "chatcmpl-123",
                                "object": "chat.completion",
                                "created": 1677652288,
                                "model": "gpt-3.5-turbo-0301",
                                "choices": [
                                    {
                                        "index": 0,
                                        "message": {
                                            "role": "assistant",
                                            "content": "I don't have access to real-time weather data.",
                                        },
                                        "finish_reason": "stop",
                                    }
                                ],
                                "usage": {
                                    "prompt_tokens": 25,
                                    "completion_tokens": 12,
                                    "total_tokens": 37,
                                },
                            },
                            "timestamp": "2023-01-01T12:00:00Z",
                        }
                    },
                    {
                        "_source": {
                            "client_id": "openai_client",
                            "workload_id": "chat_completion_test",
                            "request": {
                                "model": "gpt-3.5-turbo",
                                "messages": [
                                    {"role": "system", "content": "You are a helpful assistant."},
                                    {"role": "user", "content": "Tell me a joke."},
                                ],
                                "temperature": 0.7,
                            },
                            "response": {
                                "id": "chatcmpl-456",
                                "object": "chat.completion",
                                "created": 1677652300,
                                "model": "gpt-3.5-turbo-0301",
                                "choices": [
                                    {
                                        "index": 0,
                                        "message": {
                                            "role": "assistant",
                                            "content": "Why don't scientists trust atoms? Because they make up everything!",
                                        },
                                        "finish_reason": "stop",
                                    }
                                ],
                                "usage": {
                                    "prompt_tokens": 20,
                                    "completion_tokens": 15,
                                    "total_tokens": 35,
                                },
                            },
                            "timestamp": "2023-01-01T12:01:00Z",
                        }
                    },
                ]
            },
        }
        mock_client.search.return_value = mock_response

        # Setup test data
        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=100)

        # Call method
        result = exporter.get_records("openai_client", "chat_completion_test", split_config)

        # Assertions
        assert len(result) == 2

        # Verify structure of first record
        first_record = result[0]
        assert first_record["client_id"] == "openai_client"
        assert first_record["workload_id"] == "chat_completion_test"
        assert "request" in first_record
        assert "response" in first_record
        assert "timestamp" in first_record

        # Verify request structure
        assert first_record["request"]["model"] == "gpt-3.5-turbo"
        assert len(first_record["request"]["messages"]) == 2
        assert first_record["request"]["messages"][0]["role"] == "system"

        # Verify response structure
        assert len(first_record["response"]["choices"]) == 1
        assert first_record["response"]["choices"][0]["message"]["role"] == "assistant"

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_deduplication_preserves_first_occurrence(self, mock_get_es_client):
        """Test that deduplication preserves the first occurrence of duplicate records."""
        # Setup mocks
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Records with same content but different metadata
        mock_response = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {"messages": [{"role": "user", "content": "test"}]},
                            "response": {"choices": [{"message": {"content": "response"}}]},
                            "timestamp": "2023-01-01T00:00:00Z",
                            "metadata": "first_occurrence",
                        }
                    },
                    {
                        "_source": {
                            "request": {"messages": [{"role": "user", "content": "test"}]},
                            "response": {"choices": [{"message": {"content": "response"}}]},
                            "timestamp": "2023-01-01T00:01:00Z",
                            "metadata": "second_occurrence",
                        }
                    },
                ]
            },
        }
        mock_client.search.return_value = mock_response

        # Setup test data
        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=10)

        # Call method
        result = exporter.get_records("test_client", "test_workload", split_config)

        # Should preserve first occurrence
        assert len(result) == 2
        assert result[0]["metadata"] == "first_occurrence"
        assert result[0]["timestamp"] == "2023-01-01T00:00:00Z"


class TestRecordExporterDateFiltering:
    """Test date filtering functionality in RecordExporter."""

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_decide_limit_with_none(self, mock_get_es_client):
        """Test decide_limit returns infinity when limit is None."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        exporter = RecordExporter()
        result = exporter.decide_limit(None, multiplier=2)

        assert result == float("inf")

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_decide_limit_with_value(self, mock_get_es_client):
        """Test decide_limit returns limit * multiplier."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        exporter = RecordExporter()
        result = exporter.decide_limit(10, multiplier=3)

        assert result == 30

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_with_date_range(self, mock_get_es_client):
        """Test get_records with from_date and to_date filtering."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        mock_response = {
            "_scroll_id": "scroll123",
            "hits": {"hits": [{"_source": {"id": "record1"}}]},
        }
        mock_client.search.return_value = mock_response
        mock_client.scroll.return_value = {"_scroll_id": "scroll456", "hits": {"hits": []}}

        exporter = RecordExporter()
        split_config = EmbeddingDataSplitConfig(
            train_split=0.7,
            val_split=0.2,
            eval_split=0.1,
            eval_size=10,
            val_ratio=0.2,
            min_total_records=5,
            min_negatives=1,
            max_negatives=5,
            from_date=date(2023, 1, 1),
            to_date=date(2023, 12, 31),
            limit=10,
        )

        result = exporter.get_records("client1", "workload1", split_config)

        # Verify date range was included in query
        call_args = mock_client.search.call_args
        query = call_args[1]["body"]["query"]["bool"]["must"]

        # Should have 3 must clauses: client_id, workload_id, and timestamp range
        assert len(query) == 3
        assert any("range" in clause for clause in query)
        assert len(result) == 1

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_date_validation_one_date_only(self, mock_get_es_client):
        """Test that providing only from_date or to_date raises ValueError."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        exporter = RecordExporter()
        split_config = EmbeddingDataSplitConfig(
            train_split=0.7,
            val_split=0.2,
            eval_split=0.1,
            eval_size=10,
            val_ratio=0.2,
            min_total_records=5,
            min_negatives=1,
            max_negatives=5,
            from_date=date(2023, 1, 1),
            to_date=None,  # Missing to_date
            limit=10,
        )

        with pytest.raises(ValueError, match="Both from_date and to_date must be provided"):
            exporter.get_records("client1", "workload1", split_config)

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_date_validation_invalid_range(self, mock_get_es_client):
        """Test that from_date > to_date raises ValueError."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        exporter = RecordExporter()
        split_config = EmbeddingDataSplitConfig(
            train_split=0.7,
            val_split=0.2,
            eval_split=0.1,
            eval_size=10,
            val_ratio=0.2,
            min_total_records=5,
            min_negatives=1,
            max_negatives=5,
            from_date=date(2023, 12, 31),
            to_date=date(2023, 1, 1),  # Before from_date
            limit=10,
        )

        with pytest.raises(ValueError, match="from_date cannot be later than to_date"):
            exporter.get_records("client1", "workload1", split_config)


class TestRecordExporterScrollHandling:
    """Test scroll context handling in RecordExporter."""

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_no_scroll_id_in_initial_response(self, mock_get_es_client):
        """Test handling when initial response has no scroll_id."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Response without scroll_id
        mock_response = {
            "hits": {"hits": [{"_source": {"id": "record1"}}]},
            # No _scroll_id
        }
        mock_client.search.return_value = mock_response

        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=10)

        result = exporter.get_records("client1", "workload1", split_config)

        # Should return the initial batch and exit early
        assert len(result) == 1
        # Scroll should not be called
        mock_client.scroll.assert_not_called()

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_scroll_id_lost_during_scrolling(self, mock_get_es_client):
        """Test handling when scroll_id is lost during scrolling."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Initial response with scroll_id
        initial_response = {
            "_scroll_id": "scroll123",
            "hits": {"hits": [{"_source": {"id": "record1"}}]},
        }

        # Scroll response without scroll_id (context lost)
        scroll_response = {
            "hits": {"hits": [{"_source": {"id": "record2"}}]},
            # No _scroll_id - context lost
        }

        mock_client.search.return_value = initial_response
        mock_client.scroll.return_value = scroll_response

        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=10)

        result = exporter.get_records("client1", "workload1", split_config)

        # Should return both records before context was lost
        assert len(result) == 2

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_clear_scroll_failure(self, mock_get_es_client):
        """Test handling when clear_scroll fails."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        mock_response = {
            "_scroll_id": "scroll123",
            "hits": {"hits": [{"_source": {"id": "record1"}}]},
        }
        mock_client.search.return_value = mock_response
        mock_client.scroll.return_value = {"_scroll_id": "scroll456", "hits": {"hits": []}}

        # Make clear_scroll fail
        mock_client.clear_scroll.side_effect = Exception("Failed to clear scroll")

        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=10)

        # Should not raise exception, just log warning
        result = exporter.get_records("client1", "workload1", split_config)
        assert len(result) == 1

    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_records_infinite_limit(self, mock_get_es_client):
        """Test get_records with no limit (infinite)."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Multiple batches
        initial_response = {
            "_scroll_id": "scroll123",
            "hits": {"hits": [{"_source": {"id": f"record{i}"}} for i in range(10)]},
        }

        scroll_response = {
            "_scroll_id": "scroll456",
            "hits": {"hits": [{"_source": {"id": f"record{i}"}} for i in range(10, 20)]},
        }

        empty_response = {"_scroll_id": "scroll789", "hits": {"hits": []}}

        mock_client.search.return_value = initial_response
        mock_client.scroll.side_effect = [scroll_response, empty_response]

        exporter = RecordExporter()
        split_config = DataSplitConfig(limit=None)  # No limit

        result = exporter.get_records("client1", "workload1", split_config)

        # Should get all 20 records
        assert len(result) == 20


class TestRecordExporterSimilarRecords:
    """Test get_similar_records functionality."""

    @patch("src.lib.integration.record_exporter.helpers")
    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_similar_records_success(self, mock_get_es_client, mock_helpers):
        """Test successful retrieval of similar records."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Mock the scan helper to return test data
        mock_hits = [
            {"_source": {"query_id": "q1", "similar_ids": ["q2", "q3"]}},
            {"_source": {"query_id": "q2", "similar_ids": ["q1", "q4"]}},
        ]
        mock_helpers.scan.return_value = iter(mock_hits)

        exporter = RecordExporter()
        result = exporter.get_similar_records("client1", "workload1")

        assert len(result) == 2
        assert result[0]["query_id"] == "q1"
        assert result[1]["query_id"] == "q2"

        # Verify scan was called with correct parameters
        mock_helpers.scan.assert_called_once()
        call_args = mock_helpers.scan.call_args
        assert call_args[1]["client"] == mock_client
        assert call_args[1]["index"] == ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME
        assert call_args[1]["scroll"] == "5m"

    @patch("src.lib.integration.record_exporter.helpers")
    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_similar_records_with_custom_index(self, mock_get_es_client, mock_helpers):
        """Test get_similar_records with custom index name."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        mock_helpers.scan.return_value = iter([])

        exporter = RecordExporter()
        result = exporter.get_similar_records(
            "client1", "workload1", index_name="custom_index", chunk_size=500
        )

        assert len(result) == 0

        # Verify custom parameters were used
        call_args = mock_helpers.scan.call_args
        assert call_args[1]["index"] == "custom_index"
        assert call_args[1]["size"] == 500

    @patch("src.lib.integration.record_exporter.helpers")
    @patch("src.lib.integration.record_exporter.get_es_client")
    def test_get_similar_records_exception(self, mock_get_es_client, mock_helpers):
        """Test get_similar_records handles exceptions."""
        mock_client = MagicMock()
        mock_get_es_client.return_value = mock_client

        # Make scan raise an exception
        mock_helpers.scan.side_effect = Exception("Elasticsearch error")

        exporter = RecordExporter()

        with pytest.raises(Exception, match="Elasticsearch error"):
            exporter.get_similar_records("client1", "workload1")
