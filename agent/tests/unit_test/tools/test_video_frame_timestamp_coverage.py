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
"""Additional unit tests for video_frame_timestamp module to improve coverage."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.video_frame_timestamp import VideoFrameTimestampConfig
from vss_agents.tools.video_frame_timestamp import VideoFrameTimestampInput


class TestVideoFrameTimestampConfig:
    """Test VideoFrameTimestampConfig model."""

    def test_defaults(self):
        config = VideoFrameTimestampConfig()
        assert config.llm_name == "openai_llm"
        assert config.prompt is not None

    def test_custom(self):
        config = VideoFrameTimestampConfig(
            llm_name="custom_llm",
            prompt="Custom prompt for timestamp extraction",
        )
        assert config.llm_name == "custom_llm"


class TestVideoFrameTimestampInput:
    """Test VideoFrameTimestampInput model."""

    def test_valid_input(self):
        inp = VideoFrameTimestampInput(
            asset_file_path="/path/to/video.mp4",
            frame_offset_seconds=30.0,
        )
        assert inp.asset_file_path == "/path/to/video.mp4"
        assert inp.frame_offset_seconds == 30.0

    def test_missing_path_raises(self):
        with pytest.raises(ValidationError):
            VideoFrameTimestampInput(frame_offset_seconds=10.0)

    def test_missing_offset_raises(self):
        with pytest.raises(ValidationError):
            VideoFrameTimestampInput(asset_file_path="/path")
