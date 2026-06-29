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
Unit tests for the VST Sensor Management Service API.

Tests: sensor list, status, streams, info, QOS, system stats, timelines,
version, help, configuration.
"""
import logging

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
    validate_json_response,
    validate_list_response,
    validate_string_response,
    validate_help_response,
    extract_sensor_ids,
)

logger = logging.getLogger(__name__)

# A sensor ID that is guaranteed not to exist, used to exercise the error path.
NONEXISTENT_SENSOR_ID = "__nonexistent__"

scenarios("../../../features/unit_tests/sensor_management/sensor_management_api.feature")


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------

@given("the VST sensor management API is accessible")
def sensor_api_accessible(api_config: dict) -> None:
    assert api_config["base_url"], "Base URL must be configured"


@given("at least one sensor exists")
def at_least_one_sensor(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    """Fetch sensor list and store the first sensor ID."""
    timeout = unit_test_params.get("timeout", 30)
    resp = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/list",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    sensor_list = resp.json()
    assert isinstance(sensor_list, list), "Sensor list must be a JSON array"
    sensor_ids = extract_sensor_ids(sensor_list)
    assert len(sensor_ids) > 0, "No sensors available"
    context.first_sensor_id = sensor_ids[0]
    logger.info("Using sensor ID: %s", context.first_sensor_id)


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when("I request the list of sensors")
def request_sensor_list(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/list",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the status of all sensors")
def request_sensor_status_all(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/status",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the streams of all sensors")
def request_sensor_streams_all(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/streams",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the sensor management service version")
def request_sensor_version(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/version",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the sensor management service help")
def request_sensor_help(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/help",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the sensor management service configuration")
def request_sensor_configuration(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/configuration",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the QOS stats")
def request_sensor_qos(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/qos",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the system stats")
def request_system_stats(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/debug/system/stats",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the recording timelines for all sensors")
def request_sensor_timelines_all(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/timelines",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request streams for the first sensor")
def request_sensor_streams_by_id(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    sensor_id = context.first_sensor_id
    context.response = api_get(
        api_config["base_url"],
        f"/vst/api/v1/sensor/{sensor_id}/streams",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request status for the first sensor")
def request_sensor_status_by_id(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    sensor_id = context.first_sensor_id
    context.response = api_get(
        api_config["base_url"],
        f"/vst/api/v1/sensor/{sensor_id}/status",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request info for the first sensor")
def request_sensor_info_by_id(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    sensor_id = context.first_sensor_id
    context.response = api_get(
        api_config["base_url"],
        f"/vst/api/v1/sensor/{sensor_id}/info",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request timelines for the first sensor")
def request_sensor_timelines_by_id(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    sensor_id = context.first_sensor_id
    context.response = api_get(
        api_config["base_url"],
        f"/vst/api/v1/sensor/{sensor_id}/timelines",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request status for a non-existent sensor")
def request_status_nonexistent(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        f"/vst/api/v1/sensor/{NONEXISTENT_SENSOR_ID}/status",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when(parsers.parse('I request "{action}" for a non-existent sensor'))
def request_action_nonexistent(
    context: UnitTestContext, api_config: dict, unit_test_params: dict, action: str
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        f"/vst/api/v1/sensor/{NONEXISTENT_SENSOR_ID}/{action}",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("the sensor response status is 200")
def check_sensor_status_200(context: UnitTestContext) -> None:
    assert context.response.status_code == 200, (
        f"Expected 200, got {context.response.status_code}: {context.response.text[:500]}"
    )


@then("the sensor response status is 404")
def check_sensor_status_404(context: UnitTestContext) -> None:
    assert context.response.status_code == 404, (
        f"Expected 404 for a non-existent sensor, got {context.response.status_code}: "
        f"{context.response.text[:500]}"
    )


@then(parsers.parse('the sensor error body uses camelCase keys with error code "{code}"'))
def check_camelcase_error_envelope(context: UnitTestContext, code: str) -> None:
    data = context.response.json()
    assert isinstance(data, dict), (
        f"Error body must be a JSON object, got {type(data).__name__}: {context.response.text[:500]}"
    )
    assert "errorCode" in data, (
        f"Expected camelCase 'errorCode' key per the swagger, got keys {sorted(data)}: "
        f"{context.response.text[:500]}"
    )
    assert data["errorCode"] == code, (
        f"Expected errorCode '{code}', got '{data.get('errorCode')}': {context.response.text[:500]}"
    )
    assert "errorMessage" in data, (
        f"Expected camelCase 'errorMessage' key per the swagger, got keys {sorted(data)}: "
        f"{context.response.text[:500]}"
    )


@then('the sensor error body has no success "state" field')
def check_error_body_has_no_state(context: UnitTestContext) -> None:
    data = context.response.json()
    assert isinstance(data, dict), (
        f"Error body must be a JSON object, got {type(data).__name__}: {context.response.text[:500]}"
    )
    assert "state" not in data, (
        f"Error envelope must not embed a success 'state' field, got: {context.response.text[:500]}"
    )


@then("the sensor response is a valid JSON array")
def check_sensor_json_array(context: UnitTestContext) -> None:
    data = validate_list_response(context.response)
    logger.info("Received array with %d items", len(data))


@then("the sensor response is a valid version string")
def check_sensor_version_string(context: UnitTestContext) -> None:
    version = validate_string_response(context.response)
    assert len(version) > 0, "Version string is empty"
    logger.info("Service version: %s", version)


@then("the sensor response is a list of supported API paths")
def check_sensor_help_list(context: UnitTestContext) -> None:
    data = validate_help_response(context.response)
    logger.info("Supported APIs: %d", len(data))


@then("the sensor response contains configuration fields")
def check_sensor_configuration_fields(context: UnitTestContext) -> None:
    data = validate_json_response(context.response)
    assert isinstance(data, dict), "Configuration must be a JSON object"
    assert len(data) > 0, "Configuration is empty"
    logger.info("Configuration has %d fields", len(data))
