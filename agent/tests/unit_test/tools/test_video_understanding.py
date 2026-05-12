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

from urllib.parse import urlparse

from vss_agents.tools.video_understanding import _parse_thinking_from_content
from vss_agents.tools.video_understanding import _query_looks_like_signed_object_storage
from vss_agents.tools.video_understanding import _rebuild_vst_clip_url_with_internal_base
from vss_agents.tools.video_understanding import _response_body_looks_like_video_error


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


class TestFrameModeClipUrlHelpers:
    def test_signed_query_detection(self):
        u = "http://minio:9000/bucket/x?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=x&X-Amz-Signature=abc"
        assert _query_looks_like_signed_object_storage(urlparse(u).query)

    def test_unsigned_vst_path(self):
        assert not _query_looks_like_signed_object_storage("")

    def test_rebuild_vst_clip(self):
        out = _rebuild_vst_clip_url_with_internal_base(
            "https://proxy.example/vst/storage/temp/abc.mp4?token=1",
            "http://10.0.0.5:30888",
        )
        assert out == "http://10.0.0.5:30888/vst/storage/temp/abc.mp4?token=1"

    def test_rebuild_non_vst_returns_none(self):
        assert _rebuild_vst_clip_url_with_internal_base("http://other/path", "http://10.0.0.5:30888") is None

    def test_response_body_html_raises(self):
        try:
            _response_body_looks_like_video_error(
                "http://x",
                b"<!DOCTYPE html><html><body>403</body></html>",
                "text/html",
            )
        except ValueError as e:
            assert "HTML" in str(e)
        else:
            raise AssertionError("expected ValueError")

    def test_response_body_mp4_ok(self):
        # minimal ftyp-like presence
        data = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00" + b"\x00" * 200
        _response_body_looks_like_video_error("http://x", data, "video/mp4")
