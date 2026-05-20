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
from vss_agents.tools.video_understanding import _effective_system_prompt
from vss_agents.tools.video_understanding import _is_omni_audio_model
from vss_agents.tools.video_understanding import _should_use_video_file_base64


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


class TestIsOmniAudioModel:
    def test_detects_nemotron_omni(self):
        assert _is_omni_audio_model("nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4")

    def test_non_omni_models(self):
        assert not _is_omni_audio_model("nvidia/cosmos-reason2-8b")


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

    def test_enable_audio_skips_base64_for_remote_vlm(self):
        assert not _should_use_video_base64(
            use_base64=False,
            vlm_mode="remote",
            enable_audio=True,
        )

    def test_enable_audio_remote_uses_video_file_base64(self):
        assert _should_use_video_file_base64(
            enable_audio=True,
            vlm_mode="remote",
        )

    def test_enable_audio_local_does_not_use_video_file_base64(self):
        assert not _should_use_video_file_base64(
            enable_audio=True,
            vlm_mode="local",
        )

    def test_enable_audio_defaults_false(self):
        assert VideoUnderstandingConfig(
            vlm_name="nim_vlm",
            enable_audio=False,
        ).enable_audio is False
        assert VideoUnderstandingConfig(
            vlm_name="nim_vlm",
            enable_audio=True,
        ).enable_audio is True


class TestBuildVlmMessages:
    """Test VLM media message construction."""

    @pytest.mark.asyncio
    async def test_base64_uses_sampled_image_frames(self, monkeypatch):
        class FakeResponse:
            # aiohttp response: _build_vlm_messages reads Content-Type and validates body bytes.

            async def __aenter__(self):
                self.headers = {"Content-Type": "video/mp4"}
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def raise_for_status(self):
                return None

            async def read(self):
                return b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00" + b"\x00" * 200

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

    @pytest.mark.asyncio
    async def test_video_file_base64_inlines_full_mp4(self, monkeypatch):
        class FakeResponse:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def raise_for_status(self):
                return None

            async def read(self):
                return b"\x00\x00\x00\x20ftypisom" + b"\x01\x02\x03"

        class FakeSession:
            def __init__(self, timeout):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def get(self, url):
                return FakeResponse()

        monkeypatch.setattr("vss_agents.tools.video_understanding.aiohttp.ClientSession", FakeSession)

        messages = await _build_vlm_messages(
            "http://10.0.0.1:30888/vst/storage/video.mp4",
            "What is said?",
            use_base64=False,
            use_video_file_base64=True,
            video_length_seconds=4.0,
            num_frames=2,
            max_fps=1,
        )

        content = messages[0].content
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "video_url"
        assert content[1]["video_url"]["url"].startswith("data:video/mp4;base64,")


class TestEffectiveSystemPrompt:
    """Test Omni audio system prompt extension."""

    def test_extends_prompt_for_omni_when_enable_audio(self):
        config = VideoUnderstandingConfig(
            vlm_name="nim_vlm",
            system_prompt="You are a monitoring system.",
            enable_audio=True,
        )
        result = _effective_system_prompt(config, "nvidia/Nemotron-3-Nano-Omni-30B")
        assert "audio track" in result
        assert result.startswith("You are a monitoring system.")

    def test_unchanged_when_audio_disabled(self):
        config = VideoUnderstandingConfig(
            vlm_name="nim_vlm",
            system_prompt="You are a monitoring system.",
            enable_audio=False,
        )
        result = _effective_system_prompt(config, "nvidia/Nemotron-3-Nano-Omni-30B")
        assert result == "You are a monitoring system."

    def test_unchanged_for_non_omni_with_audio(self):
        config = VideoUnderstandingConfig(
            vlm_name="nim_vlm",
            system_prompt="You are a monitoring system.",
            enable_audio=True,
        )
        result = _effective_system_prompt(config, "nvidia/cosmos-reason1-7b")
        assert result == "You are a monitoring system."


class TestVideoUnderstandingConfig:
    """Test video understanding config shape."""

    def test_ip_translation_fields_are_not_exposed(self):
        assert "internal_ip" not in VideoUnderstandingConfig.model_fields
        assert "external_ip" not in VideoUnderstandingConfig.model_fields

    def test_remote_vlm_base64_toggle_is_not_exposed(self):
        assert "use_base64_for_remote_vlm" not in VideoUnderstandingConfig.model_fields
