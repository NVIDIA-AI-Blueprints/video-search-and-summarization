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
import shlex
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
the `{profile}` VSS profile on `{platform}`{mode}. Select no conversational
harness: do not deploy the in-stack `vss-agent` or bring up NemoClaw. Follow the
skill's documented ordering through the resolved Compose build and its readiness
gate. Ensure the operational skill `/{skill}` is selected for that deployment.

The task verifier attaches the host-side NemoClaw sandbox after your deployment
is ready. Do not use individual deployment skills as an alternative to
`/vss-build-vision-ai`. Do not stop at a generated `resolved.yml`: complete the
deployment and readiness gate before you finish. Run autonomously and do not
request confirmation.
"""


def _nemoclaw_setup_script(skill: str) -> str:
    """Return the canonical minimal host-side setup for one operational skill."""

    quoted_skill = shlex.quote(skill)
    return f"""repo="${{VSS_REPO_DIR:-$HOME/video-search-and-summarization}}"
sandbox="${{NEMOCLAW_SANDBOX_NAME:-skill-eval}}"
policy="$repo/assets/vss_nemoclaw_policy.yaml"
skill_dir="$repo/skills/operations/{quoted_skill}"

command -v nemoclaw >/dev/null 2>&1 || fail "nemoclaw CLI is unavailable"
command -v openshell >/dev/null 2>&1 || fail "openshell CLI is unavailable"
[ -f "$policy" ] || fail "VSS NemoClaw policy is missing: $policy"
[ -f "$skill_dir/SKILL.md" ] || fail "operational skill is missing: $skill_dir"

if ! timeout 30 openshell sandbox get "$sandbox" >/dev/null 2>&1; then
  echo "Onboarding NemoClaw sandbox $sandbox" >&2
  timeout --signal=TERM --kill-after=30 900 \\
    nemoclaw onboard --non-interactive --agent openclaw \\
    || fail "NemoClaw onboarding failed"
fi

timeout --signal=TERM --kill-after=30 180 \\
  nemoclaw "$sandbox" policy-add --from-file "$policy" --yes \\
  || fail "VSS NemoClaw policy setup failed"
timeout --signal=TERM --kill-after=30 180 \\
  nemoclaw "$sandbox" skill install "$skill_dir" \\
  || fail "operational skill installation failed"
"""


def _health_check_script(skill: str) -> str:
    """Provision and verify the direct Build Vision AI -> NemoClaw handoff."""

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
{_nemoclaw_setup_script(skill)}
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
    (tests / "test.sh").write_text(_health_check_script(skill), encoding="utf-8")

    build_skill = repo_root / "skills" / "vss-build-vision-ai"
    if not (build_skill / "SKILL.md").is_file():
        raise FileNotFoundError(f"Build Vision AI skill missing: {build_skill}")
    skills_dir = task_dir / "skills"
    skills_dir.mkdir()
    shutil.copytree(build_skill, skills_dir / "vss-build-vision-ai")
    return destination
