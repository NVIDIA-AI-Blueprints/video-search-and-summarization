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

"""Tool to manage real-time VLM alert rules via the Alert Bridge realtime API."""

from collections.abc import AsyncGenerator
import json
import logging
from typing import Literal

import aiohttp
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class RTVIVLMAlertConfig(FunctionBaseConfig, name="rtvi_vlm_alert"):
    """Configuration for the real-time VLM alert tool."""

    alert_bridge_url: str = Field(
        ...,
        description="Base URL for the Alert Bridge service (e.g., http://${INTERNAL_IP}:9080)",
    )
    vst_internal_url: str = Field(
        ...,
        description="Internal VST URL for API calls (e.g., http://${INTERNAL_IP}:30888)",
    )
    va_get_incidents_tool: FunctionRef | None = Field(
        default=None,
        description="Optional reference to VA MCP get_incidents tool. If provided, reuses VA for incident queries instead of direct ES access.",
    )
    default_model: str = Field(
        "nvidia/cosmos-reason1-7b",
        description="Default VLM model for caption/alert generation",
    )
    default_alert_type: str = Field(
        "alert",
        description="Default alert_type label assigned to created rules when not provided",
    )
    default_chunk_duration: int = Field(
        20,
        description="Default chunk duration in seconds",
    )
    default_chunk_overlap_duration: int = Field(
        0,
        description="Default chunk overlap duration in seconds",
    )
    default_fps: int = Field(
        1,
        description="Default frames per second to analyze",
    )
    default_vlm_input_width: int = Field(
        256,
        description="Default VLM input width",
    )
    default_vlm_input_height: int = Field(
        256,
        description="Default VLM input height",
    )
    default_enable_reasoning: bool = Field(
        False,
        description="Whether to enable VLM reasoning by default",
    )
    default_prompt: str | None = Field(
        None,
        description="Default detection prompt (if not provided via tool call)",
    )
    default_system_prompt: str | None = Field(
        None,
        description="Default system prompt (if not provided via tool call)",
    )
    timeout: int = Field(
        180,
        description="Request timeout in seconds. POST /api/v1/realtime waits for two RTVI round-trips, so allow 2x rtvi_vlm.timeout.",
    )


class RTVIVLMAlertInput(BaseModel):
    """Input for real-time VLM alert operations."""

    action: Literal["start", "stop", "get_incidents"] = Field(
        ...,
        description="Action: 'start' (create alert rule), 'stop' (delete alert rule), 'get_incidents' (query detected incidents)",
    )
    sensor_name: str | None = Field(
        None,
        description="Sensor name (e.g., HWY_20_AND_DEVON__WB). Required for all actions.",
    )
    alert_type: str | None = Field(
        None,
        description="Alert type label for the rule (e.g., 'collision', 'ppe_violation'). Only for 'start' action.",
    )
    prompt: str | None = Field(
        None,
        description="Detection prompt (e.g., 'Is there a vehicle collision? Answer YES or NO.'). Only for 'start' action.",
    )
    system_prompt: str | None = Field(
        None,
        description="System prompt for VLM. Only for 'start' action.",
    )
    # Fields for get_incidents action
    start_time: str | None = Field(
        None,
        description="Start time in ISO 8601 format (e.g., 2026-01-06T00:00:00.000Z). Only for 'get_incidents' action.",
    )
    end_time: str | None = Field(
        None,
        description="End time in ISO 8601 format. Only for 'get_incidents' action.",
    )
    max_count: int = Field(
        10,
        description="Maximum number of incidents to return. Only for 'get_incidents' action.",
    )
    incident_type: str | None = Field(
        None,
        description="Filter by incident type (e.g., 'collision'). Only for 'get_incidents' action.",
    )


class RTVIVLMAlertOutput(BaseModel):
    """Output from real-time VLM alert operations."""

    success: bool = Field(..., description="Whether the operation succeeded")
    sensor_name: str | None = Field(default=None, description="Sensor name")
    alert_rule_id: str | None = Field(
        default=None, description="Alert Bridge alert rule ID (UUID) returned by POST /api/v1/realtime"
    )
    message: str = Field(..., description="Status message")
    incidents: list[dict] | None = Field(default=None, description="List of incidents (for get_incidents action)")
    total_count: int | None = Field(
        default=None, description="Total number of incidents found (for get_incidents action)"
    )


# In-memory mapping of sensor_name -> alert_rule_id (for stop action)
_sensor_to_alert_rule_id: dict[str, str] = {}


