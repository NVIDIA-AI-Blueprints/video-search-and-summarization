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
Unit tests for the VST Live Stream Service API.

Tests non-WebRTC endpoints: streams list, version, help, configuration, picture URL.
"""
import logging

import pytest
from pytest_bdd import scenarios, given, when, then

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
    validate_json_response,
    validate_list_response,
    validate_string_response,
    validate_help_response,
    extract_stream_ids,
)

logger = logging.getLogger(__name__)

scenarios("../../../features/unit_tests/live_stream/live_stream_api.feature")


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------

@given("the VST live stream API is accessible")
def live_stream_api_accessible(api_config: dict) -> None:
    """Verify base URL is configured."""
    assert api_config["base_url"], "Base URL must be configured"


@given("at least one live stream exists")
def at_least_one_live_stream(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    """Fetch live streams and ensure at least one exists."""
    timeout = unit_test_params.get("timeout", 30)
    resp = api_get(
        api_config["base_url"],
        "/vst/api/v1/live/streams",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    data = resp.json()
    stream_ids = extract_stream_ids(data)
    assert len(stream_ids) > 0, "No live streams available"
    context.first_stream_id = stream_ids[0]


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when("I request the list of live streams")
def request_live_streams(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/live/streams",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the live stream service version")
def request_live_version(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/live/version",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the live stream service help")
def request_live_help(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/live/help",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the live stream service configuration")
def request_live_configuration(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/live/configuration",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request a live picture URL for the first stream")
def request_live_picture_url(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    stream_id = context.first_stream_id
    context.response = api_get(
        api_config["base_url"],
        f"/vst/api/v1/live/stream/{stream_id}/picture/url",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("the response status is 200")
def check_status_200(context: UnitTestContext) -> None:
    assert context.response.status_code == 200, (
        f"Expected 200, got {context.response.status_code}: {context.response.text[:500]}"
    )


@then("the response is a valid JSON array")
def check_json_array(context: UnitTestContext) -> None:
    data = validate_list_response(context.response)
    logger.info("Received array with %d items", len(data))


@then("the response is a valid version string")
def check_version_string(context: UnitTestContext) -> None:
    version = validate_string_response(context.response)
    assert len(version) > 0, "Version string is empty"
    logger.info("Service version: %s", version)


@then("the response is a list of supported API paths")
def check_help_list(context: UnitTestContext) -> None:
    data = validate_help_response(context.response)
    logger.info("Supported APIs: %d", len(data))


@then("the response contains configuration fields")
def check_configuration_fields(context: UnitTestContext) -> None:
    data = validate_json_response(context.response)
    assert isinstance(data, dict), "Configuration must be a JSON object"
    assert len(data) > 0, "Configuration is empty"
    logger.info("Configuration has %d fields", len(data))


@then("the response contains a picture URL")
def check_picture_url(context: UnitTestContext) -> None:
    data = validate_json_response(context.response)
    assert isinstance(data, dict), "Picture URL response must be a JSON object"
    assert "imageUrl" in data, f"Missing 'imageUrl' in response: {list(data.keys())}"
    assert "streamId" in data, f"Missing 'streamId' in response: {list(data.keys())}"
    logger.info("Picture URL: %s", data["imageUrl"])
