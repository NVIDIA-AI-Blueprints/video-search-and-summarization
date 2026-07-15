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
Functional tests for the VIA Engine MCP (Model Context Protocol) server.

The MCP server is started alongside the main REST API when
``LVS_ENABLE_MCP=true`` and ``LVS_MCP_PORT`` are set.

The MCP server uses SSE transport:
  - ``GET /sse``                          — opens the Server-Sent Events stream
  - ``POST /messages?session_id=<id>``   — sends JSON-RPC messages

Responses arrive on the SSE stream, not in the POST response body.
The MCP protocol also requires an ``initialize`` / ``notifications/initialized``
handshake before tool calls will be accepted.

Set ``LVS_MCP_PORT`` (default 38112) and start the server with
``LVS_ENABLE_MCP=true`` before running these tests.
"""

import json
import logging
import os
import threading
import time

import pytest
import requests

logger = logging.getLogger(__name__)

_MCP_PORT = int(os.environ.get("LVS_MCP_PORT", "38112"))
_SSE_TIMEOUT = 10  # seconds to wait for SSE session URL
_TOOL_TIMEOUT = 60  # seconds to wait for a tool-call response


def _mcp_base(base_url: str) -> str:
    """Derive the MCP base URL from the REST base URL."""
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.hostname}:{_MCP_PORT}"


@pytest.fixture(scope="module")
def mcp_session(base_url):
    """Verify the MCP server is reachable; fails with a clear message if not."""
    mcp_url = _mcp_base(base_url)
    s = requests.Session()

    fail_msg = (
        f"MCP server not enabled at {mcp_url} — "
        f"start server with LVS_ENABLE_MCP=true LVS_MCP_PORT={_MCP_PORT}"
    )
    try:
        probe = s.get(f"{mcp_url}/sse", stream=True, timeout=5)
        status = probe.status_code
        probe.close()
        if status != 200:
            pytest.fail(f"{fail_msg} (GET /sse returned {status})")
    except requests.ConnectionError as exc:
        pytest.fail(f"MCP server not reachable at {mcp_url}: {exc}")

    logger.info("MCP server reachable at %s", mcp_url)
    return s, mcp_url


# ---------------------------------------------------------------------------
# MCP SSE client with protocol handshake
# ---------------------------------------------------------------------------


class _McpSseClient:
    """Minimal synchronous MCP client over SSE transport.

    MCP SSE flow:
      1. GET /sse  →  server sends ``event: endpoint  data: /messages?session_id=X``
      2. POST initialize  →  response on SSE stream
      3. POST notifications/initialized  (no response)
      4. POST tools/call  →  response on SSE stream
    """

    def __init__(self, mcp_url: str):
        self._mcp_url = mcp_url
        self._session_url = ""
        self._responses: dict = {}
        self._lock = threading.Condition()
        self._next_id = 0
        self._session_ready = threading.Event()
        self._sse_thread = threading.Thread(target=self._read_sse, daemon=True)

    def start(self) -> None:
        """Open SSE stream and complete the MCP initialization handshake."""
        self._sse_thread.start()

        if not self._session_ready.wait(timeout=_SSE_TIMEOUT):
            raise RuntimeError(
                f"Timeout ({_SSE_TIMEOUT}s) waiting for MCP SSE session URL from {self._mcp_url}"
            )
        if not self._session_url:
            raise RuntimeError("MCP SSE stream closed without sending session URL")

        # Step 1: initialize
        init_id = self._alloc_id()
        self._post(
            {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "via-test-client", "version": "1.0.0"},
                },
            }
        )
        self._wait(init_id, timeout=10)
        logger.debug("MCP initialize handshake complete")

        # Step 2: confirm initialization (notification — no response expected)
        self._post(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Invoke an MCP tool and return the JSON-RPC response dict."""
        msg_id = self._alloc_id()
        self._post(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        )
        return self._wait(msg_id, timeout=_TOOL_TIMEOUT)

    # ------------------------------------------------------------------

    def _alloc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _post(self, payload: dict) -> None:
        resp = requests.post(self._session_url, json=payload, timeout=10)
        logger.debug(
            "POST %s id=%s → HTTP %d",
            self._session_url,
            payload.get("id"),
            resp.status_code,
        )

    def _wait(self, msg_id: int, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        with self._lock:
            while msg_id not in self._responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f"Timeout ({timeout}s) waiting for MCP response id={msg_id}")
                self._lock.wait(timeout=remaining)
            return self._responses.pop(msg_id)

    def _read_sse(self) -> None:
        try:
            resp = requests.get(
                f"{self._mcp_url}/sse",
                stream=True,
                timeout=_TOOL_TIMEOUT + 30,
                headers={"Accept": "text/event-stream"},
            )
            event_type = None
            for raw_line in resp.iter_lines():
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
                    if event_type == "endpoint":
                        url = self._mcp_url + data if data.startswith("/") else data
                        self._session_url = url
                        self._session_ready.set()
                    elif event_type == "message":
                        try:
                            msg = json.loads(data)
                            if "id" in msg:
                                with self._lock:
                                    self._responses[msg["id"]] = msg
                                    self._lock.notify_all()
                        except json.JSONDecodeError:
                            pass
                elif line == "":
                    event_type = None
        except Exception as exc:
            logger.debug("SSE reader thread exiting: %s", exc)
        finally:
            self._session_ready.set()  # unblock any waiter on error


