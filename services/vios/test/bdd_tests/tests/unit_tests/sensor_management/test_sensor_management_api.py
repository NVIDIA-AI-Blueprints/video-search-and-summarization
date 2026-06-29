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
import uuid

import pytest
from pytest_bdd import scenarios, given, when, then

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
    api_post,
    api_delete,
    validate_json_response,
    validate_list_response,
    validate_string_response,
    validate_help_response,
    extract_sensor_ids,
)

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("the sensor response status is 200")
def check_sensor_status_200(context: UnitTestContext) -> None:
    assert context.response.status_code == 200, (
        f"Expected 200, got {context.response.status_code}: {context.response.text[:500]}"
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


@then("the configuration required array fields are JSON arrays")
def check_configuration_required_arrays(context: UnitTestContext) -> None:
    """Bug 6177255: required, non-nullable array fields in the GetConfiguration
    swagger schema must be JSON arrays, never null, even when no values are set."""
    data = validate_json_response(context.response)
    assert isinstance(data, dict), "Configuration must be a JSON object"
    for field in ("ntpServers", "deviceDiscoveryInterfaces"):
        assert field in data, f"Configuration is missing required field '{field}'"
        assert isinstance(data[field], list), (
            f"Configuration field '{field}' must be a JSON array per the swagger "
            f"contract, got {type(data[field]).__name__} ({data[field]!r}); "
            f"empty configuration must serialize as [] not null"
        )
    logger.info(
        "ntpServers=%r deviceDiscoveryInterfaces=%r",
        data["ntpServers"],
        data["deviceDiscoveryInterfaces"],
    )
# ---------------------------------------------------------------------------
# Regression: GET /sensor/{id}/network must not 500 for an RTSP sensor
# (NVBug 6164112). A plain RTSP/native sensor has no ONVIF session, so the
# unsupported case must return a structured non-500 response, never a
# 500 VMSInternalError.
# ---------------------------------------------------------------------------

_NETWORK_INFO_RTSP_URL = "rtsp://192.0.2.30:554/network-info-regression"
_NETWORK_INFO_NAME_PREFIX = "bdd-network-info-"


def _wipe_leftover_network_info_sensors(api_config: dict, unit_test_params: dict) -> None:
    """Delete any sensor left behind by an earlier run of the network-info scenario."""
    base_url = api_config["base_url"]
    timeout = unit_test_params.get("timeout", 30)
    verify_ssl = api_config.get("verify_ssl", False)
    try:
        resp = api_get(base_url, "/vst/api/v1/sensor/list", verify_ssl=verify_ssl, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - best-effort cleanup
        logger.warning("network-info cleanup: sensor/list call failed: %s", exc)
        return
    if resp.status_code != 200:
        return
    try:
        sensors = resp.json()
    except ValueError:
        return
    if not isinstance(sensors, list):
        return
    for sensor in sensors:
        if not isinstance(sensor, dict):
            continue
        name = sensor.get("name") or ""
        url = sensor.get("sensorUrl") or ""
        if not (name.startswith(_NETWORK_INFO_NAME_PREFIX) or url == _NETWORK_INFO_RTSP_URL):
            continue
        sid = sensor.get("sensorId")
        if not sid:
            continue
        try:
            api_delete(base_url, f"/vst/api/v1/sensor/{sid}", verify_ssl=verify_ssl, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            logger.warning("network-info cleanup: failed to delete %s: %s", sid, exc)


@given("I have added an RTSP sensor and captured its identity")
def add_rtsp_sensor_for_network_info(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    _wipe_leftover_network_info_sensors(api_config, unit_test_params)
    timeout = unit_test_params.get("timeout", 30)
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    name = f"{_NETWORK_INFO_NAME_PREFIX}{uuid.uuid4().hex[:12]}"
    body = {
        "name": name,
        "sensorUrl": _NETWORK_INFO_RTSP_URL,
        "location": "bdd-test",
        "tags": "bdd-network-info",
    }
    resp = api_post(base_url, "/vst/api/v1/sensor/add", json_body=body, verify_ssl=verify_ssl, timeout=timeout)
    assert resp.status_code == 200, (
        f"Precondition failed: could not add RTSP sensor, got {resp.status_code}: {resp.text[:500]}"
    )
    try:
        sensor_id = resp.json().get("sensorId")
    except ValueError:
        sensor_id = None
    assert sensor_id, f"sensor/add did not return a sensorId: {resp.text[:300]}"
    context.first_sensor_id = sensor_id
    logger.info("Added RTSP sensor %s (name=%s)", sensor_id, name)


@when("I request network info for that sensor")
def request_network_info(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        f"/vst/api/v1/sensor/{context.first_sensor_id}/network",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    try:
        context.response_json = context.response.json()
    except ValueError:
        context.response_json = None
    logger.info(
        "network response: status=%d, body=%s",
        context.response.status_code, str(context.response_json)[:300],
    )


@then("the network response status is not 500")
def assert_network_status_not_500(context: UnitTestContext) -> None:
    assert context.response.status_code != 500, (
        "GET /sensor/{id}/network returned 500 for an RTSP sensor; an unsupported "
        f"sensor type must be reported with a clear non-500 response. Body: {context.response.text[:500]}"
    )


@then("the network response error_code is not VMSInternalError")
def assert_network_error_code_not_internal(context: UnitTestContext) -> None:
    body = context.response_json
    if isinstance(body, dict):
        error_code = body.get("error_code") or body.get("errorCode")
        assert error_code != "VMSInternalError", (
            "GET /sensor/{id}/network reported VMSInternalError for an RTSP sensor; the "
            f"unsupported case must use a distinct structured error code. Body: {context.response.text[:500]}"
        )


@then("I clean up the network-info test sensor")
def cleanup_network_info_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    sensor_id = getattr(context, "first_sensor_id", None)
    if not sensor_id:
        return
    timeout = unit_test_params.get("timeout", 30)
    resp = api_delete(
        api_config["base_url"],
        f"/vst/api/v1/sensor/{sensor_id}",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    if resp.status_code != 200:
        logger.warning("Cleanup delete returned %d for sensor %s", resp.status_code, sensor_id)
