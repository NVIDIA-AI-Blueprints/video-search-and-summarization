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
Regression test for bug 6267433.

VST read-only API endpoints accept POST/PUT/DELETE and return 200 instead of
405. The shared HttpServerRequestHandler dispatch layer only checks that the
verb is a syntactically valid HTTP method, never that the verb is *allowed*
for the matched route, so GET-only handlers execute their GET logic for any
verb. This test drives the running service over HTTP and asserts the method
validation contract from the bug's "Expected O/P":

  * Unsupported methods on a routed path return 405 (not 200/400/404/501).
  * Each method-rejection response carries an ``Allow`` header.
  * Method-rejection bodies use the consistent ``error_code``/``error_message``
    schema (same as the existing ``MethodNotAllowedError``).
  * OPTIONS preflight is handled (200/204/405 + Allow), not 404.
  * GET on a read-only endpoint still succeeds (the fix must not over-reject).

On the unmodified base code the first scenario outline fails because
``POST/PUT/DELETE`` on the read-only sensor endpoints return 200, and the
OPTIONS scenario fails because OPTIONS returns 404. After the fix all
scenarios pass.
"""
import logging

import requests
from pytest_bdd import scenarios, given, when, then, parsers

from ..unit_test_utils import UnitTestContext

logger = logging.getLogger(__name__)

scenarios("../../../features/unit_tests/sensor_management/http_method_validation.feature")


def _api_request(
    base_url: str,
    method: str,
    path: str,
    verify_ssl: bool = False,
    timeout: int = 30,
) -> requests.Response:
    """Issue an arbitrary-verb request against the API (GET/POST/PUT/DELETE/OPTIONS)."""
    url = f"{base_url}{path}"
    logger.info("%s %s", method, url)
    response = requests.request(
        method,
        url,
        timeout=timeout,
        verify=verify_ssl,
    )
    logger.info("Response: %d (%d bytes)", response.status_code, len(response.content))
    return response


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------

@given("the VST sensor management API is accessible")
def sensor_api_accessible(api_config: dict) -> None:
    assert api_config["base_url"], "Base URL must be configured"


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when(parsers.parse('I send a "{method}" request to "{path}"'))
def send_request(
    context: UnitTestContext,
    api_config: dict,
    unit_test_params: dict,
    method: str,
    path: str,
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = _api_request(
        api_config["base_url"],
        method,
        path,
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then(parsers.parse("the API response status is {expected:d}"))
def check_status(context: UnitTestContext, expected: int) -> None:
    assert context.response is not None, "No response captured"
    assert context.response.status_code == expected, (
        f"Expected {expected}, got {context.response.status_code}: "
        f"{context.response.text[:500]}"
    )


@then(parsers.parse("the API response status is one of {codes}"))
def check_status_one_of(context: UnitTestContext, codes: str) -> None:
    allowed = {int(c.strip()) for c in codes.split(",")}
    assert context.response is not None, "No response captured"
    assert context.response.status_code in allowed, (
        f"Expected one of {sorted(allowed)}, got {context.response.status_code}: "
        f"{context.response.text[:500]}"
    )


@then("the response carries an Allow header")
def check_allow_header(context: UnitTestContext) -> None:
    assert context.response is not None, "No response captured"
    allow = context.response.headers.get("Allow")
    assert allow, (
        "Method-rejection / preflight response must include an 'Allow' header "
        f"listing supported methods; headers were: {dict(context.response.headers)}"
    )
    logger.info("Allow header: %s", allow)


@then("the response body uses the consistent error schema")
def check_error_schema(context: UnitTestContext) -> None:
    assert context.response is not None, "No response captured"
    try:
        body = context.response.json()
    except ValueError as exc:  # pragma: no cover - body must be JSON
        raise AssertionError(
            f"Method-rejection body must be JSON, got: {context.response.text[:500]}"
        ) from exc
    assert isinstance(body, dict), (
        f"Method-rejection body must be a JSON object, got {type(body).__name__}: "
        f"{context.response.text[:500]}"
    )
    assert "error_code" in body and "error_message" in body, (
        "Method-rejection body must use the consistent error schema "
        f"(error_code/error_message); got: {body}"
    )
    logger.info("Error schema: %s", body)
