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
Regression test for bug 6216242.

The shared HTTP response-emission framework
(``src/framework/web/http_server/HttpServerRequestHandler.cpp``) writes a valid
JSON body for every non-file endpoint but unconditionally stamps it with
``Content-Type: text/plain``. Published swagger declares ``application/json``
for every operation, so OpenAPI SDKs and contract validators mis-handle the
body. This test asserts that JSON-body endpoints advertise the JSON
content type across all reachable microservices.

Against the unfixed base code these scenarios FAIL because the framework emits
``text/plain``; once the response emitter sets ``application/json`` for JSON
bodies they PASS.
"""
import logging

from pytest_bdd import scenarios, given, when, then, parsers

from ..unit_test_utils import UnitTestContext, api_get

logger = logging.getLogger(__name__)

scenarios("../../../features/unit_tests/content_type/json_content_type.feature")


@given("the VST REST API is accessible")
def rest_api_accessible(api_config: dict) -> None:
    assert api_config["base_url"], "Base URL must be configured"


@when(parsers.parse('I request the JSON endpoint "{path}"'))
def request_json_endpoint(
    context: UnitTestContext, api_config: dict, unit_test_params: dict, path: str
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        path,
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@then("the endpoint response status is 200")
def endpoint_status_200(context: UnitTestContext) -> None:
    assert context.response is not None, "No response captured"
    assert context.response.status_code == 200, (
        f"Expected status 200, got {context.response.status_code}\n"
        f"Body: {context.response.text[:500]}"
    )


@then("the endpoint response body parses as JSON")
def endpoint_body_is_json(context: UnitTestContext) -> None:
    # Body must be valid JSON regardless of the declared content type; this
    # confirms the endpoint genuinely emits a JSON body (so application/json is
    # the correct declaration) rather than incidental plain text.
    try:
        context.response_json = context.response.json()
    except ValueError as exc:
        raise AssertionError(
            f"Response body is not valid JSON: {exc}\n"
            f"Body: {context.response.text[:500]}"
        )


@then("the endpoint response Content-Type is application/json")
def endpoint_content_type_is_json(context: UnitTestContext) -> None:
    content_type = context.response.headers.get("Content-Type", "")
    assert "application/json" in content_type.lower(), (
        f"Expected Content-Type 'application/json' for JSON body, got "
        f"{content_type!r} (bug 6216242: framework emits text/plain for JSON "
        f"bodies)\nBody: {context.response.text[:500]}"
    )
