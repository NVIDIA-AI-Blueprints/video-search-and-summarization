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

import subprocess
from typing import Final

DEFAULT_COMMAND_TIMEOUT_S: Final[int] = 5


def run_text_command(command: list[str], *, timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_S) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def detect_internal_ip() -> str:
    route_info = run_text_command(
        ["ip", "-o", "route", "get", "1.1.1.1"],
        timeout_seconds=DEFAULT_COMMAND_TIMEOUT_S,
    )
    fields = route_info.split()
    for index, field in enumerate(fields):
        if field == "src" and index + 1 < len(fields):
            return fields[index + 1]
    return ""


def detect_external_ip() -> str:
    for cmd in (
        ["curl", "-s", "--max-time", str(DEFAULT_COMMAND_TIMEOUT_S), "ifconfig.me"],
        ["curl", "-s", "--max-time", str(DEFAULT_COMMAND_TIMEOUT_S), "icanhazip.com"],
    ):
        ip = run_text_command(cmd, timeout_seconds=8)
        if ip:
            return ip
    return ""