def _call_mcp_tool(mcp_url: str, tool_name: str, arguments: dict) -> dict:
    """Open a fresh MCP SSE session, run the handshake, call the tool, return result."""
    client = _McpSseClient(mcp_url)
    client.start()
    return client.call_tool(tool_name, arguments)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_mcp_health_ready_tool_returns_ok(mcp_session):
    """MCP ``health_ready`` tool returns a successful response."""
    _, mcp_url = mcp_session
    result = _call_mcp_tool(mcp_url, "health_ready", {})

    logger.info("health_ready result: %s", result)
    assert "error" not in result, f"MCP returned error for health_ready: {result.get('error')}"
    assert "result" in result, f"MCP health_ready missing 'result': {result}"


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_mcp_list_models_tool_returns_model_list(mcp_session):
    """MCP ``list_models`` tool returns at least one model."""
    _, mcp_url = mcp_session
    result = _call_mcp_tool(mcp_url, "list_models", {})

    logger.info("list_models result: %s", str(result)[:500])
    assert "error" not in result, f"MCP returned error for list_models: {result.get('error')}"
    content = result.get("result", {}).get("content", [])
    assert len(content) > 0, "MCP list_models returned empty content"


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_mcp_get_metrics_tool_returns_data(mcp_session):
    """MCP ``get_metrics`` tool returns non-empty Prometheus-format metrics."""
    _, mcp_url = mcp_session
    result = _call_mcp_tool(mcp_url, "get_metrics", {})

    logger.info("get_metrics result keys: %s", list(result.keys()))
    assert "error" not in result, f"MCP returned error for get_metrics: {result.get('error')}"
    content = result.get("result", {}).get("content", [])
    assert len(content) > 0, "MCP get_metrics returned empty content"


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_mcp_summarize_video_tool_returns_summary(mcp_session):
    """MCP ``summarize_video`` tool returns a non-empty summary string."""
    _, mcp_url = mcp_session

    models_result = _call_mcp_tool(mcp_url, "list_models", {})
    content = models_result.get("result", {}).get("content", [])
    if not content:
        pytest.skip("No models available via MCP")

    try:
        models_data = json.loads(content[0].get("text", "{}"))
        model_id = models_data.get("data", [{}])[0].get("id", "")
    except Exception:
        model_id = ""

    if not model_id:
        pytest.skip("Could not parse model_id from MCP list_models response")

    logger.info("Using model: %s", model_id)
    result = _call_mcp_tool(
        mcp_url,
        "summarize_video",
        {
            "url": (
                "https://artifactory.nvidia.com/artifactory/"
                "sw-ds-generic-bld-local/via-engine/media/bp_preview/its_264.mp4"
            ),
            "model": model_id,
            "scenario": "traffic monitoring",
            "events": ["accident", "emergency vehicle"],
            "chunk_duration": 10,
            "max_tokens": 256,
        },
    )

    logger.info("summarize_video result: %s", str(result)[:500])
    assert "error" not in result, f"MCP summarize_video returned error: {result.get('error')}"
    content = result.get("result", {}).get("content", [])
    assert len(content) > 0, "MCP summarize_video returned empty content"
