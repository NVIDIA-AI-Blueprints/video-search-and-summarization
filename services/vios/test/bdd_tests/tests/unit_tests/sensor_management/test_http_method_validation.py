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
Unit tests for uniform HTTP method validation (NVBug 6267433).

Wrong HTTP verbs on routed VST endpoints used to produce an inconsistent mix of
outcomes: read-only sensor endpoints executed their GET logic and returned 200
(POST /sensor/streams even returned the full payload), sensor/add returned 400,
and storage/info & storage/size returned 501. The fix makes every handler reject
an unsupported verb uniformly with 405 MethodNotAllowedError plus the standard
{error_code, error_message} body.

Every wrong-verb request exercised here is non-mutating: a write verb against a
read-only endpoint, or a read verb against a write-only endpoint, must be
rejected before the handler runs, so a passing (fixed) build is never mutated by
this test. On an unfixed build the read-only sensor cases would return 200, the
sensor/add cases 400, and storage/info & storage/size 501 — each failing the
405 assertion, which is the regression signal.
"""
import logging

import requests
from pytest_bdd import given, scenarios, when, then, parsers

from ..unit_test_utils import UnitTestContext

logger = logging.getLogger(__name__)

scenarios(
    "../../../features/unit_tests/sensor_management/http_method_validation.feature"
)


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------

@given("the VST API is accessible")
def vst_api_accessible(api_config: dict) -> None:
    assert api_config["base_url"], "Base URL must be configured"


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when(parsers.parse('I send a {method} request to "{path}"'))
def send_request_with_method(
    context: UnitTestContext,
    api_config: dict,
    unit_test_params: dict,
    method: str,
    path: str,
) -> None:
    """Issue an arbitrary-verb request and capture the response on the context.

    No request body is sent: the bug repro used bodyless wrong-verb requests,
    and an empty body keeps body-bearing methods (POST/PUT) past the
    dispatcher's schema validation so they reach the handler's method check.
    """
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    url = f"{base_url}{path}"

    logger.info("%s %s", method, url)
    resp = requests.request(
        method,
        url,
        timeout=timeout,
        verify=verify_ssl,
    )
    context.response = resp
    context.status_code = resp.status_code
    try:
        context.response_json = resp.json()
    except ValueError:
        context.response_json = None
    logger.info(
        "Response: status=%d body=%s",
        resp.status_code,
        str(context.response_json)[:300],
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then(parsers.parse("the response status code is {expected_status:d}"))
def assert_status_code(context: UnitTestContext, expected_status: int) -> None:
    assert context.status_code == expected_status, (
        f"Expected status {expected_status}, got {context.status_code}. "
        f"Body: {str(context.response_json)[:500]}"
    )


@then(parsers.parse('the response error_code is "{expected_code}"'))
def assert_error_code(context: UnitTestContext, expected_code: str) -> None:
    assert isinstance(context.response_json, dict), (
        f"Expected a JSON object body carrying error_code, got: "
        f"{str(context.response_json)[:300]}"
    )
    actual = context.response_json.get("error_code")
    assert actual == expected_code, (
        f"Expected error_code '{expected_code}', got '{actual}'. "
        f"Body: {str(context.response_json)[:500]}"
    )


@then("the response error_message is not empty")
def assert_error_message_present(context: UnitTestContext) -> None:
    assert isinstance(context.response_json, dict), (
        f"Expected a JSON object body carrying error_message, got: "
        f"{str(context.response_json)[:300]}"
    )
    message = context.response_json.get("error_message")
    assert isinstance(message, str) and message.strip(), (
        f"Expected a non-empty error_message, got: {message!r}. "
        f"Body: {str(context.response_json)[:500]}"
    )
