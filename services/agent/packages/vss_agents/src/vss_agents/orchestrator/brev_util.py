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
"""Brev / launchpad secure-link helpers.

The environment context file (``BREV_ENVIRONMENT_CONTEXT_PATH``) is the source of
truth for the environment id and per-port secure-link FQDNs.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

try:
    from enum import StrEnum
except ImportError:  # Python 3.10 on some notebook/eval hosts.
    from enum import Enum

    class StrEnum(str, Enum):
        pass

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_PROXY_PORT: Final[str] = "7777"
PROXY_MODE_VALUE: Final[str] = "proxy"
KIBANA_PROXY_PORT_PREFIX: Final[str] = "5601"


class BrevEnvKey(StrEnum):
    BREV_ENV_ID = "BREV_ENV_ID"
    BREV_ENVIRONMENT_CONTEXT_PATH = "BREV_ENVIRONMENT_CONTEXT_PATH"
    PROXY_PORT = "PROXY_PORT"
    PROXY_MODE = "PROXY_MODE"
    BREV_LINK_DOMAIN = "BREV_LINK_DOMAIN"
    KIBANA_PROXY_PORT_PREFIX = "KIBANA_PROXY_PORT_PREFIX"
    KIBANA_PUBLIC_URL = "KIBANA_PUBLIC_URL"
    VST_EXTERNAL_URL = "VST_EXTERNAL_URL"
    VSS_AGENT_EXTERNAL_URL = "VSS_AGENT_EXTERNAL_URL"
    VSS_AGENT_REPORTS_BASE_URL = "VSS_AGENT_REPORTS_BASE_URL"
    VSS_PUBLIC_HTTP_PROTOCOL = "VSS_PUBLIC_HTTP_PROTOCOL"
    VSS_PUBLIC_WS_PROTOCOL = "VSS_PUBLIC_WS_PROTOCOL"
    VSS_PUBLIC_HOST = "VSS_PUBLIC_HOST"
    VSS_PUBLIC_PORT = "VSS_PUBLIC_PORT"


def read_brev_environment_context(path: str | None = None) -> dict[str, Any]:
    """Return the parsed Brev environment context, or ``{}`` when unavailable.

    The context file (``BREV_ENVIRONMENT_CONTEXT_PATH``) is the source of truth for
    the environment id and per-port secure-link FQDNs. There is no hardcoded path.
    """
    if path is None:
        path = os.environ.get(BrevEnvKey.BREV_ENVIRONMENT_CONTEXT_PATH.value, "").strip()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def brev_environment_id(context: Mapping[str, Any] | None = None) -> str:
    """Best-effort Brev environment id.

    Precedence: ``BREV_ENV_ID`` env var -> context file ``environment_id``.
    """
    env_id = os.environ.get(BrevEnvKey.BREV_ENV_ID.value, "").strip()
    if env_id:
        return env_id
    if context is None:
        context = read_brev_environment_context()
    return str(context.get("environment_id", "")).strip()


def brev_secure_link_fqdn(
    destination_port: int | str,
    context: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the secure-link FQDN mapped to *destination_port*, if published."""
    try:
        port = int(destination_port)
    except (TypeError, ValueError):
        return None
    if context is None:
        context = read_brev_environment_context()
    ports = context.get("ports")
    if not isinstance(ports, list):
        return None
    for entry in ports:
        if not isinstance(entry, dict):
            continue
        raw_port = entry.get("destination_port")
        if raw_port is None:
            continue
        try:
            if int(raw_port) != port:
                continue
        except (TypeError, ValueError):
            continue
        fqdn = str(entry.get("fqdn", "")).strip()
        if fqdn:
            return fqdn
    return None


def _link_domain_from_brev_context(context: Mapping[str, Any] | None = None) -> str:
    """Derive the secure-link base domain from a port FQDN in the context file."""
    if context is None:
        context = read_brev_environment_context()
    env_id = str(context.get("environment_id", "")).strip().lower()
    ports = context.get("ports")
    if not env_id or not isinstance(ports, list):
        return ""
    marker = f"-{env_id}."
    for entry in ports:
        if not isinstance(entry, dict):
            continue
        fqdn = str(entry.get("fqdn", "")).strip().lower()
        if marker in fqdn:
            return fqdn.split(marker, 1)[1]
    return ""


def detect_brev_link_domain(
    explicit_domain: str = "",
    context: Mapping[str, Any] | None = None,
) -> str:
    """Resolve the secure-link base domain.

    Precedence:
      1. Explicit *explicit_domain* / ``BREV_LINK_DOMAIN`` override.
      2. Derived from ``BREV_ENVIRONMENT_CONTEXT_PATH`` (source of truth).

    Returns ``""`` when neither is available — never invents ``brevlab.com``.
    """
    domain = explicit_domain.strip() or os.environ.get(BrevEnvKey.BREV_LINK_DOMAIN.value, "").strip()
    if domain:
        return domain
    return _link_domain_from_brev_context(context)


def apply_brev_proxy_env(
    merged: dict[str, str],
    brev_env_id: str = "",
    *,
    explicit_link_domain: str = "",
) -> None:
    """Populate Brev secure-link / public UI env vars from the context file.

    Uses exact per-port FQDNs from ``BREV_ENVIRONMENT_CONTEXT_PATH`` only.
    No-ops when the proxy port is not published in that context.
    """
    proxy_port = (
        merged.get(BrevEnvKey.PROXY_PORT.value, "").strip()
        or os.environ.get(BrevEnvKey.PROXY_PORT.value, "").strip()
        or DEFAULT_PROXY_PORT
    )
    kibana_prefix = (
        merged.get(BrevEnvKey.KIBANA_PROXY_PORT_PREFIX.value, "").strip()
        or os.environ.get(BrevEnvKey.KIBANA_PROXY_PORT_PREFIX.value, "").strip()
        or KIBANA_PROXY_PORT_PREFIX
    )

    context = read_brev_environment_context()
    env_id = brev_env_id.strip() or brev_environment_id(context)
    link_domain = detect_brev_link_domain(explicit_link_domain, context)

    proxy_fqdn = brev_secure_link_fqdn(proxy_port, context)
    if not proxy_fqdn:
        return

    kibana_fqdn = brev_secure_link_fqdn(kibana_prefix, context)

    proxy_https = f"https://{proxy_fqdn}"
    update: dict[str, str] = {
        BrevEnvKey.PROXY_PORT.value: proxy_port,
        BrevEnvKey.PROXY_MODE.value: PROXY_MODE_VALUE,
        BrevEnvKey.KIBANA_PROXY_PORT_PREFIX.value: kibana_prefix,
        BrevEnvKey.VST_EXTERNAL_URL.value: proxy_https,
        BrevEnvKey.VSS_AGENT_EXTERNAL_URL.value: proxy_https,
        BrevEnvKey.VSS_AGENT_REPORTS_BASE_URL.value: f"{proxy_https}/static/",
        BrevEnvKey.VSS_PUBLIC_HTTP_PROTOCOL.value: "https",
        BrevEnvKey.VSS_PUBLIC_WS_PROTOCOL.value: "wss",
        BrevEnvKey.VSS_PUBLIC_HOST.value: proxy_fqdn,
        BrevEnvKey.VSS_PUBLIC_PORT.value: "443",
    }
    if env_id:
        update[BrevEnvKey.BREV_ENV_ID.value] = env_id
    if link_domain:
        update[BrevEnvKey.BREV_LINK_DOMAIN.value] = link_domain
    if kibana_fqdn:
        update[BrevEnvKey.KIBANA_PUBLIC_URL.value] = f"https://{kibana_fqdn}"
    merged.update(update)
