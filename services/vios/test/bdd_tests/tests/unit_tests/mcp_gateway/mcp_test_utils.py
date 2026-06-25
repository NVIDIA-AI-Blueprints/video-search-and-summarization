# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
MCP-specific utilities for MCP Gateway unit tests.

Provides helpers for calling MCP tools via the streamable HTTP transport
and parsing tool results into standard Python dictionaries.
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)


def parse_tool_result(result: Any) -> Dict[str, Any]:
    """Parse a CallToolResult into a Python dictionary.

    Args:
        result: The CallToolResult from session.call_tool()

    Returns:
        Parsed dictionary. Includes '_is_error' if the tool signalled an error.
    """
    is_error = getattr(result, "isError", False)

    if not result.content:
        return {"_is_error": is_error, "_empty": True}

    text = result.content[0].text
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, AttributeError):
        parsed = {"_raw_text": str(text)}

    if isinstance(parsed, dict):
        parsed["_is_error"] = is_error
    elif isinstance(parsed, list):
        parsed = {"_data": parsed, "_is_error": is_error}
    else:
        parsed = {"_data": parsed, "_is_error": is_error}

    return parsed


async def _call_tool(
    mcp_url: str,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call an MCP tool via streamable HTTP transport.

    Args:
        mcp_url: Full URL to the MCP endpoint (e.g. http://localhost:8000/mcp)
        tool_name: Name of the MCP tool to invoke
        arguments: Optional dictionary of tool arguments

    Returns:
        Parsed tool result dictionary
    """
    logger.info("Calling MCP tool '%s' with arguments: %s", tool_name, arguments)

    async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments or {})
            parsed = parse_tool_result(result)
            logger.info("Tool '%s' returned: is_error=%s", tool_name, parsed.get("_is_error"))
            return parsed


def mcp_call_tool(
    mcp_url: str,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Synchronous wrapper to call an MCP tool.

    Mirrors the api_get / api_post pattern from unit_test_utils but
    communicates over the MCP protocol instead of raw HTTP.

    Args:
        mcp_url: Full URL to the MCP endpoint
        tool_name: Name of the MCP tool to invoke
        arguments: Optional dictionary of tool arguments

    Returns:
        Parsed tool result dictionary
    """
    assert "<change-to-your-host>" not in mcp_url, (
        f"mcp_url still contains placeholder: {mcp_url}\n"
        "Update tests.unit_tests.test_parameters.mcp_url in config.json "
        "with your actual host IP (e.g. http://10.0.0.1:8001/mcp)"
    )
    logger.info("MCP URL: %s", mcp_url)
    try:
        return asyncio.run(_call_tool(mcp_url, tool_name, arguments))
    except Exception as exc:
        raise AssertionError(
            f"Failed to call MCP tool '{tool_name}' at {mcp_url}\n"
            f"Error: {exc}\n"
            "Verify: 1) MCP gateway is running  2) mcp_url in config.json is correct  "
            "3) Port is 8001 (not 8000)"
        ) from exc


def extract_sensor_ids_from_mcp(sensor_list_result: Dict[str, Any]) -> List[str]:
    """Extract sensor IDs from a sensor_list MCP tool result.

    The MCP sensor_list tool returns an object keyed by sensorId.

    Args:
        sensor_list_result: Parsed result from sensor_list tool

    Returns:
        List of sensor ID strings
    """
    return [key for key in sensor_list_result if not key.startswith("_")]


def extract_stream_ids_from_mcp(sensor_list_result: Dict[str, Any]) -> List[str]:
    """Extract stream IDs from a sensor_list MCP tool result.

    Looks for 'streamId' inside each sensor object. Falls back to the
    sensor key if no streamId field is present.

    Args:
        sensor_list_result: Parsed result from sensor_list tool

    Returns:
        List of stream ID strings (test_upload_* sensors excluded)
    """
    stream_ids: List[str] = []
    for key, value in sensor_list_result.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict):
            stream_id = value.get("streamId", key)
            if stream_id and not str(stream_id).startswith("test_upload_"):
                stream_ids.append(str(stream_id))
    return stream_ids
