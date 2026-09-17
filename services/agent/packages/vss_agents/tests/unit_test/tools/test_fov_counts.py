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

import json
from typing import ClassVar
from unittest.mock import AsyncMock

from pydantic import ValidationError
import pytest

from vss_agents.tools.fov_counts_with_chart import FOVCountsWithChartConfig
from vss_agents.tools.fov_counts_with_chart import FOVCountsWithChartInput
from vss_agents.tools.fov_counts_with_chart import FOVCountsWithChartOutput
from vss_agents.tools.fov_counts_with_chart import get_fov_counts_with_chart


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

    def test_object_type_is_required(self):
        with pytest.raises(ValidationError, match="object_type"):
            FOVCountsWithChartInput(
                sensor_id="sensor-001",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T01:00:00.000Z",
            )

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
        for obj_type in ["All", "Person", "Vehicle", "Animal"]:
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
        assert "per_type_average" in data
        assert "chart_url" in data
        assert "raw_histogram" in data


def _make_histogram_entry(start: str, obj_type: str, avg_count: float) -> dict:
    return {
        "start": start,
        "objects": [{"type": obj_type, "averageCount": avg_count}],
    }


class TestFOVCountsWithChartFunction:
    """Functional tests exercising the inner tool logic via mocked dependencies."""

    @pytest.fixture
    def fov_tools(self):
        mock_builder = AsyncMock()
        fov_histogram_tool = AsyncMock()
        chart_generator_tool = AsyncMock()

        async def _get_tool(ref, wrapper_type=None):
            if ref == "get_fov_histogram":
                return fov_histogram_tool
            if ref == "chart_generator":
                return chart_generator_tool
            raise ValueError(f"Unknown tool ref: {ref}")

        mock_builder.get_tool = _get_tool

        config = FOVCountsWithChartConfig(
            get_fov_histogram_tool="get_fov_histogram",
            chart_generator_tool="chart_generator",
        )
        return config, mock_builder, fov_histogram_tool, chart_generator_tool

    async def _get_inner_fn(self, config, builder):
        gen = get_fov_counts_with_chart.__wrapped__(config, builder)
        fn_info = await gen.__anext__()
        return fn_info.single_fn

    @pytest.mark.asyncio
    async def test_all_zero_counts_skips_chart(self, fov_tools):
        config, builder, fov_tool, chart_tool = fov_tools

        fov_tool.ainvoke.return_value = json.dumps(
            {
                "histogram": [
                    _make_histogram_entry("2025-01-01T00:00:00Z", "Person", 0),
                    _make_histogram_entry("2025-01-01T00:01:00Z", "Person", 0),
                    _make_histogram_entry("2025-01-01T00:02:00Z", "Person", 0),
                ],
            }
        )

        inner_fn = await self._get_inner_fn(config, builder)
        result = await inner_fn(
            FOVCountsWithChartInput(
                sensor_id="Camera_03",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T00:03:00.000Z",
                object_type="Person",
            )
        )

        assert result.latest_count == 0
        assert result.average_count == 0.0
        assert result.chart_url is None
        chart_tool.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_negative_counts_clamped_to_zero(self, fov_tools):
        config, builder, fov_tool, chart_tool = fov_tools

        fov_tool.ainvoke.return_value = json.dumps(
            {
                "histogram": [
                    _make_histogram_entry("2025-01-01T00:00:00Z", "Person", -5),
                    _make_histogram_entry("2025-01-01T00:01:00Z", "Person", -10),
                ],
            }
        )

        inner_fn = await self._get_inner_fn(config, builder)
        result = await inner_fn(
            FOVCountsWithChartInput(
                sensor_id="Camera_03",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T00:02:00.000Z",
                object_type="Person",
            )
        )

        assert result.latest_count == 0
        assert result.average_count == 0.0
        assert result.chart_url is None
        chart_tool.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_histogram_returns_no_data(self, fov_tools):
        config, builder, fov_tool, chart_tool = fov_tools

        fov_tool.ainvoke.return_value = json.dumps({"histogram": []})

        inner_fn = await self._get_inner_fn(config, builder)
        result = await inner_fn(
            FOVCountsWithChartInput(
                sensor_id="Camera_03",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T00:03:00.000Z",
                object_type="Person",
            )
        )

        assert result.latest_count == 0
        assert result.summary == "No data available for the specified time range"
        assert result.chart_url is None
        chart_tool.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "response,expected",
        [
            ("", "FOV histogram service returned an empty response for sensor 'gwfix6'"),
            ("not-json", "FOV histogram service returned invalid JSON for sensor 'gwfix6'"),
        ],
    )
    async def test_invalid_histogram_response_names_backend_failure(self, fov_tools, response, expected):
        config, builder, fov_tool, chart_tool = fov_tools
        fov_tool.ainvoke.return_value = response
        inner_fn = await self._get_inner_fn(config, builder)

        with pytest.raises(ValueError, match=expected):
            await inner_fn(
                FOVCountsWithChartInput(
                    sensor_id="gwfix6",
                    start_time="2025-01-01T00:00:00.000Z",
                    end_time="2025-01-01T00:00:40.000Z",
                    object_type="Person",
                )
            )

        chart_tool.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_positive_counts_generates_chart(self, fov_tools):
        config, builder, fov_tool, chart_tool = fov_tools

        fov_tool.ainvoke.return_value = json.dumps(
            {
                "histogram": [
                    _make_histogram_entry("2025-01-01T00:00:00Z", "Person", 5),
                    _make_histogram_entry("2025-01-01T00:01:00Z", "Person", 10),
                ],
            }
        )

        mock_chart_output = AsyncMock()
        mock_chart_output.success = True
        mock_chart_output.object_store_key = "fov_charts/fov_Camera_03_0.png"
        chart_tool.ainvoke.return_value = [mock_chart_output]

        inner_fn = await self._get_inner_fn(config, builder)
        result = await inner_fn(
            FOVCountsWithChartInput(
                sensor_id="Camera_03",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T00:02:00.000Z",
                object_type="Person",
            )
        )

        assert result.latest_count == 10
        assert result.average_count == 7.5
        assert result.chart_url is not None
        assert "fov_Camera_03_0.png" in result.chart_url
        chart_tool.ainvoke.assert_called_once()


