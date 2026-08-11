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

"""Shared fixtures for NVStreamer unit tests (own HTTP port, not VST ingress)."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict
from urllib.parse import urlparse

import pytest
import requests

from scripts.stream_prerequisite import nvstreamer_endpoints

logger = logging.getLogger(__name__)


def _rewrite_localhost_host(endpoint: str, api_base_url: str, port: int) -> str:
    """If config points at localhost but --base-url is remote, use that host:port."""
    parsed_ep = urlparse(endpoint)
    if parsed_ep.hostname not in ("localhost", "127.0.0.1", None):
        return endpoint.rstrip("/")
    parsed_api = urlparse(api_base_url)
    if not parsed_api.hostname or parsed_api.hostname in ("localhost", "127.0.0.1"):
        return endpoint.rstrip("/")
    scheme = parsed_api.scheme or "http"
    return f"{scheme}://{parsed_api.hostname}:{port}".rstrip("/")


@pytest.fixture(scope="session")
def nvstreamer_api_config(config: dict, api_config: dict) -> Dict[str, Any]:
    """Resolve NVStreamer base URL (env → config → derived from --base-url host)."""
    endpoints = nvstreamer_endpoints(config)
    assert endpoints, "No NVStreamer endpoints resolved"
    ns = config.get("nvstreamer", {}) if isinstance(config, dict) else {}
    port = int(ns.get("port_base", 31000))
    base = endpoints[0]
    if not os.environ.get("NVSTREAMER_ENDPOINTS"):
        base = _rewrite_localhost_host(base, api_config.get("base_url", ""), port)

    verify_ssl = api_config.get("verify_ssl", False)
    try:
        resp = requests.get(f"{base}/v1/ready", timeout=5, verify=verify_ssl)
    except requests.RequestException as exc:
        pytest.skip(f"NVStreamer not reachable at {base}: {exc}")
    if resp.status_code != 200:
        pytest.skip(
            f"NVStreamer /v1/ready at {base} returned {resp.status_code}; "
            f"set NVSTREAMER_ENDPOINTS or nvstreamer.host/port_base in config.json"
        )
    logger.info("NVStreamer unit tests targeting %s", base)
    return {"base_url": base, "verify_ssl": verify_ssl}
