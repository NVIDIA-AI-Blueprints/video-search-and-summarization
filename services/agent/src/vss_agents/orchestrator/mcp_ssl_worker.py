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

"""Optional TLS support for the ``nat mcp serve`` front end.

This module is a drop-in ``runner_class`` for ``nat mcp serve`` that toggles the
orchestrator MCP server between plain HTTP and HTTPS without patching NAT or the
upstream ``mcp`` package. It relies only on the documented NAT extension points:
a custom worker (selected via ``--runner_class`` / ``runner_class:``) whose
``create_mcp_server`` returns a ``FastMCP`` subclass. The subclass overrides the
transport ``run_*`` coroutines to thread ``ssl_certfile`` / ``ssl_keyfile`` into
the ``uvicorn.Config`` that FastMCP builds.

Toggle via environment:

- ``ORCHESTRATOR_ENABLE_HTTPS`` selects the scheme: ``true`` enables TLS, ``false``
  (the default) serves plain HTTP. Only ``true``/``false`` are accepted (case-
  insensitive). HTTPS is never inferred from cert/key presence.
- When ``ORCHESTRATOR_ENABLE_HTTPS=true``, ``ORCHESTRATOR_CERTFILE`` and
  ``ORCHESTRATOR_KEYFILE`` are both required.
- When ``ORCHESTRATOR_ENABLE_HTTPS=false``, cert/key vars are ignored (plain HTTP,
  identical to the stock worker).

Note: this override is bypassed when ``base_path`` is configured, because NAT
mounts the app with its own ``uvicorn.Config`` in that case. The orchestrator
config does not set ``base_path``.
"""

import logging
import os
from typing import cast

from mcp.server.fastmcp import FastMCP
from nat.plugins.mcp.server.front_end_plugin_worker import MCPFrontEndPluginWorker
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

ORCHESTRATOR_ENABLE_HTTPS = "ORCHESTRATOR_ENABLE_HTTPS"
ORCHESTRATOR_CERTFILE = "ORCHESTRATOR_CERTFILE"
ORCHESTRATOR_KEYFILE = "ORCHESTRATOR_KEYFILE"


def https_enabled() -> bool:
    """Return whether HTTPS is enabled via ``ORCHESTRATOR_ENABLE_HTTPS``.

    Raises:
        ValueError: if the env var is set to a value other than ``true``/``false``.
    """
    raw = (os.environ.get(ORCHESTRATOR_ENABLE_HTTPS) or "false").strip().lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ValueError(
        f"{ORCHESTRATOR_ENABLE_HTTPS} must be 'true' or 'false' (case-insensitive), got {raw!r}.",
    )


def resolve_ssl_paths() -> tuple[str | None, str | None]:
    """Return ``(certfile, keyfile)`` when HTTPS is enabled, else ``(None, None)``.

    Raises:
        ValueError: if HTTPS is enabled but either cert or key env var is unset.
        FileNotFoundError: if a configured path does not exist.
    """
    if not https_enabled():
        return None, None

    certfile = (os.environ.get(ORCHESTRATOR_CERTFILE) or "").strip() or None
    keyfile = (os.environ.get(ORCHESTRATOR_KEYFILE) or "").strip() or None

    missing = [
        name for name, val in ((ORCHESTRATOR_CERTFILE, certfile), (ORCHESTRATOR_KEYFILE, keyfile)) if val is None
    ]
    if missing:
        raise ValueError(
            f"{ORCHESTRATOR_ENABLE_HTTPS}=true but required SSL env var(s) unset: {', '.join(missing)}.",
        )

    for label, path in (("cert", certfile), ("key", keyfile)):
        if path is not None and not os.path.isfile(path):
            raise FileNotFoundError(f"MCP SSL {label} file not found: {path}")

    return certfile, keyfile


class _SSLFastMCP(FastMCP):
    """``FastMCP`` variant that enables TLS when ``ORCHESTRATOR_ENABLE_HTTPS=true``."""

    async def _run_uvicorn(self, app: ASGIApp, *, transport: str, path_suffix: str = "") -> None:
        import uvicorn

        certfile, keyfile = resolve_ssl_paths()
        scheme = "https" if https_enabled() else "http"
        logger.info(
            "Starting MCP server (%s) at %s://%s:%s%s",
            transport,
            scheme,
            self.settings.host,
            self.settings.port,
            path_suffix,
        )
        config = uvicorn.Config(
            app,
            host=self.settings.host,
            port=self.settings.port,
            log_level=self.settings.log_level.lower(),
            ssl_certfile=certfile,
            ssl_keyfile=keyfile,
        )
        await uvicorn.Server(config).serve()

    async def run_streamable_http_async(self) -> None:
        await self._run_uvicorn(
            cast("ASGIApp", self.streamable_http_app()),
            transport="streamable-http",
            path_suffix="/mcp",
        )

    async def run_sse_async(self, mount_path: str | None = None) -> None:
        await self._run_uvicorn(cast("ASGIApp", self.sse_app(mount_path)), transport="sse")


class SSLMCPWorker(MCPFrontEndPluginWorker):
    """Default MCP worker that returns a TLS-capable ``FastMCP`` instance.

    Mirrors the stock ``MCPFrontEndPluginWorker.create_mcp_server`` (including
    ``server_auth`` wiring) but instantiates :class:`_SSLFastMCP` so the
    transport honors the SSL cert/key environment toggle.
    """

    async def create_mcp_server(self) -> FastMCP:
        auth_settings = None
        token_verifier = None

        if self.front_end_config.server_auth:
            from mcp.server.auth.settings import AuthSettings
            from nat.plugins.mcp.server.introspection_token_verifier import IntrospectionTokenVerifier
            from pydantic import AnyHttpUrl

            scheme = "https" if https_enabled() else "http"
            server_url = f"{scheme}://{self.front_end_config.host}:{self.front_end_config.port}"
            auth_settings = AuthSettings(
                issuer_url=AnyHttpUrl(self.front_end_config.server_auth.issuer_url),
                required_scopes=self.front_end_config.server_auth.scopes,
                resource_server_url=AnyHttpUrl(server_url),
            )
            token_verifier = IntrospectionTokenVerifier(self.front_end_config.server_auth)

        return _SSLFastMCP(
            name=self.front_end_config.name,
            host=self.front_end_config.host,
            port=self.front_end_config.port,
            debug=self.front_end_config.debug,
            auth=auth_settings,
            token_verifier=token_verifier,
        )
