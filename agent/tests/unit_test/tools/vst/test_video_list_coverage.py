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
"""Additional unit tests for vst.video_list module to improve coverage."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.vst.video_list import VSTVideoListConfig
from vss_agents.tools.vst.video_list import VSTVideoListInput
from vss_agents.tools.vst.video_list import VSTVideoListOutput


class TestVSTVideoListConfig:
    """Test VSTVideoListConfig model."""

    def test_required_fields(self):
        config = VSTVideoListConfig(vst_internal_url="http://10.0.0.1:30888")
        assert config.vst_internal_url == "http://10.0.0.1:30888"

    def test_missing_url_raises(self):
        with pytest.raises(ValidationError):
            VSTVideoListConfig()


class TestVSTVideoListInput:
    """Test VSTVideoListInput model."""

    def test_empty_input(self):
        inp = VSTVideoListInput()
        assert inp is not None


class TestVSTVideoListOutput:
    """Test VSTVideoListOutput model."""

    def test_valid(self):
        output = VSTVideoListOutput(
            video_list=[
                {"name": "video1.mp4", "duration": 60.0},
                {"name": "video2.mp4", "duration": 120.0},
            ]
        )
        assert len(output.video_list) == 2
        assert output.video_list[0]["name"] == "video1.mp4"

    def test_empty_list(self):
        output = VSTVideoListOutput(video_list=[])
        assert output.video_list == []

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError):
            VSTVideoListOutput()
