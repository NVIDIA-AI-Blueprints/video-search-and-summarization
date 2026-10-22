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
from unittest.mock import patch

import pytest

from src.lib.integration.openai_format_validator import OpenAIFormatValidator


class TestOpenAIFormatValidator:
    """Test suite for OpenAIFormatValidator class."""

    @pytest.fixture
    def validator(self):
        """Create an OpenAIFormatValidator instance."""
        return OpenAIFormatValidator()

    @pytest.mark.parametrize(
        "record,expected",
        [
            # Valid basic chat completion format
            (
                {
                    "request": {"messages": [{"role": "user", "content": "Hello"}]},
                    "response": {"choices": [{"message": {"content": "Hi there!"}}]},
                },
                True,
            ),
            # Valid with multiple messages
            (
                {
                    "request": {
                        "messages": [
                            {"role": "system", "content": "You are helpful"},
                            {"role": "user", "content": "Hello"},
                            {"role": "assistant", "content": "Hi!"},
                            {"role": "user", "content": "How are you?"},
                        ]
                    },
                    "response": {"choices": [{"message": {"content": "I'm doing well!"}}]},
                },
                True,
            ),
            # Valid with multiple choices
            (
                {
                    "request": {"messages": [{"role": "user", "content": "Hello"}]},
                    "response": {
                        "choices": [
                            {"message": {"content": "Hi!"}},
                            {"message": {"content": "Hello!"}},
                        ]
                    },
                },
                True,
            ),
            # Valid with empty messages list
            (
                {
                    "request": {"messages": []},
                    "response": {"choices": []},
                },
                False,
            ),
            # Invalid - missing request
            (
                {
                    "response": {"choices": [{"message": {"content": "Hi!"}}]},
                },
                False,
            ),
            # Invalid - missing response
            (
                {
                    "request": {"messages": [{"role": "user", "content": "Hello"}]},
                },
                False,
            ),
            # Invalid - request is not a dict
            (
                {
                    "request": "not a dict",
                    "response": {"choices": []},
                },
                False,
            ),
            # Invalid - response is not a dict
            (
                {
                    "request": {"messages": []},
                    "response": "not a dict",
                },
                False,
            ),
            # Invalid - missing messages in request
            (
                {
                    "request": {},
                    "response": {"choices": []},
                },
                False,
            ),
            # Invalid - messages is not a list
            (
                {
                    "request": {"messages": "not a list"},
                    "response": {"choices": []},
                },
                False,
            ),
            # Invalid - missing choices in response
            (
                {
                    "request": {"messages": [{"role": "user", "content": "Hello"}]},
                    "response": {},
                },
                False,
            ),
            # Invalid - choices is not a list
            (
                {
                    "request": {"messages": [{"role": "user", "content": "Hello"}]},
                    "response": {"choices": "not a list"},
                },
                False,
            ),
            # Invalid - empty dict
            ({}, False),
            # Invalid - None values
            (
                {
                    "request": None,
                    "response": {"choices": []},
                },
                False,
            ),
            # Test case to reach line 52 (missing choices key)
            (
                {
                    "request": {"messages": [{"role": "user", "content": "test"}]},
                    "response": {"not_choices": []},
                },
                False,
            ),
            # Test case to reach line 54 (choices not a list)
            (
                {
                    "request": {"messages": [{"role": "user", "content": "test"}]},
                    "response": {"choices": {"not": "a list"}},
                },
                False,
            ),
            # Test case to reach line 56 (empty choices list)
            (
                {
                    "request": {"messages": [{"role": "user", "content": "test"}]},
                    "response": {"choices": []},
                },
                False,
            ),
        ],
    )
    def test_validate_chat_completion_format(self, validator, record, expected):
        """Test chat completion format validation with various inputs."""
        assert validator.validate_chat_completion_format(record) == expected

    @pytest.mark.parametrize(
        "record,expected",
        [
            # No tool calls - quality check fails
            (
                {
                    "request": {"messages": [{"role": "user", "content": "Hello"}]},
                    "response": {"choices": [{"message": {"content": "Hi!"}}]},
                },
                False,
            ),
            # Has tool_calls in message - quality check Passes
            (
                {
                    "request": {"messages": [{"role": "user", "content": "Get weather"}]},
                    "response": {
                        "choices": [
                            {
                                "message": {
                                    "content": "I'll check the weather",
                                    "tool_calls": [
                                        {
                                            "id": "call_123",
                                            "type": "function",
                                            "function": {
                                                "name": "get_weather",
                                                "arguments": '{"location": "NYC"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                },
                True,
            ),
            # Empty tool_calls list - quality check fails
            (
                {
                    "request": {"messages": [{"role": "user", "content": "Hello"}]},
                    "response": {"choices": [{"message": {"content": "Hi!", "tool_calls": []}}]},
                },
                False,
            ),
            # None tool_calls - quality check fails
            (
                {
                    "request": {"messages": [{"role": "user", "content": "Hello"}]},
                    "response": {"choices": [{"message": {"content": "Hi!", "tool_calls": None}}]},
                },
                False,
            ),
            # Multiple choices, one with tool calls - quality check passes
            (
                {
                    "request": {"messages": [{"role": "user", "content": "Get weather"}]},
                    "response": {
                        "choices": [
                            {"message": {"content": "Hi!"}},
                            {
                                "message": {
                                    "content": "Checking...",
                                    "tool_calls": [
                                        {"type": "function", "function": {"name": "get_weather"}}
                                    ],
                                }
                            },
                        ]
                    },
                },
                True,
            ),
            # Invalid structure - returns True (no tool calls found)
            (
                {
                    "request": {"messages": []},
                    "response": {},
                },
                False,
            ),
        ],
    )
    def test_validate_tool_calling_quality(self, validator, record, expected):
        """Test tool calling quality validation."""
        assert validator.validate_tool_calling_quality(record) == expected

    @pytest.mark.parametrize(
        "record,has_tool_calls",
        [
            # No tool calls
            (
                {
                    "response": {"choices": [{"message": {"content": "Hi!"}}]},
                },
                False,
            ),
            # Has tool_calls array
            (
                {
                    "response": {
                        "choices": [
                            {
                                "message": {
                                    "tool_calls": [
                                        {"type": "function", "function": {"name": "get_weather"}}
                                    ]
                                }
                            }
                        ]
                    },
                },
                True,
            ),
            # Empty response
            ({"response": {}}, False),
            # No response key
            ({}, False),
            # Empty choices
            ({"response": {"choices": []}}, False),
            # Invalid structure handled gracefully
            ({"response": "not a dict"}, False),
            # Tool call missing type field - should fail
            (
                {
                    "response": {
                        "choices": [
                            {"message": {"tool_calls": [{"function": {"name": "get_weather"}}]}}
                        ]
                    },
                },
                False,
            ),
            # Tool call with wrong type value - should fail
            (
                {
                    "response": {
                        "choices": [
                            {
                                "message": {
                                    "tool_calls": [
                                        {
                                            "type": "not_function",
                                            "function": {"name": "get_weather"},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                },
                False,
            ),
            # Tool call with correct type: function - should pass
            (
                {
                    "response": {
                        "choices": [
                            {
                                "message": {
                                    "tool_calls": [
                                        {"type": "function", "function": {"name": "get_weather"}}
                                    ]
                                }
                            }
                        ]
                    },
                },
                True,
            ),
        ],
    )
    def test_has_tool_calls(self, validator, record, has_tool_calls):
        """Test _has_tool_calls method."""
        assert validator._has_tool_calls(record) == has_tool_calls

    def test_parse_function_arguments_to_json(self, validator):
        """Test parsing function arguments from strings to JSON objects."""
        # Test with valid JSON string arguments
        record = {
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"location": "New York", "unit": "celsius"}',
                                    }
                                },
                                {
                                    "function": {
                                        "name": "get_time",
                                        "arguments": '{"timezone": "EST"}',
                                    }
                                },
                            ]
                        }
                    }
                ]
            }
        }

        result = validator._parse_function_arguments_to_json(record)

        # Should return True for successful parsing
        assert result is True

        # Check that arguments were parsed to dicts
        tool_calls = record["response"]["choices"][0]["message"]["tool_calls"]
        assert tool_calls[0]["function"]["arguments"] == {
            "location": "New York",
            "unit": "celsius",
        }
        assert tool_calls[1]["function"]["arguments"] == {"timezone": "EST"}

    def test_parse_function_arguments_already_parsed(self, validator):
        """Test that already parsed arguments are not modified."""
        record = {
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": {"location": "NYC", "unit": "fahrenheit"},
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }

        original_args = record["response"]["choices"][0]["message"]["tool_calls"][0]["function"][
            "arguments"
        ]
        result = validator._parse_function_arguments_to_json(record)

        # Should return True for successful processing (no parsing needed)
        assert result is True

        # Arguments should remain unchanged
        assert (
            record["response"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
            == original_args
        )

    @patch("src.lib.integration.openai_format_validator.logger")
    def test_parse_function_arguments_invalid_json(self, mock_logger, validator):
        """Test handling of invalid JSON in function arguments."""
        record = {
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": "invalid json {",
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }

        result = validator._parse_function_arguments_to_json(record)

        # Should return False for failed parsing
        assert result is False

        # Should log warning for invalid JSON
        mock_logger.warning.assert_called_once()
        assert "Failed to parse function arguments" in mock_logger.warning.call_args[0][0]

    def test_parse_function_arguments_edge_cases(self, validator):
        """Test edge cases for parsing function arguments."""
        # Empty record
        record = {}
        result = validator._parse_function_arguments_to_json(record)
        assert result is True  # Should succeed with no parsing needed

        # No tool calls
        record = {"response": {"choices": [{"message": {}}]}}
        result = validator._parse_function_arguments_to_json(record)
        assert result is True  # Should succeed with no parsing needed

        # Empty tool calls list
        record = {"response": {"choices": [{"message": {"tool_calls": []}}]}}
        result = validator._parse_function_arguments_to_json(record)
        assert result is True  # Should succeed with no parsing needed

        # Missing function key
        record = {"response": {"choices": [{"message": {"tool_calls": [{}]}}]}}
        result = validator._parse_function_arguments_to_json(record)
        assert result is True  # Should succeed with no parsing needed

        # No arguments key
        record = {
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [{"type": "function", "function": {"name": "test"}}]
                        }
                    }
                ]
            }
        }
        result = validator._parse_function_arguments_to_json(record)
        assert result is True  # Should succeed with no parsing needed

    def test_tool_properties_limit_validation(self, validator):
        """Test tool properties limit enforcement during format validation."""
        # Test with tool properties within limit (should pass format validation)
        record_within_limit = {
            "request": {
                "messages": [{"role": "user", "content": "Get weather"}],
                "tools": [
                    {
                        "function": {
                            "name": "get_weather",
                            "parameters": {
                                "properties": {
                                    "location": {"type": "string"},
                                    "unit": {"type": "string"},
                                    "date": {"type": "string"},
                                }
                            },
                        }
                    }
                ],
            },
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"type": "function", "function": {"name": "get_weather"}}
                            ]
                        }
                    }
                ]
            },
        }
        result = validator.validate_chat_completion_format(record_within_limit)
        assert result is True

        # Test with tool properties exceeding limit (should fail format validation)
        record_exceeds_limit = {
            "request": {
                "messages": [{"role": "user", "content": "Complex request"}],
                "tools": [
                    {
                        "function": {
                            "name": "complex_tool",
                            "parameters": {
                                "properties": {
                                    "prop1": {"type": "string"},
                                    "prop2": {"type": "string"},
                                    "prop3": {"type": "string"},
                                    "prop4": {"type": "string"},
                                    "prop5": {"type": "string"},
                                    "prop6": {"type": "string"},
                                    "prop7": {"type": "string"},
                                    "prop8": {"type": "string"},
                                    "prop9": {"type": "string"},  # This exceeds the limit of 8
                                }
                            },
                        }
                    }
                ],
            },
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"type": "function", "function": {"name": "complex_tool"}}
                            ]
                        }
                    }
                ]
            },
        }
        result = validator.validate_chat_completion_format(record_exceeds_limit)
        assert result is False

    def test_exception_handling(self, validator):
        """Test that exceptions are handled gracefully."""
        # Test with various malformed inputs that might raise exceptions
        malformed_records = [
            None,  # None input
            "not a dict",  # String input
            [],  # List input
            {"request": {"messages": None}},  # None where list expected
            {"response": {"choices": "not a list"}},  # String where list expected
        ]

        for record in malformed_records:
            # Should not raise exceptions, just return False
            assert validator.validate_chat_completion_format(record) is False
            assert validator.validate_tool_calling_quality(record) is False  # No tool calls found

    def test_validate_with_fixtures(
        self, validator, valid_openai_record, openai_record_with_tool_calls
    ):
        """Test validation using fixture data."""
        # Valid record should pass format validation
        assert validator.validate_chat_completion_format(valid_openai_record) is True
        assert validator.validate_tool_calling_quality(valid_openai_record) is False

        # Record with tool calls should pass format but fail quality check
        assert validator.validate_chat_completion_format(openai_record_with_tool_calls) is True
        assert validator.validate_tool_calling_quality(openai_record_with_tool_calls) is True

    def test_batch_validation(self, validator, openai_records_batch):
        """Test validation of multiple records."""
        for record in openai_records_batch:
            assert validator.validate_chat_completion_format(record) is True
            assert validator.validate_tool_calling_quality(record) is False

    def test_invalid_records_from_fixtures(self, validator, invalid_openai_records):
        """Test that all invalid records fail validation."""
        for record in invalid_openai_records:
            assert validator.validate_chat_completion_format(record) is False

    def test_nested_tool_calls(self, validator):
        """Test handling of complex nested tool call structures."""
        record = {
            "request": {"messages": [{"role": "user", "content": "Complex request"}]},
            "response": {
                "choices": [
                    {
                        "message": {
                            "content": "Processing...",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "complex_function",
                                        "arguments": json.dumps(
                                            {
                                                "nested": {
                                                    "data": ["item1", "item2"],
                                                    "config": {"key": "value"},
                                                }
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        }

        assert validator.validate_chat_completion_format(record) is True
        assert validator.validate_tool_calling_quality(record) is True

        # Parse arguments
        result = validator._parse_function_arguments_to_json(record)
        assert result is True  # Should succeed in parsing

        args = record["response"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        assert isinstance(args, dict)
        assert args["nested"]["data"] == ["item1", "item2"]

    def test_unicode_and_special_characters(self, validator):
        """Test handling of unicode and special characters in content."""
        record = {
            "request": {
                "messages": [
                    {"role": "user", "content": "Hello 你好 🌍 \n\t Special chars: <>&\"'"}
                ]
            },
            "response": {
                "choices": [{"message": {"content": "Response with émojis 🎉 and spëcial çhars"}}]
            },
        }

        assert validator.validate_chat_completion_format(record) is True
        assert validator.validate_tool_calling_quality(record) is False

    def test_very_large_record(self, validator):
        """Test handling of very large records."""
        # Create a record with many messages
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"Message {i}" * 100}
            for i in range(100)
        ]

        record = {
            "request": {"messages": messages},
            "response": {"choices": [{"message": {"content": "Final response" * 1000}}]},
        }

        assert validator.validate_chat_completion_format(record) is True
        assert validator.validate_tool_calling_quality(record) is False

    def test_multiple_choices_mixed_tool_calls(self, validator):
        """Test record with multiple choices where only some have tool calls."""
        record = {
            "request": {"messages": [{"role": "user", "content": "Multi-response"}]},
            "response": {
                "choices": [
                    {"message": {"content": "Response 1"}},
                    {"message": {"content": "Response 2", "tool_calls": []}},
                    {
                        "message": {
                            "content": "Response 3",
                            "tool_calls": [{"type": "function", "function": {"name": "test"}}],
                        }
                    },
                    {"message": {"content": "Response 4"}},
                ]
            },
        }

        assert validator.validate_chat_completion_format(record) is True
        assert validator.validate_tool_calling_quality(record) is True  # Has tool calls

    def test_empty_string_arguments(self, validator):
        """Test handling of empty string arguments in tool calls."""
        record = {
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "test_function",
                                        "arguments": "",
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }

        # Should not crash on empty string but should return False due to JSON parsing failure
        result = validator._parse_function_arguments_to_json(record)
        # Should return False since empty string is not valid JSON
        assert result is False

        # Empty string remains as is (not valid JSON)
        assert (
            record["response"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
            == ""
        )

    def test_whitespace_only_arguments(self, validator):
        """Test handling of whitespace-only arguments."""
        record = {
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "test_function",
                                        "arguments": "   \n\t   ",
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }

        result = validator._parse_function_arguments_to_json(record)
        # Should return False since whitespace-only string is not valid JSON
        assert result is False

        # Whitespace string remains as is
        assert (
            record["response"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
            == "   \n\t   "
        )

    @patch("src.lib.integration.openai_format_validator.logger")
    def test_parse_function_arguments_key_error(self, mock_logger, validator):
        """Test handling of KeyError/TypeError in parse function arguments."""
        # Create a record that will cause KeyError when accessing nested keys
        record = {
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": "not a list"  # This will cause TypeError
                        }
                    }
                ]
            }
        }

        result = validator._parse_function_arguments_to_json(record)

        # Should return False due to parsing error
        assert result is False

        # Should log warning for the error
        mock_logger.warning.assert_called_once()
        assert "Error parsing function arguments" in mock_logger.warning.call_args[0][0]

    def test_has_tool_calls_exception_handling(self, validator):
        """Test that _has_tool_calls handles exceptions gracefully."""

        # Create an object that will raise an exception during iteration
        class ExceptionRaiser:
            def get(self, key, default=None):
                if key == "response":
                    return {"choices": [ExceptionChoice()]}
                return default

        class ExceptionChoice:
            def get(self, key, default=None):
                raise RuntimeError("Intentional error for testing")

        record = ExceptionRaiser()

        # Should not raise exception, should return False
        result = validator._has_tool_calls(record)
        assert result is False

    def test_validate_chat_completion_format_exception_handling(self, validator):
        """Test that validate_chat_completion_format handles exceptions gracefully."""

        # Create an object that will raise an exception during validation
        class ExceptionRaiser:
            def __contains__(self, key):
                raise RuntimeError("Intentional error for testing")

        record = ExceptionRaiser()

        # Should not raise exception, should return False
        result = validator.validate_chat_completion_format(record)
        assert result is False

    def test_has_tool_calls_with_malformed_choices(self, validator):
        """Test _has_tool_calls with choices that cause attribute errors."""
        # Create a record where choices contains objects that don't behave like dicts
        record = {
            "response": {
                "choices": [
                    "not a dict",  # String instead of dict
                    123,  # Number instead of dict
                    None,  # None instead of dict
                ]
            }
        }

        # Should handle gracefully and return False
        result = validator._has_tool_calls(record)
        assert result is False

    def test_validate_chat_completion_format_with_invalid_nested_structure(self, validator):
        """Test validate_chat_completion_format with deeply nested invalid structures."""
        # Create a record that will cause exceptions during validation
        record = {
            "request": {
                "messages": [
                    {"role": "user", "content": "test"},
                    None,  # This will cause issues when iterating
                ]
            },
            "response": {
                "choices": [
                    {"message": {"content": "response"}},
                    "invalid choice",  # This will cause isinstance check to fail
                ]
            },
        }

        # Should handle gracefully and return False due to invalid structure
        result = validator.validate_chat_completion_format(record)
        assert result is False
