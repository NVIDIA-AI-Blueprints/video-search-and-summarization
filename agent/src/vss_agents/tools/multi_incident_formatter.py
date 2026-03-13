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

import asyncio
from collections import Counter
from collections import defaultdict
from collections.abc import AsyncGenerator
from datetime import datetime
from datetime import timedelta
import json
import logging
from typing import Any
from typing import Literal

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator

logger = logging.getLogger(__name__)


def _normalize_timestamp(timestamp: str) -> str:
    """
    Normalize timestamp to ISO 8601 format with exactly 3 digits for milliseconds.

    Handles timestamps with microseconds (6 digits) and truncates to milliseconds (3 digits).

    Args:
        timestamp: ISO timestamp string (e.g., '2025-11-17T15:16:38.273512Z' or '2025-11-17T15:16:38.273Z')

    Returns:
        Normalized timestamp with 3 decimal places (e.g., '2025-11-17T15:16:38.273Z')
    """
    # If timestamp has more than 3 decimal places, truncate to 3
    if "." in timestamp:
        date_part, rest = timestamp.split(".", 1)
        fractional_part = rest.rstrip("Z")
        # Truncate to 3 digits (milliseconds) or pad with zeros if less than 3
        fractional_part = fractional_part[:3].ljust(3, "0")
        return f"{date_part}.{fractional_part}Z"
    return timestamp


class MultiIncidentFormatterConfig(FunctionBaseConfig, name="multi_incident_formatter"):
    """Configuration for the multi-incident formatter tool."""

    video_url_tool: FunctionRef = Field(
        ...,
        description="The tool to use for getting video URLs",
    )
    picture_url_tool: FunctionRef = Field(
        ...,
        description="The tool to use for getting picture URLs",
    )
    incidents_tool: FunctionRef = Field(
        ...,
        description="The tool to use for getting incidents within a time range",
    )
    chart_generator_tool: FunctionRef | None = Field(
        default=None,
        description="The tool to use for generating charts (optional)",
    )
    generate_chart: bool = Field(
        default=False,
        description="Whether to automatically generate a chart visualizing the incidents.",
    )
    chart_base_url: str = Field(
        default="http://localhost:38000/reports/",
        description="Base URL for accessing stored chart images",
    )
    display_limit: int = Field(
        default=20,
        gt=0,
        le=100,
        description="Maximum number of incidents to format and display in UI with full details (video/snapshot URLs). "
        "Charts will show all fetched incidents regardless of this limit.",
    )


class IncidentData(BaseModel):
    """Single incident data."""

    incident_id: str = Field(..., description="Unique identifier for the incident")
    sensor_id: str = Field(..., description="Sensor ID where the incident occurred")
    start_timestamp: str = Field(..., description="Start timestamp in ISO format")
    end_timestamp: str = Field(..., description="End timestamp in ISO format")
    metadata: dict = Field(default_factory=dict, description="Additional incident metadata")


class MultiIncidentFormatterInput(BaseModel):
    """Input for the multi-incident formatter tool.

    Fetches incidents within a specified time range for a given source.
    """

    source: str = Field(..., description="Source to fetch incidents from (e.g., sensor ID or place)")
    source_type: Literal["sensor", "place"] = Field(..., description="Type of the source (must be 'sensor' or 'place')")
    start_time: str | None = Field(
        default=None,
        description="Optional start time in ISO format (e.g., '2025-09-22T14:00:00.000Z'). If omitted, fetches most recent incidents.",
    )
    end_time: str | None = Field(
        default=None,
        description="Optional end time in ISO format (e.g., '2025-09-22T15:00:00.000Z'). If omitted, fetches most recent incidents.",
    )
    max_result_size: int = Field(
        default=10000,
        description="Maximum number of incidents to fetch. "
        "Default is 10000 to get all incidents. "
        "Note: UI will display only top incidents, but charts will show all fetched incidents.",
        gt=0,
        le=10000,
    )

    @field_validator("start_time", "end_time")
    @classmethod
    def normalize_timestamps(cls, v: str | None) -> str | None:
        """Normalize timestamp to ISO 8601 format with exactly 3 digits for milliseconds."""
        if v is None:
            return None
        return _normalize_timestamp(v)


class MultiIncidentFormatterOutput(BaseModel):
    """Output from the multi-incident formatter tool."""

    formatted_incidents: str = Field(
        ...,
        description="Formatted string containing all incidents",
    )
    total_incidents: int = Field(
        ...,
        description="Total number of incidents processed",
    )
    chart_html: str | None = Field(
        default=None,
        description="HTML img tag for the generated chart (if generate_chart was True)",
    )


