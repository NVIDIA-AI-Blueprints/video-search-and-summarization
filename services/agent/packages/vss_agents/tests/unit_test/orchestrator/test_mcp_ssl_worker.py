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
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
from nat.data_models.config import Config
from nat.data_models.config import GeneralConfig
from nat.plugins.mcp.server.front_end_config import MCPFrontEndConfig
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


def _make_worker(host: str = "localhost", port: int = 9901, base_path: str | None = None) -> Any:
    worker = ssl_mod.SSLMCPWorker.__new__(ssl_mod.SSLMCPWorker)
    worker.front_end_config = SimpleNamespace(host=host, port=port, base_path=base_path)
    return worker


@pytest.mark.asyncio
async def test_create_mcp_server_substitutes_ssl_class_and_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "false")
    captured: dict[str, Any] = {}

    # Mirrors NAT: resolve FastMCP through the module global at call time.
    async def fake_super_create(self: Any) -> Any:
        captured["fastmcp_during_super"] = ssl_mod.mcp_worker.FastMCP
        return ssl_mod.mcp_worker.FastMCP(name="test")

    original = ssl_mod.mcp_worker.FastMCP

    with patch.object(ssl_mod.MCPFrontEndPluginWorker, "create_mcp_server", fake_super_create):
        server = await ssl_mod.SSLMCPWorker.create_mcp_server(_make_worker())

    assert captured["fastmcp_during_super"] is ssl_mod._SSLFastMCP
    assert ssl_mod.mcp_worker.FastMCP is original
    assert isinstance(server, ssl_mod._SSLFastMCP)


@pytest.mark.asyncio
async def test_create_mcp_server_uses_real_nat_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real NAT parent so a release that stops reading the module global fails here.

    This one calls ``MCPFrontEndPluginWorker.create_mcp_server`` itself and asserts the
    returned instance is ``_SSLFastMCP`` — the same contract the runtime guard on
    ``SSLMCPWorker.create_mcp_server`` enforces when HTTPS is enabled.
    """
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "true")
    original = ssl_mod.mcp_worker.FastMCP

    front_end = MCPFrontEndConfig(
        name="test-orchestrator-mcp",
        host="127.0.0.1",
        port=9901,
        debug=False,
        server_auth=None,
    )
    worker = ssl_mod.SSLMCPWorker(Config(general=GeneralConfig(front_end=front_end)))

    server = await worker.create_mcp_server()

    assert isinstance(server, ssl_mod._SSLFastMCP)
    assert ssl_mod.mcp_worker.FastMCP is original


@pytest.mark.asyncio
async def test_create_mcp_server_rewrites_auth_url_when_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "true")
    auth = MagicMock()

    async def fake_super_create(self: Any) -> Any:
        server = ssl_mod.mcp_worker.FastMCP(name="test")
        server.settings.auth = auth
        return server

    with patch.object(ssl_mod.MCPFrontEndPluginWorker, "create_mcp_server", fake_super_create):
        await ssl_mod.SSLMCPWorker.create_mcp_server(_make_worker(host="0.0.0.0"))

    assert auth.resource_server_url == AnyHttpUrl("https://0.0.0.0:9901")


@pytest.mark.asyncio
async def test_create_mcp_server_rejects_non_fastmcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "false")

    async def fake_super_create(self: Any) -> Any:
        return SimpleNamespace(settings=SimpleNamespace(auth=None))

    with patch.object(ssl_mod.MCPFrontEndPluginWorker, "create_mcp_server", fake_super_create):
        with pytest.raises(RuntimeError, match="non-FastMCP server"):
            await ssl_mod.SSLMCPWorker.create_mcp_server(_make_worker())


@pytest.mark.asyncio
async def test_create_mcp_server_rejects_stock_fastmcp_when_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A NAT version that bypasses the module global must fail loudly, not serve HTTP."""
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "true")

    async def fake_super_create(self: Any) -> Any:
        return FastMCP(name="test")

    with patch.object(ssl_mod.MCPFrontEndPluginWorker, "create_mcp_server", fake_super_create):
        with pytest.raises(RuntimeError, match="TLS-capable FastMCP subclass"):
            await ssl_mod.SSLMCPWorker.create_mcp_server(_make_worker())


@pytest.mark.asyncio
async def test_create_mcp_server_rejects_base_path_with_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "true")

    async def fake_super_create(self: Any) -> Any:
        raise AssertionError("must fail before delegating to NAT")

    with patch.object(ssl_mod.MCPFrontEndPluginWorker, "create_mcp_server", fake_super_create):
        with pytest.raises(ValueError, match="base_path"):
            await ssl_mod.SSLMCPWorker.create_mcp_server(_make_worker(base_path="/orchestrator"))


@pytest.mark.asyncio
async def test_create_mcp_server_allows_base_path_without_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "false")

    async def fake_super_create(self: Any) -> Any:
        return ssl_mod.mcp_worker.FastMCP(name="test")

    with patch.object(ssl_mod.MCPFrontEndPluginWorker, "create_mcp_server", fake_super_create):
        server = await ssl_mod.SSLMCPWorker.create_mcp_server(_make_worker(base_path="/orchestrator"))

    assert isinstance(server, FastMCP)


async def _capture_uvicorn_config_kwargs(mcp: ssl_mod._SSLFastMCP) -> dict[str, Any]:
    """Run ``run_streamable_http_async`` with uvicorn patched; return Config kwargs."""
    captured: dict[str, Any] = {}

    def capture_config(app: Any, **kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        captured["app"] = app
        return MagicMock(name="uvicorn.Config")

    with (
        patch("uvicorn.Config", side_effect=capture_config),
        patch("uvicorn.Server") as mock_server_cls,
        patch.object(mcp, "streamable_http_app", return_value=MagicMock(name="app")),
    ):
        mock_server_cls.return_value.serve = AsyncMock()
        await mcp.run_streamable_http_async()

    return captured


@pytest.mark.asyncio
async def test_run_streamable_http_passes_ssl_kwargs_when_https(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "true")
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_CERTFILE, str(cert))
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_KEYFILE, str(key))

    mcp = ssl_mod._SSLFastMCP(name="test", host="127.0.0.1", port=9901)
    kwargs = await _capture_uvicorn_config_kwargs(mcp)

    assert kwargs["ssl_certfile"] == str(cert)
    assert kwargs["ssl_keyfile"] == str(key)


@pytest.mark.asyncio
async def test_run_streamable_http_omits_ssl_kwargs_when_https_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """With HTTPS off, cert/key env vars must not reach uvicorn — same as the stock worker."""
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_ENABLE_HTTPS, "false")
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_CERTFILE, str(cert))
    monkeypatch.setenv(ssl_mod.ORCHESTRATOR_KEYFILE, str(key))

    mcp = ssl_mod._SSLFastMCP(name="test", host="127.0.0.1", port=9901)
    kwargs = await _capture_uvicorn_config_kwargs(mcp)

    assert kwargs["ssl_certfile"] is None
    assert kwargs["ssl_keyfile"] is None
