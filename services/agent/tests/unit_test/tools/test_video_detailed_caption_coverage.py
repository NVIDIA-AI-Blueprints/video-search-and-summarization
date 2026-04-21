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
"""Additional unit tests for video_detailed_caption module to improve coverage."""

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

    def test_custom(self):
        config = VideoDetailedCaptionConfig(
            detailed_fps=4.0,
            max_video_duration=120,
        )
        assert config.detailed_fps == 4.0
        assert config.max_video_duration == 120


class TestVideoDetailedCaptionInput:
    """Test VideoDetailedCaptionInput model."""

    def test_valid_input(self):
        inp = VideoDetailedCaptionInput(
            filename="video.mp4",
            start_timestamp=10.0,
            end_timestamp=20.0,
            user_prompt="Describe what happens",
            video_duration=100.0,
        )
        assert inp.filename == "video.mp4"
        assert inp.start_timestamp == 10.0
        assert inp.end_timestamp == 20.0

    def test_end_timestamp_capped(self):
        inp = VideoDetailedCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=200.0,
            user_prompt="test",
            video_duration=50.0,
        )
        assert inp.end_timestamp == pytest.approx(49.99)

    def test_end_timestamp_none(self):
        inp = VideoDetailedCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=None,
            user_prompt="test",
            video_duration=30.0,
        )
        assert inp.end_timestamp == pytest.approx(29.99)

    def test_zero_duration_raises(self):
        with pytest.raises(ValueError, match="Video duration must be positive"):
            VideoDetailedCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="test",
                video_duration=0.0,
            )

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            VideoDetailedCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="test",
                video_duration=100.0,
                extra="not_allowed",
            )
