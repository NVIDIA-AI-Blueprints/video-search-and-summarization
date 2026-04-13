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
"""Additional unit tests for video_caption module to improve coverage."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from pydantic import ValidationError
import pytest

from vss_agents.tools.video_caption import VideoCaptionConfig
from vss_agents.tools.video_caption import VideoCaptionInput
from vss_agents.tools.video_caption import call_vlm_partition
from vss_agents.tools.video_caption import error_messages


class TestVideoCaptionConfig:
    """Test VideoCaptionConfig model."""

    def test_required_fields(self):
        config = VideoCaptionConfig(llm_name="test-llm")
        assert config.llm_name == "test-llm"
        assert config.max_retries == 3
        assert config.max_frames_per_request == 10
        assert config.use_vss is True

    def test_custom_fields(self):
        config = VideoCaptionConfig(
            llm_name="custom-llm",
            prompt="Custom prompt {fps} {user_prompt} {start_timestamp}",
            max_retries=5,
            max_frames_per_request=20,
            use_vss=False,
            vss_backend_url="http://custom:9000",
        )
        assert config.max_retries == 5
        assert config.use_vss is False
        assert config.vss_backend_url == "http://custom:9000"


class TestVideoCaptionInput:
    """Test VideoCaptionInput model."""

    def test_valid_input(self):
        inp = VideoCaptionInput(
            filename="video.mp4",
            start_timestamp=10.0,
            end_timestamp=20.0,
            user_prompt="Describe the scene",
            fps=1.0,
            video_duration=100.0,
        )
        assert inp.filename == "video.mp4"
        assert inp.start_timestamp == 10.0
        assert inp.end_timestamp == 20.0

    def test_end_timestamp_capped_to_duration(self):
        inp = VideoCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=200.0,
            user_prompt="test",
            fps=1.0,
            video_duration=100.0,
        )
        assert inp.end_timestamp == pytest.approx(99.99)

    def test_end_timestamp_none_capped(self):
        inp = VideoCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=None,
            user_prompt="test",
            fps=1.0,
            video_duration=50.0,
        )
        assert inp.end_timestamp == pytest.approx(49.99)

    def test_zero_duration_raises(self):
        with pytest.raises(ValueError, match="Video duration must be positive"):
            VideoCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="test",
                fps=1.0,
                video_duration=0.0,
            )

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="Video duration must be positive"):
            VideoCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="test",
                fps=1.0,
                video_duration=-5.0,
            )

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            VideoCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="test",
                fps=1.0,
                video_duration=100.0,
                extra_field="not allowed",
            )


class TestErrorMessages:
    """Test error_messages list."""

    def test_error_messages_exist(self):
        assert len(error_messages) > 0
        assert any("I'm sorry" in msg for msg in error_messages)
        assert any("I'm unable" in msg for msg in error_messages)


class TestCallVlmPartition:
    """Test call_vlm_partition function."""

    @pytest.mark.asyncio
    async def test_successful_caption(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "[10.45] A person walks."
        mock_llm.ainvoke.return_value = mock_response

        start_ts, _caption = await call_vlm_partition(
            llm=mock_llm,
            base64_frames=["base64data1", "base64data2"],
            template_prompt="Describe video at fps {fps}. Query: {user_prompt}. Start: {start_timestamp}.",
            user_prompt="find person",
            start_timestamp=10.0,
            fps=1.0,
            max_retries=3,
        )
        assert start_ts == 10.0
        assert "person" in _caption

    @pytest.mark.asyncio
    async def test_retry_on_error_message(self):
        mock_llm = AsyncMock()
        error_response = MagicMock()
        error_response.content = "I'm sorry, I can't help with that"

        modified_prompt_response = MagicMock()
        modified_prompt_response.content = "Modified prompt text"

        success_response = MagicMock()
        success_response.content = "[10.0] Scene description"

        mock_llm.ainvoke.side_effect = [
            error_response,
            modified_prompt_response,
            success_response,
        ]

        start_ts, _caption = await call_vlm_partition(
            llm=mock_llm,
            base64_frames=["frame1"],
            template_prompt="fps {fps} query {user_prompt} start {start_timestamp}",
            user_prompt="test",
            start_timestamp=10.0,
            fps=1.0,
            max_retries=3,
        )
        assert start_ts == 10.0
        assert "Scene description" in _caption

    @pytest.mark.asyncio
    async def test_no_retry_for_long_error_message(self):
        """Long error messages should not trigger retry."""
        mock_llm = AsyncMock()
        long_response = MagicMock()
        long_response.content = "I'm sorry, I can't help with that" + " but here is a very long explanation " * 5
        mock_llm.ainvoke.return_value = long_response

        start_ts, _caption = await call_vlm_partition(
            llm=mock_llm,
            base64_frames=["frame1"],
            template_prompt="fps {fps} query {user_prompt} start {start_timestamp}",
            user_prompt="test",
            start_timestamp=5.0,
            fps=1.0,
            max_retries=1,
        )
        assert start_ts == 5.0
        # Should return after first call since message is long
        assert mock_llm.ainvoke.call_count == 1
