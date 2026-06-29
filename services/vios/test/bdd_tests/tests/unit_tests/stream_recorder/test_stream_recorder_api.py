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
Unit tests for the VST Stream Recorder Service API.

Tests: record streams list, version, help, configuration, recording timelines.
"""
import logging
import uuid

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
    api_post,
    api_delete,
    validate_json_response,
    validate_list_response,
    validate_string_response,
    validate_help_response,
)

logger = logging.getLogger(__name__)

scenarios("../../../features/unit_tests/stream_recorder/stream_recorder_api.feature")


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------

@given("the VST stream recorder API is accessible")
def recorder_api_accessible(api_config: dict) -> None:
    assert api_config["base_url"], "Base URL must be configured"


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when("I request the list of record streams")
def request_record_streams(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/record/streams",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the stream recorder service version")
def request_recorder_version(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/record/version",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the stream recorder service help")
def request_recorder_help(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/record/help",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the stream recorder service configuration")
def request_recorder_configuration(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/record/configuration",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the recording timelines for all record streams")
def request_record_timelines(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/record/timelines",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("the recorder response status is 200")
def check_recorder_status_200(context: UnitTestContext) -> None:
    assert context.response.status_code == 200, (
        f"Expected 200, got {context.response.status_code}: {context.response.text[:500]}"
    )


@then("the recorder response is a valid JSON array")
def check_recorder_json_array(context: UnitTestContext) -> None:
    data = validate_list_response(context.response)
    logger.info("Received array with %d items", len(data))


@then("the recorder response is a valid version string")
def check_recorder_version_string(context: UnitTestContext) -> None:
    version = validate_string_response(context.response)
    assert len(version) > 0, "Version string is empty"
    logger.info("Service version: %s", version)


@then("the recorder response is a list of supported API paths")
def check_recorder_help_list(context: UnitTestContext) -> None:
    data = validate_help_response(context.response)
    logger.info("Supported APIs: %d", len(data))


@then("the recorder response contains configuration fields")
def check_recorder_configuration_fields(context: UnitTestContext) -> None:
    data = validate_json_response(context.response)
    assert isinstance(data, dict), "Configuration must be a JSON object"
    assert len(data) > 0, "Configuration is empty"
    logger.info("Configuration has %d fields", len(data))


# ===========================================================================
# Regression for NVBug 6216297: record status field-name consistency.
#
# The per-stream endpoint GET /record/{streamId}/status returns the recording
# state under the camelCase key "recordingStatus", while the aggregate endpoint
# GET /record/status returns it under snake_case "recording_status". The
# project-wide swagger declares camelCase, so both endpoints must expose the
# state under "recordingStatus".
# ===========================================================================

# An RTSP URL is required because the recorder only accepts rtsp:// streams.
# TEST-NET-1 (RFC 5737) is reserved for documentation and never answers, but
# with verifyRtsp omitted (defaults to false) the sensor is still persisted,
# which is all this test needs: a present stream so the aggregate map is
# non-empty.
_TEMP_SENSOR_RTSP_URL = "rtsp://192.0.2.1:554/nvbug-6216297"
_TEMP_SENSOR_NAME_PREFIX = "bdd-6216297-"


class _StatusKeyContext:
    def __init__(self):
        self.stream_id = None
        self.added_sensor_id = None
        self.per_stream_json = None
        self.aggregate_json = None


@pytest.fixture
def context_6216297():
    return _StatusKeyContext()


def _list_sensor_ids(api_config, unit_test_params):
    timeout = unit_test_params.get("timeout", 30)
    resp = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/list",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    if resp.status_code != 200:
        return []
    try:
        sensors = resp.json()
    except ValueError:
        return []
    if not isinstance(sensors, list):
        return []
    return [
        s["sensorId"]
        for s in sensors
        if isinstance(s, dict) and s.get("sensorId")
    ]


@given("at least one record stream is present")
def ensure_stream_present(
    context_6216297: _StatusKeyContext, api_config: dict, unit_test_params: dict
) -> None:
    existing = _list_sensor_ids(api_config, unit_test_params)
    if existing:
        context_6216297.stream_id = existing[0]
        logger.info("Using existing stream %s for status check", context_6216297.stream_id)
        return

    # No sensors present: add a temporary one so the aggregate map is non-empty.
    timeout = unit_test_params.get("timeout", 30)
    name = f"{_TEMP_SENSOR_NAME_PREFIX}{uuid.uuid4().hex[:12]}"
    resp = api_post(
        api_config["base_url"],
        "/vst/api/v1/sensor/add",
        json_body={
            "name": name,
            "sensorUrl": _TEMP_SENSOR_RTSP_URL,
            "location": "bdd-test",
            "tags": "bdd-6216297",
        },
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    assert resp.status_code == 200, (
        f"Could not provision a test sensor (status {resp.status_code}): "
        f"{resp.text[:300]}"
    )
    try:
        sensor_id = resp.json().get("sensorId")
    except ValueError:
        sensor_id = None
    assert sensor_id, f"sensor/add did not return a sensorId: {resp.text[:300]}"
    context_6216297.stream_id = sensor_id
    context_6216297.added_sensor_id = sensor_id
    logger.info("Provisioned temporary stream %s for status check", sensor_id)


@when("I request the per-stream record status for that stream")
def request_per_stream_status(
    context_6216297: _StatusKeyContext, api_config: dict, unit_test_params: dict
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    resp = api_get(
        api_config["base_url"],
        f"/vst/api/v1/record/{context_6216297.stream_id}/status",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    assert resp.status_code == 200, (
        f"Per-stream /record/{context_6216297.stream_id}/status failed: "
        f"{resp.status_code} {resp.text[:300]}"
    )
    context_6216297.per_stream_json = resp.json()


@when("I request the aggregate record status for all streams")
def request_aggregate_status(
    context_6216297: _StatusKeyContext, api_config: dict, unit_test_params: dict
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    resp = api_get(
        api_config["base_url"],
        "/vst/api/v1/record/status",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    assert resp.status_code == 200, (
        f"Aggregate /record/status failed: {resp.status_code} {resp.text[:300]}"
    )
    context_6216297.aggregate_json = resp.json()


@then(parsers.parse('the per-stream record status uses the camelCase key "{key}"'))
def check_per_stream_key(context_6216297: _StatusKeyContext, key: str) -> None:
    body = context_6216297.per_stream_json
    assert isinstance(body, dict), (
        f"Per-stream status body must be a JSON object, got {type(body).__name__}: "
        f"{str(body)[:300]}"
    )
    assert key in body, (
        f"Per-stream /record/{{streamId}}/status must report state under "
        f"'{key}'; body was: {body}"
    )


@then(parsers.parse('every aggregate record status entry uses the camelCase key "{key}"'))
def check_aggregate_key(context_6216297: _StatusKeyContext, key: str) -> None:
    body = context_6216297.aggregate_json
    assert isinstance(body, dict), (
        f"Aggregate /record/status body must be a JSON object keyed by sensor id, "
        f"got {type(body).__name__}: {str(body)[:300]}"
    )
    assert body, (
        "Aggregate /record/status returned no entries; cannot verify the status "
        "key. Expected at least the stream provisioned by this test "
        f"({context_6216297.stream_id})."
    )

    offenders = {}
    for sid, entry in body.items():
        if not isinstance(entry, dict):
            offenders[sid] = entry
            continue
        if key not in entry:
            offenders[sid] = sorted(entry.keys())

    assert not offenders, (
        f"Aggregate /record/status entries must report state under the camelCase "
        f"key '{key}' to match the per-stream endpoint (NVBug 6216297). "
        f"Entries missing '{key}': {offenders}. Full body: {body}"
    )


@pytest.fixture(autouse=True)
def _cleanup_added_sensor(context_6216297, api_config, unit_test_params):
    yield
    sensor_id = getattr(context_6216297, "added_sensor_id", None)
    if not sensor_id:
        return
    timeout = unit_test_params.get("timeout", 30)
    try:
        resp = api_delete(
            api_config["base_url"],
            f"/vst/api/v1/sensor/{sensor_id}",
            verify_ssl=api_config.get("verify_ssl", False),
            timeout=timeout,
        )
        logger.info(
            "Cleaned up temporary sensor %s (status %d)",
            sensor_id, resp.status_code,
        )
    except Exception as exc:  # noqa: BLE001 - cleanup must never fail the test
        logger.warning("Failed to clean up temporary sensor %s: %s", sensor_id, exc)
