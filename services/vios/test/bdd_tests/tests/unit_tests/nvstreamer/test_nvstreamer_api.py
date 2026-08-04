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
Unit tests for the NVStreamer container (file-to-RTSP republisher).

These hit NVStreamer's own HTTP port (default 31000), not the VST ingress
(30888). They feed ``reports/unit_tests/nvstreamer.csv`` / ``vst-nvstreamer.xml``
via ``scripts/run_unit_tests.sh`` so the dashboard gets a container row for
nvstreamer alongside sensor / streamprocessing / ingress.

NVStreamer uses ``/api/v1/...`` (no ``/vst`` prefix required). Identity check:
``/api/v1/sensor/version`` returns ``{"type": "streamer", ...}``.

Shared ``nvstreamer_api_config`` lives in ``conftest.py``.
"""
from __future__ import annotations

from ..unit_test_utils import api_get, validate_help_response, validate_json_response, validate_list_response


def _timeout(unit_test_params: dict) -> int:
    return int(unit_test_params.get("timeout", 30))


def test_nvstreamer_ready_probes_return_200(
    nvstreamer_api_config: dict, unit_test_params: dict
) -> None:
    """K8s/compose health probes stay up."""
    timeout = _timeout(unit_test_params)
    for path in ("/v1/ready", "/v1/live", "/v1/startup"):
        response = api_get(
            nvstreamer_api_config["base_url"],
            path,
            verify_ssl=nvstreamer_api_config.get("verify_ssl", False),
            timeout=timeout,
        )
        assert response.status_code == 200, f"{path} returned {response.status_code}"


def test_nvstreamer_sensor_version_reports_streamer_type(
    nvstreamer_api_config: dict, unit_test_params: dict
) -> None:
    """Version identity: type must be streamer (not vst)."""
    response = api_get(
        nvstreamer_api_config["base_url"],
        "/api/v1/sensor/version",
        verify_ssl=nvstreamer_api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    data = validate_json_response(response)
    assert data.get("type") == "streamer", (
        f"expected type=streamer (NVStreamer), got {data!r}"
    )
    assert data.get("version"), f"missing version in {data!r}"


def test_nvstreamer_sensor_list_returns_array(
    nvstreamer_api_config: dict, unit_test_params: dict
) -> None:
    response = api_get(
        nvstreamer_api_config["base_url"],
        "/api/v1/sensor/list",
        verify_ssl=nvstreamer_api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    validate_list_response(response)


def test_nvstreamer_sensor_help_lists_endpoints(
    nvstreamer_api_config: dict, unit_test_params: dict
) -> None:
    response = api_get(
        nvstreamer_api_config["base_url"],
        "/api/v1/sensor/help",
        verify_ssl=nvstreamer_api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    validate_help_response(response)


def test_nvstreamer_sensor_configuration_returns_object(
    nvstreamer_api_config: dict, unit_test_params: dict
) -> None:
    response = api_get(
        nvstreamer_api_config["base_url"],
        "/api/v1/sensor/configuration",
        verify_ssl=nvstreamer_api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    data = validate_json_response(response)
    assert isinstance(data, dict) and data, "sensor configuration should be a non-empty object"


def test_nvstreamer_live_streams_returns_array(
    nvstreamer_api_config: dict, unit_test_params: dict
) -> None:
    response = api_get(
        nvstreamer_api_config["base_url"],
        "/api/v1/live/streams",
        verify_ssl=nvstreamer_api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    validate_list_response(response)


def test_nvstreamer_live_version_reports_streamer_type(
    nvstreamer_api_config: dict, unit_test_params: dict
) -> None:
    response = api_get(
        nvstreamer_api_config["base_url"],
        "/api/v1/live/version",
        verify_ssl=nvstreamer_api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    data = validate_json_response(response)
    assert data.get("type") == "streamer", f"expected type=streamer, got {data!r}"


def test_nvstreamer_storage_version_returns_object(
    nvstreamer_api_config: dict, unit_test_params: dict
) -> None:
    response = api_get(
        nvstreamer_api_config["base_url"],
        "/api/v1/storage/version",
        verify_ssl=nvstreamer_api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    data = validate_json_response(response)
    assert data.get("storage_management_version"), (
        f"missing storage_management_version in {data!r}"
    )


def test_nvstreamer_ui_index_and_runtime_config(
    nvstreamer_api_config: dict, unit_test_params: dict
) -> None:
    """Root UI and runtime-config.js are served in root (direct-port) mode."""
    timeout = _timeout(unit_test_params)
    base = nvstreamer_api_config["base_url"]
    verify = nvstreamer_api_config.get("verify_ssl", False)

    index = api_get(base, "/", verify_ssl=verify, timeout=timeout)
    assert index.status_code == 200, f"UI index returned {index.status_code}"

    config_js = api_get(base, "/runtime-config.js", verify_ssl=verify, timeout=timeout)
    assert config_js.status_code == 200, (
        f"runtime-config.js returned {config_js.status_code}"
    )
    assert "basePath" in config_js.text, (
        f"runtime-config.js missing basePath: {config_js.text[:200]!r}"
    )
