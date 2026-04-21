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
URL Translation Utility for VSS Agent.

Translates URLs based on VLM_MODE to ensure VLM can access video resources:
- remote: INTERNAL_IP -> EXTERNAL_IP (external VLM needs public URLs)
- local/local_shared: EXTERNAL_IP -> INTERNAL_IP (local VLM needs internal URLs)

When the application is behind a reverse proxy (e.g., Brev secure links routing
through nginx), the video URL hostname won't match either IP. In that case, if
``vst_internal_url`` is provided, the proxy base URL is replaced with the internal
VST base URL so the local VLM can reach the video directly.

Configuration (passed as arguments from tool config):
    vlm_mode: remote / local / local_shared
    external_ip: Public IP accessible from the internet
    internal_ip: Internal IP / docker host IP
    vst_internal_url: (optional) Internal VST base URL for proxy fallback
"""

import logging
from urllib.parse import ParseResult
from urllib.parse import urlparse
from urllib.parse import urlunparse

logger = logging.getLogger(__name__)


def translate_url(
    url: str,
    vlm_mode: str | None,
    internal_ip: str | None,
    external_ip: str | None,
    vst_internal_url: str | None = None,
) -> str:
    """Translate URL based on VLM_MODE.

    - remote: Replace INTERNAL_IP with EXTERNAL_IP (VLM is external, needs public URLs)
    - local/local_shared: Replace EXTERNAL_IP with INTERNAL_IP (VLM is local, needs internal URLs)

    When the URL host doesn't match either IP (e.g., behind a reverse proxy),
    falls back to replacing the base URL with ``vst_internal_url`` if provided.

    Args:
        url: The URL to translate
        vlm_mode: VLM mode ('remote', 'local', or 'local_shared'), None to skip translation
        internal_ip: Internal IP / docker host IP, None to skip translation
        external_ip: Public IP accessible from the internet, None to skip translation
        vst_internal_url: Internal VST base URL (e.g., 'http://10.0.0.1:30888').
            Used as fallback when the URL host is a proxy hostname that doesn't
            match either IP.  Only applies to local/local_shared modes.

    Returns:
        Translated URL, or original URL if no translation needed
    """
    if not url:
        return url

    # Validate vlm_mode
    if not vlm_mode:
        logger.warning(
            "URL TRANSLATION: vlm_mode is not set. "
            "Expected values: 'remote', 'local', or 'local_shared'. "
            "URL translation will be skipped."
        )
        return url

    vlm_mode = vlm_mode.lower()

    # Check for missing external_ip
    if not external_ip:
        logger.error(
            "URL TRANSLATION ERROR: external_ip is not set! "
            "Set external_ip to the public IP accessible from the internet. "
            "URLs will NOT be translated."
        )
        return url

    # Check for missing internal_ip
    if not internal_ip:
        logger.error(
            "URL TRANSLATION ERROR: internal_ip is not set! "
            "Set internal_ip to the internal/docker host IP. "
            "URLs will NOT be translated."
        )
        return url

    # Check if IPs are the same (no translation needed)
    if external_ip == internal_ip:
        logger.debug(f"URL TRANSLATION: external_ip ({external_ip}) equals internal_ip - no translation needed.")
        return url

    # Parse the URL
    parsed = urlparse(url)
    if not parsed.netloc:
        return url

    # Extract host (without port)
    host = parsed.netloc.split(":")[0]

    # Determine translation direction based on vlm_mode
    if vlm_mode == "remote":
        # Remote VLM needs external/public URLs
        source_ip = internal_ip
        target_ip = external_ip
        direction = "INTERNAL -> EXTERNAL"
    elif vlm_mode in ("local", "local_shared"):
        # Local VLM needs internal URLs
        source_ip = external_ip
        target_ip = internal_ip
        direction = "EXTERNAL -> INTERNAL"
    else:
        logger.warning(
            f"URL TRANSLATION: Unknown vlm_mode '{vlm_mode}'. Expected: 'remote', 'local', or 'local_shared'."
        )
        return url

    # Only translate if the host matches the source IP
    if host != source_ip:
        # Proxy fallback: when the app is behind a reverse proxy (e.g., Brev
        # secure links with nginx), the URL hostname is the proxy's hostname,
        # not a direct IP.  For local VLM modes, replace the proxy base URL
        # with the internal VST URL so the VLM can reach the video directly.
        if vlm_mode in ("local", "local_shared") and vst_internal_url:
            return _translate_proxy_url(url, parsed, vst_internal_url)

        logger.debug(f"URL TRANSLATION: Host '{host}' does not match source IP '{source_ip}' - no translation needed.")
        return url

    # Replace source IP with target IP in netloc
    new_netloc = parsed.netloc.replace(source_ip, target_ip, 1)
    translated = urlunparse(parsed._replace(netloc=new_netloc))

    logger.info(f"URL TRANSLATION [{direction}] (vlm_mode={vlm_mode}): Converting IP from {source_ip} to {target_ip}")
    logger.info(f"URL TRANSLATION: {url} -> {translated}")

    return translated


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
