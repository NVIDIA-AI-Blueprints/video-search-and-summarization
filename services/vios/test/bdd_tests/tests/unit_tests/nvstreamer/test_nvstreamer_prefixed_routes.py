# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Public-route tests for NVStreamer behind a prefix-stripping proxy."""

import re
from urllib.parse import urljoin

import requests

from ..unit_test_utils import api_get, validate_list_response

PREFIX = "/nvstreamer"


def _timeout(unit_test_params: dict) -> int:
    return unit_test_params.get("timeout", 30)


def test_exact_prefix_redirects_to_trailing_slash(
    api_config: dict, unit_test_params: dict
) -> None:
    response = requests.get(
        f'{api_config["base_url"]}{PREFIX}',
        allow_redirects=False,
        verify=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    assert response.status_code == 308
    assert response.headers["Location"] == f"{PREFIX}/"


def test_prefixed_ui_config_and_asset_load(
    api_config: dict, unit_test_params: dict
) -> None:
    index = api_get(
        api_config["base_url"],
        f"{PREFIX}/",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    assert index.status_code == 200

    config = api_get(
        api_config["base_url"],
        f"{PREFIX}/runtime-config.js",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    assert config.status_code == 200
    assert '"basePath":"/nvstreamer"' in config.text

    match = re.search(r'src="([^"]+\.js)"', index.text)
    assert match is not None
    asset_url = urljoin(f'{api_config["base_url"]}{PREFIX}/', match.group(1))
    asset = requests.get(
        asset_url,
        verify=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    assert asset.status_code == 200


def test_prefixed_rest_api_is_available(
    api_config: dict, unit_test_params: dict
) -> None:
    response = api_get(
        api_config["base_url"],
        f"{PREFIX}/api/v1/live/streams",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=_timeout(unit_test_params),
    )
    validate_list_response(response)
