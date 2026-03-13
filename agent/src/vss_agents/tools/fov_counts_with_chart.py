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
from datetime import datetime
import logging

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class FOVCountsWithChartConfig(FunctionBaseConfig, name="get_fov_counts_with_chart"):
    """Configuration for FOV counts with automatic chart generation."""

    get_fov_histogram_tool: FunctionRef = Field(
        ...,
        description="The tool to use for getting FOV histogram data",
    )
    chart_generator_tool: FunctionRef = Field(
        ...,
        description="The tool to use for generating charts",
    )
    chart_base_url: str = Field(
        default="http://localhost:38000/reports/",
        description="Base URL for accessing stored chart images",
    )


class FOVCountsWithChartInput(BaseModel):
    """Input for FOV counts with chart generation."""

    sensor_id: str = Field(..., description="Sensor ID to fetch counts from")
    start_time: str = Field(
        ...,
        description="Start time in ISO format (e.g., '2025-10-14T14:00:00.000Z')",
    )
    end_time: str = Field(
        ...,
        description="End time in ISO format (e.g., '2025-10-14T14:01:00.000Z')",
    )
    object_type: str | None = Field(
        default=None,
        description="Object type to count (e.g., 'Person'). If not specified, returns counts for all object types.",
    )
    bucket_count: int = Field(
        default=10,
        description="Number of time buckets for histogram (default: 10)",
    )


class FOVCountsWithChartOutput(BaseModel):
    """Output from FOV counts with chart generation."""

    summary: str = Field(..., description="Summary of the count data")
    latest_count: int = Field(..., description="Most recent object count")
    average_count: float = Field(..., description="Average count across all time bins")
    chart_url: str | None = Field(None, description="URL to the generated chart image")
    raw_histogram: dict = Field(..., description="Raw histogram data from the API")


@register_function(config_type=FOVCountsWithChartConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def get_fov_counts_with_chart(config: FOVCountsWithChartConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """Get FOV histogram data and automatically generate a visualization chart."""

    # Get the tools
    get_fov_histogram_tool = await builder.get_tool(
        config.get_fov_histogram_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN
    )
    chart_generator_tool = await builder.get_tool(config.chart_generator_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    async def _get_fov_counts_with_chart(input_data: FOVCountsWithChartInput) -> FOVCountsWithChartOutput:
        """Main implementation."""
        import json

        logger.info(
            f"Getting FOV histogram for sensor {input_data.sensor_id} from {input_data.start_time} to {input_data.end_time}"
        )

        # Step 1: Get FOV histogram data
        tool_input = {
            "source": input_data.sensor_id,
            "start_time": input_data.start_time,
            "end_time": input_data.end_time,
            "bucket_count": input_data.bucket_count,
        }
        if input_data.object_type:
            tool_input["object_type"] = input_data.object_type

        fov_result = await get_fov_histogram_tool.ainvoke(tool_input)

        # Parse the result if it's a string
        if isinstance(fov_result, str):
            fov_data = json.loads(fov_result)
        else:
            fov_data = fov_result

        logger.debug(f"FOV counts result: {fov_data}")

        # Step 2: Parse histogram data
        histogram = fov_data.get("histogram", [])
        if not histogram:
            return FOVCountsWithChartOutput(
                summary="No data available for the specified time range",
                latest_count=0,
                average_count=0.0,
                chart_url=None,
                raw_histogram=fov_data,
            )

        # Extract counts and time labels
        x_categories = []
        counts = []
        for entry in histogram:
            start_time = entry.get("start", "")
            # Format time to show only HH:MM:SS instead of full ISO timestamp
            try:
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                formatted_time = dt.strftime("%H:%M:%S")
                x_categories.append(formatted_time)
            except (ValueError, AttributeError):
                # Fallback to original if parsing fails
                x_categories.append(start_time)

            # Get the count for the specified object type (or sum all if not specified)
            objects = entry.get("objects", [])
            count = 0
            if input_data.object_type:
                # Filter by specific object type
                for obj in objects:
                    if obj.get("type") == input_data.object_type:
                        count = int(obj.get("averageCount", 0))
                        break
            else:
                # Sum all object types
                for obj in objects:
                    count += int(obj.get("averageCount", 0))
            counts.append(count)

        latest_count = counts[-1] if counts else 0
        average_count = sum(counts) / len(counts) if counts else 0.0

        logger.info(
            f"Parsed {len(counts)} histogram entries. Latest count: {latest_count}, Average: {average_count:.1f}"
        )

        # Step 3: Generate chart
        object_label = input_data.object_type if input_data.object_type else "All Objects"
        chart_input = {
            "charts_data": [
                {
                    "chart_file_format": "png",
                    "title": f"{object_label} Count at {input_data.sensor_id}",
                    "x_categories": x_categories,
                    "series": {"Count": counts},
                    "x_label": "Time",
                    "y_label": "Count",
                }
            ],
            "output_dir": "fov_charts",
            "file_prefix": f"fov_{input_data.sensor_id}_",
        }

        logger.debug(f"Calling chart_generator with input: {chart_input}")
        chart_result = await chart_generator_tool.ainvoke(chart_input)
        logger.debug(f"Chart generator returned: {chart_result}")

        # Parse chart result
        chart_url = None
        if isinstance(chart_result, str):
            # Chart result is HTML with img tag
            import re

            url_match = re.search(r'src="([^"]+)"', chart_result)
            chart_url = url_match.group(1) if url_match else None
        elif isinstance(chart_result, list) and len(chart_result) > 0:
            # Result is a list of ChartGenExecOutput
            first_chart = chart_result[0]
            if hasattr(first_chart, "object_store_key") and first_chart.object_store_key:
                chart_url = f"{config.chart_base_url}{first_chart.object_store_key}"

        logger.info(f"Chart generated successfully. URL: {chart_url}")

        # Create summary with embedded chart
        summary = (
            f"Object counts for {input_data.sensor_id} over {len(histogram)} time intervals:\n"
            f"- Latest count: {latest_count} {object_label}\n"
            f"- Average count: {average_count:.1f} {object_label}\n"
            f"- Time range: {input_data.start_time} to {input_data.end_time}"
        )

        # Embed the chart directly in the summary if available
        if chart_url:
            summary += f"\n\n![{object_label} Count Chart]({chart_url})"

        return FOVCountsWithChartOutput(
            summary=summary,
            latest_count=latest_count,
            average_count=average_count,
            chart_url=chart_url,
            raw_histogram=fov_data,
        )

    yield FunctionInfo.create(
        single_fn=_get_fov_counts_with_chart,
        description="Get field-of-view object counts for a sensor and generate a visualization chart. Returns both count statistics and a chart image.",
        input_schema=FOVCountsWithChartInput,
        single_output_schema=FOVCountsWithChartOutput,
    )
