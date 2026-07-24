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
"""Unit tests for video_caption module."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from pydantic import ValidationError
import pytest

from vss_agents.tools.video_caption import VLM_PROMPT
from vss_agents.tools.video_caption import VideoCaptionConfig
from vss_agents.tools.video_caption import VideoCaptionInput
from vss_agents.tools.video_caption import call_vlm_partition
from vss_agents.tools.video_caption import error_messages


class TestVideoCaptionConfig:
    """Test VideoCaptionConfig model."""

    def test_required_fields(self):
        config = VideoCaptionConfig(llm_name="test_llm")
        assert config.llm_name == "test_llm"
        assert config.prompt == VLM_PROMPT
        assert config.max_retries == 3
        assert config.max_frames_per_request == 10
        assert config.use_vss is True
        assert config.vss_backend_url == "http://localhost:31000"

    def test_custom_values(self):
        config = VideoCaptionConfig(
            llm_name="custom_llm",
            prompt="custom prompt",
            max_retries=5,
            max_frames_per_request=20,
            use_vss=False,
            vss_backend_url="http://custom:8080",
        )
        assert config.llm_name == "custom_llm"
        assert config.prompt == "custom prompt"
        assert config.max_retries == 5
        assert config.max_frames_per_request == 20
        assert config.use_vss is False


class TestVideoCaptionInput:
    """Test VideoCaptionInput model."""

    def test_valid_input(self):
        input_data = VideoCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=10.0,
            user_prompt="Describe the video",
            fps=1.0,
            video_duration=60.0,
        )
        assert input_data.filename == "video.mp4"
        assert input_data.start_timestamp == 0.0
        assert input_data.end_timestamp == 10.0
        assert input_data.fps == 1.0

    def test_end_timestamp_clamped_to_duration(self):
        input_data = VideoCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=100.0,  # Greater than video_duration
            user_prompt="Describe the video",
            video_duration=60.0,
        )
        # Should be clamped to video_duration - 0.01
        assert input_data.end_timestamp == 59.99

    def test_end_timestamp_none_uses_duration(self):
        input_data = VideoCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=None,
            user_prompt="Describe the video",
            video_duration=60.0,
        )
        assert input_data.end_timestamp == 59.99

    def test_negative_duration_raises(self):
        with pytest.raises(ValidationError):
            VideoCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="Describe the video",
                video_duration=-1.0,
            )

    def test_zero_duration_raises(self):
        with pytest.raises(ValidationError):
            VideoCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="Describe the video",
                video_duration=0.0,
            )

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            VideoCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="Describe the video",
                video_duration=60.0,
                extra_field="not allowed",
            )


class TestErrorMessages:
    """Test error_messages constant."""

    def test_error_messages_defined(self):
        assert len(error_messages) > 0
        assert "I'm sorry, I can't help with that" in error_messages
        assert "I'm unable to" in error_messages


class TestCallVlmPartition:
    """Test call_vlm_partition async function."""

    @pytest.mark.asyncio
    async def test_successful_caption(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "[10.45] Person walking across the street"
        mock_llm.ainvoke.return_value = mock_response

        base64_frames = ["frame1_base64", "frame2_base64"]
        template_prompt = "Test prompt fps={fps} user_prompt={user_prompt} start_timestamp={start_timestamp}"

        result = await call_vlm_partition(mock_llm, base64_frames, template_prompt, "describe", 10.0, 1.0, 3)

        assert result[0] == 10.0  # start_timestamp
        assert result[1] == "[10.45] Person walking across the street"

    @pytest.mark.asyncio
    async def test_retry_on_error_message(self):
        mock_llm = AsyncMock()
        # First call returns error, second call succeeds
        mock_error_response = MagicMock()
        mock_error_response.content = "I'm sorry, I can't help with that"

        mock_success_response = MagicMock()
        mock_success_response.content = "[10.0] Valid caption"

        mock_retry_prompt_response = MagicMock()
        mock_retry_prompt_response.content = "Modified prompt"

        mock_llm.ainvoke.side_effect = [
            mock_error_response,
            mock_retry_prompt_response,
            mock_success_response,
        ]

        base64_frames = ["frame1_base64"]
        template_prompt = "Test prompt fps={fps} user_prompt={user_prompt} start_timestamp={start_timestamp}"

        await call_vlm_partition(mock_llm, base64_frames, template_prompt, "describe", 10.0, 1.0, 3)

        assert mock_llm.ainvoke.call_count >= 2

    @pytest.mark.asyncio
    async def test_success_without_retry(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "A detailed description of the video content that is longer than 80 characters so it won't trigger retry logic."
        mock_llm.ainvoke.return_value = mock_response

        base64_frames = ["frame1_base64"]
        template_prompt = "Test prompt fps={fps} user_prompt={user_prompt} start_timestamp={start_timestamp}"

        result = await call_vlm_partition(mock_llm, base64_frames, template_prompt, "describe", 0.0, 1.0, 3)

        assert result[0] == 0.0
        assert "detailed description" in result[1]
        assert mock_llm.ainvoke.call_count == 1


class TestVLMPrompt:
    """Test VLM_PROMPT constant."""

    def test_prompt_contains_placeholders(self):
        assert "{fps}" in VLM_PROMPT
        assert "{user_prompt}" in VLM_PROMPT
        assert "{start_timestamp}" in VLM_PROMPT

    def test_prompt_formatting(self):
        formatted = VLM_PROMPT.format(fps=1.0, user_prompt="describe the scene", start_timestamp=10.0)
        assert "1.0" in formatted
        assert "describe the scene" in formatted
        assert "10.0" in formatted
