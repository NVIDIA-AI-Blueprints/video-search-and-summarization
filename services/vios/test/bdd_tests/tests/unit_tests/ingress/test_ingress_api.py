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
Basic unit tests for the VST ingress (nginx gateway).

The ingress has no business logic of its own -- it terminates HTTP on port
30888 and proxies requests to the backend microservices. These tests verify the
gateway is up and routing correctly:

  * its own /health endpoint returns 200,
  * sensor APIs are proxied to sensor-ms,
  * stream APIs are proxied to the streamprocessing-ms monolith,
  * an unknown route is answered by the gateway (a 4xx, not a dead connection).
"""
import logging

from ..unit_test_utils import api_get, validate_list_response

logger = logging.getLogger(__name__)


def _timeout(unit_test_params: dict) -> int:
    return unit_test_params.get("timeout", 30)


def test_ingress_health_endpoint_returns_200(api_config: dict, unit_test_params: dict) -> None:
    """The gateway's own /health endpoint responds 200 when the stack is up."""
    response = api_get(
        api_config["base_url"],
        "/health",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    assert response.status_code == 200, (
        f"ingress /health returned {response.status_code} (gateway not healthy)"
    )


def test_ingress_proxies_sensor_service(api_config: dict, unit_test_params: dict) -> None:
    """Sensor APIs route through the gateway to sensor-ms and return a list."""
    response = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/list",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    # validate_list_response asserts HTTP 200 + JSON array (may be empty).
    validate_list_response(response)


def test_ingress_proxies_streamprocessing_service(api_config: dict, unit_test_params: dict) -> None:
    """Live-stream APIs route through the gateway to streamprocessing-ms."""
    response = api_get(
        api_config["base_url"],
        "/vst/api/v1/live/streams",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    validate_list_response(response)


def test_ingress_returns_client_error_for_unknown_route(api_config: dict, unit_test_params: dict) -> None:
    """An unknown path is answered by the gateway with a 4xx, proving it is up
    and routing (as opposed to a connection refused / 5xx upstream failure)."""
    response = api_get(
        api_config["base_url"],
        "/vst/api/v1/__nonexistent_ingress_probe__",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    assert 400 <= response.status_code < 500, (
        f"expected a 4xx for an unknown route, got {response.status_code}"
    )
