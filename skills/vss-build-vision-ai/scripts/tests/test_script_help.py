# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CLI help contract for every executable bundled with vss-build-vision-ai."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = sorted(
    path
    for path in SCRIPTS_DIR.iterdir()
    if path.is_file() and path.suffix in {".py", ".sh"}
)


@pytest.mark.parametrize("flag", ["-h", "--help"])
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_help_exits_zero_without_calling_curl(tmp_path: Path, script: Path, flag: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "curl-called"
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/bin/sh\nprintf "called\\n" >> "$HELP_CURL_MARKER"\nexit 99\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "HELP_CURL_MARKER": str(marker),
            "NGC_CLI_API_KEY": "must-not-be-probed",
            "NGC_API_KEY": "must-not-be-probed",
            "NVIDIA_API_KEY": "must-not-be-probed",
            "HF_TOKEN": "must-not-be-probed",
            "REMOTE_API_KEY": "must-not-be-probed",
        }
    )
    command = (["bash", str(script)] if script.suffix == ".sh" else [sys.executable, str(script)])
    result = subprocess.run(
        [*command, flag],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage" in f"{result.stdout}\n{result.stderr}".lower()
    assert not marker.exists(), f"{script.name} called curl while rendering {flag}"