async def _fetch_incidents(
    formatter_input: MultiIncidentFormatterInput,
    incidents_tool: Any,
) -> list[IncidentData]:
    """Fetch incidents using the incidents tool."""
    logger.info(
        f"Fetching incidents for {formatter_input.source_type} {formatter_input.source} "
        f"(max {formatter_input.max_result_size} results)"
    )
    tool_input = {
        "source": formatter_input.source,
        "source_type": formatter_input.source_type,
        "start_time": formatter_input.start_time,
        "end_time": formatter_input.end_time,
        "max_count": formatter_input.max_result_size,
        "includes": ["object_ids", "info", "category"],
    }
    result = await incidents_tool.ainvoke(input=tool_input)

    # Parse the result into IncidentData objects
    incidents: list[IncidentData] = []
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON string: {e}")
            return incidents

    if isinstance(result, dict) and "incidents" in result:
        raw_incidents = result["incidents"]
    else:
        logger.error(f"Unexpected result format after parsing: {type(result)}")
        return incidents

    for incident in raw_incidents:
        if not isinstance(incident, dict):
            logger.warning(f"Skipping non-dict incident: {type(incident)}")
            continue

        incident_id = incident.get("Id", "unknown")
        sensor_id = incident.get("sensorId", formatter_input.source)
        start_timestamp = incident.get("timestamp", "")
        end_timestamp = incident.get("end", "")

        metadata = {
            "category": incident.get("category"),
            "type": incident.get("type"),
            "objectIds": incident.get("objectIds", []),
            "info": incident.get("info", {}),
            "place": incident.get("place", {}),
            "isAnomaly": incident.get("isAnomaly", False),
            "analyticsModule": incident.get("analyticsModule", {}),
            "frameIds": incident.get("frameIds", []),
        }

        incidents.append(
            IncidentData(
                incident_id=incident_id,
                sensor_id=sensor_id,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                metadata=metadata,
            )
        )

    logger.info(f"Fetched {len(incidents)} incidents")
    return incidents


async def _format_single_incident(
    incident: IncidentData,
    video_url_tool: Any,
    picture_url_tool: Any,
    incident_number: int,
) -> dict:
    """Format a single incident as a JSON object with video and image URLs."""
    try:
        logger.info(f"Processing incident {incident.incident_id}")

        # Get video URL
        video_url_result = await video_url_tool.ainvoke(
            input={
                "sensor_id": incident.sensor_id,
                "start_time": incident.start_timestamp,
                "end_time": incident.end_timestamp,
            }
        )
        video_url = video_url_result.video_url if hasattr(video_url_result, "video_url") else str(video_url_result)

        # Get picture URL
        picture_url_result = await picture_url_tool.ainvoke(
            input={
                "sensor_id": incident.sensor_id,
                "start_time": incident.start_timestamp,
            }
        )
        snapshot_url = (
            picture_url_result.image_url if hasattr(picture_url_result, "image_url") else str(picture_url_result)
        )

        clip_info = {
            "Timestamp": incident.start_timestamp,
            "Stream": incident.sensor_id,
            "snapshot_url": snapshot_url,
            "video_url": video_url,
        }
        alert_details = {
            "Incident ID": incident.incident_id,
            "Alert Category": incident.metadata.get("category", "Unknown Alert"),
        }
        info = incident.metadata.get("info", {})
        verification_code = info.get("verificationResponseCode", info.get("verification_response_code"))
        # Only use verification fields if verification code is 200
        if verification_code == "200" or verification_code == 200:
            verification_status = info.get("verificationResponseStatus", info.get("verification_response_status"))
            reasoning = info.get("reasoning")
            verdict = info.get("verdict")

            if verification_status:
                alert_details["Verification Status"] = verification_status
            if reasoning:
                alert_details["Reasoning"] = reasoning
            if verdict:
                alert_details["Verdict"] = verdict

        # Build the JSON structure
        incident_json = {
            "Alert Title": f"Alert Triggered {incident_number}",
            "Clip Information": clip_info,
            "Alert Details": alert_details,
        }

        return incident_json

    except Exception as e:
        logger.error(f"Error formatting incident {incident.incident_id}: {e}")
        # Return a basic error structure
        return {
            "Alert Title": f"Alert Triggered {incident_number}",
            "Clip Information": {
                "Timestamp": incident.start_timestamp,
                "Stream": incident.sensor_id,
                "snapshot_url": "Error",
                "video_url": "Error",
            },
            "Alert Details": {
                "Incident ID": incident.incident_id,
                "Alert Triggered": "Error",
                "Validation": False,
                "Alert Description": f"Failed to retrieve full details - {e!s}",
            },
        }


