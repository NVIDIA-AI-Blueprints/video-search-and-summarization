#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Create spec-declared NemoClaw provisioning tasks for an eval.

The evaluation spec owns the setup instructions. This module only turns those
instructions into ordinary coding-agent Harbor tasks and adds a final read-only
NemoClaw readiness check.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path


SETUP_TASK_PREFIX = "nemoclaw-setup"


def _spec_setup_queries(spec_path: Path, *, skill: str, platform: str) -> list[str]:
    """Return the exact NemoClaw setup queries declared by an eval spec."""

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError(f"spec is not a JSON object: {spec_path}")
    try:
        setup = spec["harness"]["nemoclaw"]["setup"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"spec must declare harness.nemoclaw.setup: {spec_path}"
        ) from exc
    if not isinstance(setup, list) or not setup:
        raise ValueError(
            f"spec harness.nemoclaw.setup must be a non-empty list: {spec_path}"
        )
    queries: list[str] = []
    for index, item in enumerate(setup, 1):
        if not isinstance(item, dict):
            raise ValueError(
                f"spec harness.nemoclaw.setup[{index}] is not an object: {spec_path}"
            )
        query = item.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(
                "spec harness.nemoclaw.setup"
                f"[{index}].query must be a non-empty string: {spec_path}"
            )
        queries.append(
            query.replace("{{platform}}", platform).replace("{{skill}}", skill)
        )
    return queries


def _phase_complete_script() -> str:
    return "#!/bin/sh\nmkdir -p /logs/verifier\nprintf '1.0\\n' > /logs/verifier/reward.txt\n"


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
  echo "Build Vision AI final result:" >&2
  jq -r 'select(.type == "result") | .result // .error // empty' \\
    /logs/agent/claude-code.txt 2>/dev/null | tail -n 40 >&2 || true
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
    """Create the spec-declared setup project and return its directory.

    Copying the original task metadata keeps worker requirements authoritative
    in the operational spec.  This helper never interprets GPU policy itself.
    """

    if not source_task_toml.is_file():
        raise FileNotFoundError(f"source task missing: {source_task_toml}")
    build_skill = repo_root / "skills" / "vss-build-vision-ai"
    if not (build_skill / "SKILL.md").is_file():
        raise FileNotFoundError(f"Build Vision AI skill missing: {build_skill}")
    queries = _spec_setup_queries(spec_path, skill=skill, platform=platform)
    for index, instruction in enumerate(queries, 1):
        task_name = f"{SETUP_TASK_PREFIX}-{index}"
        verifier = (
            _health_check_script()
            if index == len(queries)
            else _phase_complete_script()
        )
        task_dir = destination / task_name
        task_dir.mkdir(parents=True, exist_ok=False)
        (task_dir / "task.toml").write_text(
            source_task_toml.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (task_dir / "instruction.md").write_text(instruction, encoding="utf-8")
        environment = task_dir / "environment"
        environment.mkdir()
        (environment / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        solution = task_dir / "solution"
        solution.mkdir()
        (solution / "solve.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tests = task_dir / "tests"
        tests.mkdir()
        (tests / "test.sh").write_text(verifier, encoding="utf-8")
        skills_dir = task_dir / "skills"
        skills_dir.mkdir()
        shutil.copytree(build_skill, skills_dir / "vss-build-vision-ai")
    return destination
