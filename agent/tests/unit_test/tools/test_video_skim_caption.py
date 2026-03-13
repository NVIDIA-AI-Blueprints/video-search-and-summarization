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
"""Unit tests for video_skim_caption module."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.video_skim_caption import VideoSkimCaptionConfig
from vss_agents.tools.video_skim_caption import VideoSkimCaptionInput


class TestVideoSkimCaptionConfig:
    """Test VideoSkimCaptionConfig model."""

    def test_defaults(self):
        config = VideoSkimCaptionConfig()
        assert config.skim_fps == 0.5

    def test_custom_values(self):
        config = VideoSkimCaptionConfig(skim_fps=0.25)
        assert config.skim_fps == 0.25


class TestVideoSkimCaptionInput:
    """Test VideoSkimCaptionInput model."""

    def test_valid_input(self):
        input_data = VideoSkimCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=300.0,
            user_prompt="Skim and describe",
            video_duration=600.0,
        )
        assert input_data.filename == "video.mp4"
        assert input_data.start_timestamp == 0.0
        assert input_data.end_timestamp == 300.0
        assert input_data.user_prompt == "Skim and describe"
        assert input_data.video_duration == 600.0

    def test_end_timestamp_clamped_to_duration(self):
        input_data = VideoSkimCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=1000.0,  # Greater than video_duration
            user_prompt="Skim and describe",
            video_duration=600.0,
        )
        # Should be clamped to video_duration - 0.01
        assert input_data.end_timestamp == 599.99

    def test_end_timestamp_none_uses_duration(self):
        input_data = VideoSkimCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=None,
            user_prompt="Skim and describe",
            video_duration=600.0,
        )
        assert input_data.end_timestamp == 599.99

    def test_negative_duration_raises(self):
        with pytest.raises(ValidationError):
            VideoSkimCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=100.0,
                user_prompt="Describe",
                video_duration=-1.0,
            )

    def test_zero_duration_raises(self):
        with pytest.raises(ValidationError):
            VideoSkimCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=100.0,
                user_prompt="Describe",
                video_duration=0.0,
            )

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            VideoSkimCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=100.0,
                user_prompt="Describe",
                video_duration=600.0,
                extra_field="not allowed",
            )

    def test_long_video_input(self):
        # Testing with a very long video
        input_data = VideoSkimCaptionInput(
            filename="long_video.mp4",
            start_timestamp=0.0,
            end_timestamp=7199.0,  # Less than video_duration
            user_prompt="Skim through entire video",
            video_duration=7200.0,
        )
        # End timestamp within bounds should stay as-is
        assert input_data.end_timestamp == 7199.0
