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
"""Additional unit tests for vst.duration module to improve coverage."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.vst.duration import VSTDurationConfig
from vss_agents.tools.vst.duration import VSTDurationInput
from vss_agents.tools.vst.duration import VSTDurationOutput


class TestVSTDurationConfig:
    """Test VSTDurationConfig model."""

    def test_required_fields(self):
        config = VSTDurationConfig(vst_internal_url="http://10.0.0.1:30888")
        assert config.vst_internal_url == "http://10.0.0.1:30888"

    def test_missing_url_raises(self):
        with pytest.raises(ValidationError):
            VSTDurationConfig()


class TestVSTDurationInput:
    """Test VSTDurationInput model."""

    def test_valid(self):
        inp = VSTDurationInput(sensor_id="camera1")
        assert inp.sensor_id == "camera1"

    def test_empty_sensor_id_raises(self):
        with pytest.raises(ValidationError):
            VSTDurationInput(sensor_id="")

    def test_missing_sensor_id_raises(self):
        with pytest.raises(ValidationError):
            VSTDurationInput()


class TestVSTDurationOutput:
    """Test VSTDurationOutput model."""

    def test_valid(self):
        output = VSTDurationOutput(duration=300.0)
        assert output.duration == 300.0

    def test_missing_duration_raises(self):
        with pytest.raises(ValidationError):
            VSTDurationOutput()
