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

from unittest.mock import patch

import pytest

from src.api.models import WorkloadClassification
from src.config import DataSplitConfig
from src.lib.integration.data_validator import DataValidator


class TestDataValidator:
    """Test suite for DataValidator class."""

    @pytest.fixture
    def validator(self):
        """Create a DataValidator instance."""
        return DataValidator()

    @pytest.fixture
    def split_config(self):
        """Create a DataSplitConfig instance for testing."""
        return DataSplitConfig(
            val_ratio=0.2,
            limit=100,
            min_total_records=50,
            eval_size=20,
            parse_function_arguments=True,
        )

    @pytest.mark.parametrize(
        "workload_type,num_records,limit,min_records,expected_error",
        [
            # Sufficient records for GENERIC workload
            (WorkloadClassification.GENERIC, 150, 100, 20, None),
            # Exactly enough records
            (WorkloadClassification.GENERIC, 100, 100, 20, None),
            # Insufficient records, eval_size = 70 > 50
            (WorkloadClassification.GENERIC, 50, 100, 70, "Not enough records found"),
            # Tool calling workload with sufficient records which will be filtered out
            (WorkloadClassification.TOOL_CALLING, 150, 100, 20, "Insufficient valid records"),
            # Tool calling with insufficient records
            (WorkloadClassification.TOOL_CALLING, 50, 100, 20, "Insufficient valid records"),
        ],
    )
    def test_validate_records_basic_flow(
        self,
        validator,
        valid_openai_record,
        workload_type,
        num_records,
        limit,
        min_records,
        expected_error,
    ):
        """Test basic validation flow with different record counts and limits."""

        # Create test records with unique content to avoid deduplication
        records = []
        for i in range(num_records):
            record = {
                "request": {
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {
                            "role": "user",
                            "content": f"Question {i}: What is the capital of France?",
                        },
                    ]
                },
                "response": {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "The capital of France is Paris.",
                            }
                        }
                    ]
                },
            }
            records.append(record)

        if expected_error:
            with pytest.raises(ValueError) as exc_info:
                validator.validate_records(
                    records,
                    workload_type,
                    DataSplitConfig(
                        limit=limit, eval_size=min_records, min_total_records=min_records
                    ),
                )
            assert expected_error in str(exc_info.value)
        else:
            result = validator.validate_records(
                records,
                workload_type,
                DataSplitConfig(limit=limit, eval_size=min_records, min_total_records=min_records),
            )
            assert len(result) == min(limit, len(records))
            assert all(r in records for r in result)

    def test_validate_records_format_filtering(
        self, validator, valid_openai_record, invalid_openai_records
    ):
        """Test that invalid format records are filtered out."""

        # Mix valid and invalid records - create unique valid records
        valid_records = []
        for i in range(10):
            record = {
                "request": {"messages": [{"role": "user", "content": f"Valid question {i}"}]},
                "response": {"choices": [{"message": {"content": f"Valid response {i}"}}]},
            }
            valid_records.append(record)

        all_records = valid_records + invalid_openai_records

        result = validator.validate_records(
            all_records,
            WorkloadClassification.GENERIC,
            DataSplitConfig(limit=5, eval_size=5, min_total_records=5),
        )

        assert len(result) == 5
        assert all(r in valid_records for r in result)

        # Check stats
        stats = validator.get_validation_stats()
        assert stats["total_records"] == len(all_records)
        assert stats["valid_openai_format"] == len(valid_records)
        assert stats["invalid_format"] == len(invalid_openai_records)

    def test_validate_records_tool_calling_quality_filter(
        self, validator, valid_openai_record, openai_record_with_tool_calls, split_config
    ):
        """Test tool calling quality filtering for TOOL_CALLING workload."""

        # Create mix of records with and without tool calls - make them unique
        records_without_tools = []
        for i in range(10):
            record = {
                "request": {"messages": [{"role": "user", "content": f"Simple question {i}"}]},
                "response": {"choices": [{"message": {"content": f"Simple answer {i}"}}]},
            }
            records_without_tools.append(record)

        records_with_tools = []
        for i in range(5):
            record = {
                "request": {"messages": [{"role": "user", "content": f"Get weather for city {i}"}]},
                "response": {
                    "choices": [
                        {
                            "message": {
                                "content": "I'll check the weather",
                                "tool_calls": [
                                    {
                                        "id": f"call_{i}",
                                        "type": "function",
                                        "function": {
                                            "name": "get_weather",
                                            "arguments": f'{{"location": "City {i}"}}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            }
            records_with_tools.append(record)

        all_records = records_without_tools + records_with_tools

        result = validator.validate_records(
            all_records,
            WorkloadClassification.TOOL_CALLING,
            DataSplitConfig(limit=5, eval_size=5, min_total_records=5),
        )

        assert len(result) == 5
        assert all(r in records_with_tools for r in result)

        # Check stats
        stats = validator.get_validation_stats()
        assert stats["removed_quality_filters"] == len(records_without_tools)

    def test_validate_records_deduplication(self, validator, split_config):
        """Test deduplication based on user queries."""

        # Create records with duplicate user queries
        unique_record1 = {
            "request": {"messages": [{"role": "user", "content": "What is AI?"}]},
            "response": {"choices": [{"message": {"content": "AI is..."}}]},
        }

        unique_record2 = {
            "request": {"messages": [{"role": "user", "content": "Explain ML"}]},
            "response": {"choices": [{"message": {"content": "ML is..."}}]},
        }

        # Duplicate of unique_record1
        duplicate_record = {
            "request": {"messages": [{"role": "user", "content": "What is AI?"}]},
            "response": {"choices": [{"message": {"content": "Different response"}}]},
        }

        # Record with multiple user messages
        multi_user_record = {
            "request": {
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi"},
                    {"role": "user", "content": "How are you?"},
                ]
            },
            "response": {"choices": [{"message": {"content": "I'm good"}}]},
        }

        records = [unique_record1, unique_record2, duplicate_record, multi_user_record]

        result = validator.validate_records(
            records,
            WorkloadClassification.GENERIC,
            DataSplitConfig(limit=3, eval_size=3, min_total_records=3),
        )

        assert len(result) == 3
        # Duplicate should be removed
        assert duplicate_record not in result

        # Check stats
        stats = validator.get_validation_stats()
        assert stats["deduplicated_queries"] == 1

    def test_validate_records_no_user_messages(self, validator, split_config):
        """Test handling of records without user messages."""

        # Record without user messages
        no_user_record = {
            "request": {
                "messages": [
                    {"role": "system", "content": "You are helpful"},
                    {"role": "assistant", "content": "How can I help?"},
                ]
            },
            "response": {"choices": [{"message": {"content": "..."}}]},
        }

        # Normal record
        normal_record = {
            "request": {"messages": [{"role": "user", "content": "Hello"}]},
            "response": {"choices": [{"message": {"content": "Hi"}}]},
        }

        records = [no_user_record, normal_record]

        result = validator.validate_records(
            records,
            WorkloadClassification.GENERIC,
            DataSplitConfig(limit=2, eval_size=2, min_total_records=2),
        )

        # Both records should be kept
        assert len(result) == 2
        assert no_user_record in result
        assert normal_record in result

    @patch("src.lib.integration.data_validator.random")
    def test_validate_records_random_selection(
        self, mock_random, validator, valid_openai_record, monkeypatch
    ):
        """Test random selection with seed."""

        # Create more records than needed - make them unique
        records = []
        for i in range(10):
            record = {
                "request": {"messages": [{"role": "user", "content": f"Question {i}"}]},
                "response": {"choices": [{"message": {"content": f"Answer {i}"}}]},
            }
            records.append(record)

        # Mock random.sample to return first 5 records
        mock_random.sample.return_value = records[:5]

        result = validator.validate_records(
            records,
            WorkloadClassification.GENERIC,
            DataSplitConfig(limit=5, eval_size=5, min_total_records=5, random_seed=42),
        )

        # Verify seed was set
        mock_random.seed.assert_called_once_with(42)
        # Verify sample was called correctly
        mock_random.sample.assert_called_once_with(records, 5)
        assert result == records[:5]

    def test_get_tool_calling_records(self, validator, split_config_setup):
        """Test get_tool_calling_records method."""

        # Create test records
        tool_record = {
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {"name": "test", "arguments": '{"key": "value"}'},
                                }
                            ]
                        }
                    }
                ]
            }
        }

        non_tool_record = {"response": {"choices": [{"message": {"content": "Hi"}}]}}

        records = [tool_record, non_tool_record]

        result = validator.get_tool_calling_records(records, split_config_setup)

        # Only non-tool record should pass quality filter
        assert len(result) == 1
        assert result[0] == tool_record

    def test_validate_records_empty_input(self, validator, split_config):
        """Test validation with empty input."""

        with pytest.raises(ValueError) as exc_info:
            validator.validate_records(
                [],
                WorkloadClassification.GENERIC,
                DataSplitConfig(limit=10, eval_size=10, min_total_records=10),
            )

        assert "Not enough records found" in str(exc_info.value)
        assert "A minimum of 10 records is required" in str(exc_info.value)

    def test_validate_records_all_invalid_format(
        self, validator, invalid_openai_records, split_config
    ):
        """Test when all records have invalid format."""

        with pytest.raises(ValueError) as exc_info:
            validator.validate_records(
                invalid_openai_records,
                WorkloadClassification.GENERIC,
                DataSplitConfig(limit=5, eval_size=5, min_total_records=5),
            )

        assert "Insufficient valid records" in str(exc_info.value)
        assert "valid OpenAI format: 0" in str(exc_info.value)

    def test_validation_stats_tracking(self, validator, valid_openai_record, split_config):
        """Test that validation stats are properly tracked."""

        # Create records with one duplicate
        records = [
            valid_openai_record.copy(),
            valid_openai_record.copy(),  # Duplicate
            {
                "request": {"messages": [{"role": "user", "content": "Different"}]},
                "response": {"choices": [{"message": {"content": "Response"}}]},
            },
        ]

        validator.validate_records(
            records,
            WorkloadClassification.GENERIC,
            DataSplitConfig(limit=2, eval_size=2, min_total_records=2),
        )

        stats = validator.get_validation_stats()
        assert stats["total_records"] == 3
        assert stats["valid_openai_format"] == 3
        assert stats["invalid_format"] == 0
        assert stats["removed_quality_filters"] == 0
        assert stats["deduplicated_queries"] == 1
        assert stats["final_selected"] == 2

    @pytest.mark.parametrize(
        "parse_setting",
        [
            True,  # Should parse
            False,  # Should not parse
        ],
    )
    def test_parse_function_arguments_setting(self, validator, split_config_setup, parse_setting):
        """Test that parse_function_arguments setting is respected and JSON validation occurs."""

        # Create a record with tool calls that have JSON arguments
        record_with_function_calls = {
            "response": {
                "choices": [
                    {
                        "message": {
                            "content": "I'll help you with that.",
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"location": "San Francisco", "unit": "celsius"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        }
        simple_record = {"response": {"choices": [{"message": {"content": "Hi"}}]}}

        validator.get_tool_calling_records(
            [record_with_function_calls], split_config_setup
        )  # updates in place

        if parse_setting:
            tool_call = record_with_function_calls["response"]["choices"][0]["message"][
                "tool_calls"
            ][0]
            arguments_str = tool_call["function"]["arguments"]

            # This should not raise an exception if it's valid JSON
            try:
                assert isinstance(tool_call, dict)
                assert "location" in arguments_str
                assert "unit" in arguments_str
                assert arguments_str["location"] == "San Francisco"
                assert arguments_str["unit"] == "celsius"
            except Exception:
                pytest.fail(
                    "Function arguments should be valid JSON when parse_function_arguments=True"
                )

        assert validator.get_tool_calling_records([simple_record], split_config_setup) == []

    def test_deduplicate_records_complex_queries(self, validator):
        """Test deduplication with complex multi-turn conversations."""
        # Records with same user queries but in different order
        record1 = {
            "request": {
                "messages": [
                    {"role": "user", "content": "What is AI?"},
                    {"role": "assistant", "content": "AI is..."},
                    {"role": "user", "content": "Tell me more"},
                ]
            },
            "response": {"choices": [{"message": {"content": "..."}}]},
        }

        # Same user queries, same order - should be duplicate
        record2 = {
            "request": {
                "messages": [
                    {"role": "user", "content": "What is AI?"},
                    {"role": "assistant", "content": "Different response"},
                    {"role": "user", "content": "Tell me more"},
                ]
            },
            "response": {"choices": [{"message": {"content": "..."}}]},
        }

        # Different user queries
        record3 = {
            "request": {
                "messages": [
                    {"role": "user", "content": "What is ML?"},
                    {"role": "assistant", "content": "ML is..."},
                    {"role": "user", "content": "Tell me more"},
                ]
            },
            "response": {"choices": [{"message": {"content": "..."}}]},
        }

        records = [record1, record2, record3]
        result = validator._deduplicate_records(records)

        assert len(result) == 2
        assert record1 in result
        assert record2 not in result  # Duplicate removed
        assert record3 in result

    def test_deduplicate_records_error_handling(self, validator):
        """Test deduplication handles malformed records gracefully."""
        records = [
            # Valid record
            {
                "request": {"messages": [{"role": "user", "content": "Hello"}]},
                "response": {"choices": [{"message": {"content": "Hi"}}]},
            },
            # Missing request
            {"response": {"choices": []}},
            # Missing messages
            {"request": {}, "response": {"choices": []}},
            # Messages not a list
            {"request": {"messages": "not a list"}, "response": {"choices": []}},
        ]

        # Should not raise exception
        result = validator._deduplicate_records(records)

        # All records should be kept (malformed ones can't be deduplicated)
        assert len(result) == 4

    def test_validate_records_limit_less_than_min_records_error(self, validator):
        """Test error when limit is less than min_total_records."""
        # Create valid records
        records = []
        for i in range(10):
            record = {
                "request": {"messages": [{"role": "user", "content": f"Question {i}"}]},
                "response": {"choices": [{"message": {"content": f"Answer {i}"}}]},
            }
            records.append(record)

        # Test case where limit (5) < min_total_records (10)
        with pytest.raises(ValueError) as exc_info:
            validator.validate_records(
                records,
                WorkloadClassification.GENERIC,
                DataSplitConfig(limit=5, min_total_records=10, eval_size=5),
            )

        assert "limit cannot be less than the minimum number of records" in str(exc_info.value)
        assert "limit is 5, but the minimum number of records is 10" in str(exc_info.value)
