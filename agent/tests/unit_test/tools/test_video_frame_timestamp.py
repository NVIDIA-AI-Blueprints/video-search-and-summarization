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
"""Unit tests for video_frame_timestamp module."""

from pydantic import ValidationError
import pytest
from vss_agents.prompt import VIDEO_FRAME_TIMESTAMP_PROMPT
from vss_agents.tools.video_frame_timestamp import VideoFrameTimestampConfig
from vss_agents.tools.video_frame_timestamp import VideoFrameTimestampInput


class TestVideoFrameTimestampConfig:
    """Test VideoFrameTimestampConfig model."""

    def test_defaults(self):
        config = VideoFrameTimestampConfig()
        assert config.llm_name == "openai_llm"
        assert config.prompt == VIDEO_FRAME_TIMESTAMP_PROMPT

    def test_custom_values(self):
        config = VideoFrameTimestampConfig(
            llm_name="custom_llm",
            prompt="Custom prompt for timestamp extraction",
        )
        assert config.llm_name == "custom_llm"
        assert config.prompt == "Custom prompt for timestamp extraction"


class TestVideoFrameTimestampInput:
    """Test VideoFrameTimestampInput model."""

    def test_valid_input(self):
        input_data = VideoFrameTimestampInput(
            asset_file_path="/path/to/video.mp4",
            frame_offset_seconds=10.5,
        )
        assert input_data.asset_file_path == "/path/to/video.mp4"
        assert input_data.frame_offset_seconds == 10.5

    def test_zero_offset(self):
        input_data = VideoFrameTimestampInput(
            asset_file_path="/path/to/video.mp4",
            frame_offset_seconds=0.0,
        )
        assert input_data.frame_offset_seconds == 0.0

    def test_large_offset(self):
        input_data = VideoFrameTimestampInput(
            asset_file_path="/path/to/video.mp4",
            frame_offset_seconds=3600.0,  # 1 hour
        )
        assert input_data.frame_offset_seconds == 3600.0

    def test_missing_asset_file_path_raises(self):
        with pytest.raises(ValidationError):
            VideoFrameTimestampInput(
                frame_offset_seconds=10.0,
            )

    def test_missing_frame_offset_raises(self):
        with pytest.raises(ValidationError):
            VideoFrameTimestampInput(
                asset_file_path="/path/to/video.mp4",
            )

    def test_negative_offset_allowed(self):
        # Negative offset might be valid in some cases
        input_data = VideoFrameTimestampInput(
            asset_file_path="/path/to/video.mp4",
            frame_offset_seconds=-5.0,
        )
        assert input_data.frame_offset_seconds == -5.0


class TestVideoFrameTimestampPrompt:
    """Test VIDEO_FRAME_TIMESTAMP_PROMPT usage."""

    def test_prompt_is_string(self):
        assert isinstance(VIDEO_FRAME_TIMESTAMP_PROMPT, str)

    def test_prompt_not_empty(self):
        assert len(VIDEO_FRAME_TIMESTAMP_PROMPT) > 0
