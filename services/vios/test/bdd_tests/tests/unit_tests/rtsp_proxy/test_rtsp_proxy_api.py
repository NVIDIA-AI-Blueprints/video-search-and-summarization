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
Unit tests for the VST RTSP Proxy Stream Service API.

Tests: streams list, configuration, proxy info.
"""
import logging

import pytest
from pytest_bdd import scenarios, given, when, then

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
    validate_json_response,
    validate_list_response,
    validate_dict_response,
)

logger = logging.getLogger(__name__)

scenarios("../../../features/unit_tests/rtsp_proxy/rtsp_proxy_api.feature")


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------

@given("the VST RTSP proxy API is accessible")
def rtsp_proxy_api_accessible(api_config: dict) -> None:
    assert api_config["base_url"], "Base URL must be configured"


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when("I request the list of proxy streams")
def request_proxy_streams(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/proxy/streams",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the RTSP proxy service configuration")
def request_proxy_configuration(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/proxy/configuration",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the RTSP proxy info")
def request_proxy_info(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/proxy/info",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("the proxy response status is 200")
def check_proxy_status_200(context: UnitTestContext) -> None:
    assert context.response.status_code == 200, (
        f"Expected 200, got {context.response.status_code}: {context.response.text[:500]}"
    )


@then("the proxy response is a valid JSON array")
def check_proxy_json_array(context: UnitTestContext) -> None:
    data = validate_list_response(context.response)
    logger.info("Received array with %d items", len(data))


@then("the proxy response contains configuration fields")
def check_proxy_configuration_fields(context: UnitTestContext) -> None:
    data = validate_json_response(context.response)
    assert isinstance(data, dict), "Configuration must be a JSON object"
    assert len(data) > 0, "Configuration is empty"
    logger.info("Configuration has %d fields", len(data))


@then("the proxy info contains server entries and stats")
def check_proxy_info_fields(context: UnitTestContext) -> None:
    data = validate_dict_response(context.response)

    server_keys = [k for k in data if k.startswith("server")]
    assert len(server_keys) > 0, (
        f"Expected at least one serverN entry, got keys: {list(data.keys())}"
    )

    first_server = data[server_keys[0]]
    assert isinstance(first_server, dict), (
        f"Expected serverN value to be a dict, got {type(first_server).__name__}"
    )
    assert "urlPrefix" in first_server, (
        f"Missing 'urlPrefix' in {server_keys[0]}: {first_server}"
    )

    assert "stats" in data, f"Missing 'stats' entry in proxy info: {list(data.keys())}"
    stats = data["stats"]
    assert "activeClientSessions" in stats, (
        f"Missing 'activeClientSessions' in stats: {stats}"
    )

    logger.info("Proxy info: %d server(s), activeSessions=%s",
                len(server_keys), stats.get("activeClientSessions"))
