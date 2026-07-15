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
Pytest configuration for tests/functional/.

Supports two modes:

  Black-box (CI) mode — pass ``--base-url``:
      pytest tests/functional/ -m test_in_ci --base-url http://localhost:38111

  In-process mode — omit ``--base-url`` (ViaTestServer spun up automatically):
      pytest tests/functional/ -v -m "not slow"
"""

import os

import pytest
import requests

_TEST_SERVER_PORT = 48100


def pytest_addoption(parser):
    """Add custom command-line options for the functional test suite."""
    try:
        parser.addoption(
            "--base-url",
            action="store",
            default=os.environ.get("VIA_BASE_URL", None),
            help="Base URL of a running VIA Engine API.  When omitted an in-process "
            "ViaTestServer is started automatically.",
        )
    except ValueError:
        # --base-url may already be registered when pytest collects
        # multiple test packages in the same session.
        pass
    try:
        parser.addoption(
            "--http-timeout",
            action="store",
            type=int,
            default=30,
            help="HTTP request timeout in seconds (default: 30)",
        )
    except ValueError:
        pass
    try:
        parser.addoption(
            "--response-truncate",
            action="store",
            type=int,
            default=2000,
            help="Truncate response body in logs (default: 2000, 0=unlimited)",
        )
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def base_url(request):
    """Return the base URL to test against.

    * If ``--base-url`` is provided, yield that URL (black-box / CI mode).
    * Otherwise spin up an in-process :class:`ViaTestServer` and yield its URL.
      The server is torn down at the end of the session.
    """
    url = request.config.getoption("--base-url")
    if url:
        yield url.rstrip("/")
        return

    # In-process mode: start a minimal server instance.
    # Requires VIA to be importable and the necessary env vars to be set.
    # Import lazily so the module can be collected in lightweight containers
    # (e.g. python:3.12-slim) that don't have the full VIA stack installed.
    from tests.common import ViaTestServer

    server_args = os.environ.get(
        "VIA_TEST_SERVER_ARGS",
        "--vlm-model-type openai-compat --disable-cv-pipeline --disable-guardrails",
    )
    # Enable dev API routes (/files, /files/{id}, etc.) for in-process test server.
    # This is controlled by env var only; VIA_DEV_API=true persists for the session.
    os.environ.setdefault("VIA_DEV_API", "true")
    # Skip ffprobe-based codec validation for uploaded files in test mode.
    # Functional tests use placeholder binary files; real codec validation
    # requires actual video/image assets which are not available in-process.
    os.environ.setdefault("VSS_SKIP_INPUT_MEDIA_VERIFICATION", "1")
    server = ViaTestServer(
        server_args=server_args,
        port=_TEST_SERVER_PORT,
        startup_timeout_sec=60,
    )
    try:
        server.start_server()
    except Exception as exc:
        pytest.skip(
            f"Could not start in-process ViaTestServer (missing credentials or GPU?): {exc}"
        )
    yield f"http://localhost:{_TEST_SERVER_PORT}"
    server.stop_server()


@pytest.fixture(scope="session")
def timeout(request):
    """Request timeout in seconds."""
    return request.config.getoption("--http-timeout")


@pytest.fixture(scope="session")
def response_truncate(request):
    """Response truncation limit for log output."""
    truncate = request.config.getoption("--response-truncate")
    return float("inf") if truncate == 0 else truncate


@pytest.fixture(scope="session")
def session():
    """Shared HTTP session with JSON content-type header."""
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
    return s


@pytest.fixture(scope="session")
def shared_state():
    """Mutable dict shared across tests within a session (e.g. to pass model_id)."""
    return {}
