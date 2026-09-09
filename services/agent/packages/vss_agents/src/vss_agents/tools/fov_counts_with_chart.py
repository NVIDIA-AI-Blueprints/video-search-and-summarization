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
    object_type: str = Field(
        ...,
        min_length=1,
        description=(
            "Required count scope. Pass the exact detected class (e.g. 'Person') to count one class, "
            "or pass 'All' explicitly to aggregate every detected class. Never use 'All' for a question "
            "about a specific class: asking about people requires object_type='Person'."
        ),
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
    per_type_average: dict[str, float] = Field(
        default_factory=dict,
        description="Per-class averages, populated when object_type is 'All'",
    )
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

        # "All" is an explicit public selector. The histogram API represents that scope by omitting
        # its optional object_type filter.
        selected_object_type = None if input_data.object_type.casefold() == "all" else input_data.object_type

        # Step 1: Get FOV histogram data
        tool_input = {
            "source": input_data.sensor_id,
            "start_time": input_data.start_time,
            "end_time": input_data.end_time,
            "bucket_count": input_data.bucket_count,
        }
        if selected_object_type:
            tool_input["object_type"] = selected_object_type

        fov_result = await get_fov_histogram_tool.ainvoke(tool_input)

        # Parse the result if it's a string
        if isinstance(fov_result, str):
            if not fov_result.strip():
                raise ValueError(
                    f"FOV histogram service returned an empty response for sensor '{input_data.sensor_id}'",
                )
            try:
                fov_data = json.loads(fov_result)
            except json.JSONDecodeError as ex:
                raise ValueError(
                    f"FOV histogram service returned invalid JSON for sensor '{input_data.sensor_id}'",
                ) from ex
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
        per_type_totals: dict[str, int] = {}
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

            # Get the selected class count, or sum all classes for the explicit "All" scope.
            objects = entry.get("objects", [])
            count = 0
            if selected_object_type:
                # Filter by specific object type
                for obj in objects:
                    if obj.get("type") == selected_object_type:
                        count = max(0, int(obj.get("averageCount", 0)))
                        break
            else:
                # Sum all object types
                for obj in objects:
                    obj_count = max(0, int(obj.get("averageCount", 0)))
                    count += obj_count
                    obj_type = obj.get("type", "Unknown")
                    per_type_totals[obj_type] = per_type_totals.get(obj_type, 0) + obj_count
            counts.append(count)

        latest_count = counts[-1] if counts else 0
        average_count = sum(counts) / len(counts) if counts else 0.0

        logger.info(
            f"Parsed {len(counts)} histogram entries. Latest count: {latest_count}, Average: {average_count:.1f}"
        )

        # Step 3: Generate chart only if there is meaningful data to visualize
        object_label = selected_object_type or "All Objects"
        chart_url = None

        has_nonzero_data = any(c > 0 for c in counts)

        logger.info(f"has_nonzero_data: {has_nonzero_data}")

        if has_nonzero_data:
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
                "file_prefix": f"fov_{input_data.sensor_id}_{int(datetime.now().timestamp())}_",
            }

            logger.debug(f"Calling chart_generator with input: {chart_input}")
            chart_result = await chart_generator_tool.ainvoke(chart_input)
            logger.debug(f"Chart generator returned: {chart_result}")

            if isinstance(chart_result, str):
                import re

                url_match = re.search(r'src="([^"]+)"', chart_result)
                chart_url = url_match.group(1) if url_match else None
            elif isinstance(chart_result, list) and len(chart_result) > 0:
                first_chart = chart_result[0]
                if hasattr(first_chart, "object_store_key") and first_chart.object_store_key:
                    chart_url = f"{config.chart_base_url}{first_chart.object_store_key}"

            logger.info(f"Chart generated successfully. URL: {chart_url}")
        else:
            logger.info("All counts are zero — skipping chart generation.")

        # Create summary with embedded chart
        summary = (
            f"Object counts for {input_data.sensor_id} over {len(histogram)} time intervals:\n"
            f"- Latest count: {latest_count} {object_label}\n"
            f"- Average count: {average_count:.1f} {object_label}\n"
            f"- Time range: {input_data.start_time} to {input_data.end_time}"
        )

        per_type_average = {obj_type: total / len(counts) for obj_type, total in sorted(per_type_totals.items())}

        # The explicit All scope sums heterogeneous classes. Carry the per-class split so callers
        # cannot mistake that aggregate for the count of one class.
        if not selected_object_type and per_type_average:
            breakdown = ", ".join(f"{obj_type}: {average:.1f}" for obj_type, average in per_type_average.items())
            summary += (
                "\n- Scope: All (the counts above are the sum of every detected class, not a count of"
                " people or any other single class)"
                f"\n- Per-class average: {breakdown}"
            )

        # Embed the chart directly in the summary if available
        if chart_url:
            summary += f"\n\n![{object_label} Count Chart]({chart_url})"

        return FOVCountsWithChartOutput(
            summary=summary,
            latest_count=latest_count,
            average_count=average_count,
            per_type_average=per_type_average,
            chart_url=chart_url,
            raw_histogram=fov_data,
        )

    yield FunctionInfo.create(
        single_fn=_get_fov_counts_with_chart,
        description=(
            "Get field-of-view counts and a chart. object_type is required: pass the exact class "
            "(use 'Person' for people) or explicitly pass 'All' for an all-class aggregate and breakdown."
        ),
        input_schema=FOVCountsWithChartInput,
        single_output_schema=FOVCountsWithChartOutput,
    )
