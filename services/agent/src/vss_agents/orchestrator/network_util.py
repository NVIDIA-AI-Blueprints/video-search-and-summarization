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
"""Network configuration and IP detection utilities."""

from __future__ import annotations

from enum import StrEnum
import os
from pathlib import Path
import subprocess
from typing import Final

DEFAULT_COMMAND_TIMEOUT_S: Final[int] = 5
DEFAULT_PROXY_PORT: Final[str] = "7777"
PROXY_MODE_VALUE: Final[str] = "proxy"
KIBANA_PROXY_PORT_PREFIX: Final[str] = "56010"


class BrevEnvKey(StrEnum):
    BREV_ENV_ID = "BREV_ENV_ID"
    PROXY_PORT = "PROXY_PORT"
    PROXY_MODE = "PROXY_MODE"
    BREV_LINK_PREFIX = "BREV_LINK_PREFIX"
    BREV_WS_AGENT_URL = "BREV_WS_AGENT_URL"
    BREV_API_URL = "BREV_API_URL"
    BREV_VST_API_URL = "BREV_VST_API_URL"
    BREV_MDX_URL = "BREV_MDX_URL"
    BREV_KIBANA_URL = "BREV_KIBANA_URL"
    KIBANA_PUBLIC_URL = "KIBANA_PUBLIC_URL"
    BREV_MAP_URL = "BREV_MAP_URL"
    VST_EXTERNAL_URL = "VST_EXTERNAL_URL"
    VSS_AGENT_EXTERNAL_URL = "VSS_AGENT_EXTERNAL_URL"
    VSS_AGENT_REPORTS_BASE_URL = "VSS_AGENT_REPORTS_BASE_URL"


def run_text_command(command: list[str], *, timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_S) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def detect_internal_ip() -> str:
    return run_text_command(
        [
            "bash",
            "-lc",
            "ip route get 1.1.1.1 | awk '/src/ {for (i=1;i<=NF;i++) if ($i==\"src\") print $(i+1)}'",
        ],
        timeout_seconds=DEFAULT_COMMAND_TIMEOUT_S,
    )


def detect_external_ip() -> str:
    for cmd in (
        ["curl", "-s", "--max-time", str(DEFAULT_COMMAND_TIMEOUT_S), "ifconfig.me"],
        ["curl", "-s", "--max-time", str(DEFAULT_COMMAND_TIMEOUT_S), "icanhazip.com"],
    ):
        ip = run_text_command(cmd, timeout_seconds=8)
        if ip:
            return ip
    return ""


def read_etc_environment() -> dict[str, str]:
    env: dict[str, str] = {}
    path = Path("/etc/environment")
    if not path.is_file():
        return env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def apply_brev_proxy_env(merged: dict[str, str], brev_env_id: str) -> None:
    proxy_port = (
        merged.get(BrevEnvKey.PROXY_PORT.value, "").strip()
        or os.environ.get(BrevEnvKey.PROXY_PORT.value, "").strip()
        or DEFAULT_PROXY_PORT
    )
    link_prefix = (
        merged.get(BrevEnvKey.BREV_LINK_PREFIX.value, "").strip()
        or os.environ.get(BrevEnvKey.BREV_LINK_PREFIX.value, "").strip()
        or f"{proxy_port}0"
    )
    brev_base = f"{brev_env_id}.brevlab.com"
    proxy_https = f"https://{link_prefix}-{brev_base}"
    proxy_wss = f"wss://{link_prefix}-{brev_base}"
    merged.update(
        {
            BrevEnvKey.BREV_ENV_ID.value: brev_env_id,
            BrevEnvKey.PROXY_PORT.value: proxy_port,
            BrevEnvKey.PROXY_MODE.value: PROXY_MODE_VALUE,
            BrevEnvKey.BREV_WS_AGENT_URL.value: f"{proxy_wss}/websocket",
            BrevEnvKey.BREV_API_URL.value: f"{proxy_https}/api/v1",
            BrevEnvKey.BREV_VST_API_URL.value: f"{proxy_https}/vst/api",
            BrevEnvKey.BREV_MDX_URL.value: proxy_https,
            BrevEnvKey.BREV_KIBANA_URL.value: f"https://{KIBANA_PROXY_PORT_PREFIX}-{brev_base}",
            BrevEnvKey.KIBANA_PUBLIC_URL.value: f"https://{KIBANA_PROXY_PORT_PREFIX}-{brev_base}",
            BrevEnvKey.BREV_MAP_URL.value: proxy_https,
            BrevEnvKey.VST_EXTERNAL_URL.value: proxy_https,
            BrevEnvKey.VSS_AGENT_EXTERNAL_URL.value: proxy_https,
            BrevEnvKey.VSS_AGENT_REPORTS_BASE_URL.value: f"{proxy_https}/static/",
        }
    )
