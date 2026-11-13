# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("validation_status", [0, 1])
def test_staged_compose_validation_blocks_deployment_and_keeps_env_order(tmp_path, validation_status):
    source = (ROOT / "deploy/docker/scripts/dev-profile.sh").read_text()
    start = source.index("  # Validate the same staged inputs used by up")
    end = source.index("  # Docker login to nvcr.io", start)
    block = source[start:end]
    tools = tmp_path / "tools"
    tools.mkdir()
    calls = tmp_path / "calls.jsonl"
    docker = tools / "docker"
    docker.write_text(
        "#!" + sys.executable + "\nimport json,os,sys\n"
        "with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "sys.exit(int(os.environ['VALIDATION_STATUS']) if '--quiet' in sys.argv else 0)\n"
    )
    docker.chmod(0o700)
    script = (
        "run_deployment() {\n"
        "  local deployment_directory=" + shlex.quote(str(tmp_path)) + "\n"
        "  local profile=alerts\n"
        "  local compose_files=(-f compose.yml -f 'an overlay.yml')\n" + block +
        "  echo DEPLOYMENT_REACHED\n}\nrun_deployment\n"
    )
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True,
        env={**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
             "CALLS": str(calls), "VALIDATION_STATUS": str(validation_status)})
    invocations = [json.loads(line) for line in calls.read_text().splitlines()]
    expected = ["compose", "-f", "compose.yml", "-f", "an overlay.yml",
        "--env-file", "containers.env", "--env-file", "developer-profiles/dev-profile-alerts/.env",
        "--env-file", "developer-profiles/dev-profile-alerts/generated.env"]
    assert invocations[0] == expected + ["config", "--quiet"]
    assert result.returncode == validation_status
    assert ("DEPLOYMENT_REACHED" in result.stdout) == (validation_status == 0)
    if validation_status:
        assert len(invocations) == 1
    else:
        assert invocations[1] == expected + ["config", "--images"]
