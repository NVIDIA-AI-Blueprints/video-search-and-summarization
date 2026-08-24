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

"""
Multi-Incident Report Agent - Deterministic tool-calling workflow for multiple incidents.

This agent fetches and formats multiple incidents with URLs, charts, and visualizations.
"""

from collections.abc import AsyncGenerator
import logging
import time
from typing import Literal

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from vss_agents.agents.data_models import AgentMessageChunk
from vss_agents.agents.data_models import AgentMessageChunkType
from vss_agents.agents.data_models import AgentOutput

logger = logging.getLogger(__name__)

_ARTIFACT_DISPLAY_NOTE = (
    "NOTE: The formatted incident list is already being displayed to the user automatically. "
    "You may use the incident details above (IDs, timestamps, categories) to continue your plan. "
    "In your FINAL answer to the user, do not repeat the full incident list since it is already shown."
)


class MultiReportAgentInput(BaseModel):
    """
    Input for the Multi-Incident Report Agent.

    This agent handles fetching and formatting multiple incidents within a specified time range.
    """

    source: str = Field(..., description="Source to fetch incidents from (e.g., sensor ID, place)")
    source_type: Literal["sensor", "place"] = Field(..., description="Type of the source (must be 'sensor', 'place')")
    start_time: str | None = Field(
        default=None,
        description="Optional start time in ISO format (e.g., '2025-09-22T14:00:00.000Z'). If omitted, fetches most recent incidents.",
    )
    end_time: str | None = Field(
        default=None,
        description="Optional end time in ISO format (e.g., '2025-09-22T15:00:00.000Z'). If omitted, fetches most recent incidents.",
    )
    # Optional parameter - if not provided, falls back to config.max_incidents
    max_result_size: int | None = Field(
        default=None,
        description="Maximum number of incidents to return. If not specified, uses max_incidents from config.",
        gt=0,
    )


class MultiReportAgentConfig(FunctionBaseConfig, name="multi_report_agent"):
    """Config for the multi-incident report agent."""

    # Tool references
    multi_incident_tool: FunctionRef = Field(
        description="Tool to format multiple incidents with URLs/charts (e.g., multi_incident_formatter)"
    )

    # Configuration defaults
    max_incidents: int = Field(
        default=10000,
        ge=1,
        le=10000,
        description="Maximum number of incidents to fetch. "
        "Used when max_result_size is not specified in the request. "
        "UI will display just the top incidents, but charts will show all fetched incidents.",
    )


@register_function(config_type=MultiReportAgentConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def multi_report_agent(config: MultiReportAgentConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    Multi-incident report agent.

    Executes deterministic tool sequence:
    - multi_incident_formatter → fetches incidents, adds URLs, formats, generates charts

    Args:
        config: Configuration with tool references and max_incidents default
        builder: NAT builder for tool resolution

    Yields:
        FunctionInfo for the multi report agent
    """
    # Get tool references
    multi_incident_tool = await builder.get_tool(config.multi_incident_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    logger.info("Multi Report Agent tools initialized successfully")

    async def _execute_multi_report(
        source: str,
        source_type: str,
        start_time: str | None = None,
        end_time: str | None = None,
        max_result_size: int | None = None,
    ) -> AsyncGenerator[AgentMessageChunk]:
        """
        Execute multi-incident report generation.

        Args:
            source: Source to fetch incidents from (sensor ID, place)
            source_type: Type of the source
            start_time: Optional start time in ISO format
            end_time: Optional end time in ISO format
            max_result_size: Maximum number of incidents (if None, uses config.max_incidents)

        Yields:
            AgentMessageChunk objects for tool calls and final result
        """
        logger.info("Generating multi-incident report")
        start_time_exec = time.time()

        try:
            # Use max_result_size from input if provided, otherwise fallback to config.max_incidents
            effective_max_size = max_result_size if max_result_size is not None else config.max_incidents

            logger.info(
                f"Calling multi_incident_formatter for {source_type} {source} (max {effective_max_size} results)"
            )

            # Yield tool call chunk
            tool_args = {
                "source": source,
                "source_type": source_type,
                "start_time": start_time,
                "end_time": end_time,
                "max_result_size": effective_max_size,
            }
            yield AgentMessageChunk(
                type=AgentMessageChunkType.TOOL_CALL, content=f"Tool: multi_incident_formatter\nArgs: {tool_args}"
            )

            # Call multi_incident_formatter with translated source_type
            formatter_result = await multi_incident_tool.ainvoke(tool_args)

            logger.debug(f"multi_incident_formatter returned: {type(formatter_result)}")

            # Extract data from formatter result
            formatted_incidents = ""
            incident_count = 0
            side_effects = {}

            if hasattr(formatter_result, "formatted_incidents"):
                formatted_incidents = formatter_result.formatted_incidents
                incident_count = formatter_result.total_incidents
                if formatter_result.chart_html:
                    side_effects["chart_html"] = formatter_result.chart_html
                if formatted_incidents:
                    side_effects["formatted_incidents"] = formatted_incidents
            elif isinstance(formatter_result, dict):
                formatted_incidents = formatter_result.get("formatted_incidents", "")
                incident_count = formatter_result.get("total_incidents", 0)
                if "chart_html" in formatter_result:
                    side_effects["chart_html"] = formatter_result["chart_html"]
                if formatted_incidents:
                    side_effects["formatted_incidents"] = formatted_incidents
            else:
                formatted_incidents = str(formatter_result)

            logger.info("Multi-incident report generated successfully")

            if side_effects:
                side_effects["artifact_note"] = _ARTIFACT_DISPLAY_NOTE

            execution_time_ms = int((time.time() - start_time_exec) * 1000)
            agent_output = AgentOutput(
                messages=[
                    f"Found {incident_count} incident{'s' if incident_count != 1 else ''} for {source_type} {source}",
                    formatted_incidents,
                ],
                side_effects=side_effects,
                status="success",
                metadata={
                    "incident_count": incident_count,
                    "source": source,
                    "source_type": source_type,
                    "report_type": "multi_incident",
                    "generation_time_ms": execution_time_ms,
                    "max_result_size": effective_max_size,
                },
            )
            yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=agent_output.model_dump_json())

        except (ValueError, KeyError, AttributeError) as e:
            logger.exception("Failed to execute multi-incident report")
            execution_time_ms = int((time.time() - start_time_exec) * 1000)
            error_output = AgentOutput(
                messages=[f"Error generating multi-incident report: {e!s}"],
                status="error",
                error_message=f"Failed to generate multi-incident report: {e!s}",
                metadata={
                    "generation_time_ms": execution_time_ms,
                    "report_type": "multi_incident",
                },
            )
            yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=error_output.model_dump_json())
        except Exception:
            logger.exception("Unexpected error in multi-incident report execution")
            execution_time_ms = int((time.time() - start_time_exec) * 1000)
            error_output = AgentOutput(
                messages=["Unexpected error generating multi-incident report"],
                status="error",
                error_message="Unexpected error in multi-incident report execution",
                metadata={
                    "generation_time_ms": execution_time_ms,
                    "report_type": "multi_incident",
                },
            )
            yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=error_output.model_dump_json())

    # Register the function
    yield FunctionInfo.create(
        stream_fn=_execute_multi_report,
        description=(
            "Generate multi-incident reports showing formatted lists of multiple incidents "
            "with URLs, charts, and visualizations. "
            "Streams reasoning steps showing tool calls to multi_incident_formatter."
        ),
        input_schema=MultiReportAgentInput,
        stream_output_schema=AgentMessageChunk,
    )
