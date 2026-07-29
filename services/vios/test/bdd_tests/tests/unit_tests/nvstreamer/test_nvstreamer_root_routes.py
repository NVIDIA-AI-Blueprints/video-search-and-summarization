# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Direct-port routing tests for NVStreamer root mode."""

import os

import pytest

from ..unit_test_utils import api_get, validate_list_response

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NVSTREAMER_ROUTE_TESTS") != "1",
    reason="Set RUN_NVSTREAMER_ROUTE_TESTS=1 to run disabled NVStreamer route tests",
)


def _timeout(unit_test_params: dict) -> int:
    return unit_test_params.get("timeout", 30)


def test_root_mode_serves_ui_and_root_config(
    api_config: dict, unit_test_params: dict
) -> None:
    index = api_get(
        api_config["base_url"],
        "/",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    assert index.status_code == 200

    config = api_get(
        api_config["base_url"],
        "/runtime-config.js",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    assert config.status_code == 200
    assert '"basePath":""' in config.text


def test_root_mode_serves_unprefixed_api(
    api_config: dict, unit_test_params: dict
) -> None:
    response = api_get(
        api_config["base_url"],
        "/api/v1/live/streams",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    validate_list_response(response)


def test_root_mode_does_not_add_nvstreamer_prefix(
    api_config: dict, unit_test_params: dict
) -> None:
    for path in ("/nvstreamer/", "/nvstreamer/api/v1/live/streams"):
        response = api_get(
            api_config["base_url"],
            path,
            verify_ssl=api_config.get("verify_ssl", False),
            timeout=_timeout(unit_test_params),
        )
        assert response.status_code == 404, f"{path} returned {response.status_code}"


def test_root_mode_health_probes_remain_available(
    api_config: dict, unit_test_params: dict
) -> None:
    for path in ("/v1/ready", "/v1/live", "/v1/startup"):
        response = api_get(
            api_config["base_url"],
            path,
            verify_ssl=api_config.get("verify_ssl", False),
            timeout=_timeout(unit_test_params),
        )
        assert response.status_code == 200, f"{path} returned {response.status_code}"
