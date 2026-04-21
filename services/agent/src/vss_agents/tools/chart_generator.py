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
from collections.abc import AsyncGenerator
from enum import StrEnum
import io
import logging
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse
from urllib.parse import urlunparse

import matplotlib
import matplotlib.pyplot as plt
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.api_server import ChatRequest
from nat.data_models.api_server import ChatResponse
from nat.data_models.component_ref import ObjectStoreRef
from nat.data_models.function import FunctionBaseConfig
from nat.object_store.models import ObjectStoreItem
from pydantic import AnyUrl
from pydantic import BaseModel
from pydantic import Field
from pydantic import HttpUrl
from pydantic import UrlConstraints
from pydantic import field_validator

logger = logging.getLogger(__name__)


class ChartType(StrEnum):
    BAR = "bar"
    PIE = "pie"


class ChartFileFormat(StrEnum):
    PNG = "png"
    SVG = "svg"
    JPEG = "jpeg"


class ChartData(BaseModel):
    chart_file_format: ChartFileFormat = ChartFileFormat.PNG
    title: str = ""


class BarChartData(ChartData):
    x_categories: list[str]
    series: dict[str, list[float]]
    x_label: str = ""
    y_label: str = ""


class PieChartData(ChartData):
    sizes: list[float]
    labels: list[str]


S3Url = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["s3"])]


class ChartGeneratorConfig(FunctionBaseConfig, name="chart_generator"):
    object_store_name: ObjectStoreRef | None = Field(
        default=None, description="The object store to store generated images."
    )

    object_store_base_url: HttpUrl | S3Url = Field(
        default=HttpUrl("http://localhost:8000/static/"),
        description="The base URL of the object store for serving files via HTTP.",
    )

    @field_validator("object_store_base_url", mode="before")
    @classmethod
    def must_be_directory_url(cls, v: str) -> str:
        parsed = urlparse(v)

        if parsed.query or parsed.fragment:
            raise ValueError("URL must not contain query or fragment")

        normalized_path = parsed.path.rstrip("/")

        last_segment = os.path.basename(normalized_path)
        if "." in last_segment:
            raise ValueError("URL must point to a directory, not a file")

        final_path = normalized_path + "/"

        new_url = urlunparse(parsed._replace(path=final_path))
        return new_url


class ChartGeneratorInput(BaseModel):
    """Input for the chart generation tool"""

    charts_data: list[BarChartData | PieChartData]
    output_dir: str | None = None
    file_prefix: str = "chart_"

    @field_validator("output_dir", mode="before")
    @classmethod
    def validate_and_sanitize_output_dir(cls, v: str | None) -> str | None:
        if v is None:
            return None

        # We return absolute path without first / to avoid double // in the URL
        return str(Path("/" + v).resolve())[1:]


class ChartGenExecOutput(BaseModel):
    success: bool
    error_message: str | None
    object_store_key: str | None = Field(
        default=None,
        description="Object store key for the generated chart.",
    )


def plot_bar_chart(bar_chart_data: BarChartData) -> matplotlib.figure.Figure:
    """
    Generates a grouped bar chart and returns the figure & axes.

    Parameters:
    - x_categories: list[str] | Categories for the x-axis
    - series: dict[str, list[float]] | Dict of series to plot,
        e.g. {"value1": [10, 20], "value2": [15, 25]}
    - title: str | Title of the chart
    - xlabel: str | Label for the x-axis
    - ylabel: str | Label for the y-axis

    Returns:
    - fig, ax: matplotlib Figure and Axes objects
    """
    x_categories = bar_chart_data.x_categories
    series = bar_chart_data.series
    title = bar_chart_data.title
    x_label = bar_chart_data.x_label
    y_label = bar_chart_data.y_label

    fig, ax = plt.subplots()

    n_series = len(series)
    x_positions = range(len(x_categories))
    bar_width = 0.8 / n_series

    for i, (label, y_values) in enumerate(series.items()):
        ax.bar([pos + i * bar_width for pos in x_positions], y_values, width=bar_width, label=label)

    ax.set_xticks([pos + bar_width * (n_series - 1) / 2 for pos in x_positions])
    ax.set_xticklabels(x_categories, rotation=45, ha="right")
    ax.set_title(title)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.legend()
    fig.tight_layout()

    return fig


