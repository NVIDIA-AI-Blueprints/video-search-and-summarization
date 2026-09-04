#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Create the coding-agent provisioning task used before a NemoClaw eval.

The task is intentionally a tiny Harbor task, not another deployment
implementation.  Its agent follows ``/vss-build-vision-ai`` on the remote
worker; that skill owns the Compose build, readiness gate, and host-side
NemoClaw setup.  Harbor only supplies the normal coding-agent execution and
the same Brev worker that the subsequent operational scenarios use.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path


BOOTSTRAP_TASK = "build-vision-bootstrap"


def _spec_deployment(spec_path: Path) -> tuple[str, str]:
    """Return the declarative profile and deploy mode for an operational spec."""

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError(f"spec is not a JSON object: {spec_path}")
    profile = str(spec.get("profile") or "base").strip()
    deploy_mode = str(spec.get("deploy_mode") or "").strip()
    if not profile:
        raise ValueError(f"spec has an empty profile: {spec_path}")
    return profile, deploy_mode


def _instruction(*, skill: str, platform: str, profile: str, deploy_mode: str) -> str:
    mode = f" in `{deploy_mode}` mode" if deploy_mode else ""
    return f"""You are the provisioning phase of a non-interactive skill evaluation.

Use `/vss-build-vision-ai` from `$HOME/video-search-and-summarization` to deploy
the `{profile}` VSS profile on `{platform}`{mode}. Select the host-side
NemoClaw harness (not the in-stack `vss-agent`) and follow the skill's documented
ordering: deploy the resolved Compose build, pass its readiness gate, resolve
the VSS origin, then bring up NemoClaw. Ensure the operational skill
`/{skill}` is selected for that deployment.

The following Harbor task will exercise only that operational skill through the
NemoClaw sandbox. Do not use individual deployment skills as an alternative to
`/vss-build-vision-ai`. Do not stop at a generated `resolved.yml`: complete the
host-side harness bring-up and verify the sandbox gateway answers before you
finish. Run autonomously and do not request confirmation.
"""


def _health_check_script() -> str:
    """Verifier for the contract handed from Build Vision AI to NemoClaw."""

    return """#!/bin/sh
set -eu
sandbox="${NEMOCLAW_SANDBOX_NAME:-skill-eval}"
port="${NEMOCLAW_DASHBOARD_PORT:-18789}"
gateway_port="${NEMOCLAW_GATEWAY_PORT:-8990}"
gateway="nemoclaw-$gateway_port"
if [ "$gateway_port" = 8080 ]; then gateway="nemoclaw"; fi
reward_dir="/logs/verifier"
mkdir -p "$reward_dir"
set +e
output="$(timeout 30 openshell sandbox exec --name "$sandbox" -g "$gateway" -- sh -lc \
  "code=\\$(curl --noproxy '*' -sS --connect-timeout 3 --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:$port/health) && { [ \\"\\$code\\" = 200 ] || [ \\"\\$code\\" = 401 ]; }" 2>&1)"
status=$?
set -e
case "$status" in
  0)
    printf 'NemoClaw sandbox %s gateway is healthy on %s\\n' "$sandbox" "$port"
    printf '1.0\\n' > "$reward_dir/reward.txt"
    ;;
  *)
    printf 'NemoClaw sandbox %s gateway is not healthy: %s\\n' "$sandbox" "$output" >&2
    printf '0.0\\n' > "$reward_dir/reward.txt"
    ;;
esac
"""


def create_bootstrap_task(
    *,
    destination: Path,
    source_task_toml: Path,
    spec_path: Path,
    skill: str,
    platform: str,
    repo_root: Path,
) -> Path:
    """Create a one-task Harbor project and return its project directory.

    Copying the original task metadata keeps worker requirements authoritative
    in the operational spec.  This helper never interprets GPU policy itself.
    """

    if not source_task_toml.is_file():
        raise FileNotFoundError(f"source task missing: {source_task_toml}")
    profile, deploy_mode = _spec_deployment(spec_path)
    task_dir = destination / BOOTSTRAP_TASK
    task_dir.mkdir(parents=True, exist_ok=False)
    (task_dir / "task.toml").write_text(
        source_task_toml.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (task_dir / "instruction.md").write_text(
        _instruction(
            skill=skill,
            platform=platform,
            profile=profile,
            deploy_mode=deploy_mode,
        ),
        encoding="utf-8",
    )
    environment = task_dir / "environment"
    environment.mkdir()
    (environment / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    solution = task_dir / "solution"
    solution.mkdir()
    (solution / "solve.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tests = task_dir / "tests"
    tests.mkdir()
    (tests / "test.sh").write_text(_health_check_script(), encoding="utf-8")

    build_skill = repo_root / "skills" / "vss-build-vision-ai"
    if not (build_skill / "SKILL.md").is_file():
        raise FileNotFoundError(f"Build Vision AI skill missing: {build_skill}")
    skills_dir = task_dir / "skills"
    skills_dir.mkdir()
    shutil.copytree(build_skill, skills_dir / "vss-build-vision-ai")
    return destination
