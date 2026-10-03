# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check the pinned Node runtime accepts Harbor's OpenClaw preamble."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SHIM = Path(__file__).resolve().parents[2] / ".openclaw" / "nvm-shim.sh"


def test_harbor_node_selection_uses_pinned_node_without_installing(tmp_path: Path) -> None:
    node = tmp_path / "node"
    node.write_text('#!/bin/sh\n[ "$1" = "-v" ] || exit 2\nprintf "v24.18.1\\n"\n')
    node.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}

    def run(action: str, version: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", "-c", '. "$1"; nvm "$2" "$3"', "shim-test", str(SHIM), action, version],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    selected = run("use", "22")
    assert selected.returncode == 0
    assert "v24.18.1" in selected.stdout
    assert run("use", "24").returncode == 0
    assert run("install", "22").returncode != 0
    assert run("use", "18").returncode != 0
