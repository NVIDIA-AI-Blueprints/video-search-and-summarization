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
Single Incident Report Agent - Deterministic tool-calling workflow.

This agent generates detailed reports for single incidents.
No LLM is used for decision-making; it follows a predetermined tool sequence:
  1. Get most recent incident from video analytics
  2. Generate detailed report with video analysis

For multiple incidents, use multi_report_agent instead.
For long videos, use lvs_agent instead.
"""

from collections.abc import AsyncGenerator
from datetime import datetime
import json
import logging
import time
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
from pydantic import model_validator

from vss_agents.agents.data_models import AgentMessageChunk
from vss_agents.agents.data_models import AgentMessageChunkType
from vss_agents.agents.data_models import AgentOutput

logger = logging.getLogger(__name__)

_ARTIFACT_DISPLAY_NOTE = (
    "Do not include or offer to provide report download links in your final response "
    "since they are already displayed to the user automatically."
)


def _append_artifact_display_note(side_effects: dict[str, Any]) -> None:
    """Add a note to top agent when report artifacts are included in the subagent side effects."""
    if side_effects:
        side_effects["artifact_note"] = _ARTIFACT_DISPLAY_NOTE


# ========== REPORT AGENT MODELS ==========


class ReportAgentInput(BaseModel):
    """
    Input for the deterministic Report Agent (Single Incident).

    This agent handles detailed single incident analysis with Video Analytics MCP.
    For multiple incidents, use multi_report_agent instead.
    """

    # Time range parameters
    start_time: datetime | None = Field(default=None, description="Start time for incident search.")

    end_time: datetime | None = Field(default=None, description="End time for incident search.")

    # Incident/source identifiers
    incident_id: str | None = Field(
        default=None,
        description="Specific incident ID. If provided, other search params are ignored.",
    )

    source: str | None = Field(
        default=None,
        description="Source to filter incidents (sensor ID or place/city name). Also accepts 'sensor_id' as an alias.",
    )

    source_type: Literal["sensor", "place"] | None = Field(
        default=None, description="Type of the source. Must be 'sensor' or 'place'. Required if source is provided."
    )

    vlm_reasoning: bool | None = Field(
        default=None,
        description="Enable VLM reasoning mode for video analysis. If None, uses video_understanding config default.",
    )

    llm_reasoning: bool | None = Field(
        default=None,
        description="Enable LLM reasoning mode for report generation. If None, uses workflow config default.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_sensor_id(cls, data: Any) -> Any:
        """Accept sensor_id as shorthand for source + source_type='sensor'."""
        if isinstance(data, dict) and "sensor_id" in data:
            if not data.get("source"):
                data["source"] = data["sensor_id"]
                if not data.get("source_type"):
                    data["source_type"] = "sensor"
            del data["sensor_id"]
        return data


class VideoReportAgentInput(BaseModel):
    """
    Input for the Video(uploaded) Report Agent (Mode 3).

    This mode works without Video Analytics MCP - directly analyzes uploaded videos from VST.
    No incident database required. Always analyzes the full video.
    Supports parallel processing of multiple videos when using LVS (Long Video Summarization).
    """

    sensor_id: str | list[str] = Field(
        ...,
        description="VST sensor ID(s) for video retrieval. Can be a single sensor_id string or a list of sensor_ids for parallel processing with LVS.",
    )
    user_query: str = Field(
        "Generate a detailed report of the video.",
        description="The user's question or analysis request for this video",
    )
    vlm_reasoning: bool | None = Field(
        default=None,
        description="Enable VLM reasoning mode for video analysis. If None, uses video_understanding config default.",
    )


class ReportAgentConfig(FunctionBaseConfig, name="report_agent"):
    """Config for the single incident report agent."""

    # Tool references - Video Analytics MCP tools are optional (if None, runs in Mode 3/Video(uploaded) Report mode)
    get_incidents_tool: FunctionRef | None = Field(
        default=None,
        description="Tool to get incidents from video analytics (e.g., video_analytics_mcp.video_analytics.get_incidents). If None, runs in Mode 3 (Video(uploaded) Report mode)",
    )
    get_incident_tool: FunctionRef | None = Field(
        default=None,
        description="Tool to get a single incident by ID (e.g., video_analytics_mcp.video_analytics.get_incident). If None, runs in Mode 3 (Video(uploaded) Report mode)",
    )
    template_report_tool: FunctionRef | None = Field(
        default=None,
        description="Tool to generate detailed single incident report (e.g., template_report_gen). Used for Video Analytics MCP mode.",
    )
    video_report_tool: FunctionRef | None = Field(
        default=None,
        description="Tool to generate Video(uploaded) video analysis reports (e.g., video_report_gen). Used for Video(uploaded) Report mode.",
    )


@register_function(config_type=ReportAgentConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def report_agent(config: ReportAgentConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    Deterministic report agent with automatic mode detection.

    Modes:
    - Video Analytics MCP mode: Incident-based reports with Elasticsearch (when Video Analytics MCP tools configured)
        - SINGLE_INCIDENT: get_incidents(max=1) → template_report_gen
        - MULTI_INCIDENT: get_incidents(max=N) → template_report_gen
    - Video(uploaded) Report mode: Direct video analysis without Video Analytics MCP (when Video Analytics MCP tools not configured)
    """

    # === MODE DETECTION ===
    # Check if Video Analytics MCP tools are configured
    va_mcp_enabled = config.get_incidents_tool is not None and config.get_incident_tool is not None

    if va_mcp_enabled:
        logger.info("Report Agent running in Mode 1 (Video Analytics MCP enabled)")
    else:
        logger.info("Report Agent running in Mode 3 (Video(uploaded) Report mode - no Video Analytics MCP)")

    # === LOAD TOOLS CONDITIONALLY ===
    get_incidents_tool = None
    get_incident_tool = None
    template_report_tool = None
    video_report_tool = None

    if va_mcp_enabled:
        logger.info("Loading Video Analytics MCP tools")
        if not config.get_incidents_tool:
            raise ValueError("get_incidents_tool must be configured for Video Analytics MCP mode")
        if not config.get_incident_tool:
            raise ValueError("get_incident_tool must be configured for Video Analytics MCP mode")
        if not config.template_report_tool:
            raise ValueError("template_report_tool must be configured for Video Analytics MCP mode")

        get_incidents_tool = await builder.get_tool(config.get_incidents_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        get_incident_tool = await builder.get_tool(config.get_incident_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        template_report_tool = await builder.get_tool(
            config.template_report_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN
        )

        logger.info("Video Analytics MCP tools loaded successfully")
    else:
        logger.info("Loading Video(uploaded) Report tools")
        if not config.video_report_tool:
            raise ValueError(
                "video_report_tool must be configured for Video(uploaded) Report mode. Otherwise Video Analytics MCP tools must be configured."
            )
        video_report_tool = await builder.get_tool(config.video_report_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        logger.info("Video(uploaded) Report tools loaded successfully")

    logger.info(
        f"Report Agent initialized ({'Video Analytics MCP mode' if va_mcp_enabled else 'Video(uploaded) Report mode'})"
    )

    # Define mode-specific execution functions
    if va_mcp_enabled:

        async def _execute_report_va_mcp(
            source: str | None = None,
            source_type: Literal["sensor", "place"] | None = None,
            start_time: datetime | None = None,
            end_time: datetime | None = None,
            incident_id: str | None = None,
            vlm_reasoning: bool | None = None,
            llm_reasoning: bool | None = None,
        ) -> AsyncGenerator[AgentMessageChunk]:
            """
            Execute single incident report generation.

            Args:
                source: Source to filter incidents (sensor ID or place/city name)
                source_type: Type of the source ('sensor' or 'place')
                start_time: Start time for incident search
                end_time: End time for incident search
                incident_id: Specific incident ID

            Yields:
            AgentMessageChunk objects for tool calls and final result
            """
            logger.info("Executing incident-based single incident report")
            execution_start_time = time.time()

            # Construct Mode 1 input
            report_input = ReportAgentInput(
                source=source,
                source_type=source_type,
                start_time=start_time,
                end_time=end_time,
                incident_id=incident_id,
                vlm_reasoning=vlm_reasoning,
                llm_reasoning=llm_reasoning,
            )

            try:
                async for chunk in _handle_single_incident(report_input):
                    yield chunk
            except (ValueError, KeyError, AttributeError, json.JSONDecodeError) as e:
                logger.exception("Report Agent: Failed to execute incident report")
                execution_time_ms = int((time.time() - execution_start_time) * 1000)
                error_output = AgentOutput(
                    messages=[f"Report Agent: Error generating incident report: {e!s}"],
                    status="error",
                    error_message=f"Report Agent: Failed to generate incident report: {e!s}",
                    metadata={
                        "generation_time_ms": execution_time_ms,
                        "report_type": "single_incident",
                    },
                )
                yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=error_output.model_dump_json())
            except Exception:
                logger.exception("Report Agent: Unexpected error in incident report execution")
                execution_time_ms = int((time.time() - execution_start_time) * 1000)
                error_output = AgentOutput(
                    messages=["Report Agent: Unexpected error generating incident report"],
                    status="error",
                    error_message="Report Agent: Unexpected error in incident report execution",
                    metadata={
                        "generation_time_ms": execution_time_ms,
                        "report_type": "single_incident",
                    },
                )
                yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=error_output.model_dump_json())

    else:  # Video(uploaded) Report mode (no Video Analytics MCP)

        async def _execute_report_video(
            sensor_id: str | list[str],
            user_query: str,
            vlm_reasoning: bool | None = None,
        ) -> AsyncGenerator[AgentMessageChunk]:
            """
            Execute Video(uploaded) Report generation (video-based, no Video Analytics MCP).

            Args:
                sensor_id: VST sensor ID(s) (filename(s) of uploaded video(s)). Can be a single string or list for parallel processing.
                user_query: The user's question or analysis request

            Returns:
                AgentMessageChunk objects for tool calls and final result
            """
            logger.info("Executing Video(uploaded) Report (video-based)")
            execution_start_time = time.time()

            # Construct Mode 3 input
            video_report_input = VideoReportAgentInput(
                sensor_id=sensor_id,
                user_query=user_query,
                vlm_reasoning=vlm_reasoning,
            )

            try:
                async for chunk in _video_report_agent(video_report_input):
                    yield chunk
            except (ValueError, KeyError, AttributeError) as e:
                logger.exception("Report Agent: Failed to execute direct video analysis report")
                execution_time_ms = int((time.time() - execution_start_time) * 1000)

                # Check if this is a websocket connection error
                error_str = str(e)
                if (
                    "No human prompt callback was registered" in error_str
                    or "Unable to handle requested prompt" in error_str
                ):
                    user_message = (
                        "Could not start human in the loop workflow over websocket. "
                        "Please check that websocket connection is enabled in the UI and that the IP of agent "
                        "is set correctly in the settings panel from the left lower side."
                    )
                    error_message = f"Report Agent: Websocket connection error - {user_message}"
                else:
                    user_message = f"Report Agent: Error generating video analysis report: {error_str}"
                    error_message = f"Report Agent: Failed to generate video analysis report: {error_str}"

                error_output = AgentOutput(
                    messages=[user_message],
                    status="error",
                    error_message=error_message,
                    metadata={
                        "generation_time_ms": execution_time_ms,
                        "report_type": "video_report",
                        "mode": "video(uploaded) report",
                    },
                )
                yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=error_output.model_dump_json())
            except Exception:
                logger.exception("Report Agent: Unexpected error in direct video analysis report execution")
                execution_time_ms = int((time.time() - execution_start_time) * 1000)
                error_output = AgentOutput(
                    messages=["Report Agent: Unexpected error generating video analysis report"],
                    status="error",
                    error_message="Report Agent: Unexpected error in video analysis report execution",
                    metadata={
                        "generation_time_ms": execution_time_ms,
                        "report_type": "video_report",
                        "mode": "video(uploaded) report",
                    },
                )
                yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=error_output.model_dump_json())

    async def _handle_single_incident(report_input: ReportAgentInput) -> AsyncGenerator[AgentMessageChunk]:
        """
        Mode 1: Get incident and generate detailed report.

        Tool sequence:
        1. get_incidents(max_count=1)/get_incident → get most recent incident or get a specific incident by ID
        2. template_report_gen(incident_id) → generate detailed report
        """
        # These tools are guaranteed to be set when va_mcp_enabled is True
        assert get_incident_tool is not None
        assert get_incidents_tool is not None
        assert template_report_tool is not None

        logger.info("Mode 1: Single incident report")
        incident = None

        # If incident_id is provided, get specific incident
        if report_input.incident_id:
            logger.info(f"Getting incident by ID: {report_input.incident_id}")

            tool_call_args = {"id": report_input.incident_id, "includes": ["objectIds", "info"]}
            yield AgentMessageChunk(
                type=AgentMessageChunkType.TOOL_CALL, content=f"Tool: get_incident\nArgs: {tool_call_args}"
            )
            incident_result = await get_incident_tool.ainvoke(tool_call_args)
            if isinstance(incident_result, str):
                try:
                    incident = json.loads(incident_result)
                except json.JSONDecodeError:
                    logger.exception("Report Agent: Failed to parse get_incident response as JSON: %s", incident_result)
                    error_output = AgentOutput(
                        messages=[
                            f"Report Agent: Unable to parse incident data for ID '{report_input.incident_id}'. The Video Analytics service returned an invalid response."
                        ],
                        status="error",
                        error_message="Report Agent: Failed to parse Video Analytics MCP tool response",
                    )
                    yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=error_output.model_dump_json())
                    return
            else:
                incident = incident_result

            if not incident:
                no_incident_output = AgentOutput(
                    messages=[f"No incident found with ID '{report_input.incident_id}'."],
                    status="success",
                    metadata={"incident_id": report_input.incident_id},
                )
                yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=no_incident_output.model_dump_json())
                return
        else:
            get_incidents_params = {
                "max_count": 1,
                "includes": ["objectIds", "info"],
                "source": report_input.source,
                "source_type": report_input.source_type,
                "start_time": report_input.start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                if report_input.start_time
                else None,
                "end_time": report_input.end_time.strftime("%Y-%m-%dT%H:%M:%S.000Z") if report_input.end_time else None,
            }
            logger.info(f"Getting incidents with params: {get_incidents_params}")

            yield AgentMessageChunk(
                type=AgentMessageChunkType.TOOL_CALL, content=f"Tool: get_incidents\nArgs: {get_incidents_params}"
            )
            incidents_result = await get_incidents_tool.ainvoke(get_incidents_params)
            if isinstance(incidents_result, str):
                try:
                    parsed_result = json.loads(incidents_result)
                    incidents = parsed_result.get("incidents", [])
                except json.JSONDecodeError:
                    logger.exception(
                        "Report Agent: Failed to parse get_incidents response as JSON: %s", incidents_result
                    )
                    error_output = AgentOutput(
                        messages=[
                            "Report Agent: Unable to parse incidents data. The Video Analytics service returned an invalid response."
                        ],
                        status="error",
                        error_message="Report Agent: Failed to parse Video Analytics MCP tool response",
                    )
                    yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=error_output.model_dump_json())
                    return
            else:
                # Assume it's already parsed (tuple format)
                incidents, _ = incidents_result
            if not incidents:
                no_incidents_output = AgentOutput(
                    messages=["No incidents found with the specified criteria."],
                    status="success",
                    metadata={"incident_count": 0},
                )
                yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=no_incidents_output.model_dump_json())
                return

            incident = incidents[0]

        # Handle both "Id" and "id" field names
        incident_id = incident.get("Id") or incident.get("id") or "unknown"
        logger.info(f"Found incident: {incident_id}")

        # Step 2: Generate detailed report
        logger.info("Generating detailed report")

        report_tool_args = {
            "incident_id": incident_id,
            "alert_sensor_id": incident.get("sensorId"),
            "alert_from_timestamp": incident.get("timestamp"),
            "alert_to_timestamp": incident.get("end"),
            "alert_metadata": incident,  # Pass the entire incident object as metadata
            "vlm_reasoning": report_input.vlm_reasoning,  # Pass VLM reasoning flag
            "llm_reasoning": report_input.llm_reasoning,  # Pass LLM reasoning flag
        }

        yield AgentMessageChunk(
            type=AgentMessageChunkType.TOOL_CALL,
            content=f"Tool: template_report_gen\nArgs: {{'incident_id': '{incident_id}'}}",
        )
        report_result = await template_report_tool.ainvoke(report_tool_args)
        logger.info("Single incident report generated successfully")

        side_effects = {}
        if hasattr(report_result, "http_url") or hasattr(report_result, "pdf_url"):
            downloads = ["**Report Downloads:**"]
            if hasattr(report_result, "http_url") and report_result.http_url:
                downloads.append(f"- [Markdown Report]({report_result.http_url})")
            if hasattr(report_result, "pdf_url") and report_result.pdf_url:
                downloads.append(f"- [PDF Report]({report_result.pdf_url})")
            side_effects["report_downloads"] = "\n".join(downloads) + "\n"
        if hasattr(report_result, "image_url") or hasattr(report_result, "video_url"):
            media = ["**Media:**"]
            if hasattr(report_result, "image_url") and report_result.image_url:
                media.append(f"- ![Incident Snapshot]({report_result.image_url})")
            if hasattr(report_result, "video_url") and report_result.video_url:
                media.append(f"- [Incident Video]({report_result.video_url})")
            side_effects["media"] = "\n".join(media) + "\n"
        _append_artifact_display_note(side_effects)

        agent_output = AgentOutput(
            messages=[f"Report generated successfully for incident {incident_id}"],
            side_effects=side_effects,
            status="success",
            metadata={
                "incident_count": 1,
                "incident_id": incident_id,
                "sensor_id": incident.get("sensorId"),
                "report_type": "single_incident",
            },
        )
        yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=agent_output.model_dump_json())

    async def _video_report_agent(video_report_input: VideoReportAgentInput) -> AsyncGenerator[AgentMessageChunk]:
        """
        Video(uploaded) Report mode: Direct video analysis without Video Analytics MCP.

        This mode works with uploaded videos from VST.
        Delegates to video_report_gen tool which handles:
        1. VLM prompt sanitization (removes SOM markers)
        2. Video analysis via video_understanding or lvs_video_understanding
        3. Parallel processing for multiple videos (when using LVS)
        4. Report formatting with optional template
        5. Media URL fetching

        Args:
            video_report_input: Video(uploaded) Report mode-specific input with sensor_id(s) and user_query

        Returns:
            AgentOutput with video analysis and media URLs
        """

        # This tool is guaranteed to be set when va_mcp_enabled is False
        assert video_report_tool is not None

        # Normalize sensor_id for logging
        sensor_ids = (
            [video_report_input.sensor_id]
            if isinstance(video_report_input.sensor_id, str)
            else video_report_input.sensor_id
        )

        if len(sensor_ids) > 1:
            logger.info(f"Video(uploaded) Report mode: Requesting reports for {len(sensor_ids)} videos: {sensor_ids}")
        else:
            logger.info(f"Video(uploaded) Report mode: Analyzing uploaded video '{sensor_ids[0]}'")

        try:
            # Call the Video(uploaded) Report generation tool
            # The tool handles parallel processing internally for LVS videos
            tool_input: dict[str, Any] = {
                "sensor_id": video_report_input.sensor_id,
                "user_query": video_report_input.user_query,
            }
            # Add vlm_reasoning if provided
            if video_report_input.vlm_reasoning is not None:
                tool_input["vlm_reasoning"] = video_report_input.vlm_reasoning

            report_result = await video_report_tool.ainvoke(tool_input)
        except Exception as e:
            logger.exception(f"Report Agent: Video analysis report generation failed for videos {sensor_ids}: {e}")
            raise ValueError(
                f"Report Agent: Failed to generate video analysis report for videos {sensor_ids}: {e}"
            ) from e

        # Check if report was cancelled (no http_url means no report was generated)
        if not report_result.http_url:
            logger.info(f"Video report cancelled for {sensor_ids}")
            agent_output = AgentOutput(
                messages=[report_result.summary or "Report generation was cancelled."],
                side_effects={},
                status="success",
                metadata={"sensor_id": video_report_input.sensor_id},
            )
            yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=agent_output.model_dump_json())
            return

        logger.info(f"Video(uploaded) report generated successfully for {sensor_ids}")

        # Format output
        side_effects = {}

        # Format HITL prompts if available (from LVS) - used in both side_effects and messages
        hitl_text = None
        if hasattr(report_result, "hitl_prompts") and report_result.hitl_prompts:
            hitl = report_result.hitl_prompts
            prompts_parts = ["**Prompts:**"]
            if hitl.get("scenario"):
                prompts_parts.append(f"- Scenario: {hitl['scenario']}")
            if hitl.get("events"):
                prompts_parts.append(f"- Events of interest: {', '.join(hitl['events'])}")
            if hitl.get("objects_of_interest"):
                prompts_parts.append(f"- Objects of interest: {', '.join(hitl['objects_of_interest'])}")
            hitl_text = "\n".join(prompts_parts) + "\n"
            side_effects["hitl_prompts"] = hitl_text

        # Handle multi-video vs single-video downloads
        if hasattr(report_result, "all_reports") and report_result.all_reports:
            # Multi-video: format per-video download links
            downloads = ["**Report Downloads:**", ""]
            for video_report in report_result.all_reports:
                sensor_id = video_report.get("sensor_id", "Unknown")
                downloads.append(f"**{sensor_id}:**")
                if video_report.get("http_url"):
                    downloads.append(f"  - [Markdown Report]({video_report['http_url']})")
                if video_report.get("pdf_url"):
                    downloads.append(f"  - [PDF Report]({video_report['pdf_url']})")
                downloads.append("")  # Add blank line between videos
            side_effects["report_downloads"] = "\n".join(downloads)

            # Multi-video: format per-video media links
            media_links = ["**Media:**", ""]
            for video_report in report_result.all_reports:
                if video_report.get("video_url"):
                    sensor_id = video_report.get("sensor_id", "Unknown")
                    media_links.append(f"**{sensor_id}:**")
                    media_links.append(f"  - [Video Playback]({video_report['video_url']})")
                    media_links.append("")  # Add blank line between videos
            if len(media_links) > 2:  # More than just header and blank line
                side_effects["media"] = "\n".join(media_links)
        else:
            # Single video: original format
            downloads = ["**Report Downloads:**"]
            downloads.append(f"- [Markdown Report]({report_result.http_url})")
            if report_result.pdf_url:
                downloads.append(f"- [PDF Report]({report_result.pdf_url})")
            side_effects["report_downloads"] = "\n".join(downloads) + "\n"

            if report_result.video_url:
                media = ["**Media:**"]
                media.append(f"- [Video Playback]({report_result.video_url})")
                side_effects["media"] = "\n".join(media) + "\n"
        _append_artifact_display_note(side_effects)

        # Build messages list
        # Format sensor_id(s) for natural language display
        if isinstance(video_report_input.sensor_id, list):
            if len(video_report_input.sensor_id) == 1:
                sensor_display = video_report_input.sensor_id[0]
            elif len(video_report_input.sensor_id) == 2:
                sensor_display = f"{video_report_input.sensor_id[0]} & {video_report_input.sensor_id[1]}"
            else:
                sensor_display = ", ".join(video_report_input.sensor_id[:-1]) + f" & {video_report_input.sensor_id[-1]}"
        else:
            sensor_display = video_report_input.sensor_id

        messages = [
            f"Video analysis complete for '{sensor_display}'.\n",
            f"Query: {video_report_input.user_query}\n",
        ]

        # Add HITL prompts if available (from LVS)
        if hitl_text:
            messages.append(f"\n{hitl_text}")

        messages.append(report_result.summary)

        agent_output = AgentOutput(
            messages=messages,
            side_effects=side_effects,
            status="success",
            metadata={
                "sensor_id": video_report_input.sensor_id,
                "report_type": "video_report",
                "file_size": report_result.file_size,
                "pdf_file_size": report_result.pdf_file_size,
            },
        )
        yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=agent_output.model_dump_json())

    # Register the function with dynamic schema based on Video Analytics MCP availability
    if va_mcp_enabled:
        yield FunctionInfo.create(
            stream_fn=_execute_report_va_mcp,
            description=(
                "Generate detailed single incident reports using deterministic tool sequences. "
                "Fetches the most recent incident and generates a comprehensive report with video analysis. "
                "For multiple incidents, use multi_report_agent instead. "
                "Returns AgentOutput with messages, side_effects (reports, URLs), and metadata."
            ),
            input_schema=ReportAgentInput,
            stream_output_schema=AgentMessageChunk,
        )
    else:  # Video(uploaded) Report mode
        yield FunctionInfo.create(
            stream_fn=_execute_report_video,
            description=(
                "Generate video analysis reports for uploaded videos without requiring incident database. "
                "Analyzes full videos directly from VST based on sensor_id (filename). "
                "Supports parallel processing of multiple videos when using LVS (handled by video_report_gen tool). "
                "Returns AgentOutput with messages, side_effects (reports, URLs), and metadata."
            ),
            input_schema=VideoReportAgentInput,
            stream_output_schema=AgentMessageChunk,
        )