def plot_pie_chart(pie_chart_data: PieChartData) -> matplotlib.figure.Figure:
    """
    Plot a pie chart using Matplotlib.

    Parameters:
    - pie_chart_data: PieChartData

    Example:
    ```
    plot_pie_chart(
        PieChartData(sizes=[30, 20, 50], labels=["A", "B", "C"], title="Pie Chart"),
    )
    ```
    """

    sizes = pie_chart_data.sizes
    labels = pie_chart_data.labels
    title = pie_chart_data.title

    fig, ax = plt.subplots()
    wedges, *_ = ax.pie(
        sizes,
        labels=labels,
    )

    if title:
        ax.set_title(title)

    if labels is not None:
        ax.legend(wedges, labels, loc="best")

    plt.tight_layout()

    return fig


def convert_to_format(chart: matplotlib.figure.Figure, chart_file_format: ChartFileFormat) -> bytes:
    buf = io.BytesIO()
    chart.savefig(buf, format=chart_file_format.value)
    buf.seek(0)
    return buf.getvalue()


def _str_input_converter(input: str) -> ChartGeneratorInput:
    return ChartGeneratorInput.model_validate_json(input)


def _chat_request_input_converter(request: ChatRequest) -> ChartGeneratorInput:
    try:
        return ChartGeneratorInput.model_validate_json(request.messages[-1].content)
    except Exception:
        logger.exception("Error in chat request input converter.")
        raise


@register_function(config_type=ChartGeneratorConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def chart_generator(config: ChartGeneratorConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    if config.object_store_name:
        object_store = await builder.get_object_store_client(object_store_name=config.object_store_name)
    else:
        object_store = None

    def _output_converter(output: list[ChartGenExecOutput]) -> str:
        output_str = ""
        for chart in output:
            if chart.success and chart.object_store_key:
                output_str += f'<img src="{config.object_store_base_url}{chart.object_store_key}" alt="Image" />'

        return output_str

    def _chat_response_output_converter(response: list[ChartGenExecOutput]) -> ChatResponse:
        return ChatResponse.from_string(_output_converter(response))

    async def generate_chart(chart_generator_input: ChartGeneratorInput) -> list[ChartGenExecOutput]:
        exec_outputs = []
        for i, chart_data in enumerate(chart_generator_input.charts_data):
            success = False
            error_message = None
            try:
                match chart_data:
                    case BarChartData():
                        chart = plot_bar_chart(chart_data)
                    case PieChartData():
                        chart = plot_pie_chart(chart_data)
                    case other:
                        raise RuntimeError(f"Unsupported chart data type: {other}")
                chart_bytes = convert_to_format(chart, chart_data.chart_file_format)
                key = None
                success = True
                if object_store and chart_generator_input.output_dir:
                    output_dir = chart_generator_input.output_dir
                    item = ObjectStoreItem(data=chart_bytes, content_type=f"image/{chart_data.chart_file_format.value}")
                    key = f"{output_dir}/{chart_generator_input.file_prefix}{i}.{chart_data.chart_file_format.value}"
                    await object_store.upsert_object(key, item)
                    success = True
                else:
                    raise ValueError("object_store and output_dir must be provided for chart generation")
            except Exception as e:
                raise RuntimeError("Failed to generate chart.") from e

            exec_outputs.append(
                ChartGenExecOutput(
                    success=success,
                    error_message=error_message,
                    object_store_key=key,
                )
            )

        return exec_outputs

    try:
        yield FunctionInfo.create(
            single_fn=generate_chart,
            description="Generate chart",
            input_schema=ChartGeneratorInput,
            single_output_schema=list[ChartGenExecOutput],
            converters=[
                _str_input_converter,
                _chat_request_input_converter,
                _output_converter,
                _chat_response_output_converter,
            ],
        )
    except Exception:
        logger.error("Error in chart generator, exit early")
        raise
