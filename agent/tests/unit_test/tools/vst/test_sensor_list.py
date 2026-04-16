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
"""Unit tests for vst.sensor_list module."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.vst.sensor_list import VSTSensorListConfig
from vss_agents.tools.vst.sensor_list import VSTSensorListInput
from vss_agents.tools.vst.sensor_list import VSTSensorListOutput


class TestVSTSensorListConfig:
    """Test VSTSensorListConfig model."""

    def test_required_fields(self):
        config = VSTSensorListConfig(
            vst_internal_url="http://localhost:30888",
        )
        assert config.vst_internal_url == "http://localhost:30888"

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            VSTSensorListConfig()


class TestVSTSensorListInput:
    """Test VSTSensorListInput model."""

    def test_empty_input(self):
        input_data = VSTSensorListInput()
        assert input_data is not None


class TestVSTSensorListOutput:
    """Test VSTSensorListOutput model."""

    def test_empty_list(self):
        output = VSTSensorListOutput(sensor_names=[])
        assert output.sensor_names == []

    def test_single_sensor(self):
        output = VSTSensorListOutput(sensor_names=["camera-001"])
        assert output.sensor_names == ["camera-001"]
        assert len(output.sensor_names) == 1

    def test_multiple_sensors(self):
        sensors = ["camera-001", "camera-002", "camera-003"]
        output = VSTSensorListOutput(sensor_names=sensors)
        assert output.sensor_names == sensors
        assert len(output.sensor_names) == 3

    def test_serialization(self):
        output = VSTSensorListOutput(sensor_names=["sensor-a", "sensor-b"])
        data = output.model_dump()
        assert "sensor_names" in data
        assert data["sensor_names"] == ["sensor-a", "sensor-b"]

    def test_various_sensor_names(self):
        sensor_names = [
            "Main Street Camera",
            "sensor-001",
            "CAMERA_ABC_123",
            "camera.prod.east.1",
        ]
        output = VSTSensorListOutput(sensor_names=sensor_names)
        assert len(output.sensor_names) == 4
        for name in sensor_names:
            assert name in output.sensor_names
