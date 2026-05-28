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

"""
URL Rewriting Utility for VSS Agent.

Rewrites service URLs when an in-deployment consumer needs to bypass a public
reverse proxy and reach the internal service endpoint directly.
"""

import logging
from urllib.parse import ParseResult
from urllib.parse import urlparse
from urllib.parse import urlunparse

logger = logging.getLogger(__name__)


# Routing table: path prefix -> internal port.
# Used to resolve proxy URLs (no explicit port) to the correct internal service.
# Order matters — longest/most-specific prefixes first.
_PROXY_ROUTE_TABLE: list[tuple[str, int]] = [
    ("/vst/", 30888),
    ("/api/v1/", 8000),
    ("/chat/", 8000),
    ("/static/", 8000),
    ("/health", 8000),
    ("/incidents", 8081),
    ("/livez", 8081),
]
_PROXY_DEFAULT_PORT = 8000  # agent as fallback


def rewrite_url_host(url: str, target_ip: str) -> str:
    """Replace the host in *url* with *target_ip*, preserving path, query, and fragment.

    When the URL has an explicit port (e.g. ``http://1.2.3.4:30888/...``),
    the port and scheme are preserved as-is — this is the normal direct-IP case.

    When there is no explicit port and the host is not already *target_ip*,
    the URL is assumed to be coming through a reverse proxy (e.g. a Brev
    secure link like ``https://7777-abc.brevlab.com/vst/...``).  In that
    case the scheme is forced to ``http`` and the port is resolved from the
    path prefix via :data:`_PROXY_ROUTE_TABLE`.

    Args:
        url: The URL to rewrite.
        target_ip: The IP address to substitute (e.g. ``10.0.1.1``).

    Returns:
        URL rewritten to reach the internal service directly.
    """
    parsed = urlparse(url)
    if parsed.port:
        # Explicit port — direct-IP URL, simple host swap.
        new_netloc = f"{target_ip}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=new_netloc))

    host = parsed.hostname or ""
    if host == target_ip:
        # Already pointing at target — nothing to do.
        return url

    # No explicit port and host != target_ip → proxy URL.
    # Look up the internal port from the path prefix.
    port = _PROXY_DEFAULT_PORT
    path = parsed.path or "/"
    for prefix, p in _PROXY_ROUTE_TABLE:
        if path.startswith(prefix):
            port = p
            break

    new_netloc = f"{target_ip}:{port}"
    translated = urlunparse(parsed._replace(scheme="http", netloc=new_netloc))
    logger.info(f"URL REWRITE [PROXY -> INTERNAL]: {url} -> {translated}")
    return translated


def rewrite_to_internal_vst_url(url: str, vst_internal_url: str | None) -> str:
    """Replace any VST media URL base with the configured internal VST base.

    Use this when the agent or another internal service will fetch VST media
    directly. It is independent of VLM mode because the consumer is known to be
    inside the deployment boundary.
    """
    if not url or not vst_internal_url:
        return url

    parsed = urlparse(url)
    if not parsed.netloc:
        return url

    path = parsed.path or ""
    if not path.startswith("/vst/"):
        logger.debug(f"URL TRANSLATION: URL path '{path}' is not a VST path - no internal VST rewrite needed.")
        return url

    return _translate_proxy_url(url, parsed, vst_internal_url)


def _translate_proxy_url(url: str, parsed: ParseResult, vst_internal_url: str) -> str:
    """Replace a proxy base URL with the internal VST base URL.

    When behind a reverse proxy, the video URL looks like:
        https://proxy-host:port/vst/storage/file.mp4
    The internal VST URL is:
        http://internal-ip:30888
    So the translated URL becomes:
        http://internal-ip:30888/vst/storage/file.mp4

    The path is preserved as-is since the proxy forwards ``/vst/`` to VST
    without rewriting.
    """
    internal_parsed = urlparse(vst_internal_url.rstrip("/"))
    translated = urlunparse(
        parsed._replace(
            scheme=internal_parsed.scheme,
            netloc=internal_parsed.netloc,
        )
    )

    logger.info(
        f"URL TRANSLATION [PROXY -> INTERNAL] (behind reverse proxy): "
        f"Replacing proxy base URL with internal VST URL ({vst_internal_url})"
    )
    logger.info(f"URL TRANSLATION: {url} -> {translated}")
    return translated
