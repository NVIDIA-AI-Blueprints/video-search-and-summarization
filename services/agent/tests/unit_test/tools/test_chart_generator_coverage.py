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
"""Additional unit tests for chart_generator module to improve coverage."""

import json
from unittest.mock import MagicMock

import matplotlib
import matplotlib.pyplot as plt
from pydantic import ValidationError
import pytest

from vss_agents.tools.chart_generator import BarChartData
from vss_agents.tools.chart_generator import ChartFileFormat
from vss_agents.tools.chart_generator import ChartGeneratorConfig
from vss_agents.tools.chart_generator import ChartGeneratorInput
from vss_agents.tools.chart_generator import ChartGenExecOutput
from vss_agents.tools.chart_generator import ChartType
from vss_agents.tools.chart_generator import PieChartData
from vss_agents.tools.chart_generator import _chat_request_input_converter
from vss_agents.tools.chart_generator import _str_input_converter
from vss_agents.tools.chart_generator import convert_to_format
from vss_agents.tools.chart_generator import plot_bar_chart
from vss_agents.tools.chart_generator import plot_pie_chart


class TestChartType:
    """Test ChartType enum."""

    def test_bar(self):
        assert ChartType.BAR == "bar"

    def test_pie(self):
        assert ChartType.PIE == "pie"


class TestChartFileFormat:
    """Test ChartFileFormat enum."""

    def test_png(self):
        assert ChartFileFormat.PNG == "png"

    def test_svg(self):
        assert ChartFileFormat.SVG == "svg"

    def test_jpeg(self):
        assert ChartFileFormat.JPEG == "jpeg"


class TestBarChartData:
    """Test BarChartData model."""

    def test_basic(self):
        data = BarChartData(
            x_categories=["A", "B", "C"],
            series={"count": [10, 20, 30]},
        )
        assert data.x_categories == ["A", "B", "C"]
        assert data.series == {"count": [10, 20, 30]}
        assert data.chart_file_format == ChartFileFormat.PNG
        assert data.title == ""

    def test_with_labels(self):
        data = BarChartData(
            x_categories=["X", "Y"],
            series={"s1": [1, 2], "s2": [3, 4]},
            x_label="Categories",
            y_label="Values",
            title="Test Chart",
        )
        assert data.x_label == "Categories"
        assert data.y_label == "Values"
        assert data.title == "Test Chart"


class TestPieChartData:
    """Test PieChartData model."""

    def test_basic(self):
        data = PieChartData(sizes=[30, 70], labels=["A", "B"])
        assert data.sizes == [30, 70]
        assert data.labels == ["A", "B"]

    def test_with_title(self):
        data = PieChartData(sizes=[10, 20, 70], labels=["X", "Y", "Z"], title="Pie")
        assert data.title == "Pie"


class TestPlotBarChart:
    """Test plot_bar_chart function."""

    def test_basic_bar_chart(self):
        data = BarChartData(
            x_categories=["A", "B"],
            series={"count": [10, 20]},
            title="Test Bar",
            x_label="Cat",
            y_label="Val",
        )
        fig = plot_bar_chart(data)
        assert isinstance(fig, matplotlib.figure.Figure)
        plt.close(fig)

    def test_multiple_series(self):
        data = BarChartData(
            x_categories=["A", "B", "C"],
            series={"s1": [1, 2, 3], "s2": [4, 5, 6]},
        )
        fig = plot_bar_chart(data)
        assert isinstance(fig, matplotlib.figure.Figure)
        plt.close(fig)


class TestPlotPieChart:
    """Test plot_pie_chart function."""

    def test_basic_pie_chart(self):
        data = PieChartData(sizes=[30, 70], labels=["A", "B"], title="Test Pie")
        fig = plot_pie_chart(data)
        assert isinstance(fig, matplotlib.figure.Figure)
        plt.close(fig)

    def test_pie_chart_no_title(self):
        data = PieChartData(sizes=[50, 50], labels=["X", "Y"])
        fig = plot_pie_chart(data)
        assert isinstance(fig, matplotlib.figure.Figure)
        plt.close(fig)


