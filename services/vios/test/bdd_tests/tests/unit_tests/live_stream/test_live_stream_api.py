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
import time
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


# ---------------------------------------------------------------------------
# Regression for NVBug 6167266 ("Live Streams tag filter does not show/select
# tags created on VST sensors"). Tags persisted on a sensor record must be
# echoed back in the GET /api/v1/live/streams response so the Live Streams UI
# can populate and filter by those tags. Folded in from the standalone
# live_stream_sensor_tags feature/test.
# ---------------------------------------------------------------------------

TEST_SENSOR_NAME_PREFIX = "bdd-tags-6167266-"
TEST_RTSP_URL = "rtsp://192.0.2.33:554/tagged"
STREAMS_PATH = "/vst/api/v1/live/streams"


def _wipe_leftover_test_sensors(api_config: dict, unit_test_params: dict) -> None:
    """Delete any sensor created by this regression module.

    Keyed on the TEST_SENSOR_NAME_PREFIX so reruns are self-healing and the
    main sensor inventory is never touched.
    """
    base_url = api_config["base_url"]
    timeout = unit_test_params.get("timeout", 30)
    verify_ssl = api_config.get("verify_ssl", False)
    try:
        resp = api_get(
            base_url, "/vst/api/v1/sensor/list",
            verify_ssl=verify_ssl, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - cleanup must never raise
        logger.warning("tags cleanup: sensor/list call failed: %s", exc)
        return
    if resp.status_code != 200:
        logger.warning("tags cleanup: sensor/list returned %d", resp.status_code)
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
            api_delete(
                base_url, f"/vst/api/v1/sensor/{sid}",
                verify_ssl=verify_ssl, timeout=timeout,
            )
            logger.info("tags cleanup: deleted stale sensor %s (name=%s)", sid, name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tags cleanup: failed to delete sensor %s: %s", sid, exc)


@pytest.fixture(autouse=True)
def _tagged_sensor_cleanup(api_config, unit_test_params):
    """Wipe the regression sensor before and after the scenario."""
    _wipe_leftover_test_sensors(api_config, unit_test_params)
    yield
    _wipe_leftover_test_sensors(api_config, unit_test_params)


def _streams_for_sensor(streams_response, sensor_id):
    """Return the list of stream objects keyed under sensor_id.

    The /live/streams payload is a list of single-key objects:
    [ { "<sensorId>": [ {stream}, ... ] }, ... ].
    """
    result = []
    if not isinstance(streams_response, list):
        return result
    for item in streams_response:
        if not isinstance(item, dict):
            continue
        streams = item.get(sensor_id)
        if isinstance(streams, list):
            result.extend(s for s in streams if isinstance(s, dict))
        elif isinstance(streams, dict):
            result.append(streams)
    return result


@given("an RTSP sensor tagged with a unique tag has been added")
def add_tagged_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    name = f"{TEST_SENSOR_NAME_PREFIX}{uuid.uuid4().hex[:12]}"
    tag = f"view-{uuid.uuid4().hex[:8]}"
    body = {
        "name": name,
        "sensorUrl": TEST_RTSP_URL,
        "location": "bdd-test",
        "tags": tag,
    }
    resp = api_post(
        api_config["base_url"],
        "/vst/api/v1/sensor/add",
        json_body=body,
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    assert resp.status_code == 200, (
        f"Setup add-sensor failed: status={resp.status_code}, body={resp.text[:300]}"
    )
    payload = resp.json()
    sensor_id = payload.get("sensorId") if isinstance(payload, dict) else None
    assert sensor_id, f"add-sensor response missing sensorId: {payload!r}"
    context.first_sensor_id = sensor_id
    context.expected_tag = tag
    context.added_sensor_name = name
    logger.info("Added tagged sensor %s (name=%s, tag=%s)", sensor_id, name, tag)


@when("I request the list of live streams and wait for the tagged sensor")
def request_live_streams_until_present(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    sensor_id = context.first_sensor_id
    deadline = time.monotonic() + 30
    last_resp = None
    while time.monotonic() < deadline:
        last_resp = api_get(
            api_config["base_url"],
            STREAMS_PATH,
            verify_ssl=api_config.get("verify_ssl", False),
            timeout=timeout,
        )
        if last_resp.status_code == 200:
            try:
                data = last_resp.json()
            except ValueError:
                data = None
            if data is not None and _streams_for_sensor(data, sensor_id):
                break
        time.sleep(2)
    context.response = last_resp


@then("every live stream for the tagged sensor includes a tags field matching the sensor tag")
def check_streams_carry_tags(context: UnitTestContext) -> None:
    data = context.response.json()
    streams = _streams_for_sensor(data, context.first_sensor_id)
    assert streams, (
        f"Tagged sensor {context.first_sensor_id} produced no live stream "
        f"objects in {STREAMS_PATH}; cannot validate tags. Response: "
        f"{str(data)[:500]}"
    )
    for stream in streams:
        assert "tags" in stream, (
            f"Live stream object for sensor {context.first_sensor_id} is missing "
            f"the 'tags' field (NVBug 6167266). Stream keys: "
            f"{sorted(stream.keys())}; full object: {stream!r}"
        )
        assert stream["tags"] == context.expected_tag, (
            f"Live stream 'tags' mismatch for sensor {context.first_sensor_id}: "
            f"expected {context.expected_tag!r}, got {stream['tags']!r}"
        )
    logger.info(
        "Verified %d live stream object(s) for sensor %s carry tag %s",
        len(streams), context.first_sensor_id, context.expected_tag,
    )
