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
"""Unit tests for fov_counts_with_chart module."""

from vss_agents.tools.fov_counts_with_chart import FOVCountsWithChartConfig
from vss_agents.tools.fov_counts_with_chart import FOVCountsWithChartInput
from vss_agents.tools.fov_counts_with_chart import FOVCountsWithChartOutput


class TestFOVCountsWithChartConfig:
    """Test FOVCountsWithChartConfig model."""

    def test_config_creation(self):
        config = FOVCountsWithChartConfig(
            get_fov_histogram_tool="get_fov_histogram",
            chart_generator_tool="chart_generator",
        )
        assert config.get_fov_histogram_tool == "get_fov_histogram"
        assert config.chart_generator_tool == "chart_generator"
        assert config.chart_base_url == "http://localhost:38000/reports/"

    def test_config_custom_base_url(self):
        config = FOVCountsWithChartConfig(
            get_fov_histogram_tool="get_fov_histogram",
            chart_generator_tool="chart_generator",
            chart_base_url="http://example.com/charts/",
        )
        assert config.chart_base_url == "http://example.com/charts/"


class TestFOVCountsWithChartInput:
    """Test FOVCountsWithChartInput model."""

    def test_input_minimal(self):
        input_data = FOVCountsWithChartInput(
            sensor_id="sensor-001",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
        )
        assert input_data.sensor_id == "sensor-001"
        assert input_data.object_type is None
        assert input_data.bucket_count == 10

    def test_input_full(self):
        input_data = FOVCountsWithChartInput(
            sensor_id="sensor-002",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            object_type="Person",
            bucket_count=20,
        )
        assert input_data.object_type == "Person"
        assert input_data.bucket_count == 20

    def test_input_various_object_types(self):
        for obj_type in ["Person", "Vehicle", "Animal"]:
            input_data = FOVCountsWithChartInput(
                sensor_id="sensor",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T01:00:00.000Z",
                object_type=obj_type,
            )
            assert input_data.object_type == obj_type


class TestFOVCountsWithChartOutput:
    """Test FOVCountsWithChartOutput model."""

    def test_output_creation(self):
        output = FOVCountsWithChartOutput(
            summary="Found 100 objects",
            latest_count=15,
            average_count=12.5,
            raw_histogram={"histogram": []},
        )
        assert output.summary == "Found 100 objects"
        assert output.latest_count == 15
        assert output.average_count == 12.5
        assert output.chart_url is None

    def test_output_with_chart_url(self):
        output = FOVCountsWithChartOutput(
            summary="Objects counted",
            latest_count=10,
            average_count=8.0,
            chart_url="http://localhost:38000/reports/chart.png",
            raw_histogram={"histogram": [{"count": 10}]},
        )
        assert output.chart_url == "http://localhost:38000/reports/chart.png"

    def test_output_zero_counts(self):
        output = FOVCountsWithChartOutput(
            summary="No objects found",
            latest_count=0,
            average_count=0.0,
            raw_histogram={},
        )
        assert output.latest_count == 0
        assert output.average_count == 0.0

    def test_output_serialization(self):
        output = FOVCountsWithChartOutput(
            summary="Test",
            latest_count=5,
            average_count=5.0,
            raw_histogram={"test": True},
        )
        data = output.model_dump()
        assert "summary" in data
        assert "latest_count" in data
        assert "average_count" in data
        assert "chart_url" in data
        assert "raw_histogram" in data
