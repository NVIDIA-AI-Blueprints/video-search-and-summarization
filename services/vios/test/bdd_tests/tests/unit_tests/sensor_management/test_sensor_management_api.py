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


# ---------------------------------------------------------------------------
# NVBug 6155392: deterministic camera-name ordering of GET /sensor/list.
#
# GET /sensor/list must return sensors sorted by camera name regardless of add
# order. Root cause: each sensor is keyed by a random UUID and readSensorDetails
# runs "SELECT * ... WHERE device_id = $1" with no ORDER BY, so the list comes
# back in UUID/heap order. This scenario adds "Camera_*" sensors in reverse
# order and asserts the API returns them in ascending camera-name order. The
# sensor URLs use TEST-NET-1 (192.0.2.x, RFC 5737), so sensors are accepted but
# never stream; each added sensor is cleaned up before and after the scenario.
# ---------------------------------------------------------------------------

SENSOR_LIST_PATH = "/vst/api/v1/sensor/list"
SENSOR_ADD_PATH = "/vst/api/v1/sensor/add"

# A unique-per-run prefix isolates this scenario's sensors from any others on
# the device. Because the prefix is identical for every sensor we add, the
# ascending order of the full names is determined entirely by the "Camera_*"
# suffix, mirroring the camera-name ordering described in the bug.
RUN_TAG = uuid.uuid4().hex[:8]
TEST_SENSOR_NAME_PREFIX = f"bdd-order-{RUN_TAG}-"

# Camera-name suffixes drawn from the bug's 28-camera dataset. "Camera" (no
# numeric suffix) is included because it appeared in the reported list and is
# a natural ordering edge case ("Camera" sorts before "Camera_08").
CAMERA_SUFFIXES = [
    "Camera",
    "Camera_08",
    "Camera_10",
    "Camera_11",
    "Camera_15",
    "Camera_18",
    "Camera_19",
    "Camera_20",
]


def _full_name(suffix: str) -> str:
    return f"{TEST_SENSOR_NAME_PREFIX}{suffix}"


def _sensor_url(suffix: str) -> str:
    # Distinct per sensor: the server treats sensorUrl as a uniqueness key.
    return f"rtsp://192.0.2.1:554/{RUN_TAG}/{suffix}"


def _wipe_leftover_test_sensors(api_config: dict, unit_test_params: dict) -> None:
    """Delete any sensor created by this ordering scenario (self-healing)."""
    base_url = api_config["base_url"]
    timeout = unit_test_params.get("timeout", 30)
    verify_ssl = api_config.get("verify_ssl", False)
    try:
        resp = api_get(
            base_url, SENSOR_LIST_PATH, verify_ssl=verify_ssl, timeout=timeout,
        )
    except Exception as exc:
        logger.warning("name-order cleanup: sensor/list call failed: %s", exc)
        return
    if resp.status_code != 200:
        logger.warning("name-order cleanup: sensor/list returned %d", resp.status_code)
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
        if not name.startswith(TEST_SENSOR_NAME_PREFIX):
            continue
        sid = sensor.get("sensorId")
        if not sid:
            continue
        try:
            del_resp = api_delete(
                base_url, f"/vst/api/v1/sensor/{sid}",
                verify_ssl=verify_ssl, timeout=timeout,
            )
            logger.info(
                "name-order cleanup: deleted stale sensor %s (name=%s, status=%d)",
                sid, name, del_resp.status_code,
            )
        except Exception as exc:
            logger.warning("name-order cleanup: failed to delete sensor %s: %s", sid, exc)


@pytest.fixture
def _name_order_sensor_cleanup(api_config, unit_test_params):
    """Wipe this scenario's sensors both before and after the scenario."""
    _wipe_leftover_test_sensors(api_config, unit_test_params)
    yield
    _wipe_leftover_test_sensors(api_config, unit_test_params)


@given("a set of camera sensors added in non-sequential name order")
def add_cameras_out_of_order(
    context: UnitTestContext,
    api_config: dict,
    unit_test_params: dict,
    _name_order_sensor_cleanup,
) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    # Add in reverse-sorted order so the persisted/insertion order is the
    # opposite of the expected camera-name order. This guarantees the pre-fix
    # list (heap/UUID order) is not accidentally already sorted by name.
    add_order = list(reversed(CAMERA_SUFFIXES))
    context.added_camera_names = [_full_name(s) for s in CAMERA_SUFFIXES]

    for suffix in add_order:
        name = _full_name(suffix)
        body = {
            "name": name,
            "sensorUrl": _sensor_url(suffix),
            "location": "bdd-test",
            "tags": "bdd-name-order",
        }
        resp = api_post(
            base_url, SENSOR_ADD_PATH,
            json_body=body, verify_ssl=verify_ssl, timeout=timeout,
        )
        assert resp.status_code == 200, (
            f"Failed to add sensor {name}: status={resp.status_code}, "
            f"body={resp.text[:300]}"
        )
        logger.info("Added sensor %s (url=%s)", name, _sensor_url(suffix))


@then("the camera sensors I added appear in ascending camera-name order")
def assert_cameras_in_name_order(context: UnitTestContext) -> None:
    assert context.response.status_code == 200, (
        f"sensor/list failed: status={context.response.status_code}, "
        f"body={context.response.text[:300]}"
    )
    sensor_list = context.response.json()
    assert isinstance(sensor_list, list), (
        f"Expected a JSON array, got {type(sensor_list).__name__}"
    )

    expected_names = set(context.added_camera_names)

    # Names of our test sensors, in the order the API returned them.
    observed = [
        s.get("name")
        for s in sensor_list
        if isinstance(s, dict) and s.get("name") in expected_names
    ]

    # All added sensors must be present (the bug also reported only a subset
    # registering; here we additionally guard that none are dropped).
    assert set(observed) == expected_names, (
        f"sensor/list did not return all added camera sensors.\n"
        f"Missing: {sorted(expected_names - set(observed))}\n"
        f"Observed: {observed}"
    )

    expected_order = sorted(expected_names)
    assert observed == expected_order, (
        "sensor/list returned camera sensors in a non-deterministic order "
        "instead of ascending camera-name order (NVBug 6155392).\n"
        f"Observed order: {observed}\n"
        f"Expected order: {expected_order}"
    )
