# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute the documented readiness gate against representative Compose states."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BUILD_SKILL = Path(__file__).resolve().parents[2]


@pytest.fixture
def fake_runtime(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "if os.environ.get('VSS_TEST_DOCKER_FAIL'):\n"
        "    sys.exit(3)\n"
        "states = json.loads(os.environ['VSS_TEST_STATES'])\n"
        "if '--services' in args:\n"
        "    for i in range(int(os.environ['VSS_TEST_EXPECTED'])):\n"
        "        print(f'service-{i}')\n"
        "elif 'ps' in args:\n"
        "    if '--all' not in args:\n"
        "        states = [s for s in states if s['State'] == 'running']\n"
        "    for i, state in enumerate(states):\n"
        "        print(f'container-{i}' if '-q' in args else json.dumps(state))\n"
        "else:\n"
        "    sys.exit(2)\n"
    )
    docker.chmod(0o755)
    return bin_dir, {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _state(state: str, health: str = "", exit_code: int = 0) -> dict:
    return {
        "Name": "vss-test",
        "State": state,
        "Health": health,
        "ExitCode": exit_code,
        "Status": state,
    }


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required by the gate")
@pytest.mark.parametrize(
    ("states", "expected", "docker_fails", "passes"),
    [
        ([_state("running", "healthy"), _state("exited")], 2, False, True),
        ([_state("running")], 1, False, True),
        ([], 0, False, False),
        ([], 1, False, False),
        ([_state("running")], 2, False, False),
        ([_state("created"), _state("running")], 2, False, False),
        ([_state("running", "unhealthy"), _state("running")], 2, False, False),
        ([_state("running", "starting")], 1, False, False),
        ([_state("restarting"), _state("running")], 2, False, False),
        ([_state("exited", exit_code=1), _state("running")], 2, False, False),
        ([_state("running")], 1, True, False),
    ],
)
def test_documented_gate_checks_all_container_states(
    fake_runtime: tuple[Path, dict[str, str]],
    states: list[dict],
    expected: int,
    docker_fails: bool,
    passes: bool,
) -> None:
    _, env = fake_runtime
    env.update(
        VSS_TEST_STATES=json.dumps(states),
        VSS_TEST_EXPECTED=str(expected),
        VSS_TEST_DOCKER_FAIL="1" if docker_fails else "",
    )
    reference = (BUILD_SKILL / "references/readiness.md").read_text()
    gate = re.search(r"```bash\n(.*?)\n```", reference, re.DOTALL)
    assert gate is not None
    result = subprocess.run(
        ["bash", "-c", gate.group(1)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) is passes, result.stderr


@pytest.mark.parametrize("step", (0, 1))
@pytest.mark.parametrize(
    ("available", "cli_exit", "passes"),
    [(True, 0, True), (False, 0, False), (True, 3, False)],
)
def test_vdr2_capability_checks_use_installed_cli_and_exit_codes(
    fake_runtime: tuple[Path, dict[str, str]],
    tmp_path: Path,
    step: int,
    available: bool,
    cli_exit: int,
    passes: bool,
) -> None:
    bin_dir, env = fake_runtime
    vss = bin_dir / "vss"
    vss.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "if sys.argv[1:] == ['configure', 'check']:\n"
        "    print(os.environ['VSS_TEST_CAPABILITY'])\n"
        "    sys.exit(int(os.environ['VSS_TEST_CLI_EXIT']))\n"
    )
    vss.chmod(0o755)
    group = "vlm" if step == 0 else "summarize"
    env.update(
        VSS_TEST_CAPABILITY=f"  {group}  {'available' if available else 'unavailable'}",
        VSS_TEST_CLI_EXIT=str(cli_exit),
    )
    spec = json.loads(
        (BUILD_SKILL / "evals/vdr_2_add_alerting_summarization.json").read_text()
    )
    check = next(
        c for c in spec["expects"][step]["checks"] if "vss configure check" in c
    )
    command = (
        check.split("`", 2)[1]
        .replace("{{repo_root}}", str(tmp_path))
        .replace("$HOME/.local/bin", str(bin_dir))
    )
    result = subprocess.run(
        command,
        shell=True,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) is passes, result.stderr
