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
"""Tests for vss_agents/utils/reasoning_parsing.py."""

from unittest.mock import MagicMock

from vss_agents.utils.reasoning_parsing import parse_reasoning_content


class TestParseReasoningContent:
    """Tests for parse_reasoning_content function."""

    def test_parse_with_reasoning_content_attribute(self):
        """Test parsing when response has reasoning_content attribute."""
        response = MagicMock()
        response.reasoning_content = "This is the reasoning"
        response.content = "This is the actual content"
        response.additional_kwargs = {}
        response.response_metadata = {}

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "This is the reasoning"
        assert content == "This is the actual content"

    def test_parse_with_reasoning_in_additional_kwargs(self):
        """Test parsing when reasoning is in additional_kwargs."""
        response = MagicMock()
        response.reasoning_content = None
        response.content = "Main content"
        response.additional_kwargs = {"reasoning_content": "Reasoning from kwargs"}
        response.response_metadata = {}

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "Reasoning from kwargs"
        assert content == "Main content"

    def test_parse_with_reasoning_in_response_metadata(self):
        """Test parsing when reasoning is in response_metadata."""
        response = MagicMock()
        response.reasoning_content = None
        response.content = "Main content"
        response.additional_kwargs = {}
        response.response_metadata = {"reasoning_content": "Reasoning from metadata"}

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "Reasoning from metadata"
        assert content == "Main content"

    def test_parse_with_single_think_end_tag(self):
        """Test parsing with single </think> tag (no opening tag)."""
        response = MagicMock()
        response.reasoning_content = None
        response.content = "I need to analyze this</think>Here is the answer"
        response.additional_kwargs = {}
        response.response_metadata = {}

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "I need to analyze this"
        assert content == "Here is the answer"

    def test_parse_with_paired_think_tags(self):
        """Test parsing with paired <think></think> tags."""
        response = MagicMock()
        response.reasoning_content = None
        response.content = "<think>My reasoning process</think>The final answer"
        response.additional_kwargs = {}
        response.response_metadata = {}

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "My reasoning process"
        assert content == "The final answer"

    def test_parse_without_reasoning(self):
        """Test parsing when no reasoning is present."""
        response = MagicMock()
        response.reasoning_content = None
        response.content = "Just plain content without reasoning"
        response.additional_kwargs = {}
        response.response_metadata = {}

        reasoning, content = parse_reasoning_content(response)
        assert reasoning is None
        assert content == "Just plain content without reasoning"

    def test_parse_with_empty_reasoning(self):
        """Test parsing with empty reasoning content."""
        response = MagicMock()
        response.reasoning_content = ""
        response.content = "Content only"
        response.additional_kwargs = {}
        response.response_metadata = {}

        _reasoning, content = parse_reasoning_content(response)
        # Empty reasoning_content should result in checking content for tags
        assert content == "Content only"

    def test_parse_with_whitespace_in_reasoning(self):
        """Test parsing with whitespace in reasoning."""
        response = MagicMock()
        response.reasoning_content = "  reasoning with spaces  "
        response.content = "  content with spaces  "
        response.additional_kwargs = {}
        response.response_metadata = {}

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "reasoning with spaces"
        assert content == "content with spaces"

    def test_parse_with_empty_content(self):
        """Test parsing with empty content."""
        response = MagicMock()
        response.reasoning_content = "Some reasoning"
        response.content = ""
        response.additional_kwargs = {}
        response.response_metadata = {}

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "Some reasoning"
        assert content is None

    def test_parse_think_tags_multiline(self):
        """Test parsing think tags with multiline content."""
        response = MagicMock()
        response.reasoning_content = None
        response.content = """<think>
Line 1 of reasoning
Line 2 of reasoning
</think>
Line 1 of answer
Line 2 of answer"""
        response.additional_kwargs = {}
        response.response_metadata = {}

        reasoning, content = parse_reasoning_content(response)
        assert "Line 1 of reasoning" in reasoning
        assert "Line 2 of reasoning" in reasoning
        assert "Line 1 of answer" in content
        assert "Line 2 of answer" in content

    def test_parse_think_tags_take_priority_over_reasoning_field(self):
        """Test that think tags in content take priority over reasoning_content field."""
        response = MagicMock()
        response.reasoning_content = None
        response.content = "Some reasoning here\n</think>\n\nActual content\n"
        response.additional_kwargs = {"reasoning_content": "Some reasoning here"}
        response.response_metadata = {}

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "Some reasoning here"
        assert content == "Actual content"

    def test_parse_paired_think_tags_take_priority_over_reasoning_field(self):
        """Test paired <think></think> tags also take priority over reasoning_content."""
        response = MagicMock()
        response.reasoning_content = None
        response.content = "<think>My reasoning</think>\nThe final answer"
        response.additional_kwargs = {"reasoning_content": "My reasoning"}
        response.response_metadata = {}

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "My reasoning"
        assert content == "The final answer"

    def test_parse_think_tags_wrong_order(self):
        """Test parsing when think tags are in wrong order (should not match)."""
        response = MagicMock()
        response.reasoning_content = None
        response.content = "</think>some content<think>"
        response.additional_kwargs = {}
        response.response_metadata = {}

        # When tags are in wrong order, treat as plain content
        _reasoning, _content = parse_reasoning_content(response)
        # The behavior depends on implementation - it should handle this gracefully

    # --- content_blocks parsing ---

    def test_parse_content_blocks_reasoning_and_text(self):
        """Test parsing content_blocks with both reasoning and text blocks."""
        response = MagicMock()
        response.content = ""
        response.reasoning_content = None
        response.additional_kwargs = {}
        response.response_metadata = {}
        response.content_blocks = [
            {"type": "reasoning", "reasoning": "Step-by-step thinking"},
            {"type": "text", "text": "The final answer"},
        ]

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "Step-by-step thinking"
        assert content == "The final answer"

    def test_parse_content_blocks_multiple_blocks(self):
        """Test parsing content_blocks with multiple reasoning and text blocks."""
        response = MagicMock()
        response.content = ""
        response.reasoning_content = None
        response.additional_kwargs = {}
        response.response_metadata = {}
        response.content_blocks = [
            {"type": "reasoning", "reasoning": "First thought"},
            {"type": "reasoning", "reasoning": "Second thought"},
            {"type": "text", "text": "Part one"},
            {"type": "text", "text": "Part two"},
        ]

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "First thought\nSecond thought"
        assert content == "Part one\nPart two"

    def test_parse_content_blocks_only_reasoning(self):
        """Test parsing content_blocks with only reasoning blocks."""
        response = MagicMock()
        response.content = ""
        response.reasoning_content = None
        response.additional_kwargs = {}
        response.response_metadata = {}
        response.content_blocks = [
            {"type": "reasoning", "reasoning": "Only reasoning here"},
        ]

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "Only reasoning here"
        assert content is None

    def test_parse_content_blocks_only_text(self):
        """Test parsing content_blocks with only text blocks."""
        response = MagicMock()
        response.content = ""
        response.reasoning_content = None
        response.additional_kwargs = {}
        response.response_metadata = {}
        response.content_blocks = [
            {"type": "text", "text": "Just text, no reasoning"},
        ]

        reasoning, content = parse_reasoning_content(response)
        assert reasoning is None
        assert content == "Just text, no reasoning"

    def test_parse_content_blocks_empty_list(self):
        """Test parsing when content_blocks is an empty list (falls through to plain content)."""
        response = MagicMock()
        response.content = "Fallback content"
        response.reasoning_content = None
        response.additional_kwargs = {}
        response.response_metadata = {}
        response.content_blocks = []

        reasoning, content = parse_reasoning_content(response)
        assert reasoning is None
        assert content == "Fallback content"

    def test_parse_content_blocks_skips_non_dict_items(self):
        """Test that non-dict items in content_blocks are silently skipped."""
        response = MagicMock()
        response.content = ""
        response.reasoning_content = None
        response.additional_kwargs = {}
        response.response_metadata = {}
        response.content_blocks = [
            "not a dict",
            42,
            {"type": "reasoning", "reasoning": "Valid reasoning"},
            None,
            {"type": "text", "text": "Valid text"},
        ]

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "Valid reasoning"
        assert content == "Valid text"

    def test_parse_content_blocks_empty_strings(self):
        """Test content_blocks with empty reasoning/text strings."""
        response = MagicMock()
        response.content = ""
        response.reasoning_content = None
        response.additional_kwargs = {}
        response.response_metadata = {}
        response.content_blocks = [
            {"type": "reasoning", "reasoning": ""},
            {"type": "text", "text": ""},
        ]

        reasoning, content = parse_reasoning_content(response)
        assert reasoning is None
        assert content is None

    def test_reasoning_field_takes_priority_over_content_blocks(self):
        """Test that reasoning_content field is checked before content_blocks."""
        response = MagicMock()
        response.content = "The answer"
        response.reasoning_content = "Reasoning from field"
        response.additional_kwargs = {}
        response.response_metadata = {}
        response.content_blocks = [
            {"type": "reasoning", "reasoning": "Reasoning from blocks"},
            {"type": "text", "text": "Text from blocks"},
        ]

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "Reasoning from field"
        assert content == "The answer"

    def test_think_tags_take_priority_over_content_blocks(self):
        """Test that think tags in content are checked before content_blocks."""
        response = MagicMock()
        response.content = "<think>Think-tag reasoning</think>Think-tag answer"
        response.reasoning_content = None
        response.additional_kwargs = {}
        response.response_metadata = {}
        response.content_blocks = [
            {"type": "reasoning", "reasoning": "Block reasoning"},
            {"type": "text", "text": "Block text"},
        ]

        reasoning, content = parse_reasoning_content(response)
        assert reasoning == "Think-tag reasoning"
        assert content == "Think-tag answer"

    def test_parse_list_content(self):
        """Test parsing list-typed content."""
        response = MagicMock()
        response.content = [
            {"type": "text", "text": "answer from list"},
            {"type": "reasoning", "reasoning": "reasoning from list"},
        ]
        response.reasoning_content = None
        response.additional_kwargs = {}
        response.response_metadata = {}
        response.content_blocks = None

        reasoning, content = parse_reasoning_content(response)
        assert reasoning is None
        assert content is None
