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
Shared fixtures for unit tests (API tests) across all VST microservices.
"""
import logging
from urllib.parse import urlparse

import pytest

from .unit_test_utils import UnitTestContext

logger = logging.getLogger(__name__)

MCP_DEFAULT_PORT = 8001


@pytest.fixture(scope="function")
def context():
    """Create a fresh context for each scenario."""
    ctx = UnitTestContext()
    logger.info("Created new unit test context")
    return ctx


@pytest.fixture(scope="session")
def unit_test_params(config):
    """Get unit test parameters from config.

    If mcp_url is not explicitly set, it is derived from api.base_url
    by extracting the host and using port 8001 with the /mcp path.
    """
    params = config.get("tests", {}).get("unit_tests", {}).get("test_parameters", {
        "timeout": 30,
    })

    mcp_url = params.get("mcp_url", "")
    needs_derive = not mcp_url or "<change-to-your-host>" in mcp_url

    if needs_derive:
        base_url = config.get("api", {}).get("base_url", "")
        if base_url:
            parsed = urlparse(base_url)
            host = parsed.hostname or "localhost"
            params["mcp_url"] = f"http://{host}:{MCP_DEFAULT_PORT}/mcp"
            logger.info("Derived mcp_url from api.base_url: %s", params["mcp_url"])

    return params
