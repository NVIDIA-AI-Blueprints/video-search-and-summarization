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
Unit tests for the VST Storage Management Service API.

Tests: storage size, info, version, help, configuration, file list, protected files.
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
    validate_dict_response,
)

logger = logging.getLogger(__name__)

scenarios("../../../features/unit_tests/storage_management/storage_management_api.feature")


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------

@given("the VST storage management API is accessible")
def storage_api_accessible(api_config: dict) -> None:
    assert api_config["base_url"], "Base URL must be configured"


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when("I request the total storage size")
def request_storage_size(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/size",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the storage info")
def request_storage_info(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/info",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the storage management service version")
def request_storage_version(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/version",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the storage management service help")
def request_storage_help(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/help",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the storage management service configuration")
def request_storage_configuration(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/configuration",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the list of all media files")
def request_file_list(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/file/list",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the protected file list")
def request_protected_files(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/file/protected",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("the storage response status is 200")
def check_storage_status_200(context: UnitTestContext) -> None:
    assert context.response.status_code == 200, (
        f"Expected 200, got {context.response.status_code}: {context.response.text[:500]}"
    )


@then("the storage info contains total used and available fields")
def check_storage_info_fields(context: UnitTestContext) -> None:
    data = validate_dict_response(context.response)
    expected_fields = ["total", "used", "available"]
    for field in expected_fields:
        assert field in data, f"Missing field '{field}' in storage info: {list(data.keys())}"
    logger.info("Storage: total=%s, used=%s, available=%s",
                data.get("total"), data.get("used"), data.get("available"))


@then("the storage response is a valid version string")
def check_storage_version_string(context: UnitTestContext) -> None:
    version = validate_string_response(context.response)
    assert len(version) > 0, "Version string is empty"
    logger.info("Service version: %s", version)


@then("the storage response is a list of supported API paths")
def check_storage_help_list(context: UnitTestContext) -> None:
    data = validate_help_response(context.response)
    logger.info("Supported APIs: %d", len(data))


@then("the storage response contains configuration fields")
def check_storage_configuration_fields(context: UnitTestContext) -> None:
    data = validate_json_response(context.response)
    assert isinstance(data, dict), "Configuration must be a JSON object"
    assert len(data) > 0, "Configuration is empty"
    logger.info("Configuration has %d fields", len(data))
