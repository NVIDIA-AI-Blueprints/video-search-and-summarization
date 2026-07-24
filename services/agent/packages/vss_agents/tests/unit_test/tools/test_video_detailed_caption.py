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
"""Unit tests for video_detailed_caption module."""

from pydantic import ValidationError
import pytest

from vss_agents.tools.video_detailed_caption import VideoDetailedCaptionConfig
from vss_agents.tools.video_detailed_caption import VideoDetailedCaptionInput


class TestVideoDetailedCaptionConfig:
    """Test VideoDetailedCaptionConfig model."""

    def test_defaults(self):
        config = VideoDetailedCaptionConfig()
        assert config.detailed_fps == 2.0
        assert config.max_video_duration == 60

    def test_custom_values(self):
        config = VideoDetailedCaptionConfig(
            detailed_fps=4.0,
            max_video_duration=120,
        )
        assert config.detailed_fps == 4.0
        assert config.max_video_duration == 120


class TestVideoDetailedCaptionInput:
    """Test VideoDetailedCaptionInput model."""

    def test_valid_input(self):
        input_data = VideoDetailedCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=30.0,
            user_prompt="Describe in detail",
            video_duration=60.0,
        )
        assert input_data.filename == "video.mp4"
        assert input_data.start_timestamp == 0.0
        assert input_data.end_timestamp == 30.0
        assert input_data.user_prompt == "Describe in detail"
        assert input_data.video_duration == 60.0

    def test_end_timestamp_clamped_to_duration(self):
        input_data = VideoDetailedCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=100.0,  # Greater than video_duration
            user_prompt="Describe in detail",
            video_duration=60.0,
        )
        # Should be clamped to video_duration - 0.01
        assert input_data.end_timestamp == 59.99

    def test_end_timestamp_none_uses_duration(self):
        input_data = VideoDetailedCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=None,
            user_prompt="Describe in detail",
            video_duration=60.0,
        )
        assert input_data.end_timestamp == 59.99

    def test_negative_duration_raises(self):
        with pytest.raises(ValidationError):
            VideoDetailedCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="Describe",
                video_duration=-1.0,
            )

    def test_zero_duration_raises(self):
        with pytest.raises(ValidationError):
            VideoDetailedCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="Describe",
                video_duration=0.0,
            )

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            VideoDetailedCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="Describe",
                video_duration=60.0,
                extra_field="not allowed",
            )

    def test_missing_filename_raises(self):
        with pytest.raises(ValidationError):
            VideoDetailedCaptionInput(
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="Describe",
                video_duration=60.0,
            )

    def test_missing_start_timestamp_raises(self):
        with pytest.raises(ValidationError):
            VideoDetailedCaptionInput(
                filename="video.mp4",
                end_timestamp=10.0,
                user_prompt="Describe",
                video_duration=60.0,
            )

    def test_missing_user_prompt_raises(self):
        with pytest.raises(ValidationError):
            VideoDetailedCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                video_duration=60.0,
            )

    def test_missing_video_duration_raises(self):
        # Missing video_duration triggers KeyError in model_validator before field validation
        with pytest.raises(KeyError):
            VideoDetailedCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="Describe",
            )
