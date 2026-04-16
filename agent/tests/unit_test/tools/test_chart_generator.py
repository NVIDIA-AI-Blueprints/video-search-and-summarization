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
"""Unit tests for chart_generator module."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.chart_generator import BarChartData
from vss_agents.tools.chart_generator import ChartData
from vss_agents.tools.chart_generator import ChartFileFormat
from vss_agents.tools.chart_generator import ChartGeneratorConfig
from vss_agents.tools.chart_generator import ChartGeneratorInput
from vss_agents.tools.chart_generator import ChartGenExecOutput
from vss_agents.tools.chart_generator import ChartType
from vss_agents.tools.chart_generator import PieChartData
from vss_agents.tools.chart_generator import _str_input_converter
from vss_agents.tools.chart_generator import convert_to_format
from vss_agents.tools.chart_generator import plot_bar_chart
from vss_agents.tools.chart_generator import plot_pie_chart


class TestChartType:
    """Test ChartType enum."""

    def test_chart_type_values(self):
        assert ChartType.BAR == "bar"
        assert ChartType.PIE == "pie"

    def test_chart_type_all_values(self):
        assert len(ChartType) == 2


class TestChartFileFormat:
    """Test ChartFileFormat enum."""

    def test_chart_file_format_values(self):
        assert ChartFileFormat.PNG == "png"
        assert ChartFileFormat.SVG == "svg"
        assert ChartFileFormat.JPEG == "jpeg"


class TestChartData:
    """Test ChartData base model."""

    def test_chart_data_defaults(self):
        data = ChartData()
        assert data.chart_file_format == ChartFileFormat.PNG
        assert data.title == ""

    def test_chart_data_with_values(self):
        data = ChartData(chart_file_format=ChartFileFormat.SVG, title="Test Chart")
        assert data.chart_file_format == ChartFileFormat.SVG
        assert data.title == "Test Chart"


class TestBarChartData:
    """Test BarChartData model."""

    def test_bar_chart_data_creation(self):
        data = BarChartData(
            x_categories=["A", "B", "C"],
            series={"values": [10.0, 20.0, 30.0]},
        )
        assert data.x_categories == ["A", "B", "C"]
        assert data.series == {"values": [10.0, 20.0, 30.0]}
        assert data.x_label == ""
        assert data.y_label == ""

    def test_bar_chart_data_full(self):
        data = BarChartData(
            x_categories=["Jan", "Feb", "Mar"],
            series={"sales": [100.0, 150.0, 200.0], "expenses": [80.0, 90.0, 100.0]},
            x_label="Month",
            y_label="Amount",
            title="Monthly Report",
            chart_file_format=ChartFileFormat.PNG,
        )
        assert data.x_label == "Month"
        assert data.y_label == "Amount"
        assert data.title == "Monthly Report"
        assert len(data.series) == 2

    def test_bar_chart_data_empty_series(self):
        data = BarChartData(
            x_categories=["A"],
            series={},
        )
        assert data.series == {}


class TestPieChartData:
    """Test PieChartData model."""

    def test_pie_chart_data_creation(self):
        data = PieChartData(
            sizes=[30.0, 20.0, 50.0],
            labels=["A", "B", "C"],
        )
        assert data.sizes == [30.0, 20.0, 50.0]
        assert data.labels == ["A", "B", "C"]
        assert data.title == ""

    def test_pie_chart_data_with_title(self):
        data = PieChartData(
            sizes=[25.0, 25.0, 50.0],
            labels=["X", "Y", "Z"],
            title="Distribution",
        )
        assert data.title == "Distribution"


class TestChartGeneratorConfig:
    """Test ChartGeneratorConfig model."""

    def test_config_defaults(self):
        config = ChartGeneratorConfig()
        assert config.object_store_name is None
        assert str(config.object_store_base_url) == "http://localhost:8000/static/"

    def test_config_with_custom_url(self):
        config = ChartGeneratorConfig(object_store_base_url="http://example.com/charts")
        # The validator adds trailing slash
        assert str(config.object_store_base_url).endswith("/")

    def test_config_url_with_trailing_slash(self):
        config = ChartGeneratorConfig(object_store_base_url="http://example.com/charts/")
        assert str(config.object_store_base_url) == "http://example.com/charts/"

    def test_config_url_with_query_fails(self):
        with pytest.raises(ValidationError):
            ChartGeneratorConfig(object_store_base_url="http://example.com/charts?query=1")

    def test_config_url_with_fragment_fails(self):
        with pytest.raises(ValidationError):
            ChartGeneratorConfig(object_store_base_url="http://example.com/charts#section")

    def test_config_url_pointing_to_file_fails(self):
        with pytest.raises(ValidationError):
            ChartGeneratorConfig(object_store_base_url="http://example.com/charts/image.png")


class TestChartGeneratorInput:
    """Test ChartGeneratorInput model."""

    def test_input_with_bar_chart(self):
        bar_data = BarChartData(
            x_categories=["A", "B"],
            series={"data": [1.0, 2.0]},
        )
        input_data = ChartGeneratorInput(charts_data=[bar_data])
        assert len(input_data.charts_data) == 1
        assert input_data.output_dir is None
        assert input_data.file_prefix == "chart_"

    def test_input_with_pie_chart(self):
        pie_data = PieChartData(
            sizes=[50.0, 50.0],
            labels=["Yes", "No"],
        )
        input_data = ChartGeneratorInput(charts_data=[pie_data])
        assert len(input_data.charts_data) == 1

    def test_input_with_mixed_charts(self):
        bar_data = BarChartData(x_categories=["A"], series={"x": [1.0]})
        pie_data = PieChartData(sizes=[100.0], labels=["All"])
        input_data = ChartGeneratorInput(charts_data=[bar_data, pie_data])
        assert len(input_data.charts_data) == 2

    def test_input_output_dir_sanitization(self):
        input_data = ChartGeneratorInput(
            charts_data=[],
            output_dir="charts/subfolder/../other",
        )
        # Should be normalized
        assert ".." not in str(input_data.output_dir)

    def test_input_output_dir_absolute_path(self):
        input_data = ChartGeneratorInput(
            charts_data=[],
            output_dir="/absolute/path",
        )
        assert input_data.output_dir is not None

    def test_input_output_dir_none(self):
        input_data = ChartGeneratorInput(charts_data=[])
        assert input_data.output_dir is None


class TestChartGenExecOutput:
    """Test ChartGenExecOutput model."""

    def test_output_success(self):
        output = ChartGenExecOutput(
            success=True,
            error_message=None,
            object_store_key="charts/chart_0.png",
        )
        assert output.success is True
        assert output.error_message is None
        assert output.object_store_key == "charts/chart_0.png"

    def test_output_failure(self):
        output = ChartGenExecOutput(
            success=False,
            error_message="Generation failed",
        )
        assert output.success is False
        assert output.error_message == "Generation failed"
        assert output.object_store_key is None


class TestPlotBarChart:
    """Test plot_bar_chart function."""

    def test_plot_bar_chart_single_series(self):
        data = BarChartData(
            x_categories=["A", "B", "C"],
            series={"values": [10.0, 20.0, 30.0]},
            title="Test Bar Chart",
            x_label="Categories",
            y_label="Values",
        )
        fig = plot_bar_chart(data)
        assert fig is not None
        # Cleanup
        import matplotlib.pyplot as plt

        plt.close(fig)

    def test_plot_bar_chart_multiple_series(self):
        data = BarChartData(
            x_categories=["Q1", "Q2", "Q3", "Q4"],
            series={
                "2023": [100.0, 120.0, 150.0, 180.0],
                "2024": [110.0, 130.0, 160.0, 200.0],
            },
            title="Quarterly Sales Comparison",
        )
        fig = plot_bar_chart(data)
        assert fig is not None
        import matplotlib.pyplot as plt

        plt.close(fig)

    def test_plot_bar_chart_empty_series(self):
        data = BarChartData(
            x_categories=["A", "B"],
            series={"empty": [0.0, 0.0]},  # Use empty values instead of empty dict
        )
        fig = plot_bar_chart(data)
        assert fig is not None
        import matplotlib.pyplot as plt

        plt.close(fig)


class TestPlotPieChart:
    """Test plot_pie_chart function."""

    def test_plot_pie_chart_basic(self):
        data = PieChartData(
            sizes=[30.0, 20.0, 50.0],
            labels=["A", "B", "C"],
            title="Distribution",
        )
        fig = plot_pie_chart(data)
        assert fig is not None
        import matplotlib.pyplot as plt

        plt.close(fig)

    def test_plot_pie_chart_no_title(self):
        data = PieChartData(
            sizes=[50.0, 50.0],
            labels=["Yes", "No"],
        )
        fig = plot_pie_chart(data)
        assert fig is not None
        import matplotlib.pyplot as plt

        plt.close(fig)


class TestConvertToFormat:
    """Test convert_to_format function."""

    def test_convert_to_png(self):
        data = BarChartData(
            x_categories=["A"],
            series={"data": [1.0]},
        )
        fig = plot_bar_chart(data)
        result = convert_to_format(fig, ChartFileFormat.PNG)
        assert isinstance(result, bytes)
        assert len(result) > 0
        # PNG files start with specific bytes
        assert result[:4] == b"\x89PNG"
        import matplotlib.pyplot as plt

        plt.close(fig)

    def test_convert_to_svg(self):
        data = PieChartData(
            sizes=[100.0],
            labels=["All"],
        )
        fig = plot_pie_chart(data)
        result = convert_to_format(fig, ChartFileFormat.SVG)
        assert isinstance(result, bytes)
        assert b"<svg" in result
        import matplotlib.pyplot as plt

        plt.close(fig)


class TestStrInputConverter:
    """Test _str_input_converter function."""

    def test_convert_json_string(self):
        json_str = '{"charts_data": [], "file_prefix": "test_"}'
        result = _str_input_converter(json_str)
        assert isinstance(result, ChartGeneratorInput)
        assert result.file_prefix == "test_"

    def test_convert_with_chart_data(self):
        json_str = """
        {
            "charts_data": [
                {
                    "x_categories": ["A", "B"],
                    "series": {"values": [1.0, 2.0]}
                }
            ]
        }
        """
        result = _str_input_converter(json_str)
        assert len(result.charts_data) == 1

    def test_convert_invalid_json(self):
        with pytest.raises(Exception):
            _str_input_converter("not valid json")
