#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Create the deployment and NemoClaw provisioning task for an eval.

The task is intentionally a tiny Harbor task, not another deployment
implementation. Its agent follows ``/vss-build-vision-ai`` on the remote
worker to deploy and validate the Compose build. Its verifier then uses the
existing NemoClaw CLI contract to attach the named sandbox to that ready
deployment. Harbor only supplies the normal coding-agent execution and the
same Brev worker that the subsequent operational scenarios use.
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
the `{profile}` VSS profile on `{platform}`{mode}, with NemoClaw as its only
conversational harness. Follow the skill's documented ordering through the
resolved Compose build, its readiness gate, and the host-side NemoClaw bring-up.
Use the sandbox name and model-provider settings supplied in the environment and
ensure the operational skill `/{skill}` is installed in that sandbox.

Do not use individual deployment skills as an alternative to
`/vss-build-vision-ai`. Do not stop at a generated `resolved.yml` or a ready VSS
deployment: complete the Build Vision AI NemoClaw handoff before you finish.
Run autonomously and do not request confirmation.
"""


def _health_check_script() -> str:
    """Verify the direct Build Vision AI -> NemoClaw handoff."""

    return f"""#!/bin/sh
set -u
sandbox="${{NEMOCLAW_SANDBOX_NAME:-skill-eval}}"
port="${{NEMOCLAW_DASHBOARD_PORT:-18789}}"
reward_dir="/logs/verifier"
mkdir -p "$reward_dir"
fail() {{
  printf '%s\\n' "$1" >&2
  printf '0.0\\n' > "$reward_dir/reward.txt"
  exit 0
}}
command -v nemoclaw >/dev/null 2>&1 || fail "Build Vision AI did not install the nemoclaw CLI"
command -v openshell >/dev/null 2>&1 || fail "Build Vision AI did not install the openshell CLI"
if ! timeout 30 openshell sandbox get "$sandbox" >/dev/null 2>&1; then
  echo "Recent Build Vision AI NemoClaw setup errors:" >&2
  find "${{VSS_REPO_DIR:-$HOME/video-search-and-summarization}}/_builds" \\
    -name nemoclaw-setup.log -type f -mmin -120 -print0 2>/dev/null \\
    | xargs -0 -r grep -Eai 'error|failed|failure|traceback|exception|assert' \\
    | tail -n 80 >&2 || true
  fail "Build Vision AI did not create the NemoClaw sandbox $sandbox"
fi
# On a warm worker the default dashboard port can be occupied. NemoClaw then
# selects the next available 1878x/1879x port; detect it once and leave the
# result where headless_runner reads its runtime contract.
gateway_ready=0
for candidate in "$port" 18789 18790 18791 18792 18793 18794 18795 18796 18797 18798 18799; do
  if timeout 15 openshell sandbox exec --name "$sandbox" -- sh -lc \
    "code=\\$(curl --noproxy '*' -sS --connect-timeout 3 --max-time 10 -o /dev/null -w '%{{http_code}}' http://127.0.0.1:$candidate/health) && [ \\"\\$code\\" = 200 -o \\"\\$code\\" = 401 ]" >/dev/null 2>&1; then
    port="$candidate"
    gateway_ready=1
    break
  fi
done
if [ "$gateway_ready" -eq 1 ]; then
  mkdir -p /tmp/skill-eval/nemoclaw
  printf 'export NEMOCLAW_DASHBOARD_PORT=%s\\n' "$port" > /tmp/skill-eval/nemoclaw/nemoclaw.env
fi
set +e
output="$(timeout 30 openshell sandbox exec --name "$sandbox" -- sh -lc \
  "code=\\$(curl --noproxy '*' -sS --connect-timeout 3 --max-time 10 -o /dev/null -w '%{{http_code}}' http://127.0.0.1:$port/health) && {{ [ \\"\\$code\\" = 200 ] || [ \\"\\$code\\" = 401 ]; }}" 2>&1)"
status=$?
set -e
case "$status" in
  0)
    printf 'NemoClaw sandbox %s gateway is healthy on %s\\n' "$sandbox" "$port"
    printf '1.0\\n' > "$reward_dir/reward.txt"
    ;;
  *)
    printf 'NemoClaw sandbox %s gateway is not healthy: %s\\n' "$sandbox" "$output" >&2
    # The bootstrap runs on the Brev host, so record the host-side OpenShell
    # view here. This distinguishes a failed direct onboard from a gateway
    # that existed during onboarding but died before Harbor verified it.
    # These commands expose sandbox metadata only; no token/config dump and no
    # repair action belongs in the eval harness.
    printf '%s\\n' 'OpenShell sandbox state:' >&2
    timeout 15 openshell sandbox get "$sandbox" >&2 || true
    printf '%s\\n' 'NemoClaw sandbox status:' >&2
    timeout 15 nemoclaw "$sandbox" status >&2 || true
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
