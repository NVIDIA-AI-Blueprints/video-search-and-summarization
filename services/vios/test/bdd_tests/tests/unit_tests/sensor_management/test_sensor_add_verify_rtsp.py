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
Unit tests for the verifyRtsp pre-flight DESCRIBE check on POST /sensor/add.

Covers NVBug 6141772 ("Add Sensor accepts unreachable RTSP URL and incorrectly
marks the sensor as online"). The fix introduces an opt-in verifyRtsp flag:

  * verifyRtsp: true  -> server runs an RTSP DESCRIBE; unreachable URLs are
                          rejected with a 4xx and the sensor is not persisted.
  * verifyRtsp: false -> existing behaviour is preserved (sensor is accepted).
  * field omitted     -> defaults to false; same as the explicit-false case.

The unreachable URL points at 192.0.2.1 (TEST-NET-1, RFC 5737), which is
reserved for documentation/examples and is guaranteed routable-but-unanswered
on any conforming network — forcing the server's DESCRIBE attempt to time out,
which is exactly the failure mode verifyRtsp is built to detect. Loopback
addresses (e.g. 127.0.0.1:1) fail fast with ECONNREFUSED, which some RTSP
client paths treat as a transient/skip condition rather than "unreachable".
"""
import logging
import time
import uuid

import pytest
from pytest_bdd import given, scenarios, when, then, parsers

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
    api_post,
    api_delete,
)

logger = logging.getLogger(__name__)

scenarios(
    "../../../features/unit_tests/sensor_management/sensor_add_verify_rtsp.feature"
)

# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------

@given("the VST sensor management API is accessible")
def sensor_api_accessible(api_config: dict) -> None:
    assert api_config["base_url"], "Base URL must be configured"


UNREACHABLE_RTSP_URL = "rtsp://192.0.2.1:554/nonexistent"
TEST_SENSOR_NAME_PREFIX = "bdd-verifyrtsp-"


def _unique_name() -> str:
    return f"{TEST_SENSOR_NAME_PREFIX}{uuid.uuid4().hex[:12]}"


def _wipe_leftover_test_sensors(api_config: dict, unit_test_params: dict) -> None:
    """Delete any sensor created by an earlier verifyRtsp scenario.

    The three scenarios in this module all use the same UNREACHABLE_RTSP_URL
    and a name with TEST_SENSOR_NAME_PREFIX. The server treats sensorUrl as a
    uniqueness key, so when the rejection scenario fails (server didn't
    actually reject), the resulting persisted sensor blocks every later
    scenario with `InvalidParameterError: Sensor exists already`. Wiping
    leftover entries by URL OR name prefix makes each scenario self-healing.
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
        logger.warning("verifyRtsp cleanup: sensor/list call failed: %s", exc)
        return
    if resp.status_code != 200:
        logger.warning(
            "verifyRtsp cleanup: sensor/list returned %d", resp.status_code,
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
        if not (name.startswith(TEST_SENSOR_NAME_PREFIX) or url == UNREACHABLE_RTSP_URL):
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
                "verifyRtsp cleanup: deleted stale sensor %s (name=%s, url=%s, status=%d)",
                sid, name, url, del_resp.status_code,
            )
        except Exception as exc:
            logger.warning(
                "verifyRtsp cleanup: failed to delete sensor %s: %s", sid, exc,
            )


@pytest.fixture(autouse=True)
def _verify_rtsp_sensor_cleanup(api_config, unit_test_params):
    """Wipe leftover verifyRtsp sensors both before and after each scenario.

    Runs unconditionally even if the scenario's assertions fail, so a
    failed rejection-scenario never cascades into "Sensor exists already"
    on the next test.
    """
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
# When
# ---------------------------------------------------------------------------

@when("I POST to sensor/add with an unreachable RTSP URL and verifyRtsp set to true")
def post_add_with_verify_true(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    name = _unique_name()
    context.added_sensor_name = name
    body = {
        "name": name,
        "sensorUrl": UNREACHABLE_RTSP_URL,
        "location": "bdd-test",
        "tags": "bdd-verifyRtsp",
        "verifyRtsp": True,
    }
    _post_add_sensor(context, api_config, unit_test_params, body)


@when("I POST to sensor/add with an unreachable RTSP URL and no verifyRtsp flag")
def post_add_without_verify_flag(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    name = _unique_name()
    context.added_sensor_name = name
    body = {
        "name": name,
        "sensorUrl": UNREACHABLE_RTSP_URL,
        "location": "bdd-test",
        "tags": "bdd-verifyRtsp",
    }
    _post_add_sensor(context, api_config, unit_test_params, body)


@when("I POST to sensor/add with an unreachable RTSP URL and verifyRtsp set to false")
def post_add_with_verify_false(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    name = _unique_name()
    context.added_sensor_name = name
    body = {
        "name": name,
        "sensorUrl": UNREACHABLE_RTSP_URL,
        "location": "bdd-test",
        "tags": "bdd-verifyRtsp",
        "verifyRtsp": False,
    }
    _post_add_sensor(context, api_config, unit_test_params, body)


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("the sensor add response status is 4xx")
def assert_response_4xx(context: UnitTestContext) -> None:
    assert 400 <= context.status_code < 500, (
        f"Expected 4xx rejection for unreachable RTSP URL with verifyRtsp=true, "
        f"got {context.status_code}. Body: {str(context.response_json)[:500]}"
    )


@then("the sensor add response status is 200")
def assert_response_200(context: UnitTestContext) -> None:
    assert context.status_code == 200, (
        f"Expected 200, got {context.status_code}. "
        f"Body: {str(context.response_json)[:500]}"
    )
    sensor_id = (
        context.response_json.get("sensorId")
        if isinstance(context.response_json, dict) else None
    )
    assert sensor_id, (
        f"200 response did not include a sensorId; got: "
        f"{str(context.response_json)[:300]}"
    )
    context.first_sensor_id = sensor_id


@then("the unreachable sensor is not present in /sensor/list")
def assert_sensor_not_persisted(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    # Brief settle so the rejection has time to take effect (it shouldn't, but
    # we want to catch a race where the sensor is briefly persisted).
    time.sleep(1)
    resp = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/list",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    assert resp.status_code == 200, f"sensor/list failed: {resp.status_code}"
    sensor_list = resp.json()
    assert isinstance(sensor_list, list)

    name = context.added_sensor_name
    matches = [
        s for s in sensor_list
        if isinstance(s, dict) and s.get("name") == name
    ]
    if matches:
        # Unexpected: clean up so the next test run is not polluted.
        for s in matches:
            sid = s.get("sensorId")
            if sid:
                api_delete(
                    api_config["base_url"],
                    f"/vst/api/v1/sensor/{sid}",
                    verify_ssl=api_config.get("verify_ssl", False),
                    timeout=timeout,
                )
        pytest.fail(
            f"Unreachable sensor was persisted despite verifyRtsp=true: "
            f"name={name}, matches={matches}"
        )


@then("I clean up the added sensor")
def cleanup_added_sensor(
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
