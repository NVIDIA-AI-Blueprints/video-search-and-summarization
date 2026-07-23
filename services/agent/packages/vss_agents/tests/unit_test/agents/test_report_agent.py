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
"""Unit tests for report_agent module."""

from datetime import datetime

from pydantic import ValidationError
import pytest

from vss_agents.agents.report_agent import ReportAgentInput
from vss_agents.agents.report_agent import VideoReportAgentInput


class TestReportAgentInput:
    """Test ReportAgentInput model."""

    def test_defaults(self):
        input_data = ReportAgentInput()
        assert input_data.start_time is None
        assert input_data.end_time is None
        assert input_data.incident_id is None
        assert input_data.source is None
        assert input_data.source_type is None
        assert input_data.vlm_reasoning is None

    def test_with_incident_id(self):
        input_data = ReportAgentInput(incident_id="incident-123")
        assert input_data.incident_id == "incident-123"

    def test_with_time_range(self):
        start = datetime(2025, 1, 1, 0, 0)
        end = datetime(2025, 1, 1, 23, 59)
        input_data = ReportAgentInput(start_time=start, end_time=end)
        assert input_data.start_time == start
        assert input_data.end_time == end

    def test_with_source_sensor(self):
        input_data = ReportAgentInput(source="sensor-001", source_type="sensor")
        assert input_data.source == "sensor-001"
        assert input_data.source_type == "sensor"

    def test_with_source_place(self):
        input_data = ReportAgentInput(source="Main Street", source_type="place")
        assert input_data.source_type == "place"

    def test_invalid_source_type(self):
        with pytest.raises(ValidationError):
            ReportAgentInput(source="test", source_type="invalid")

    def test_vlm_reasoning_enabled(self):
        input_data = ReportAgentInput(vlm_reasoning=True)
        assert input_data.vlm_reasoning is True

    def test_vlm_reasoning_disabled(self):
        input_data = ReportAgentInput(vlm_reasoning=False)
        assert input_data.vlm_reasoning is False


class TestVideoReportAgentInput:
    """Test VideoReportAgentInput model."""

    def test_all_fields(self):
        input_data = VideoReportAgentInput(sensor_id="vst-sensor-001", user_query="What's happening in this video?")
        assert input_data.sensor_id == "vst-sensor-001"
        assert input_data.user_query == "What's happening in this video?"

    def test_missing_sensor_id(self):
        with pytest.raises(ValidationError):
            VideoReportAgentInput(user_query="test")

    def test_only_sensor_id(self):
        input_data = VideoReportAgentInput(sensor_id="vst-sensor-001")
        assert input_data.sensor_id == "vst-sensor-001"
        assert input_data.user_query == "Generate a detailed report of the video."
