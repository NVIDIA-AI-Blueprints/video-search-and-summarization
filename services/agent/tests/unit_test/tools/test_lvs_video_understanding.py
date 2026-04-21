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
"""Unit tests for lvs_video_understanding module."""

from pydantic import ValidationError
import pytest

from vss_agents.tools.lvs_video_understanding import LVSVideoUnderstandingConfig
from vss_agents.tools.lvs_video_understanding import LVSVideoUnderstandingInput


class TestLVSVideoUnderstandingConfig:
    """Test LVSVideoUnderstandingConfig model."""

    def test_with_required_fields(self):
        config = LVSVideoUnderstandingConfig(
            lvs_backend_url="http://localhost:38111",
            hitl_scenario_template="Scenario: {scenario}",
            hitl_events_template="Events: {events}",
            hitl_objects_template="Objects: {objects}",
        )
        assert config.lvs_backend_url == "http://localhost:38111"
        assert config.hitl_scenario_template == "Scenario: {scenario}"
        assert config.hitl_events_template == "Events: {events}"
        assert config.hitl_objects_template == "Objects: {objects}"
        # Check defaults
        assert config.conn_timeout_ms == 5000
        assert config.read_timeout_ms == 600000
        assert config.model == "gpt-4o"
        assert config.video_url_tool == "vst_video_url"

    def test_custom_timeouts(self):
        config = LVSVideoUnderstandingConfig(
            lvs_backend_url="http://localhost:38111",
            hitl_scenario_template="Scenario template",
            hitl_events_template="Events template",
            hitl_objects_template="Objects template",
            conn_timeout_ms=10000,
            read_timeout_ms=1200000,
        )
        assert config.conn_timeout_ms == 10000
        assert config.read_timeout_ms == 1200000

    def test_custom_model(self):
        config = LVSVideoUnderstandingConfig(
            lvs_backend_url="http://localhost:38111",
            hitl_scenario_template="Scenario template",
            hitl_events_template="Events template",
            hitl_objects_template="Objects template",
            model="custom-model",
        )
        assert config.model == "custom-model"

    def test_custom_video_url_tool(self):
        config = LVSVideoUnderstandingConfig(
            lvs_backend_url="http://localhost:38111",
            hitl_scenario_template="Scenario template",
            hitl_events_template="Events template",
            hitl_objects_template="Objects template",
            video_url_tool="custom_video_tool",
        )
        assert config.video_url_tool == "custom_video_tool"

    def test_missing_lvs_backend_url_fails(self):
        with pytest.raises(ValidationError):
            LVSVideoUnderstandingConfig(
                hitl_scenario_template="Scenario template",
                hitl_events_template="Events template",
                hitl_objects_template="Objects template",
            )

    def test_missing_hitl_template_fails(self):
        with pytest.raises(ValidationError):
            LVSVideoUnderstandingConfig(
                lvs_backend_url="http://localhost:38111",
                hitl_events_template="Events template",
                hitl_objects_template="Objects template",
            )


class TestLVSVideoUnderstandingInput:
    """Test LVSVideoUnderstandingInput model."""

    def test_basic_input(self):
        input_data = LVSVideoUnderstandingInput(
            sensor_id="sensor-001",
        )
        assert input_data.sensor_id == "sensor-001"

    def test_missing_sensor_id_fails(self):
        with pytest.raises(ValidationError):
            LVSVideoUnderstandingInput()

    def test_empty_sensor_id_fails(self):
        with pytest.raises(ValidationError):
            LVSVideoUnderstandingInput(
                sensor_id="",
            )
