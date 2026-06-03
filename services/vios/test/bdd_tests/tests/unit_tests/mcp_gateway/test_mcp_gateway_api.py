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
Unit tests for the VST MCP Gateway Service API.

Tests MCP tools: sensor list/status/health/scan, sensor by-ID queries,
recording start/stop/status/timelines, live picture base64/URL,
and storage file list/paths.
"""
import logging
from typing import Any, Dict

import pytest
from pytest_bdd import scenarios, given, when, then

from ..unit_test_utils import UnitTestContext
from .mcp_test_utils import (
    mcp_call_tool,
    extract_sensor_ids_from_mcp,
    extract_stream_ids_from_mcp,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.mcp_gateway

scenarios("../../../features/unit_tests/mcp_gateway/mcp_gateway_api.feature")


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------

@given("the MCP gateway is accessible")
def mcp_gateway_accessible(unit_test_params: Dict[str, Any]) -> None:
    """Verify the MCP URL is configured."""
    mcp_url = unit_test_params.get("mcp_url", "")
    assert mcp_url, "MCP URL must be configured in unit_tests.test_parameters.mcp_url"


@given("at least one sensor is available via MCP")
def at_least_one_sensor_via_mcp(
    context: UnitTestContext, unit_test_params: Dict[str, Any]
) -> None:
    """Call sensor_list via MCP and store the first sensor ID."""
    mcp_url = unit_test_params["mcp_url"]
    result = mcp_call_tool(mcp_url, "sensor_list")
    sensor_ids = extract_sensor_ids_from_mcp(result)
    assert len(sensor_ids) > 0, "No sensors discovered from MCP server"
    context.first_sensor_id = sensor_ids[0]
    logger.info("Using sensor ID: %s", context.first_sensor_id)


@given("at least one stream is available via MCP")
def at_least_one_stream_via_mcp(
    context: UnitTestContext, unit_test_params: Dict[str, Any]
) -> None:
    """Call sensor_list via MCP and store the first stream ID."""
    mcp_url = unit_test_params["mcp_url"]
    result = mcp_call_tool(mcp_url, "sensor_list")
    stream_ids = extract_stream_ids_from_mcp(result)
    assert len(stream_ids) > 0, "No streams discovered from MCP server"
    context.first_stream_id = stream_ids[0]
    logger.info("Using stream ID: %s", context.first_stream_id)


# ---------------------------------------------------------------------------
# When -- sensor tools
# ---------------------------------------------------------------------------

@when("I call the sensor_list tool")
def call_sensor_list(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'sensor_list' and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(mcp_url, "sensor_list")


@when("I call the sensor_status tool")
def call_sensor_status(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'sensor_status' and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(mcp_url, "sensor_status")


@when("I call the sensor_health_check tool")
def call_sensor_health_check(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'sensor_health_check' and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(mcp_url, "sensor_health_check")


@when("I call the sensor_scan tool")
def call_sensor_scan(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'sensor_scan' and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(mcp_url, "sensor_scan")


@when("I call the sensor_status_by_id tool for the first sensor")
def call_sensor_status_by_id(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'sensor_status_by_id' with context.first_sensor_id and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(
        mcp_url, "sensor_status_by_id",
        arguments={"sensor_id": context.first_sensor_id},
    )


@when("I call the sensor_info_by_id tool for the first sensor")
def call_sensor_info_by_id(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'sensor_info_by_id' with context.first_sensor_id and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(
        mcp_url, "sensor_info_by_id",
        arguments={"sensor_id": context.first_sensor_id},
    )


@when("I call the sensor_settings_by_id tool for the first sensor")
def call_sensor_settings_by_id(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'sensor_settings_by_id' with context.first_sensor_id and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(
        mcp_url, "sensor_settings_by_id",
        arguments={"sensor_id": context.first_sensor_id},
    )


# ---------------------------------------------------------------------------
# When -- recording tools
# ---------------------------------------------------------------------------

@when("I call the record_stream_status tool for the first stream")
def call_record_stream_status(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'record_stream_status' with context.first_stream_id and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(
        mcp_url, "record_stream_status",
        arguments={"stream_id": context.first_stream_id},
    )


@when("I call the record_stream_timelines tool for the first stream")
def call_record_stream_timelines(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'record_stream_timelines' with context.first_stream_id and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(
        mcp_url, "record_stream_timelines",
        arguments={"stream_id": context.first_stream_id},
    )


@when("I call the record_stream_start tool for the first stream")
def call_record_stream_start(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'record_stream_start' with context.first_stream_id and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(
        mcp_url, "record_stream_start",
        arguments={"stream_id": context.first_stream_id},
    )


@when("I call the record_stream_stop tool for the first stream")
def call_record_stream_stop(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'record_stream_stop' with context.first_stream_id and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(
        mcp_url, "record_stream_stop",
        arguments={"stream_id": context.first_stream_id},
    )


# ---------------------------------------------------------------------------
# When -- picture tools
# ---------------------------------------------------------------------------

@when("I call the get_live_picture_base64 tool for the first stream")
def call_get_live_picture_base64(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'get_live_picture_base64' with context.first_stream_id and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(
        mcp_url, "get_live_picture_base64",
        arguments={"stream_id": context.first_stream_id},
    )


@when("I call the get_live_picture_url tool for the first stream")
def call_get_live_picture_url(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'get_live_picture_url' with context.first_stream_id and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(
        mcp_url, "get_live_picture_url",
        arguments={"stream_id": context.first_stream_id},
    )


# ---------------------------------------------------------------------------
# When -- storage tools
# ---------------------------------------------------------------------------

@when("I call the storage_file_list tool")
def call_storage_file_list(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'storage_file_list' and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(mcp_url, "storage_file_list")


@when("I call the storage_file_list_by_sensor tool for the first sensor")
def call_storage_file_list_by_sensor(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'storage_file_list_by_sensor' with context.first_sensor_id and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(
        mcp_url, "storage_file_list_by_sensor",
        arguments={"sensor_id": context.first_sensor_id},
    )


@when("I call the storage_file_path_by_sensor tool for the first sensor")
def call_storage_file_path_by_sensor(context: UnitTestContext, unit_test_params: Dict[str, Any]) -> None:
    """Call MCP tool 'storage_file_path_by_sensor' with context.first_sensor_id and store result in context.response_json."""
    mcp_url = unit_test_params["mcp_url"]
    context.response_json = mcp_call_tool(
        mcp_url, "storage_file_path_by_sensor",
        arguments={"sensor_id": context.first_sensor_id},
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("the MCP response is valid JSON")
def check_mcp_response_valid(context: UnitTestContext) -> None:
    """Verify the tool returned parsed JSON without error."""
    data = context.response_json
    assert data is not None, "MCP tool returned no result"
    assert not data.get("_is_error", False), f"MCP tool returned an error: {data}"
    assert "error" not in data, f"MCP tool response contains error: {data.get('error')}"
    logger.info("MCP response OK (valid JSON, no error)")


@then("the MCP response contains sensor data")
def check_mcp_sensor_data(context: UnitTestContext) -> None:
    """Verify sensor_list returned at least one sensor."""
    data = context.response_json
    sensor_keys = [k for k in data if not k.startswith("_")]
    assert len(sensor_keys) > 0, "sensor_list returned no sensors"
    logger.info("sensor_list returned %d sensor(s)", len(sensor_keys))


@then("the MCP response contains a status field")
def check_mcp_status_field(context: UnitTestContext) -> None:
    """Verify the response has a 'status' key."""
    data = context.response_json
    assert "status" in data, f"Response missing 'status' field. Keys: {list(data.keys())}"
    logger.info("Health check status: %s", data["status"])


@then("the MCP response contains base64 image data")
def check_mcp_base64_image(context: UnitTestContext) -> None:
    """Verify the response contains base64-encoded image fields."""
    data = context.response_json
    assert "image" in data, f"Missing 'image' field. Keys: {list(data.keys())}"
    assert "content_type" in data, f"Missing 'content_type'. Keys: {list(data.keys())}"
    assert "size_bytes" in data, f"Missing 'size_bytes'. Keys: {list(data.keys())}"
    assert data.get("encoding") == "base64", (
        f"Expected encoding='base64', got '{data.get('encoding')}'"
    )
    assert str(data["image"]).startswith("data:"), "Image data should start with 'data:' URI"
    logger.info(
        "Picture: content_type=%s, size_bytes=%s",
        data["content_type"], data["size_bytes"],
    )


@then("the MCP response contains an image URL")
def check_mcp_image_url(context: UnitTestContext) -> None:
    """Verify the response contains a temporary image URL."""
    data = context.response_json
    assert "imageUrl" in data, f"Missing 'imageUrl'. Keys: {list(data.keys())}"
    image_url = data["imageUrl"]
    assert str(image_url).startswith("http"), (
        f"imageUrl should start with 'http', got: {str(image_url)[:50]}"
    )
    logger.info("Image URL: %s...", str(image_url)[:80])
