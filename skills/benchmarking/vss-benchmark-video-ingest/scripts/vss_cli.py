# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolve an installed VSS CLI once; never install or synchronize during a run."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class CliResult:
    exit_code: int
    body: dict[str, Any]
    detail: str = ""


class VssCli:
    def __init__(
        self,
        repo: Path,
        config_home: Path | None = None,
        executable: str | None = None,
    ):
        self.repo = repo.expanduser().resolve()
        self.env = os.environ.copy()
        if config_home is not None:
            self.env["VSS_CONFIG_HOME"] = str(config_home.expanduser().resolve())
        self.config_home = self.env.get("VSS_CONFIG_HOME", str(Path.home() / ".vss"))

        selected = os.path.expanduser(executable) if executable is not None else "vss"
        direct = shutil.which(selected)
        if direct is None:
            raise ValueError(
                f"VSS CLI executable is missing or not executable: {selected}. "
                "Install it per AGENTS.md or set --cli-executable."
            )
        binary = Path(direct).resolve()
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError(f"VSS CLI executable is missing or not executable: {binary}")
        self.command = (str(binary),)
        # uv tool installs expose a symlink; resolve its actual environment.
        if (binary.parent.parent / "pyvenv.cfg").is_file():
            self.env["VIRTUAL_ENV"] = str(binary.parent.parent)
            self.env["PATH"] = str(binary.parent) + os.pathsep + self.env.get("PATH", "")

    def call(self, *args: str) -> CliResult:
        # CLI owns its bounded HTTP/timeline waits. Never retry a mutating command.
        result = subprocess.run([*self.command, *args], env=self.env, capture_output=True, text=True, check=False)
        try:
            body = json.loads(result.stdout)
        except json.JSONDecodeError:
            body = None
        detail = result.stderr.strip()
        if result.returncode:
            return CliResult(
                result.returncode, body if isinstance(body, dict) else {}, (detail or result.stdout.strip())[:1000]
            )
        if not isinstance(body, dict):
            return CliResult(0, {}, "CLI protocol error: exit 0 without a JSON object")
        return CliResult(0, body, detail[:1000])

    def deployment(self, *, check_health: bool = True) -> dict[str, Any]:
        result = self.call("configure", "show")
        if result.exit_code:
            raise ValueError(f"vss configure show exited {result.exit_code}: {result.detail}")
        services = result.body.get("services")
        if not isinstance(services, dict) or not isinstance(services.get("vst"), dict):
            raise ValueError("CLI configuration has no VIOS route; run vss configure --base-url ORIGIN first")
        url = services["vst"].get("url")
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("missing HTTP(S) URL")
            # Accessing port validates its range and rejects malformed values.
            parsed.port
        except ValueError as exc:
            raise ValueError("CLI configuration has an invalid VIOS URL; run vss configure again") from exc
        if check_health:
            # Only VIOS is a CLI upload dependency; an absent Agent/LVS is normal.
            probe = self.call("vios", "list", "--type", "video")
            if probe.exit_code:
                raise ValueError(f"vss vios list exited {probe.exit_code}: {probe.detail}")
            sensors = probe.body.get("sensors")
            if not isinstance(sensors, list) or any(not isinstance(row, dict) for row in sensors):
                raise ValueError("vss vios list protocol error: expected a sensors array of objects")
        return result.body