async def _generate_incidents_chart(
    incidents: list[IncidentData], chart_generator_tool: Any, chart_base_url: str | None
) -> str:
    """Generate a chart visualization of incident categories distribution.

    Args:
        incidents: List of incident data
        chart_generator_tool: The chart generator tool (LangChain wrapped)
        chart_base_url: Base URL for chart images

    Returns:
        HTML string with img tag for the chart
    """
    # Get category from metadata, default to "Unknown" if missing
    incident_categories = [inc.metadata.get("category") or "Unknown" for inc in incidents]
    category_counts = Counter(incident_categories)

    # Filter out empty string keys (keep "Unknown")
    valid_categories = {k: v for k, v in category_counts.items() if k and str(k).strip()}
    if not valid_categories:
        valid_categories = {"Unknown": len(incidents)}

    chart_input = {
        "charts_data": [
            {
                "sizes": list(valid_categories.values()),
                "labels": list(valid_categories.keys()),
                "title": "Incidents by Type",
                "chart_file_format": "png",
            }
        ],
        "output_dir": "incident_charts",
        "file_prefix": "incidents_",
    }

    result = await chart_generator_tool.ainvoke(input=chart_input)

    # The result is a list of ChartGenExecOutput - manually convert to HTML
    if isinstance(result, list):
        output_html = ""
        for chart in result:
            if chart.success and chart.object_store_key and chart_base_url:
                output_html += f'<img src="{chart_base_url}{chart.object_store_key}" alt="Incident Chart" />'
        return output_html
    else:
        return str(result)


def _determine_optimal_bin_size(incidents: list[IncidentData]) -> str | None:
    """Automatically determine the optimal bin size based on incident count, timestamp range, and density.

    Strategy:
    - Aims for 20-50 bins for optimal visualization
    - Considers both time range and incident density
    - Adjusts based on total incident count to prevent over-binning

    Args:
        incidents: List of incident data

    Returns:
        Optimal bin size string ('1min', '10min', '1hr', '1day') or None if no valid timestamps
    """
    if not incidents:
        return None

    timestamps = []
    for inc in incidents:
        try:
            timestamp = datetime.fromisoformat(inc.start_timestamp.replace("Z", "+00:00"))
            timestamps.append(timestamp)
        except Exception as e:
            logger.warning(f"Failed to parse timestamp {inc.start_timestamp}: {e}")
            continue

    if len(timestamps) < 2:
        return "10min"

    min_time = min(timestamps)
    max_time = max(timestamps)
    time_range = max_time - min_time
    total_seconds = time_range.total_seconds()
    total_minutes = total_seconds / 60
    total_hours = total_minutes / 60
    total_days = total_hours / 24

    # Target 30 bins for optimal visualization (acceptable range: 25-35)
    target_bins = 30
    min_bins = 25
    max_bins = 35

    # Calculate what each bin size would give us
    bins_1min = total_minutes
    bins_10min = total_minutes / 10
    bins_1hr = total_hours
    bins_1day = total_days

    logger.debug(f"Time range: {total_days:.2f} days, {total_hours:.2f} hours, {total_minutes:.2f} minutes")
    logger.debug(
        f"Potential bins - 1min: {bins_1min:.0f}, 10min: {bins_10min:.0f}, 1hr: {bins_1hr:.0f}, 1day: {bins_1day:.0f}"
    )

    # Choose bin size closest to target_bins, within acceptable range
    bin_options = [
        ("1day", bins_1day),
        ("1hr", bins_1hr),
        ("10min", bins_10min),
        ("1min", bins_1min),
    ]

    # Filter options that fall within acceptable range [min_bins, max_bins]
    valid_options = [(size, count) for size, count in bin_options if min_bins <= count <= max_bins]

    if valid_options:
        # Choose the option closest to target_bins within the acceptable range
        best_option = min(valid_options, key=lambda x: abs(x[1] - target_bins))
        logger.debug(f"Selected bin size: {best_option[0]} ({best_option[1]:.0f} bins)")
        return best_option[0]

    # If no options fall within range, choose the closest option to the range
    # Prefer options just below min_bins over those above max_bins
    below_min = [(size, count) for size, count in bin_options if count < min_bins and count > 0]
    above_max = [(size, count) for size, count in bin_options if count > max_bins]

    if below_min:
        # Choose the one with most bins (closest to min_bins)
        best_option = max(below_min, key=lambda x: x[1])
    elif above_max:
        # Choose the one with fewest bins (closest to max_bins)
        best_option = min(above_max, key=lambda x: x[1])
    else:
        # Fallback to any non-zero option
        best_option = max(bin_options, key=lambda x: x[1] if x[1] > 0 else 0)

    logger.debug(f"Selected bin size: {best_option[0]} ({best_option[1]:.0f} bins) - outside target range")
    return best_option[0]