class TestFOVCountsObjectTypeScope:
    """A warehouse sensor sees 7 classes; only the Person filter answers "how many people"."""

    # Mirrors the warehouse detector label set, where Person is a small fraction of all detections.
    WAREHOUSE_BUCKET: ClassVar[dict] = {
        "start": "2025-01-01T00:00:00Z",
        "objects": [
            {"type": "Person", "averageCount": 1},
            {"type": "Pallet", "averageCount": 10},
            {"type": "Forklift", "averageCount": 2},
            {"type": "Transporter", "averageCount": 2},
            {"type": "Nova_Carter", "averageCount": 1},
        ],
    }

    @pytest.fixture
    def fov_tools(self):
        mock_builder = AsyncMock()
        fov_histogram_tool = AsyncMock()
        chart_generator_tool = AsyncMock()

        async def _get_tool(ref, wrapper_type=None):
            if ref == "get_fov_histogram":
                return fov_histogram_tool
            if ref == "chart_generator":
                return chart_generator_tool
            raise ValueError(f"Unknown tool ref: {ref}")

        mock_builder.get_tool = _get_tool
        config = FOVCountsWithChartConfig(
            get_fov_histogram_tool="get_fov_histogram",
            chart_generator_tool="chart_generator",
        )
        return config, mock_builder, fov_histogram_tool, chart_generator_tool

    async def _get_inner_fn(self, config, builder):
        gen = get_fov_counts_with_chart.__wrapped__(config, builder)
        fn_info = await gen.__anext__()
        return fn_info.single_fn

    @pytest.mark.asyncio
    async def test_person_filter_counts_only_people(self, fov_tools):
        config, builder, fov_tool, chart_tool = fov_tools
        fov_tool.ainvoke.return_value = json.dumps({"histogram": [self.WAREHOUSE_BUCKET]})
        chart_tool.ainvoke.return_value = []

        inner_fn = await self._get_inner_fn(config, builder)
        result = await inner_fn(
            FOVCountsWithChartInput(
                sensor_id="Camera_01",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T00:10:00.000Z",
                object_type="Person",
            )
        )

        assert result.latest_count == 1
        assert result.average_count == 1.0
        assert "SCOPE" not in result.summary

    @pytest.mark.asyncio
    async def test_explicit_all_returns_aggregate_and_breakdown(self, fov_tools):
        config, builder, fov_tool, chart_tool = fov_tools
        fov_tool.ainvoke.return_value = json.dumps({"histogram": [self.WAREHOUSE_BUCKET]})
        chart_tool.ainvoke.return_value = []

        inner_fn = await self._get_inner_fn(config, builder)
        result = await inner_fn(
            FOVCountsWithChartInput(
                sensor_id="Camera_01",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T00:10:00.000Z",
                object_type="All",
            )
        )

        # The explicit All scope aggregates classes while preserving the per-class result.
        assert result.latest_count == 16
        assert result.per_type_average["Person"] == 1.0
        assert result.per_type_average["Pallet"] == 10.0
        assert "Scope: All" in result.summary
        assert "Person: 1.0" in result.summary
        assert "Pallet: 10.0" in result.summary
        fov_tool.ainvoke.assert_awaited_once()
        assert "object_type" not in fov_tool.ainvoke.await_args.args[0]
