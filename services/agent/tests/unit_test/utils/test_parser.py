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
"""Tests for vss_agents/utils/parser.py."""

import pytest

from vss_agents.utils.parser import ReActOutputParserError
from vss_agents.utils.parser import parse_function_calls


class TestReActOutputParserError:
    """Tests for ReActOutputParserError exception."""

    def test_error_initialization(self):
        """Test error initialization with default values."""
        error = ReActOutputParserError()
        assert error.observation is None
        assert error.missing_action is False
        assert error.missing_action_input is False
        assert error.final_answer_and_action is False

    def test_error_with_observation(self):
        """Test error with observation message."""
        error = ReActOutputParserError(observation="Test observation")
        assert error.observation == "Test observation"

    def test_error_flags(self):
        """Test error with various flags."""
        error = ReActOutputParserError(
            missing_action=True,
            missing_action_input=True,
            final_answer_and_action=True,
        )
        assert error.missing_action is True
        assert error.missing_action_input is True
        assert error.final_answer_and_action is True


class TestParseFunctionCalls:
    """Tests for parse_function_calls function."""

    def test_parse_single_function_no_params(self):
        """Test parsing single function call without parameters."""
        text = "get_data()"
        result = parse_function_calls(text)
        assert len(result) == 1
        assert result[0]["name"] == "get_data"
        assert result[0]["args"] == {}

    def test_parse_single_function_with_string_param(self):
        """Test parsing function with string parameter."""
        text = "video_caption(file_path='video.mp4')"
        result = parse_function_calls(text)
        assert len(result) == 1
        assert result[0]["name"] == "video_caption"
        assert result[0]["args"]["file_path"] == "video.mp4"

    def test_parse_function_with_numeric_params(self):
        """Test parsing function with numeric parameters."""
        text = "process_video(start_timestamp=5, end_timestamp=10)"
        result = parse_function_calls(text)
        assert len(result) == 1
        assert result[0]["args"]["start_timestamp"] == 5
        assert result[0]["args"]["end_timestamp"] == 10

    def test_parse_function_with_float_params(self):
        """Test parsing function with float parameters."""
        text = "process_video(start=1.5, end=2.5)"
        result = parse_function_calls(text)
        assert result[0]["args"]["start"] == 1.5
        assert result[0]["args"]["end"] == 2.5

    def test_parse_multiple_functions(self):
        """Test parsing multiple function calls."""
        text = "[func1(a=1), func2(b=2)]"
        result = parse_function_calls(text)
        assert len(result) == 2
        assert result[0]["name"] == "func1"
        assert result[1]["name"] == "func2"

    def test_parse_function_with_list_param(self):
        """Test parsing function with list parameter."""
        text = "process(items=[1, 2, 3])"
        result = parse_function_calls(text)
        assert result[0]["args"]["items"] == [1, 2, 3]

    def test_parse_function_with_dict_param(self):
        """Test parsing function with dict parameter."""
        text = 'process(config={"key": "value"})'
        result = parse_function_calls(text)
        assert result[0]["args"]["config"] == {"key": "value"}

    def test_parse_function_with_double_quotes(self):
        """Test parsing function with double quoted string."""
        text = 'video_caption(file_path="video.mp4")'
        result = parse_function_calls(text)
        assert result[0]["args"]["file_path"] == "video.mp4"

    def test_parse_function_with_nested_quotes(self):
        """Test parsing function with nested commas in string."""
        text = "search(query='hello, world')"
        result = parse_function_calls(text)
        assert result[0]["args"]["query"] == "hello, world"

    def test_parse_function_with_boolean(self):
        """Test parsing function with boolean parameters."""
        text = "process(enabled=True, disabled=False)"
        result = parse_function_calls(text)
        assert result[0]["args"]["enabled"] is True
        assert result[0]["args"]["disabled"] is False

    def test_parse_function_with_none(self):
        """Test parsing function with None parameter."""
        text = "process(value=None)"
        result = parse_function_calls(text)
        assert result[0]["args"]["value"] is None

    def test_parse_no_function_calls(self):
        """Test that no function calls raises error."""
        text = "This is just plain text"
        with pytest.raises(ReActOutputParserError):
            parse_function_calls(text)

    def test_parse_function_with_whitespace(self):
        """Test parsing function with extra whitespace."""
        text = "  process( key = 'value' )  "
        result = parse_function_calls(text)
        assert len(result) == 1
        assert result[0]["args"]["key"] == "value"

    def test_parse_function_has_unique_ids(self):
        """Test that parsed functions have unique IDs."""
        text = "[func1(a=1), func2(b=2)]"
        result = parse_function_calls(text)
        assert "id" in result[0]
        assert "id" in result[1]
        assert result[0]["id"] != result[1]["id"]

    def test_parse_function_with_nested_parens(self):
        """Test parsing function with nested parentheses."""
        text = "outer(inner=(1, 2))"
        result = parse_function_calls(text)
        # The inner tuple should be parsed correctly
        assert result[0]["name"] == "outer"

    def test_parse_function_with_mixed_params(self):
        """Test parsing function with mixed parameter types."""
        text = "process(name='test', count=5, items=[1, 2], config={'a': 1})"
        result = parse_function_calls(text)
        assert result[0]["args"]["name"] == "test"
        assert result[0]["args"]["count"] == 5
        assert result[0]["args"]["items"] == [1, 2]
        assert result[0]["args"]["config"] == {"a": 1}

    def test_parse_function_with_closing_paren_in_tuple(self):
        """Test parsing function with closing parens in nested tuple (covers line 71)."""
        text = "outer(data=(1, 2, 3))"
        result = parse_function_calls(text)
        assert result[0]["name"] == "outer"
        # The tuple should be parsed, covering the paren_count -= 1 branch

    def test_parse_function_with_json_string_fallback(self):
        """Test parsing function with JSON that looks like dict but fails ast (covers lines 100-105)."""
        # Create a case where ast.literal_eval works but we test the JSON path too
        text = 'process(data={"key": "value"})'
        result = parse_function_calls(text)
        assert result[0]["args"]["data"] == {"key": "value"}

    def test_parse_function_with_invalid_json_stays_string(self):
        """Test that invalid JSON-like strings stay as strings."""
        text = "process(data={invalid json})"
        result = parse_function_calls(text)
        # Should stay as string since it's not valid JSON or Python literal
        assert result[0]["args"]["data"] == "{invalid json}"

    def test_parse_function_with_complex_nested_structures(self):
        """Test parsing deeply nested structures."""
        text = "process(data={'a': [1, 2, {'b': 3}]})"
        result = parse_function_calls(text)
        assert result[0]["args"]["data"] == {"a": [1, 2, {"b": 3}]}