@register_function(config_type=RTVIVLMAlertConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def rtvi_vlm_alert(config: RTVIVLMAlertConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    Start or stop a real-time VLM alert rule for a sensor.

    Actions:
    - start: POST /api/v1/realtime on Alert Bridge to create a rule. Alert Bridge
      registers the stream with RTVI VLM and starts caption/alert generation
      transactionally; on any failure it rolls back.
    - stop: DELETE /api/v1/realtime/{alert_rule_id} on Alert Bridge to stop
      caption generation and remove the underlying RTVI VLM stream.

    Both actions use sensor_name only. The RTSP URL is fetched from VST live streams API.
    """

    async def _get_live_streams() -> dict[str, dict]:
        """Fetch live streams from VST. Returns mapping of sensor_name -> {"stream_id": ..., "url": ...}."""
        vst_url = f"{config.vst_internal_url.rstrip('/')}/vst/api/v1/live/streams"
        timeout = aiohttp.ClientTimeout(total=config.timeout)

        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(vst_url) as response:
            response.raise_for_status()
            # VST returns text/plain content type but body is JSON
            streams_data = json.loads(await response.text())

            # Parse response: [{"stream_id": [{"name": ..., "url": ..., "streamId": ...}]}, ...]
            result = {}
            for item in streams_data:
                for stream_id, streams in item.items():
                    if streams and isinstance(streams, list):
                        stream_info = streams[0]
                        name = stream_info.get("name")
                        url = stream_info.get("url")
                        if name and url:
                            result[name] = {"stream_id": stream_id, "url": url}
            return result

    async def _rtvi_vlm_alert(input_data: RTVIVLMAlertInput) -> RTVIVLMAlertOutput:
        """Execute real-time VLM alert operation against Alert Bridge."""
        base_url = config.alert_bridge_url.rstrip("/")
        logger.info(f"Alert Bridge base URL: {base_url}")
        timeout = aiohttp.ClientTimeout(total=config.timeout)

        sensor_name = input_data.sensor_name

        # === GET_INCIDENTS === Query incidents via VA MCP tool
        if input_data.action == "get_incidents":
            if not sensor_name:
                return RTVIVLMAlertOutput(
                    success=False,
                    message="sensor_name is required for 'get_incidents' action.",
                )

            # Check if VA tool is configured
            if not config.va_get_incidents_tool:
                return RTVIVLMAlertOutput(
                    success=False,
                    sensor_name=sensor_name,
                    message="va_get_incidents_tool is not configured. Cannot query incidents.",
                )

            try:
                # Get the VA get_incidents tool
                va_tool = await builder.get_tool(config.va_get_incidents_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

                # Build input for VA tool - use sensor_name directly as source
                # When sensor_name is provided to RTVI-VLM, it's used as sensor_id in Kafka messages
                va_input = {
                    "source": sensor_name,
                    "source_type": "sensor",
                    "max_count": input_data.max_count,
                }

                # Add time range if provided (VA tool requires both start and end)
                if input_data.start_time and input_data.end_time:
                    va_input["start_time"] = input_data.start_time
                    va_input["end_time"] = input_data.end_time

                # Call VA tool
                result = await va_tool.ainvoke(input=va_input)

                # Parse result - VA tool returns {"incidents": [...], "has_more": bool}
                if isinstance(result, str):
                    result = json.loads(result)

                incidents = result.get("incidents", [])
                total = len(incidents)

                return RTVIVLMAlertOutput(
                    success=True,
                    sensor_name=sensor_name,
                    message=f"Found {total} incidents for sensor '{sensor_name}'.",
                    incidents=incidents,
                    total_count=total,
                )
            except Exception as e:
                logger.error(f"VA get_incidents error: {e}")
                return RTVIVLMAlertOutput(
                    success=False,
                    sensor_name=sensor_name,
                    message=f"Failed to query incidents: {e}",
                )

        # Validate sensor_name for start/stop actions
        if input_data.action in ("start", "stop") and not sensor_name:
            return RTVIVLMAlertOutput(
                success=False,
                message=f"sensor_name is required for action '{input_data.action}'.",
            )

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # === START ===
                if input_data.action == "start":
                    # Fetch live streams and find the sensor's RTSP URL
                    live_streams = await _get_live_streams()

                    if sensor_name not in live_streams:
                        return RTVIVLMAlertOutput(
                            success=False,
                            sensor_name=sensor_name,
                            message=f"Sensor '{sensor_name}' not found in VST live streams. "
                            f"Available sensors: {sorted(live_streams.keys())}",
                        )

                    rtsp_url = live_streams[sensor_name]["url"]
                    logger.info(f"Creating realtime alert rule for sensor: {sensor_name}, RTSP: {rtsp_url}")

                    prompt = (
                        input_data.prompt
                        or config.default_prompt
                        or "Describe any notable events or anomalies in this video stream."
                    )
                    system_prompt = (
                        input_data.system_prompt
                        or config.default_system_prompt
                        or "You are a video monitoring assistant. Provide detailed observations about relevant events."
                    )
                    alert_type = input_data.alert_type or config.default_alert_type

                    payload = {
                        "live_stream_url": rtsp_url,
                        "alert_type": alert_type,
                        "sensor_name": sensor_name,
                        "prompt": prompt,
                        "system_prompt": system_prompt,
                        "model": config.default_model,
                        "chunk_duration": config.default_chunk_duration,
                        "chunk_overlap_duration": config.default_chunk_overlap_duration,
                        "num_frames_per_second_or_fixed_frames_chunk": config.default_fps,
                        "use_fps_for_chunking": True,
                        "vlm_input_width": config.default_vlm_input_width,
                        "vlm_input_height": config.default_vlm_input_height,
                        "enable_reasoning": config.default_enable_reasoning,
                    }

                    async with session.post(f"{base_url}/api/v1/realtime", json=payload) as response:
                        body = await response.text()
                        if response.status not in (200, 201):
                            return RTVIVLMAlertOutput(
                                success=False,
                                sensor_name=sensor_name,
                                message=f"Failed to create alert rule (HTTP {response.status}): {body}",
                            )

                        try:
                            result = json.loads(body)
                        except json.JSONDecodeError:
                            return RTVIVLMAlertOutput(
                                success=False,
                                sensor_name=sensor_name,
                                message=f"Invalid JSON response from Alert Bridge: {body}",
                            )

                        alert_rule_id = result.get("id")
                        if not alert_rule_id:
                            return RTVIVLMAlertOutput(
                                success=False,
                                sensor_name=sensor_name,
                                message=f"Alert Bridge response missing 'id': {result}",
                            )

                    _sensor_to_alert_rule_id[sensor_name] = alert_rule_id
                    logger.info(f"Realtime alert rule {alert_rule_id} created for sensor {sensor_name}")

                    return RTVIVLMAlertOutput(
                        success=True,
                        sensor_name=sensor_name,
                        alert_rule_id=alert_rule_id,
                        message=f"Real-time VLM alert started for sensor {sensor_name}.",
                    )

                # === STOP ===
                elif input_data.action == "stop":
                    assert sensor_name is not None  # validated above for stop action
                    alert_rule_id = _sensor_to_alert_rule_id.get(sensor_name)

                    if not alert_rule_id:
                        return RTVIVLMAlertOutput(
                            success=False,
                            sensor_name=sensor_name,
                            message=f"No active alert found for sensor '{sensor_name}'. "
                            f"Active sensors: {list(_sensor_to_alert_rule_id.keys())}",
                        )

                    logger.info(f"Deleting realtime alert rule {alert_rule_id} for sensor {sensor_name}")

                    async with session.delete(f"{base_url}/api/v1/realtime/{alert_rule_id}") as response:
                        body = await response.text()

                        if response.status in (200, 204):
                            _sensor_to_alert_rule_id.pop(sensor_name, None)
                            return RTVIVLMAlertOutput(
                                success=True,
                                sensor_name=sensor_name,
                                alert_rule_id=alert_rule_id,
                                message=f"Real-time VLM alert stopped for sensor {sensor_name}.",
                            )
                        elif response.status == 404:
                            _sensor_to_alert_rule_id.pop(sensor_name, None)
                            return RTVIVLMAlertOutput(
                                success=True,
                                sensor_name=sensor_name,
                                alert_rule_id=alert_rule_id,
                                message=f"Alert for sensor {sensor_name} was already stopped.",
                            )
                        else:
                            return RTVIVLMAlertOutput(
                                success=False,
                                sensor_name=sensor_name,
                                alert_rule_id=alert_rule_id,
                                message=f"Failed to delete alert rule (HTTP {response.status}): {body}",
                            )

        except aiohttp.ClientError as e:
            logger.error(f"Alert Bridge connection error: {e}")
            return RTVIVLMAlertOutput(
                success=False,
                sensor_name=sensor_name,
                message=f"Connection error: {e}",
            )
        except Exception as e:
            logger.error(f"Realtime alert operation failed: {e}")
            return RTVIVLMAlertOutput(
                success=False,
                sensor_name=sensor_name,
                message=str(e),
            )

    yield FunctionInfo.create(
        single_fn=_rtvi_vlm_alert,
        description=_rtvi_vlm_alert.__doc__,
        input_schema=RTVIVLMAlertInput,
        single_output_schema=RTVIVLMAlertOutput,
    )