class TestConvertToFormat:
    """Test convert_to_format function."""

    def test_convert_to_png(self):
        data = BarChartData(x_categories=["A"], series={"s": [1]})
        fig = plot_bar_chart(data)
        result = convert_to_format(fig, ChartFileFormat.PNG)
        assert isinstance(result, bytes)
        assert len(result) > 0
        plt.close(fig)

    def test_convert_to_svg(self):
        data = BarChartData(x_categories=["A"], series={"s": [1]})
        fig = plot_bar_chart(data)
        result = convert_to_format(fig, ChartFileFormat.SVG)
        assert isinstance(result, bytes)
        assert len(result) > 0
        plt.close(fig)


class TestChartGeneratorConfig:
    """Test ChartGeneratorConfig model."""

    def test_defaults(self):
        config = ChartGeneratorConfig()
        assert config.object_store_name is None
        assert "localhost" in str(config.object_store_base_url)

    def test_custom_url(self):
        config = ChartGeneratorConfig(object_store_base_url="http://storage.example.com/charts/")
        assert "storage.example.com" in str(config.object_store_base_url)

    def test_url_with_query_raises(self):
        with pytest.raises(ValidationError):
            ChartGeneratorConfig(object_store_base_url="http://example.com/path?q=1")

    def test_url_with_fragment_raises(self):
        with pytest.raises(ValidationError):
            ChartGeneratorConfig(object_store_base_url="http://example.com/path#frag")

    def test_url_pointing_to_file_raises(self):
        with pytest.raises(ValidationError):
            ChartGeneratorConfig(object_store_base_url="http://example.com/file.png")

    def test_url_normalization(self):
        config = ChartGeneratorConfig(object_store_base_url="http://example.com/charts")
        assert str(config.object_store_base_url).endswith("/")


class TestChartGeneratorInput:
    """Test ChartGeneratorInput model."""

    def test_basic(self):
        bar = BarChartData(x_categories=["A"], series={"s": [1]})
        inp = ChartGeneratorInput(charts_data=[bar])
        assert len(inp.charts_data) == 1
        assert inp.output_dir is None
        assert inp.file_prefix == "chart_"

    def test_output_dir_sanitized(self):
        bar = BarChartData(x_categories=["A"], series={"s": [1]})
        inp = ChartGeneratorInput(charts_data=[bar], output_dir="relative/path")
        assert ".." not in (inp.output_dir or "")

    def test_output_dir_none(self):
        bar = BarChartData(x_categories=["A"], series={"s": [1]})
        inp = ChartGeneratorInput(charts_data=[bar], output_dir=None)
        assert inp.output_dir is None


class TestChartGenExecOutput:
    """Test ChartGenExecOutput model."""

    def test_success(self):
        output = ChartGenExecOutput(
            success=True,
            error_message=None,
            object_store_key="charts/chart_0.png",
        )
        assert output.success is True
        assert output.object_store_key == "charts/chart_0.png"

    def test_failure(self):
        output = ChartGenExecOutput(
            success=False,
            error_message="Failed to generate",
        )
        assert output.success is False
        assert output.error_message == "Failed to generate"


class TestStrInputConverter:
    """Test _str_input_converter function."""

    def test_valid_json(self):
        bar_data = {"x_categories": ["A"], "series": {"s": [1]}}
        input_json = json.dumps({"charts_data": [bar_data]})
        result = _str_input_converter(input_json)
        assert len(result.charts_data) == 1

    def test_invalid_json_raises(self):
        with pytest.raises(Exception):
            _str_input_converter("not json")


class TestChatRequestInputConverter:
    """Test _chat_request_input_converter function."""

    def test_valid_request(self):
        bar_data = {"x_categories": ["A"], "series": {"s": [1]}}
        content = json.dumps({"charts_data": [bar_data]})
        mock_message = MagicMock()
        mock_message.content = content
        mock_request = MagicMock()
        mock_request.messages = [mock_message]

        result = _chat_request_input_converter(mock_request)
        assert len(result.charts_data) == 1

    def test_invalid_content_raises(self):
        mock_message = MagicMock()
        mock_message.content = "not valid json"
        mock_request = MagicMock()
        mock_request.messages = [mock_message]

        with pytest.raises(Exception):
            _chat_request_input_converter(mock_request)
