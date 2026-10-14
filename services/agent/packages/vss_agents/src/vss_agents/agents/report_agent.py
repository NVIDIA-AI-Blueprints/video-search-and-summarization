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
from dataclasses import dataclass
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

INCIDENT_REPORT_METADATA_FIELDS = ["category", "place", "objectIds", "info"]
_LLM_REPORT_GENERATION_ERROR = "Failed to generate custom report with LLM"

_ARTIFACT_DISPLAY_NOTE = (
    "Do not include or offer to provide report download links in your final response "
    "since they will be automatically appended to your final response to the user."
)
_REPORT_DOWNLOADS_HEADING = "**Report Downloads:**"
_MEDIA_HEADING = "**Media:**"


def _is_llm_report_generation_failure(error: BaseException) -> bool:
    """True when this error is the generic custom-report LLM failure or was raised from it."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if str(current) == _LLM_REPORT_GENERATION_ERROR:
            return True
        current = current.__cause__
    return False


def _log_incident_report_failure(error: BaseException, *, unexpected: bool = False) -> None:
    """Log an incident-report failure without a provider prompt or model response.

    ``raise ... from e`` keeps the provider error for debugging, and
    ``logger.exception()`` would write that chain. The generic LLM failure is
    logged as its type only.
    """
    if _is_llm_report_generation_failure(error):
        logger.error(  # NOSONAR S8572: chained provider errors can echo the prompt; log the exception type only
            "Report Agent: Failed to execute incident report: %s",
            type(error).__name__,
        )
        return
    if unexpected:
        logger.exception("Report Agent: Unexpected error in incident report execution")
        return
    logger.exception("Report Agent: Failed to execute incident report")


def _append_artifact_display_note(side_effects: dict[str, Any]) -> None:
    """Add a note to top agent when report artifacts are included in the subagent side effects."""
    if side_effects:
        side_effects["artifact_note"] = _ARTIFACT_DISPLAY_NOTE


def _build_report_side_effects(report_result: Any, incident_id: str) -> dict[str, Any]:
    """Build the download and media side effects for a generated incident report.

    ``TemplateReportGenOutput`` declares ``http_url`` and ``pdf_url`` as required
    fields, so ``hasattr`` is always true for them: only the values say whether the
    object store produced anything linkable. Gating the section on the attribute
    instead emitted a bare ``**Report Downloads:**`` heading with no anchors
    whenever both URLs came back empty, which is indistinguishable -- to a reader
    and to any link assertion -- from report generation having silently failed.

    Media is treated differently on purpose: an incident need not have a snapshot
    or a clip, so an absent media block is normal and is omitted rather than
    announced. A report with nothing at all to download is not normal, so it says
    so explicitly and logs a warning.
    """

    side_effects: dict[str, Any] = {}

    downloads = [
        f"- [{label}]({url})"
        for label, url in (
            ("Markdown Report", getattr(report_result, "http_url", "")),
            ("PDF Report", getattr(report_result, "pdf_url", "")),
        )
        if url
    ]
    if downloads:
        side_effects["report_downloads"] = "\n".join([_REPORT_DOWNLOADS_HEADING, *downloads]) + "\n"
    else:
        logger.warning(
            "Report for incident %s produced no downloadable artifact: the object store returned "
            "neither a markdown nor a PDF URL",
            incident_id,
        )
        side_effects["report_downloads"] = (
            f"{_REPORT_DOWNLOADS_HEADING} none - the report was generated, but the object store returned "
            "no markdown or PDF URL, so there is nothing to download.\n"
        )

    media = [
        f"- {prefix}[{label}]({url})"
        for prefix, label, url in (
            ("!", "Incident Snapshot", getattr(report_result, "image_url", "")),
            ("", "Incident Video", getattr(report_result, "video_url", "")),
        )
        if url
    ]
    if media:
        side_effects["media"] = "\n".join([_MEDIA_HEADING, *media]) + "\n"

    return side_effects


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

    vlm_verified: bool | None = Field(
        default=None,
        description="Optional runtime override for VLM-verified incident lookup. If None, uses video_analytics config default.",
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
    Input for the Video(uploaded) / RTSP Stream Report Agent (Mode 3).

    This mode works without Video Analytics MCP - directly analyzes uploaded videos
    or configured live streams from VST. No incident database required.
    Supports parallel processing of multiple videos when using LVS (Long Video Summarization).
    RTSP streams (media_type='rtsp') require a single sensor_id and a start_time/end_time window.
    """

    sensor_id: str | list[str] = Field(
        ...,
        description=(
            "For media_type='video': VST sensor ID(s) (filename(s) of uploaded video). "
            "Can be a single string or a list of sensor_ids for parallel processing with LVS. "
            "For media_type='rtsp': VST stream/camera name (must be a single string)."
        ),
    )
    user_query: str = Field(
        "Generate a detailed report of the video.",
        description="The user's question or analysis request for this video/stream",
    )
    vlm_reasoning: bool | None = Field(
        default=None,
        description="Enable VLM reasoning mode for video analysis. If None, uses video_understanding config default. Ignored for RTSP streams.",
    )
    media_type: Literal["video", "rtsp"] = Field(
        default="video",
        description=(
            "Type of source: 'video' (default; uploaded VST file, supports multi-video batch) or "
            "'rtsp' (configured live/camera stream; requires start_time/end_time and a single sensor_id)."
        ),
    )
    start_time: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Start time in seconds (offset from stream start). Required when media_type='rtsp'. "
            "Ignored when media_type='video'."
        ),
    )
    end_time: float | None = Field(
        default=None,
        ge=0,
        description=(
            "End time in seconds (offset from stream start). Required when media_type='rtsp'; "
            "use 0 for 'no upper bound (until now)'. Ignored when media_type='video'."
        ),
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

    if _va_mcp_enabled(config):
        _log_report_agent_mode(va_mcp_enabled=True)
        tools = await _load_incident_tools(config, builder)
        _log_report_agent_initialized(va_mcp_enabled=True)
        yield _incident_function_info(_IncidentReportHandler(tools))
        return

    _log_report_agent_mode(va_mcp_enabled=False)
    video_report_tool = await _load_video_report_tool(config, builder)
    _log_report_agent_initialized(va_mcp_enabled=False)
    yield _video_function_info(_VideoReportHandler(video_report_tool))


def _va_mcp_enabled(config: ReportAgentConfig) -> bool:
    """Video Analytics MCP mode requires both incident lookup tools."""
    return config.get_incidents_tool is not None and config.get_incident_tool is not None


def _log_report_agent_mode(*, va_mcp_enabled: bool) -> None:
    if va_mcp_enabled:
        logger.info("Report Agent running in Mode 1 (Video Analytics MCP enabled)")
        return
    logger.info("Report Agent running in Mode 3 (Video(uploaded) Report mode - no Video Analytics MCP)")


def _log_report_agent_initialized(*, va_mcp_enabled: bool) -> None:
    mode = "Video Analytics MCP mode" if va_mcp_enabled else "Video(uploaded) Report mode"
    logger.info(f"Report Agent initialized ({mode})")


def _require_incident_tools(config: ReportAgentConfig) -> None:
    if not config.get_incidents_tool:
        raise ValueError("get_incidents_tool must be configured for Video Analytics MCP mode")
    if not config.get_incident_tool:
        raise ValueError("get_incident_tool must be configured for Video Analytics MCP mode")
    if not config.template_report_tool:
        raise ValueError("template_report_tool must be configured for Video Analytics MCP mode")


async def _load_incident_tools(config: ReportAgentConfig, builder: Builder) -> "_IncidentReportTools":
    logger.info("Loading Video Analytics MCP tools")
    _require_incident_tools(config)
    assert config.get_incidents_tool is not None
    assert config.get_incident_tool is not None
    assert config.template_report_tool is not None
    get_incidents_tool = await builder.get_tool(config.get_incidents_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    get_incident_tool = await builder.get_tool(config.get_incident_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    template_report_tool = await builder.get_tool(config.template_report_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    logger.info("Video Analytics MCP tools loaded successfully")
    return _IncidentReportTools(
        get_incidents_tool=get_incidents_tool,
        get_incident_tool=get_incident_tool,
        template_report_tool=template_report_tool,
    )


async def _load_video_report_tool(config: ReportAgentConfig, builder: Builder) -> Any:
    logger.info("Loading Video(uploaded) Report tools")
    if not config.video_report_tool:
        raise ValueError(
            "video_report_tool must be configured for Video(uploaded) Report mode. Otherwise Video Analytics MCP tools must be configured."
        )
    video_report_tool = await builder.get_tool(config.video_report_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    logger.info("Video(uploaded) Report tools loaded successfully")
    return video_report_tool


_INCIDENT_FUNCTION_DESCRIPTION = (
    "Generate detailed single incident reports using deterministic tool sequences. "
    "Fetches the most recent incident and generates a comprehensive report with video analysis. "
    "For multiple incidents, use multi_report_agent instead. "
    "Returns AgentOutput with messages, side_effects (reports, URLs), and metadata."
)

_VIDEO_FUNCTION_DESCRIPTION = (
    "Generate analysis reports for uploaded videos OR configured live streams without requiring "
    "an incident database. "
    "For uploaded videos (media_type='video', default): analyzes full videos directly from VST "
    "based on sensor_id (filename); supports parallel processing of multiple videos via LVS. "
    "For live RTSP streams (media_type='rtsp'): analyzes a configured stream over a "
    "[start_time, end_time] window in seconds (use end_time=0 for 'until now'); requires a single "
    "sensor_id (stream name). If the stream has no captions yet, the response will instruct the "
    "user to confirm caption generation by saying 'start captioning <name>'. The "
    "caller MUST surface that message verbatim and STOP — do NOT auto-call lvs_config_media. "
    "Returns AgentOutput with messages, side_effects (reports, URLs), and metadata."
)


def _incident_function_info(handler: "_IncidentReportHandler") -> FunctionInfo:
    return FunctionInfo.create(
        stream_fn=handler.__call__,
        description=_INCIDENT_FUNCTION_DESCRIPTION,
        input_schema=ReportAgentInput,
        stream_output_schema=AgentMessageChunk,
    )


def _video_function_info(handler: "_VideoReportHandler") -> FunctionInfo:
    return FunctionInfo.create(
        stream_fn=handler.__call__,
        description=_VIDEO_FUNCTION_DESCRIPTION,
        input_schema=VideoReportAgentInput,
        stream_output_schema=AgentMessageChunk,
    )


def _final_chunk(output: AgentOutput) -> AgentMessageChunk:
    return AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=output.model_dump_json())


def _tool_call_chunk(tool_name: str, args: dict[str, Any]) -> AgentMessageChunk:
    return AgentMessageChunk(type=AgentMessageChunkType.TOOL_CALL, content=f"Tool: {tool_name}\nArgs: {args}")


def _elapsed_ms(execution_start_time: float) -> int:
    return int((time.time() - execution_start_time) * 1000)


def _incident_error_detail(error: BaseException) -> str:
    if _is_llm_report_generation_failure(error):
        return "Failed to generate incident report"
    return str(error)


def _incident_handled_error_chunk(error: BaseException, execution_start_time: float) -> AgentMessageChunk:
    _log_incident_report_failure(error)
    detail = _incident_error_detail(error)
    return _final_chunk(
        AgentOutput(
            messages=[f"Report Agent: Error generating incident report: {detail}"],
            status="error",
            error_message=f"Report Agent: Failed to generate incident report: {detail}",
            metadata={
                "generation_time_ms": _elapsed_ms(execution_start_time),
                "report_type": "single_incident",
            },
        )
    )


def _incident_unexpected_error_chunk(error: BaseException, execution_start_time: float) -> AgentMessageChunk:
    _log_incident_report_failure(error, unexpected=True)
    return _final_chunk(
        AgentOutput(
            messages=["Report Agent: Unexpected error generating incident report"],
            status="error",
            error_message="Report Agent: Unexpected error in incident report execution",
            metadata={
                "generation_time_ms": _elapsed_ms(execution_start_time),
                "report_type": "single_incident",
            },
        )
    )


def _missing_incident_output(incident_id: str) -> AgentOutput:
    return AgentOutput(
        messages=[f"No incident found with ID '{incident_id}'."],
        status="success",
        metadata={"incident_id": incident_id},
    )


def _no_incidents_output() -> AgentOutput:
    return AgentOutput(
        messages=["No incidents found with the specified criteria."],
        status="success",
        metadata={"incident_count": 0},
    )


def _invalid_incidents_output() -> AgentOutput:
    return AgentOutput(
        messages=[
            "Report Agent: Unable to parse incidents data. The Video Analytics service returned an invalid response."
        ],
        status="error",
        error_message="Report Agent: Failed to parse Video Analytics MCP tool response",
    )


def _format_query_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _latest_incident_query(report_input: ReportAgentInput) -> dict[str, Any]:
    return {
        "max_count": 1,
        "includes": INCIDENT_REPORT_METADATA_FIELDS,
        "source": report_input.source,
        "source_type": report_input.source_type,
        "vlm_verified": report_input.vlm_verified,
        "start_time": _format_query_time(report_input.start_time),
        "end_time": _format_query_time(report_input.end_time),
    }


def _incident_lookup_args(incident_id: str, vlm_verified: bool | None) -> dict[str, Any]:
    return {
        "id": incident_id,
        "includes": INCIDENT_REPORT_METADATA_FIELDS,
        "vlm_verified": vlm_verified,
    }


def _incident_identifier(incident: dict[str, Any]) -> str:
    return incident.get("Id") or incident.get("id") or "unknown"


def _template_report_args(report_input: ReportAgentInput, incident: dict[str, Any], incident_id: str) -> dict[str, Any]:
    return {
        "incident_id": incident_id,
        "alert_sensor_id": incident.get("sensorId"),
        "alert_from_timestamp": incident.get("timestamp"),
        "alert_to_timestamp": incident.get("end"),
        "alert_metadata": incident,
        "vlm_reasoning": report_input.vlm_reasoning,
        "llm_reasoning": report_input.llm_reasoning,
    }


def _parse_incident_json(incident_result: str) -> dict[str, Any] | None:
    try:
        parsed_incident = json.loads(incident_result)
    except json.JSONDecodeError:
        logger.exception("Report Agent: Failed to parse get_incident response as JSON: %s", incident_result)
        return None
    return parsed_incident or None


def _parse_incident_payload(incident_result: Any) -> dict[str, Any] | None:
    if isinstance(incident_result, str):
        return _parse_incident_json(incident_result)
    return incident_result or None


def _parse_incidents_json(incidents_result: str) -> tuple[list[Any], AgentOutput | None]:
    try:
        parsed_result = json.loads(incidents_result)
    except json.JSONDecodeError:
        logger.exception("Report Agent: Failed to parse get_incidents response as JSON: %s", incidents_result)
        return [], _invalid_incidents_output()
    return parsed_result.get("incidents", []), None


def _parse_incidents_result(incidents_result: Any) -> tuple[list[Any], AgentOutput | None]:
    if isinstance(incidents_result, str):
        return _parse_incidents_json(incidents_result)
    incidents, _ignored = incidents_result
    return incidents, None


def _successful_incident_output(report_result: Any, incident: dict[str, Any], incident_id: str) -> AgentOutput:
    side_effects = _build_report_side_effects(report_result, incident_id)
    _append_artifact_display_note(side_effects)
    return AgentOutput(
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


@dataclass
class _IncidentReportTools:
    """Incident lookup and template-report tools for one registered agent."""

    get_incidents_tool: Any
    get_incident_tool: Any
    template_report_tool: Any


@dataclass
class _IncidentReportHandler:
    """Execute one incident report. The registered function only selects this handler."""

    tools: _IncidentReportTools

    async def __call__(
        self,
        source: str | None = None,
        source_type: Literal["sensor", "place"] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        incident_id: str | None = None,
        vlm_verified: bool | None = None,
        vlm_reasoning: bool | None = None,
        llm_reasoning: bool | None = None,
    ) -> AsyncGenerator[AgentMessageChunk]:
        """Execute single incident report generation."""
        logger.info("Executing incident-based single incident report")
        execution_start_time = time.time()
        report_input = ReportAgentInput(
            source=source,
            source_type=source_type,
            start_time=start_time,
            end_time=end_time,
            incident_id=incident_id,
            vlm_verified=vlm_verified,
            vlm_reasoning=vlm_reasoning,
            llm_reasoning=llm_reasoning,
        )
        try:
            async for chunk in self._handle_single_incident(report_input):
                yield chunk
        except (ValueError, KeyError, AttributeError) as error:
            yield _incident_handled_error_chunk(error, execution_start_time)
        except Exception as error:
            yield _incident_unexpected_error_chunk(error, execution_start_time)

    async def _handle_single_incident(self, report_input: ReportAgentInput) -> AsyncGenerator[AgentMessageChunk]:
        """Get one incident, then generate its report."""
        logger.info("Mode 1: Single incident report")
        if report_input.incident_id:
            async for chunk in self._chunks_for_incident_id(report_input):
                yield chunk
            return
        async for chunk in self._chunks_for_latest_incident(report_input):
            yield chunk

    async def _fetch_incident_by_id(self, incident_id: str, vlm_verified: bool | None) -> dict[str, Any] | None:
        incident_result = await self.tools.get_incident_tool.ainvoke(_incident_lookup_args(incident_id, vlm_verified))
        return _parse_incident_payload(incident_result)

    async def _chunks_for_incident_id(self, report_input: ReportAgentInput) -> AsyncGenerator[AgentMessageChunk]:
        incident_id = report_input.incident_id
        assert incident_id is not None
        logger.info(f"Getting incident by ID: {incident_id}")
        tool_call_args = _incident_lookup_args(incident_id, report_input.vlm_verified)
        yield _tool_call_chunk("get_incident", tool_call_args)
        incident = await self._fetch_incident_by_id(incident_id, report_input.vlm_verified)
        if not incident and report_input.vlm_verified is not True:
            logger.info(
                "Incident %s not found in default index; retrying get_incident with vlm_verified=true",
                incident_id,
            )
            retry_args = _incident_lookup_args(incident_id, True)
            yield _tool_call_chunk("get_incident", retry_args)
            incident = await self._fetch_incident_by_id(incident_id, True)
        if not incident:
            yield _final_chunk(_missing_incident_output(incident_id))
            return
        async for chunk in self._chunks_for_generated_report(report_input, incident):
            yield chunk

    async def _load_latest_incidents(self, params: dict[str, Any]) -> tuple[list[Any], AgentOutput | None]:
        incidents_result = await self.tools.get_incidents_tool.ainvoke(params)
        return _parse_incidents_result(incidents_result)

    async def _chunks_for_latest_incident(self, report_input: ReportAgentInput) -> AsyncGenerator[AgentMessageChunk]:
        params = _latest_incident_query(report_input)
        logger.info(f"Getting incidents with params: {params}")
        yield _tool_call_chunk("get_incidents", params)
        incidents, error_output = await self._load_latest_incidents(params)
        if error_output is not None:
            yield _final_chunk(error_output)
            return
        if not incidents:
            yield _final_chunk(_no_incidents_output())
            return
        async for chunk in self._chunks_for_generated_report(report_input, incidents[0]):
            yield chunk

    async def _chunks_for_generated_report(
        self,
        report_input: ReportAgentInput,
        incident: dict[str, Any],
    ) -> AsyncGenerator[AgentMessageChunk]:
        incident_id = _incident_identifier(incident)
        logger.info(f"Found incident: {incident_id}")
        logger.info("Generating detailed report")
        yield AgentMessageChunk(
            type=AgentMessageChunkType.TOOL_CALL,
            content=f"Tool: template_report_gen\nArgs: {{'incident_id': '{incident_id}'}}",
        )
        report_result = await self.tools.template_report_tool.ainvoke(
            _template_report_args(report_input, incident, incident_id)
        )
        logger.info("Single incident report generated successfully")
        yield _final_chunk(_successful_incident_output(report_result, incident, incident_id))


def _sensor_ids(sensor_id: str | list[str]) -> list[str]:
    if isinstance(sensor_id, str):
        return [sensor_id]
    return list(sensor_id)


def _log_video_target(video_report_input: VideoReportAgentInput, sensor_ids: list[str]) -> None:
    if video_report_input.media_type == "rtsp":
        logger.info(
            "RTSP Stream Report mode: Analyzing stream '%s' from %s to %s",
            sensor_ids[0],
            video_report_input.start_time,
            video_report_input.end_time,
        )
        return
    if len(sensor_ids) > 1:
        logger.info(f"Video(uploaded) Report mode: Requesting reports for {len(sensor_ids)} videos: {sensor_ids}")
        return
    logger.info(f"Video(uploaded) Report mode: Analyzing uploaded video '{sensor_ids[0]}'")


def _video_tool_input(video_report_input: VideoReportAgentInput) -> dict[str, Any]:
    tool_input: dict[str, Any] = {
        "sensor_id": video_report_input.sensor_id,
        "user_query": video_report_input.user_query,
        "media_type": video_report_input.media_type,
    }
    if video_report_input.vlm_reasoning is not None:
        tool_input["vlm_reasoning"] = video_report_input.vlm_reasoning
    if video_report_input.start_time is not None:
        tool_input["start_time"] = video_report_input.start_time
    if video_report_input.end_time is not None:
        tool_input["end_time"] = video_report_input.end_time
    return tool_input


def _format_hitl_prompts(hitl: dict[str, Any] | None) -> str | None:
    if not hitl:
        return None
    prompts_parts = ["**Prompts:**"]
    if hitl.get("scenario"):
        prompts_parts.append(f"- Scenario: {hitl['scenario']}")
    if hitl.get("events"):
        prompts_parts.append(f"- Events of interest: {', '.join(hitl['events'])}")
    if hitl.get("objects_of_interest"):
        prompts_parts.append(f"- Objects of interest: {', '.join(hitl['objects_of_interest'])}")
    if len(prompts_parts) == 1:
        return None
    return "\n".join(prompts_parts) + "\n"


def _multi_video_download_lines(video_report: dict[str, Any]) -> list[str]:
    sensor_id = video_report.get("sensor_id", "Unknown")
    lines = [f"**{sensor_id}:**"]
    if video_report.get("http_url"):
        lines.append(f"  - [Markdown Report]({video_report['http_url']})")
    if video_report.get("pdf_url"):
        lines.append(f"  - [PDF Report]({video_report['pdf_url']})")
    lines.append("")
    return lines


def _format_multi_video_downloads(all_reports: list[dict[str, Any]]) -> str:
    downloads = [_REPORT_DOWNLOADS_HEADING, ""]
    for video_report in all_reports:
        downloads.extend(_multi_video_download_lines(video_report))
    return "\n".join(downloads)


def _multi_video_media_lines(video_report: dict[str, Any]) -> list[str]:
    video_url = video_report.get("video_url")
    if not video_url:
        return []
    sensor_id = video_report.get("sensor_id", "Unknown")
    return [f"**{sensor_id}:**", f"  - [Video Playback]({video_url})", ""]


def _format_multi_video_media(all_reports: list[dict[str, Any]]) -> str | None:
    media_links = [_MEDIA_HEADING, ""]
    for video_report in all_reports:
        media_links.extend(_multi_video_media_lines(video_report))
    if len(media_links) <= 2:
        return None
    return "\n".join(media_links)


def _format_single_video_downloads(report_result: Any) -> str:
    downloads = [_REPORT_DOWNLOADS_HEADING, f"- [Markdown Report]({report_result.http_url})"]
    if report_result.pdf_url:
        downloads.append(f"- [PDF Report]({report_result.pdf_url})")
    return "\n".join(downloads) + "\n"


def _format_single_video_media(report_result: Any) -> str | None:
    if not report_result.video_url:
        return None
    return "\n".join([_MEDIA_HEADING, f"- [Video Playback]({report_result.video_url})"]) + "\n"


def _add_video_download_side_effects(side_effects: dict[str, Any], report_result: Any) -> None:
    reports = getattr(report_result, "all_reports", None)
    if reports:
        side_effects["report_downloads"] = _format_multi_video_downloads(reports)
        media = _format_multi_video_media(reports)
        if media:
            side_effects["media"] = media
        return
    side_effects["report_downloads"] = _format_single_video_downloads(report_result)
    media = _format_single_video_media(report_result)
    if media:
        side_effects["media"] = media


def _build_video_side_effects(report_result: Any) -> dict[str, Any]:
    side_effects: dict[str, Any] = {}
    warning = getattr(report_result, "lvs_fallback_warning", None)
    if warning:
        side_effects["lvs_fallback_warning"] = warning
    hitl_text = _format_hitl_prompts(getattr(report_result, "hitl_prompts", None))
    if hitl_text:
        side_effects["hitl_prompts"] = hitl_text
    _add_video_download_side_effects(side_effects, report_result)
    _append_artifact_display_note(side_effects)
    return side_effects


def _format_sensor_list(sensor_ids: list[str]) -> str:
    if len(sensor_ids) == 1:
        return sensor_ids[0]
    if len(sensor_ids) == 2:
        return f"{sensor_ids[0]} & {sensor_ids[1]}"
    return ", ".join(sensor_ids[:-1]) + f" & {sensor_ids[-1]}"


def _format_sensor_display(sensor_id: str | list[str]) -> str:
    if not isinstance(sensor_id, list):
        return sensor_id
    return _format_sensor_list(sensor_id)


def _video_success_messages(video_report_input: VideoReportAgentInput, report_result: Any) -> list[Any]:
    sensor_display = _format_sensor_display(video_report_input.sensor_id)
    return [
        f"Video analysis complete for '{sensor_display}'.\n",
        f"Query: {video_report_input.user_query}\n",
        report_result.summary,
    ]


def _video_success_output(video_report_input: VideoReportAgentInput, report_result: Any) -> AgentOutput:
    return AgentOutput(
        messages=_video_success_messages(video_report_input, report_result),
        side_effects=_build_video_side_effects(report_result),
        status="success",
        metadata={
            "sensor_id": video_report_input.sensor_id,
            "report_type": "video_report",
            "file_size": report_result.file_size,
            "pdf_file_size": report_result.pdf_file_size,
        },
    )


def _cancelled_video_output(video_report_input: VideoReportAgentInput, report_result: Any) -> AgentOutput:
    return AgentOutput(
        messages=[report_result.summary or "Report generation was cancelled."],
        side_effects={},
        status="success",
        metadata={"sensor_id": video_report_input.sensor_id},
    )


def _is_websocket_prompt_error(error_str: str) -> bool:
    return "No human prompt callback was registered" in error_str or "Unable to handle requested prompt" in error_str


def _video_known_error_messages(error: BaseException) -> tuple[str, str]:
    error_str = str(error)
    if _is_websocket_prompt_error(error_str):
        user_message = (
            "Could not start human in the loop workflow over websocket. "
            "Please check that websocket connection is enabled in the UI and that the IP of agent "
            "is set correctly in the settings panel from the left lower side."
        )
        return user_message, f"Report Agent: Websocket connection error - {user_message}"
    return (
        f"Report Agent: Error generating video analysis report: {error_str}",
        f"Report Agent: Failed to generate video analysis report: {error_str}",
    )


def _video_error_metadata(execution_start_time: float) -> dict[str, Any]:
    return {
        "generation_time_ms": _elapsed_ms(execution_start_time),
        "report_type": "video_report",
        "mode": "video(uploaded) report",
    }


def _video_known_error_chunk(error: BaseException, execution_start_time: float) -> AgentMessageChunk:
    logger.exception("Report Agent: Failed to execute direct video analysis report")
    user_message, error_message = _video_known_error_messages(error)
    return _final_chunk(
        AgentOutput(
            messages=[user_message],
            status="error",
            error_message=error_message,
            metadata=_video_error_metadata(execution_start_time),
        )
    )


def _video_unexpected_error_chunk(execution_start_time: float) -> AgentMessageChunk:
    logger.exception("Report Agent: Unexpected error in direct video analysis report execution")
    return _final_chunk(
        AgentOutput(
            messages=["Report Agent: Unexpected error generating video analysis report"],
            status="error",
            error_message="Report Agent: Unexpected error in video analysis report execution",
            metadata=_video_error_metadata(execution_start_time),
        )
    )


@dataclass
class _VideoReportHandler:
    """Execute one uploaded-video or RTSP report. The registered function only selects this handler."""

    video_report_tool: Any

    async def __call__(
        self,
        sensor_id: str | list[str],
        user_query: str,
        vlm_reasoning: bool | None = None,
        media_type: Literal["video", "rtsp"] = "video",
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> AsyncGenerator[AgentMessageChunk]:
        """Execute Video(uploaded) / RTSP Stream Report generation."""
        logger.info(
            "Executing Report Agent (media_type=%s, sensor_id=%s)",
            media_type,
            sensor_id,
        )
        execution_start_time = time.time()
        video_report_input = VideoReportAgentInput(
            sensor_id=sensor_id,
            user_query=user_query,
            vlm_reasoning=vlm_reasoning,
            media_type=media_type,
            start_time=start_time,
            end_time=end_time,
        )
        try:
            async for chunk in self._run(video_report_input):
                yield chunk
        except (ValueError, KeyError, AttributeError) as error:
            yield _video_known_error_chunk(error, execution_start_time)
        except Exception:
            yield _video_unexpected_error_chunk(execution_start_time)

    async def _invoke_video_tool(self, video_report_input: VideoReportAgentInput, sensor_ids: list[str]) -> Any:
        try:
            return await self.video_report_tool.ainvoke(_video_tool_input(video_report_input))
        except Exception as error:
            logger.exception(f"Report Agent: Video analysis report generation failed for videos {sensor_ids}: {error}")
            raise ValueError(
                f"Report Agent: Failed to generate video analysis report for videos {sensor_ids}: {error}"
            ) from error

    async def _run(self, video_report_input: VideoReportAgentInput) -> AsyncGenerator[AgentMessageChunk]:
        sensor_ids = _sensor_ids(video_report_input.sensor_id)
        _log_video_target(video_report_input, sensor_ids)
        report_result = await self._invoke_video_tool(video_report_input, sensor_ids)
        if not report_result.http_url:
            logger.info(f"Video report cancelled for {sensor_ids}")
            yield _final_chunk(_cancelled_video_output(video_report_input, report_result))
            return
        logger.info(f"Video(uploaded) report generated successfully for {sensor_ids}")
        yield _final_chunk(_video_success_output(video_report_input, report_result))
