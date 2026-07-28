# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Tests for orchestrator MCP TLS worker helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch

from pydantic import AnyHttpUrl
import pytest

from vss_agents.orchestrator import mcp_ssl_worker as ssl_mod


def test_https_enabled_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, raising=False)
    assert ssl_mod.https_enabled() is False


def test_https_enabled_true_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "TRUE")
    assert ssl_mod.https_enabled() is True
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "false")
    assert ssl_mod.https_enabled() is False


def test_https_enabled_rejects_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "yes")
    with pytest.raises(ValueError, match="must be 'true' or 'false'"):
        ssl_mod.https_enabled()


def test_resolve_ssl_paths_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "false")
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_CERTFILE, "/tmp/cert.pem")
    assert ssl_mod.resolve_ssl_paths() == (None, None)


def test_resolve_ssl_paths_requires_both(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_text("cert", encoding="utf-8")
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "true")
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_CERTFILE, str(cert))
    monkeypatch.delenv(ssl_mod.ORCHESTRATOR_KEYFILE, raising=False)
    with pytest.raises(ValueError, match=ssl_mod.ORCHESTRATOR_KEYFILE):
        ssl_mod.resolve_ssl_paths()


def test_resolve_ssl_paths_returns_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "true")
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_CERTFILE, str(cert))
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_KEYFILE, str(key))
    assert ssl_mod.resolve_ssl_paths() == (str(cert), str(key))


@pytest.mark.asyncio
async def test_create_mcp_server_substitutes_ssl_class_and_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "false")
    captured: dict[str, Any] = {}

    class FakeParentServer:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(auth=None)

    async def fake_super_create(self: Any) -> FakeParentServer:
        captured["fastmcp_during_super"] = ssl_mod.mcp_worker.FastMCP
        return FakeParentServer()

    worker = ssl_mod.SSLMCPWorker.__new__(ssl_mod.SSLMCPWorker)
    worker.front_end_config = SimpleNamespace(host="localhost", port=9901)
    original = ssl_mod.mcp_worker.FastMCP

    with patch.object(ssl_mod.MCPFrontEndPluginWorker, "create_mcp_server", fake_super_create):
        server = await ssl_mod.SSLMCPWorker.create_mcp_server(worker)

    assert captured["fastmcp_during_super"] is ssl_mod._SSLFastMCP
    assert ssl_mod.mcp_worker.FastMCP is original
    assert isinstance(server, FakeParentServer)


@pytest.mark.asyncio
async def test_create_mcp_server_rewrites_auth_url_when_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "true")
    auth = MagicMock()

    class FakeParentServer:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(auth=auth)

    async def fake_super_create(self: Any) -> FakeParentServer:
        return FakeParentServer()

    worker = ssl_mod.SSLMCPWorker.__new__(ssl_mod.SSLMCPWorker)
    worker.front_end_config = SimpleNamespace(host="0.0.0.0", port=9901)

    with patch.object(ssl_mod.MCPFrontEndPluginWorker, "create_mcp_server", fake_super_create):
        await ssl_mod.SSLMCPWorker.create_mcp_server(worker)

    assert auth.resource_server_url == AnyHttpUrl("https://0.0.0.0:9901")
