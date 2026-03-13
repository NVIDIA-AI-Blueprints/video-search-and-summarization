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
"""Additional unit tests for video_skim_caption module to improve coverage."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.video_skim_caption import VideoSkimCaptionConfig
from vss_agents.tools.video_skim_caption import VideoSkimCaptionInput


class TestVideoSkimCaptionConfig:
    """Test VideoSkimCaptionConfig model."""

    def test_defaults(self):
        config = VideoSkimCaptionConfig()
        assert config.skim_fps == 0.5

    def test_custom_fps(self):
        config = VideoSkimCaptionConfig(skim_fps=0.25)
        assert config.skim_fps == 0.25


class TestVideoSkimCaptionInput:
    """Test VideoSkimCaptionInput model."""

    def test_valid_input(self):
        inp = VideoSkimCaptionInput(
            filename="long_video.mp4",
            start_timestamp=0.0,
            end_timestamp=300.0,
            user_prompt="Summarize",
            video_duration=600.0,
        )
        assert inp.filename == "long_video.mp4"
        assert inp.start_timestamp == 0.0
        assert inp.end_timestamp == 300.0

    def test_end_timestamp_capped(self):
        inp = VideoSkimCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=1000.0,
            user_prompt="test",
            video_duration=100.0,
        )
        assert inp.end_timestamp == pytest.approx(99.99)

    def test_end_timestamp_none(self):
        inp = VideoSkimCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=None,
            user_prompt="test",
            video_duration=200.0,
        )
        assert inp.end_timestamp == pytest.approx(199.99)

    def test_zero_duration_raises(self):
        with pytest.raises(ValueError, match="Video duration must be positive"):
            VideoSkimCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="test",
                video_duration=0.0,
            )

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            VideoSkimCaptionInput(
                filename="video.mp4",
                start_timestamp=0.0,
                end_timestamp=10.0,
                user_prompt="test",
                video_duration=100.0,
                extra="no",
            )
