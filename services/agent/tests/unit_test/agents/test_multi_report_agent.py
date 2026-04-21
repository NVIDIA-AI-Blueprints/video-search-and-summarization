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
"""Unit tests for multi_report_agent module."""

from pydantic import ValidationError
import pytest

from vss_agents.agents.multi_report_agent import MultiReportAgentConfig
from vss_agents.agents.multi_report_agent import MultiReportAgentInput


class TestMultiReportAgentInput:
    """Test MultiReportAgentInput model."""

    def test_input_minimal_sensor(self):
        input_data = MultiReportAgentInput(
            source="sensor-001",
            source_type="sensor",
        )
        assert input_data.source == "sensor-001"
        assert input_data.source_type == "sensor"
        assert input_data.start_time is None
        assert input_data.end_time is None
        assert input_data.max_result_size is None

    def test_input_minimal_place(self):
        input_data = MultiReportAgentInput(
            source="Building A",
            source_type="place",
        )
        assert input_data.source_type == "place"

    def test_input_with_time_range(self):
        input_data = MultiReportAgentInput(
            source="sensor-002",
            source_type="sensor",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T23:59:59.000Z",
        )
        assert input_data.start_time == "2025-01-01T00:00:00.000Z"
        assert input_data.end_time == "2025-01-01T23:59:59.000Z"

    def test_input_with_max_result_size(self):
        input_data = MultiReportAgentInput(
            source="sensor-003",
            source_type="sensor",
            max_result_size=50,
        )
        assert input_data.max_result_size == 50

    def test_input_max_result_size_must_be_positive(self):
        with pytest.raises(ValidationError):
            MultiReportAgentInput(
                source="sensor",
                source_type="sensor",
                max_result_size=0,
            )
        with pytest.raises(ValidationError):
            MultiReportAgentInput(
                source="sensor",
                source_type="sensor",
                max_result_size=-1,
            )

    def test_input_invalid_source_type(self):
        with pytest.raises(ValidationError):
            MultiReportAgentInput(
                source="test",
                source_type="invalid",
            )


class TestMultiReportAgentConfig:
    """Test MultiReportAgentConfig model."""

    def test_config_creation(self):
        config = MultiReportAgentConfig(
            multi_incident_tool="multi_incident_formatter",
        )
        assert config.multi_incident_tool == "multi_incident_formatter"
        assert config.max_incidents == 10000

    def test_config_custom_max_incidents(self):
        config = MultiReportAgentConfig(
            multi_incident_tool="formatter",
            max_incidents=100,
        )
        assert config.max_incidents == 100

    def test_config_max_incidents_minimum(self):
        config = MultiReportAgentConfig(
            multi_incident_tool="formatter",
            max_incidents=1,
        )
        assert config.max_incidents == 1

    def test_config_max_incidents_maximum(self):
        config = MultiReportAgentConfig(
            multi_incident_tool="formatter",
            max_incidents=10000,
        )
        assert config.max_incidents == 10000

    def test_config_max_incidents_below_minimum(self):
        with pytest.raises(ValidationError):
            MultiReportAgentConfig(
                multi_incident_tool="formatter",
                max_incidents=0,
            )

    def test_config_max_incidents_above_maximum(self):
        with pytest.raises(ValidationError):
            MultiReportAgentConfig(
                multi_incident_tool="formatter",
                max_incidents=10001,
            )
