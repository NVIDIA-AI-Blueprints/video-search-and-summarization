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
"""Tests for chart_generator inner function via generator invocation."""

from unittest.mock import AsyncMock

import matplotlib.pyplot as plt
import pytest
from vss_agents.tools.chart_generator import BarChartData
from vss_agents.tools.chart_generator import ChartGeneratorConfig
from vss_agents.tools.chart_generator import ChartGeneratorInput
from vss_agents.tools.chart_generator import PieChartData
from vss_agents.tools.chart_generator import chart_generator


class TestChartGeneratorInner:
    """Test the inner generate_chart function."""

    @pytest.fixture
    def config(self):
        return ChartGeneratorConfig(
            object_store_name="test_store",
            object_store_base_url="http://localhost:8000/static/",
        )

    @pytest.fixture
    def mock_builder(self):
        builder = AsyncMock()
        mock_object_store = AsyncMock()
        mock_object_store.upsert_object.return_value = None
        builder.get_object_store_client.return_value = mock_object_store
        return builder

    @pytest.mark.asyncio
    async def test_generate_bar_chart(self, config, mock_builder):
        gen = chart_generator.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        bar_data = BarChartData(x_categories=["A", "B"], series={"count": [10, 20]}, title="Test")
        inp = ChartGeneratorInput(charts_data=[bar_data], output_dir="charts")
        result = await inner_fn(inp)

        assert len(result) == 1
        assert result[0].success is True
        assert result[0].object_store_key is not None
        plt.close("all")

    @pytest.mark.asyncio
    async def test_generate_pie_chart(self, config, mock_builder):
        gen = chart_generator.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        pie_data = PieChartData(sizes=[30, 70], labels=["A", "B"], title="Pie")
        inp = ChartGeneratorInput(charts_data=[pie_data], output_dir="charts")
        result = await inner_fn(inp)

        assert len(result) == 1
        assert result[0].success is True
        plt.close("all")

    @pytest.mark.asyncio
    async def test_generate_multiple_charts(self, config, mock_builder):
        gen = chart_generator.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        bar_data = BarChartData(x_categories=["X"], series={"s": [1]})
        pie_data = PieChartData(sizes=[50, 50], labels=["A", "B"])
        inp = ChartGeneratorInput(charts_data=[bar_data, pie_data], output_dir="charts")
        result = await inner_fn(inp)

        assert len(result) == 2
        assert all(r.success for r in result)
        plt.close("all")

    @pytest.mark.asyncio
    async def test_no_object_store_raises(self, mock_builder):
        config = ChartGeneratorConfig()  # No object_store_name
        gen = chart_generator.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        bar_data = BarChartData(x_categories=["A"], series={"s": [1]})
        inp = ChartGeneratorInput(charts_data=[bar_data])
        with pytest.raises(RuntimeError, match="Failed to generate chart"):
            await inner_fn(inp)
        plt.close("all")

    @pytest.mark.asyncio
    async def test_output_converter(self, config, mock_builder):
        gen = chart_generator.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        assert function_info.converters is not None
        assert len(function_info.converters) >= 3