async def _generate_time_series_chart(
    incidents: list[IncidentData],
    chart_generator_tool: Any,
    chart_base_url: str | None,
    bin_size: str,
) -> str:
    """Generate a time-series bar chart showing incident count over time.

    Args:
        incidents: List of incident data
        chart_generator_tool: The chart generator tool (LangChain wrapped)
        chart_base_url: Base URL for chart images
        bin_size: Time bin size - '1min', '10min', '1hr', or '1day'

    Returns:
        HTML string with img tag for the chart
    """
    # Map bin_size to timedelta
    bin_deltas = {
        "1min": timedelta(minutes=1),
        "10min": timedelta(minutes=10),
        "1hr": timedelta(hours=1),
        "1day": timedelta(days=1),
    }

    if bin_size not in bin_deltas:
        logger.error(f"Invalid bin_size: {bin_size}. Must be one of {list(bin_deltas.keys())}")
        return ""

    # Parse timestamps and bin them
    binned_counts: defaultdict[datetime, int] = defaultdict(int)
    for inc in incidents:
        try:
            timestamp = datetime.fromisoformat(inc.start_timestamp.replace("Z", "+00:00"))
            # Round down to the nearest bin
            bin_start = timestamp.replace(second=0, microsecond=0)
            if bin_size == "1min":
                pass  # Already at minute precision
            elif bin_size == "10min":
                bin_start = bin_start.replace(minute=(bin_start.minute // 10) * 10)
            elif bin_size == "1hr":
                bin_start = bin_start.replace(minute=0)
            elif bin_size == "1day":
                bin_start = bin_start.replace(hour=0, minute=0)

            binned_counts[bin_start] += 1
        except Exception as e:
            logger.warning(f"Failed to parse timestamp {inc.start_timestamp}: {e}")
            continue

    if not binned_counts:
        logger.warning("No valid timestamps found for time-series chart")
        return ""

    # Sort bins chronologically
    sorted_bins = sorted(binned_counts.keys())

    # Format labels based on bin size
    if bin_size == "1day":
        labels = [bin_time.strftime("%Y-%m-%d") for bin_time in sorted_bins]
    elif bin_size in ["1hr", "10min"]:
        labels = [bin_time.strftime("%m-%d %H:%M") for bin_time in sorted_bins]
    else:  # 1min
        labels = [bin_time.strftime("%H:%M:%S") for bin_time in sorted_bins]

    counts = [binned_counts[bin_time] for bin_time in sorted_bins]

    # Create bar chart input
    chart_input = {
        "charts_data": [
            {
                "x_categories": labels,
                "series": {"Incidents": counts},
                "x_label": "Time",
                "y_label": "Incident Count",
                "title": f"Incidents Over Time ({bin_size} bins)",
                "chart_file_format": "png",
            }
        ],
        "output_dir": "incident_charts",
        "file_prefix": "incidents_timeseries_",
    }

    result = await chart_generator_tool.ainvoke(input=chart_input)

    # Convert result to HTML
    if isinstance(result, list):
        output_html = ""
        for chart in result:
            if chart.success and chart.object_store_key and chart_base_url:
                output_html += f'<img src="{chart_base_url}{chart.object_store_key}" alt="Time Series Chart" />'
        return output_html
    else:
        return str(result)


async def _multi_incident_formatter_impl(
    formatter_input: MultiIncidentFormatterInput,
    video_url_tool: Any,
    picture_url_tool: Any,
    incidents_tool: Any,
    chart_generator_tool: Any | None = None,
    generate_chart: bool = False,
    chart_base_url: str | None = None,
    display_limit: int = 20,
) -> MultiIncidentFormatterOutput:
    """
    Fetch and format multiple incidents in parallel.

    This tool fetches incidents from a sensor and formats each one by:
    1. Fetching ALL incidents for chart data
    2. Displaying only top 20 incidents with video/snapshot URLs
    3. Generating charts based on ALL incidents for accurate visualization
    4. Using improved bin size calculation based on total incident count

    Input:
        source: Source to fetch incidents from (sensor ID, place)
        source_type: Type of the source
        start_time: Optional start time in ISO format
        end_time: Optional end time in ISO format
        max_result_size: Maximum number of incidents to fetch (default: 10000, max: 10000)

    Returns:
        MultiIncidentFormatterOutput: Top N formatted incidents as JSON string with <incidents> tags and chart based on ALL incidents
    """
    try:
        incidents = await _fetch_incidents(formatter_input, incidents_tool)

        if not incidents:
            empty_output = '\n<incidents>\n{\n  "incidents": []\n}\n</incidents>'
            return MultiIncidentFormatterOutput(
                formatted_incidents=empty_output,
                total_incidents=0,
                chart_html=None,
            )

        logger.info(f"Fetched {len(incidents)} total incidents. Will display top {display_limit} in UI.")

        # Step 2: Take only top N incidents for formatting (display_limit from input)
        incidents_to_format = incidents[:display_limit]

        logger.info(f"Formatting {len(incidents_to_format)} incidents in parallel")

        # Step 3: Format selected incidents in parallel
        tasks = [
            _format_single_incident(
                incident,
                video_url_tool,
                picture_url_tool,
                incident_number=i + 1,
            )
            for i, incident in enumerate(incidents_to_format)
        ]
        formatted_results = await asyncio.gather(*tasks)

        # Step 4: Build the final JSON structure with <incidents> tags
        incidents_json = {
            "incidents": formatted_results,
            "total_incidents": len(incidents),
            "displayed_incidents": len(formatted_results),
        }

        json_string = json.dumps(incidents_json, indent=2)
        formatted_output = f"\n<incidents>\n{json_string}\n</incidents>"

        # Step 5: Generate charts based on ALL fetched incidents (not just displayed 20)
        chart_html = None
        all_charts_html = []

        # Generate pie chart if generate_chart flag is True
        if generate_chart and chart_generator_tool:
            try:
                pie_chart = await _generate_incidents_chart(incidents, chart_generator_tool, chart_base_url)
                if pie_chart:
                    all_charts_html.append(pie_chart)
                logger.info(f"Successfully generated incidents pie chart from {len(incidents)} incidents")
            except Exception as e:
                logger.error(f"Failed to generate pie chart: {e}", exc_info=True)

        # Generate time-series bar chart with improved bin size calculation
        if generate_chart and chart_generator_tool:
            try:
                # Determine optimal bin size based on ALL fetched incidents
                bin_size = _determine_optimal_bin_size(incidents)
                logger.info(f"Auto-determined optimal bin size: {bin_size} based on {len(incidents)} incidents")

                if bin_size:
                    time_series_chart = await _generate_time_series_chart(
                        incidents, chart_generator_tool, chart_base_url, bin_size
                    )
                    if time_series_chart:
                        all_charts_html.append(time_series_chart)
                    logger.info(f"Successfully generated time-series chart with bin size {bin_size}")
            except Exception as e:
                logger.error(f"Failed to generate time-series chart: {e}", exc_info=True)

        if all_charts_html:
            chart_html = "\n".join(all_charts_html)

        return MultiIncidentFormatterOutput(
            formatted_incidents=formatted_output,
            total_incidents=len(incidents),
            chart_html=chart_html,
        )

    except Exception as e:
        logger.error(f"Error in multi-incident formatter: {e}")
        raise


@register_function(config_type=MultiIncidentFormatterConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def multi_incident_formatter(
    config: MultiIncidentFormatterConfig, builder: Builder
) -> AsyncGenerator[FunctionInfo]:
    video_url_tool = await builder.get_tool(config.video_url_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    picture_url_tool = await builder.get_tool(config.picture_url_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    incidents_tool = await builder.get_tool(config.incidents_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    chart_generator_tool = None
    if config.chart_generator_tool:
        chart_generator_tool = await builder.get_tool(
            config.chart_generator_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN
        )

    async def _multi_incident_formatter(formatter_input: MultiIncidentFormatterInput) -> MultiIncidentFormatterOutput:
        return await _multi_incident_formatter_impl(
            formatter_input,
            video_url_tool,
            picture_url_tool,
            incidents_tool,
            chart_generator_tool,
            config.generate_chart,
            config.chart_base_url,
            config.display_limit,
        )

    yield FunctionInfo.create(
        single_fn=_multi_incident_formatter,
        description=_multi_incident_formatter_impl.__doc__,
        input_schema=MultiIncidentFormatterInput,
        single_output_schema=MultiIncidentFormatterOutput,
    )
