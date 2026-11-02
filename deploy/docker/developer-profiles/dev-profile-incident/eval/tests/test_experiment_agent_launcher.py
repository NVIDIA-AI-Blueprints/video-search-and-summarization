# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The local experiment agent runs the code of the checkout it is launched from, and sees that checkout's ffmpeg."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

PROFILE = Path(__file__).resolve().parents[2]
LAUNCHER = PROFILE / "eval" / "experiments" / "agent" / "start-experiment-agent.sh"
REPO = PROFILE.parents[3]


@pytest.mark.skipif(not (REPO / ".git").exists(), reason="needs a git checkout")
def test_the_launcher_pins_its_own_checkout_and_tools_dir(tmp_path: Path) -> None:
    if (REPO / "services" / "agent" / ".env_file").exists():
        pytest.skip("a services/agent/.env_file is present; the launcher refuses it by design")
    fake_nat = tmp_path / "nat"
    fake_nat.write_text("#!/bin/sh\nexit 0\n")
    fake_nat.chmod(0o755)
    env = {**os.environ, "EXPERIMENT_AGENT_DRY_RUN": "1", "NAT_BIN": str(fake_nat)}
    done = subprocess.run(
        ["bash", str(LAUNCHER), "--upstream", "http://127.0.0.1:8899"],
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )
    assert done.returncode == 0, done.stderr
    line = next(line for line in done.stdout.splitlines() if line.startswith("dry run:"))
    assert f"PYTHONPATH={REPO / 'services' / 'agent' / 'src'}" in line, "the agent must run this checkout's code"
    assert f"PATH={REPO / '.tools' / 'ffmpeg' / 'bin'}:/usr/bin:/bin" in line, "the checkout's ffmpeg comes first"
    assert "INCIDENT_EXPERIMENTS_ENABLED=1" in line and "SUPABASE" not in line and "R2_" not in line
