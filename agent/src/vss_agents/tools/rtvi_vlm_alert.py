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

"""Tool to configure real-time VLM stream monitoring via RTVI-VLM API."""

from collections.abc import AsyncGenerator
import contextlib
import json
import logging
import re
from typing import Literal
from urllib.parse import urlparse

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
    """Configuration for the RTVI-VLM alert tool."""

    rtvi_vlm_base_url: str = Field(
        ...,
        description="Base URL for RTVI-VLM service (e.g., http://localhost:8000)",
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
    default_chunk_duration: int = Field(
        20,
        description="Default chunk duration in seconds",
    )
    default_fps: int = Field(
        1,
        description="Default frames per second to analyze",
    )
    default_prompt: str | None = Field(
        None,
        description="Default detection prompt (if not provided via tool call)",
    )
    default_system_prompt: str | None = Field(
        None,
        description="Default system prompt (if not provided via tool call)",
    )
    timeout: int = Field(60, description="Request timeout in seconds")


class RTVIVLMAlertInput(BaseModel):
    """Input for RTVI-VLM stream alert operations."""

    action: Literal["start", "stop", "get_incidents"] = Field(
        ...,
        description="Action: 'start' (begin monitoring), 'stop' (end monitoring), 'get_incidents' (query detected incidents)",
    )
    sensor_name: str | None = Field(
        None,
        description="Sensor name (e.g., HWY_20_AND_DEVON__WB). Required for all actions.",
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
    """Output from RTVI-VLM alert operations."""

    success: bool = Field(..., description="Whether the operation succeeded")
    sensor_name: str | None = Field(default=None, description="Sensor name")
    stream_id: str | None = Field(default=None, description="RTVI-VLM stream ID (UUID)")
    message: str = Field(..., description="Status message")
    incidents: list[dict] | None = Field(default=None, description="List of incidents (for get_incidents action)")
    total_count: int | None = Field(
        default=None, description="Total number of incidents found (for get_incidents action)"
    )


# In-memory mapping of sensor_name -> rtvi_stream_id (for stop action)
_sensor_to_rtvi_stream_id: dict[str, str] = {}


@register_function(config_type=RTVIVLMAlertConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def rtvi_vlm_alert(config: RTVIVLMAlertConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    Start or stop real-time VLM alert monitoring for a sensor.

    Actions:
    - start: Add stream to RTVI-VLM + start caption/alert generation
    - stop: Stop caption generation + delete stream

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
        """Execute RTVI-VLM stream alert operation."""
        base_url = config.rtvi_vlm_base_url.rstrip("/")
        logger.info(f"RTVI-VLM base URL: {base_url}")
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

                    # Get the RTSP URL from VST and replace internal IP with VST host IP
                    rtsp_url = live_streams[sensor_name]["url"]
                    vst_host = urlparse(config.vst_internal_url).hostname
                    rtsp_url = re.sub(r"rtsp://[\d.]+:", f"rtsp://{vst_host}:", rtsp_url)
                    logger.info(f"Starting RTVI-VLM alert for sensor: {sensor_name}, RTSP: {rtsp_url}")

                    # Step 1: Add stream
                    add_payload = {
                        "streams": [
                            {
                                "liveStreamUrl": rtsp_url,
                                "description": sensor_name,
                                "sensor_name": sensor_name,
                            }
                        ]
                    }

                    async with session.post(f"{base_url}/v1/streams/add", json=add_payload) as response:
                        if response.status != 200:
                            error = await response.text()
                            return RTVIVLMAlertOutput(
                                success=False,
                                sensor_name=sensor_name,
                                message=f"Failed to add stream: {error}",
                            )

                        result = await response.json()
                        rtvi_stream_id = result.get("results", [{}])[0].get("id")
                        if not rtvi_stream_id:
                            return RTVIVLMAlertOutput(
                                success=False,
                                sensor_name=sensor_name,
                                message=f"Failed to get rtvi_stream_id from response: {result}",
                            )

                    logger.info(f"Stream added with RTVI ID: {rtvi_stream_id}")

                    # Save mapping for stop action (in-memory only)
                    _sensor_to_rtvi_stream_id[sensor_name] = rtvi_stream_id

                    # Step 2: Start caption/alert generation
                    # Use prompt from: tool input > config default > generic fallback
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

                    caption_payload = {
                        "id": rtvi_stream_id,
                        "model": config.default_model,
                        "stream": True,
                        "chunk_duration": config.default_chunk_duration,
                        "num_frames_per_second_or_fixed_frames_chunk": config.default_fps,
                        "use_fps_for_chunking": True,
                        "prompt": prompt,
                        "system_prompt": system_prompt,
                    }

                    async with session.post(
                        f"{base_url}/v1/generate_captions_alerts", json=caption_payload
                    ) as response:
                        if response.status != 200:
                            error = await response.text()
                            # Try to clean up the added stream
                            with contextlib.suppress(Exception):
                                await session.delete(f"{base_url}/v1/streams/delete/{rtvi_stream_id}")
                            return RTVIVLMAlertOutput(
                                success=False,
                                sensor_name=sensor_name,
                                stream_id=rtvi_stream_id,
                                message=f"Stream added but failed to start monitoring: {error}",
                            )

                    return RTVIVLMAlertOutput(
                        success=True,
                        sensor_name=sensor_name,
                        stream_id=rtvi_stream_id,
                        message=f"Real-time VLM alert started for sensor {sensor_name}.",
                    )

                # === STOP ===
                elif input_data.action == "stop":
                    assert sensor_name is not None  # validated above for stop action
                    # Get rtvi_stream_id from mapping
                    rtvi_stream_id = _sensor_to_rtvi_stream_id.get(sensor_name)

                    if not rtvi_stream_id:
                        return RTVIVLMAlertOutput(
                            success=False,
                            sensor_name=sensor_name,
                            message=f"No active alert found for sensor '{sensor_name}'. "
                            f"Active sensors: {list(_sensor_to_rtvi_stream_id.keys())}",
                        )

                    logger.info(f"Stopping RTVI-VLM alert for sensor: {sensor_name}, rtvi_stream_id: {rtvi_stream_id}")

                    # Step 1: Stop caption generation
                    try:
                        async with session.delete(
                            f"{base_url}/v1/generate_captions_alerts/{rtvi_stream_id}"
                        ) as response:
                            if response.status not in (200, 204, 404):
                                error = await response.text()
                                logger.warning(f"Failed to stop captions: {error}")
                    except Exception as e:
                        logger.warning(f"Error stopping captions: {e}")

                    # Step 2: Delete stream
                    async with session.delete(f"{base_url}/v1/streams/delete/{rtvi_stream_id}") as response:
                        # Remove from mapping regardless of result
                        _sensor_to_rtvi_stream_id.pop(sensor_name, None)

                        if response.status in (200, 204):
                            return RTVIVLMAlertOutput(
                                success=True,
                                sensor_name=sensor_name,
                                stream_id=rtvi_stream_id,
                                message=f"Real-time VLM alert stopped for sensor {sensor_name}.",
                            )
                        elif response.status == 404:
                            return RTVIVLMAlertOutput(
                                success=True,
                                sensor_name=sensor_name,
                                stream_id=rtvi_stream_id,
                                message=f"Alert for sensor {sensor_name} was already stopped.",
                            )
                        else:
                            error = await response.text()
                            return RTVIVLMAlertOutput(
                                success=False,
                                sensor_name=sensor_name,
                                stream_id=rtvi_stream_id,
                                message=f"Failed to delete stream: {error}",
                            )

        except aiohttp.ClientError as e:
            logger.error(f"RTVI-VLM connection error: {e}")
            return RTVIVLMAlertOutput(
                success=False,
                sensor_name=sensor_name,
                message=f"Connection error: {e}",
            )
        except Exception as e:
            logger.error(f"RTVI-VLM operation failed: {e}")
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
