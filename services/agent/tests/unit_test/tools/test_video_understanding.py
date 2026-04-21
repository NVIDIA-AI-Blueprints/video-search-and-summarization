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
"""Unit tests for video_understanding module."""

from vss_agents.tools.video_understanding import _parse_thinking_from_content


class TestParseThinkingFromContent:
    """Test _parse_thinking_from_content function."""

    def test_empty_content(self):
        """Test with empty content."""
        thinking, answer = _parse_thinking_from_content("")
        assert thinking is None
        assert answer == ""

    def test_none_content(self):
        """Test with None content."""
        thinking, answer = _parse_thinking_from_content(None)
        assert thinking is None
        assert answer is None

    def test_no_tags(self):
        """Test content without thinking tags."""
        content = "This is a simple response without any tags."
        thinking, answer = _parse_thinking_from_content(content)
        assert thinking is None
        assert answer == content

    def test_think_and_answer_tags(self):
        """Test content with both <think> and <answer> tags."""
        content = "<think>I need to analyze this video.</think><answer>The video shows a car.</answer>"
        thinking, answer = _parse_thinking_from_content(content)
        assert thinking == "I need to analyze this video."
        assert answer == "The video shows a car."

    def test_only_think_tags(self):
        """Test content with only <think> tags, no <answer> tags."""
        content = "<think>Analyzing the video...</think>The result is positive."
        thinking, answer = _parse_thinking_from_content(content)
        assert thinking == "Analyzing the video..."
        assert answer == "The result is positive."

    def test_think_tags_with_whitespace(self):
        """Test content with whitespace around tags."""
        content = "<think>  Thinking content  </think>  <answer>  Answer content  </answer>"
        thinking, answer = _parse_thinking_from_content(content)
        assert "Thinking content" in thinking
        assert "Answer content" in answer

    def test_malformed_tags_start_after_end(self):
        """Test content where tags are in wrong order."""
        content = "</think>Content<think>"
        _thinking, answer = _parse_thinking_from_content(content)
        # Should return original content when malformed
        assert answer == content

    def test_nested_content_in_think(self):
        """Test content with nested text in think tags."""
        content = "<think>Step 1: Analyze. Step 2: Conclude.</think><answer>Final answer here.</answer>"
        thinking, answer = _parse_thinking_from_content(content)
        assert "Step 1" in thinking
        assert "Final answer" in answer

    def test_empty_think_tags(self):
        """Test content with empty think tags."""
        content = "<think></think>The answer is 42."
        thinking, answer = _parse_thinking_from_content(content)
        assert thinking == ""
        assert answer == "The answer is 42."

    def test_content_before_think(self):
        """Test content that has text before think tags."""
        content = "Intro text <think>Thinking here</think><answer>Answer here</answer>"
        thinking, answer = _parse_thinking_from_content(content)
        assert thinking == "Thinking here"
        assert answer == "Answer here"

    def test_empty_answer_after_think(self):
        """Test that empty answer returns empty string."""
        content = "<think>All reasoning here.</think>"
        thinking, answer = _parse_thinking_from_content(content)
        assert thinking == "All reasoning here."
        assert answer == ""
