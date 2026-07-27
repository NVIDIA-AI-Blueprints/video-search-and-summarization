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

import pytest
from pytest_bdd import scenarios, given, when, then

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
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


@when("I request the sensor service version")
def request_sensor_version(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.sensor_version_response = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/version",
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


def _extract_version(response) -> str:
    """Pull the version value out of a version-endpoint response.

    Handles both the plain-string form and the object forms used across
    microservices (Record MS: ``{"recorder_version": ...}``; Sensor MS:
    ``{"type": ..., "version": ...}``).
    """
    assert response is not None, "Version endpoint was not queried"
    assert response.status_code == 200, (
        f"Expected status 200, got {response.status_code}: {response.text[:500]}"
    )
    data = response.json()
    if isinstance(data, str):
        return data.strip()
    assert isinstance(data, dict), (
        f"Unexpected version payload type {type(data).__name__}: {data!r}"
    )
    for key in ("recorder_version", "version", "Version"):
        if key in data and isinstance(data[key], str):
            return data[key].strip()
    raise AssertionError(f"No version field found in response: {data!r}")


@then('the recorder reported version is not the placeholder "0.0.1"')
def check_recorder_version_not_placeholder(context: UnitTestContext) -> None:
    recorder_version = _extract_version(context.response)
    logger.info("Record MS reported version: %s", recorder_version)
    assert recorder_version != "0.0.1", (
        "Record MS /record/version returned the hardcoded placeholder '0.0.1' "
        "instead of the deployed build version (bug 6303142)"
    )


@then("the recorder reported version matches the sensor reported build version")
def check_recorder_version_matches_build(context: UnitTestContext) -> None:
    recorder_version = _extract_version(context.response)
    sensor_version = _extract_version(context.sensor_version_response)
    logger.info(
        "Record MS version: %s | Sensor MS (build) version: %s",
        recorder_version, sensor_version,
    )
    assert sensor_version and sensor_version != "0.0.1", (
        f"Sensor MS version looks invalid ('{sensor_version}'); cannot use it "
        "as the build-version source of truth"
    )
    assert recorder_version == sensor_version, (
        f"Record MS version mismatch: recorder reports '{recorder_version}' but "
        f"the deployed build version is '{sensor_version}' (bug 6303142)"
    )


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
