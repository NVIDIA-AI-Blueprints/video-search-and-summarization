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
Unit tests for POST /sensor/add duplicate-sensor rejection responses.

The error_message must distinguish a URL/IP conflict from a pure name
collision. URL/IP conflicts (including the URL+name match case) are reported
as "Sensor exists already"; name-only collisions are reported as "User given
name is invalid or already exists".

The conflict URLs use TEST-NET-1 (192.0.2.x, RFC 5737) so the sensor is
accepted (verifyRtsp is not set, so the server skips RTSP DESCRIBE) but never
streams to anything real. Each scenario cleans up its persisted sensor in
both the success and failure paths.
"""
import logging
import uuid

import pytest
from pytest_bdd import given, parsers, scenarios, when, then

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
    api_post,
    api_delete,
)

logger = logging.getLogger(__name__)

scenarios(
    "../../../features/unit_tests/sensor_management/sensor_add_duplicate.feature"
)


TEST_SENSOR_NAME_PREFIX = "bdd-dup-"
PRIMARY_RTSP_URL = "rtsp://192.0.2.10:554/primary"
ALT_RTSP_URL = "rtsp://192.0.2.20:554/alt"


def _unique_name() -> str:
    return f"{TEST_SENSOR_NAME_PREFIX}{uuid.uuid4().hex[:12]}"


def _wipe_leftover_test_sensors(api_config: dict, unit_test_params: dict) -> None:
    """Delete any sensor created by an earlier duplicate-add scenario.

    The scenarios in this module all reuse a small set of TEST-NET-1 URLs and
    a name prefix, so a previous run that crashed mid-scenario can leave a
    persisted sensor that blocks the next run with
    "Sensor exists already". Wiping by URL OR name prefix makes each scenario
    self-healing.
    """
    base_url = api_config["base_url"]
    timeout = unit_test_params.get("timeout", 30)
    verify_ssl = api_config.get("verify_ssl", False)
    try:
        resp = api_get(
            base_url, "/vst/api/v1/sensor/list",
            verify_ssl=verify_ssl, timeout=timeout,
        )
    except Exception as exc:
        logger.warning("duplicate-add cleanup: sensor/list call failed: %s", exc)
        return
    if resp.status_code != 200:
        logger.warning(
            "duplicate-add cleanup: sensor/list returned %d", resp.status_code,
        )
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
        if not (
            name.startswith(TEST_SENSOR_NAME_PREFIX)
            or url in (PRIMARY_RTSP_URL, ALT_RTSP_URL)
        ):
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
                "duplicate-add cleanup: deleted stale sensor %s (name=%s, url=%s, status=%d)",
                sid, name, url, del_resp.status_code,
            )
        except Exception as exc:
            logger.warning(
                "duplicate-add cleanup: failed to delete sensor %s: %s", sid, exc,
            )


@pytest.fixture(autouse=True)
def _duplicate_add_sensor_cleanup(api_config, unit_test_params):
    """Wipe leftover duplicate-add sensors both before and after each scenario."""
    _wipe_leftover_test_sensors(api_config, unit_test_params)
    yield
    _wipe_leftover_test_sensors(api_config, unit_test_params)


def _post_add_sensor(
    context: UnitTestContext,
    api_config: dict,
    unit_test_params: dict,
    body: dict,
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)

    resp = api_post(
        base_url,
        "/vst/api/v1/sensor/add",
        json_body=body,
        verify_ssl=verify_ssl,
        timeout=timeout,
    )
    context.response = resp
    context.status_code = resp.status_code
    try:
        context.response_json = resp.json()
    except ValueError:
        context.response_json = None
    logger.info(
        "Add-sensor response: status=%d, body=%s",
        resp.status_code,
        str(context.response_json)[:300],
    )


# ---------------------------------------------------------------------------
# Background / Given
# ---------------------------------------------------------------------------

@given("the VST sensor management API is accessible")
def sensor_api_accessible(api_config: dict) -> None:
    assert api_config["base_url"], "Base URL must be configured"


@given("I have added an RTSP sensor and captured its identity")
def add_first_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    name = _unique_name()
    body = {
        "name": name,
        "sensorUrl": PRIMARY_RTSP_URL,
        "location": "bdd-test",
        "tags": "bdd-duplicate-add",
    }
    _post_add_sensor(context, api_config, unit_test_params, body)
    assert context.status_code == 200, (
        f"Setup add-sensor failed: status={context.status_code}, "
        f"body={str(context.response_json)[:300]}"
    )
    assert isinstance(context.response_json, dict), (
        f"Setup add-sensor response not an object: {context.response_json!r}"
    )
    sensor_id = context.response_json.get("sensorId")
    assert sensor_id, (
        f"Setup add-sensor response missing sensorId: {context.response_json!r}"
    )
    context.first_sensor_id = sensor_id
    context.added_sensor_name = name


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when("I POST to sensor/add with the same RTSP URL but a different name")
def post_same_url_diff_name(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    body = {
        "name": _unique_name(),
        "sensorUrl": PRIMARY_RTSP_URL,
        "location": "bdd-test",
        "tags": "bdd-duplicate-add",
    }
    _post_add_sensor(context, api_config, unit_test_params, body)


@when("I POST to sensor/add with a different RTSP URL but the same name")
def post_diff_url_same_name(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    body = {
        "name": context.added_sensor_name,
        "sensorUrl": ALT_RTSP_URL,
        "location": "bdd-test",
        "tags": "bdd-duplicate-add",
    }
    _post_add_sensor(context, api_config, unit_test_params, body)


@when("I POST to sensor/add with the same RTSP URL and the same name")
def post_same_url_same_name(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    body = {
        "name": context.added_sensor_name,
        "sensorUrl": PRIMARY_RTSP_URL,
        "location": "bdd-test",
        "tags": "bdd-duplicate-add",
    }
    _post_add_sensor(context, api_config, unit_test_params, body)


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("the sensor add response status is 4xx")
def assert_response_4xx(context: UnitTestContext) -> None:
    assert 400 <= context.status_code < 500, (
        f"Expected 4xx rejection for duplicate sensor, "
        f"got {context.status_code}. Body: {str(context.response_json)[:500]}"
    )


@then(parsers.parse('the response error_message contains "{expected}"'))
def assert_error_message_contains(context: UnitTestContext, expected: str) -> None:
    assert isinstance(context.response_json, dict), (
        f"Expected JSON object response, got: {context.response_json!r}"
    )
    error_message = context.response_json.get("error_message", "")
    assert expected in error_message, (
        f"error_message did not contain expected substring.\n"
        f"  expected substring: {expected!r}\n"
        f"  actual error_message: {error_message!r}\n"
        f"  full body: {context.response_json!r}"
    )


@then("I clean up the first added sensor")
def cleanup_first_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    sensor_id = context.first_sensor_id
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
        logger.warning(
            "Cleanup delete returned %d for sensor %s: %s",
            resp.status_code, sensor_id, resp.text[:300],
        )
