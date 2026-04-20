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
"""Unit tests for rtvi_vlm_alert module."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.rtvi_vlm_alert import RTVIVLMAlertConfig
from vss_agents.tools.rtvi_vlm_alert import RTVIVLMAlertInput
from vss_agents.tools.rtvi_vlm_alert import RTVIVLMAlertOutput
from vss_agents.tools.rtvi_vlm_alert import _sensor_to_rtvi_stream_id


class TestRTVIVLMAlertConfig:
    """Test RTVIVLMAlertConfig model."""

    def test_required_fields(self):
        config = RTVIVLMAlertConfig(
            rtvi_vlm_base_url="http://localhost:8000",
            vst_internal_url="http://10.0.0.1:30888",
        )
        assert config.rtvi_vlm_base_url == "http://localhost:8000"
        assert config.vst_internal_url == "http://10.0.0.1:30888"
        assert config.default_model == "nvidia/cosmos-reason1-7b"
        assert config.default_chunk_duration == 20
        assert config.default_fps == 1
        assert config.timeout == 60

    def test_custom_defaults(self):
        config = RTVIVLMAlertConfig(
            rtvi_vlm_base_url="http://localhost:8000",
            vst_internal_url="http://10.0.0.1:30888",
            default_model="custom-model",
            default_chunk_duration=10,
            default_fps=2,
            default_prompt="Detect collisions",
            default_system_prompt="You are a monitor",
            timeout=30,
        )
        assert config.default_model == "custom-model"
        assert config.default_chunk_duration == 10
        assert config.default_fps == 2
        assert config.default_prompt == "Detect collisions"
        assert config.default_system_prompt == "You are a monitor"
        assert config.timeout == 30

    def test_optional_va_tool(self):
        config = RTVIVLMAlertConfig(
            rtvi_vlm_base_url="http://localhost:8000",
            vst_internal_url="http://10.0.0.1:30888",
            va_get_incidents_tool="va_get_incidents",
        )
        assert config.va_get_incidents_tool == "va_get_incidents"

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            RTVIVLMAlertConfig(
                rtvi_vlm_base_url="http://localhost:8000",
            )


class TestRTVIVLMAlertInput:
    """Test RTVIVLMAlertInput model."""

    def test_start_action(self):
        inp = RTVIVLMAlertInput(
            action="start",
            sensor_name="HWY_20",
            prompt="Detect collisions",
        )
        assert inp.action == "start"
        assert inp.sensor_name == "HWY_20"

    def test_stop_action(self):
        inp = RTVIVLMAlertInput(action="stop", sensor_name="HWY_20")
        assert inp.action == "stop"

    def test_get_incidents_action(self):
        inp = RTVIVLMAlertInput(
            action="get_incidents",
            sensor_name="HWY_20",
            start_time="2026-01-06T00:00:00.000Z",
            end_time="2026-01-07T00:00:00.000Z",
            max_count=5,
            incident_type="collision",
        )
        assert inp.action == "get_incidents"
        assert inp.max_count == 5
        assert inp.incident_type == "collision"

    def test_defaults(self):
        inp = RTVIVLMAlertInput(action="start")
        assert inp.sensor_name is None
        assert inp.prompt is None
        assert inp.system_prompt is None
        assert inp.start_time is None
        assert inp.end_time is None
        assert inp.max_count == 10
        assert inp.incident_type is None

    def test_invalid_action_raises(self):
        with pytest.raises(ValidationError):
            RTVIVLMAlertInput(action="invalid")


class TestRTVIVLMAlertOutput:
    """Test RTVIVLMAlertOutput model."""

    def test_success_output(self):
        output = RTVIVLMAlertOutput(
            success=True,
            sensor_name="HWY_20",
            stream_id="uuid-123",
            message="Started monitoring",
        )
        assert output.success is True
        assert output.stream_id == "uuid-123"

    def test_failure_output(self):
        output = RTVIVLMAlertOutput(
            success=False,
            message="sensor_name is required",
        )
        assert output.success is False

    def test_incidents_output(self):
        output = RTVIVLMAlertOutput(
            success=True,
            sensor_name="HWY_20",
            message="Found 3 incidents",
            incidents=[{"id": "1"}, {"id": "2"}, {"id": "3"}],
            total_count=3,
        )
        assert output.total_count == 3
        assert len(output.incidents) == 3

    def test_defaults(self):
        output = RTVIVLMAlertOutput(success=True, message="ok")
        assert output.sensor_name is None
        assert output.stream_id is None
        assert output.incidents is None
        assert output.total_count is None


class TestSensorToRtviStreamIdMapping:
    """Test the in-memory sensor mapping."""

    def test_mapping_is_dict(self):
        assert isinstance(_sensor_to_rtvi_stream_id, dict)
