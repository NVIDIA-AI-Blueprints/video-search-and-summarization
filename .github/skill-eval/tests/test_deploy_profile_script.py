# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for skills/vss-deploy-profile/scripts/deploy.sh.

Each test pins something a reader cannot recover from the script alone: that
the skill still points at it, that it performs the documented steps in the
documented order, and that the readiness gate cannot pass vacuously.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SKILL = REPO / "skills" / "vss-deploy-profile"
SCRIPT = SKILL / "scripts" / "deploy.sh"
SKILL_MD = SKILL / "SKILL.md"
CI_YML = REPO / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def body() -> str:
    return SCRIPT.read_text()


def _code(body: str) -> str:
    return "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))


def test_script_is_present_and_executable() -> None:
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_script_parses() -> None:
    proc = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_skill_md_points_at_the_script() -> None:
    """A script the skill never mentions is one the agent never runs."""
    assert "scripts/deploy.sh" in SKILL_MD.read_text()


def test_ci_runs_this_file() -> None:
    """CI names its test files one by one, so an unregistered test never runs."""
    assert Path(__file__).name in CI_YML.read_text()


def test_documented_steps_appear_in_documented_order(body: str) -> None:
    steps = [
        r'cp "\$\{ENV_POST\}" "\$\{ENV_GEN\}"',   # 1c, from overrides.env
        r"config >resolved\.yml",                  # 3, dry-run
        r"normalize_resolved_yml\.py",             # 3d, strip dangling depends_on
        r"-f resolved\.yml up -d",                 # 5, deploy
    ]
    positions = []
    for pattern in steps:
        match = re.search(pattern, body)
        assert match, f"deploy.sh no longer performs: {pattern}"
        positions.append(match.start())
    assert positions == sorted(positions), "steps are out of documented order"


def test_both_env_files_are_passed_to_config_and_up(body: str) -> None:
    """Without the pair, COMPOSE_PROFILES can be unset and up -d starts nothing."""
    joined = body.replace("\\\n", " ")
    for command in ("config >resolved.yml", "-f resolved.yml up -d"):
        line = next(ln for ln in joined.splitlines() if command in ln)
        assert '--env-file "${ENV_SRC}" --env-file "${ENV_GEN}"' in line, line


def test_compose_commands_name_the_project(body: str) -> None:
    """COMPOSE_PROJECT_NAME in the environment outranks resolved.yml's name: key."""
    joined = _code(body).replace("\\\n", " ")
    for line in joined.splitlines():
        if "resolved.yml ps" in line or "resolved.yml up -d" in line:
            assert '-p "${project}"' in line, line


def test_no_blanket_force_recreate(body: str) -> None:
    """It destroys warm NIM containers, costing a cold start each."""
    offenders = [ln for ln in _code(body).splitlines() if "--force-recreate" in ln]
    assert not offenders, offenders


def test_carries_no_model_or_hardware_identifier(body: str) -> None:
    """Placement is the caller's decision and arrives through --set.

    A default baked in here is not validated by dev-profile.sh on the remote
    path, so a name the repo does not know deploys healthy and fails at
    inference instead of at startup.
    """
    code = _code(body)
    for pattern in (r"nvidia/[a-z0-9]", r"Qwen/", r"ngc:nim/"):
        assert not re.search(pattern, code), f"deploy.sh hardcodes a model id: {pattern}"
    for profile in ("H100", "L40S", "RTXPRO6000BW", "DGX-SPARK"):
        assert profile not in code, f"deploy.sh duplicates hardware detection: {profile}"


def test_unexpanded_token_check_ignores_compose_escapes(body: str) -> None:
    """$${VAR} is a literal passed to the container, not a missing value."""
    match = re.search(r"grep -nE '(\S+)' resolved\.yml", body)
    assert match, "the Step 3b check is gone"
    assert "(^|[^$])" in match.group(1)


def test_service_count_cannot_be_read_back_out_of_resolved_yml(body: str) -> None:
    """resolved.yml keeps each service's profiles: key.

    Reading the service list back without COMPOSE_PROFILES filters every
    profile-gated service out and reports zero, which makes the gate vacuous.
    """
    assert "config --services" not in body, "count services from the rendered project, not by re-filtering"
    assert '[[ "${n_expected}" -gt 0 ]] || fail' in body, "a zero count must not be accepted"


def test_readiness_gate_cannot_pass_on_an_empty_or_shrinking_stack(body: str) -> None:
    assert '"${total}" -lt "${n_expected}"' in body, "a vanished container must end the wait"
    assert '"${unsettled}" -eq 0' in body


def test_terminal_container_states_end_the_wait(body: str) -> None:
    """Waiting out the clock on a container that cannot recover helps nobody."""
    code = _code(body)
    for state in ("dead:", "paused:", "running:unhealthy"):
        assert state in code, f"{state} is not treated as terminal"
    assert "RESTART_LIMIT" in code, "a crash-looping container must not burn the whole timeout"


def test_a_clean_one_shot_container_is_not_a_failure(body: str) -> None:
    """The init containers exit 0 and never report a health status."""
    assert re.search(r'exited:\*\)\s+\[\[ "\$\{code\}" -eq 0 \]\]', _code(body))


def test_failure_is_visible_to_the_caller(body: str) -> None:
    assert "fail() {" in body
    assert "RESULT: FAILED" in body
    assert "RESULT: OK" in body


@pytest.mark.parametrize("argv", [["--profile"], ["--set"], ["--timeout"], ["--repo"]])
def test_a_flag_without_a_value_exits_instead_of_spinning(argv: list[str]) -> None:
    """`shift 2` on a one-element tail fails, and with no `set -e` the parse loop repeats."""
    proc = subprocess.run([str(SCRIPT), *argv], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "requires a value" in proc.stderr


def test_an_unknown_profile_lists_the_real_ones(tmp_path: Path) -> None:
    (tmp_path / "deploy" / "docker" / "developer-profiles" / "dev-profile-base").mkdir(parents=True)
    proc = subprocess.run(
        [str(SCRIPT), "--profile", "nosuch", "--repo", str(tmp_path)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 2
    assert "unknown profile 'nosuch'" in proc.stderr
    assert "base" in proc.stderr


def test_an_invalid_override_key_is_rejected(tmp_path: Path) -> None:
    """A key that is a regex, or not a key at all, must not reach generated.env."""
    pdir = tmp_path / "deploy" / "docker" / "developer-profiles" / "dev-profile-base"
    pdir.mkdir(parents=True)
    (pdir / ".env").write_text("A=1\n")
    (pdir / "overrides.env").write_text("A=1\n")
    proc = subprocess.run(
        [str(SCRIPT), "--profile", "base", "--repo", str(tmp_path), "--set", "not a key=1"],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 1
    assert "invalid override key" in proc.stderr
