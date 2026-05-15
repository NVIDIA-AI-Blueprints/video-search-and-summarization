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

import pytest

from vss_agents.tools.video_understanding import VideoUnderstandingConfig
from vss_agents.tools.video_understanding import _build_vlm_messages
from vss_agents.tools.video_understanding import _parse_thinking_from_content
from vss_agents.tools.video_understanding import _should_use_video_base64
from vss_agents.tools.video_understanding import _vlm_profile_media_overrides


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


class TestShouldUseVideoBase64:
    """Test video base64 selection for remote VLMs."""

    def test_explicit_base64_enabled_for_local_vlm(self):
        assert _should_use_video_base64(
            use_base64=True,
            vlm_mode="local",
        )

    def test_remote_vlm_uses_base64(self):
        assert _should_use_video_base64(
            use_base64=False,
            vlm_mode="remote",
        )

    def test_local_vlm_does_not_use_base64_by_default(self):
        assert not _should_use_video_base64(
            use_base64=False,
            vlm_mode="local_shared",
        )


class TestBuildVlmMessages:
    """Test VLM media message construction."""

    @pytest.mark.asyncio
    async def test_base64_uses_sampled_image_frames(self, monkeypatch):
        class FakeResponse:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def raise_for_status(self):
                return None

            async def read(self):
                return b"video-bytes"

        class FakeSession:
            def __init__(self, timeout):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def get(self, url):
                assert url == "http://10.0.0.1:30888/vst/storage/video.mp4"
                return FakeResponse()

        def fake_frame_select(path, start, end, step_size):
            assert path.endswith(".mp4")
            assert start == 0.0
            assert end == 4.0
            assert step_size == 2.0
            return ["frame-a", "frame-b"]

        monkeypatch.setattr("vss_agents.tools.video_understanding.aiohttp.ClientSession", FakeSession)
        monkeypatch.setattr("vss_agents.tools.video_understanding.frame_select", fake_frame_select)

        messages = await _build_vlm_messages(
            "http://10.0.0.1:30888/vst/storage/video.mp4",
            "What is happening?",
            use_base64=True,
            video_length_seconds=4.0,
            num_frames=2,
            max_fps=1,
        )

        content = messages[0].content
        assert content[0]["type"] == "text"
        assert "sequence of frames" in content[0]["text"]
        assert content[1:] == [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,frame-a"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,frame-b"}},
        ]


class TestVideoUnderstandingConfig:
    """Test video understanding config shape."""

    def test_vlm_profile_media_overrides_from_extras(self):
        """VLM profile YAML extras should be picked up for video understanding."""
        from nat.llm.nim_llm import NIMModelConfig

        cfg = NIMModelConfig(
            model_name="nvidia/test",
            max_frames=30,
            max_fps=2,
            min_pixels=100,
            max_pixels=200,
            reasoning=True,
        )
        overrides = _vlm_profile_media_overrides(cfg)
        assert overrides == {
            "max_frames": 30,
            "max_fps": 2,
            "min_pixels": 100,
            "max_pixels": 200,
            "reasoning": True,
        }

    def test_vlm_profile_media_overrides_partial(self):
        """Only keys present on the profile should appear in overrides."""
        from nat.llm.nim_llm import NIMModelConfig

        cfg = NIMModelConfig(model_name="nvidia/test", max_frames=7)
        overrides = _vlm_profile_media_overrides(cfg)
        assert overrides == {"max_frames": 7}

    def test_vlm_profile_media_overrides_none(self):
        assert _vlm_profile_media_overrides(None) == {}

    def test_vlm_profile_media_overrides_non_model_returns_empty(self):
        """Mis-typed config should not crash; overrides fall back to VideoUnderstandingConfig."""
        assert _vlm_profile_media_overrides(object()) == {}

    def test_ip_translation_fields_are_not_exposed(self):
        assert "internal_ip" not in VideoUnderstandingConfig.model_fields
        assert "external_ip" not in VideoUnderstandingConfig.model_fields

    def test_remote_vlm_base64_toggle_is_not_exposed(self):
        assert "use_base64_for_remote_vlm" not in VideoUnderstandingConfig.model_fields
