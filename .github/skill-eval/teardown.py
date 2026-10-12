# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render the spec's teardown as a host coding task, separate from expects[]."""

from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from pathlib import Path


def load_teardown(spec_path: Path) -> dict | None:
    if not spec_path.is_file():
        return None
    spec = json.loads(spec_path.read_text())
    config = spec.get("teardown")
    if config is None:
        return None
    if not isinstance(config, dict):
        raise TypeError("teardown must be an object")
    query, checks = config.get("query"), config.get("checks")
    timeout = config.get("timeout_sec", 600)
    if not isinstance(query, str) or "vss-build-vision-ai" not in query:
        raise ValueError("teardown.query must request vss-build-vision-ai")
    if (
        not isinstance(checks, list)
        or not checks
        or any(not isinstance(check, str) or not check.strip() for check in checks)
    ):
        raise ValueError("teardown.checks must be a nonempty list of checks")
    if type(timeout) is not int or not 60 <= timeout <= 1800:
        raise ValueError("teardown.timeout_sec must be an integer between 60 and 1800")
    return {"query": query, "checks": checks, "timeout_sec": timeout}


def deployment_environment(results_root: Path, chain_key: str) -> dict[str, str]:
    """Stable per-chain names shared by the setup query and teardown query."""
    digest = hashlib.sha256(
        f"{results_root.resolve()}:{chain_key}".encode()
    ).hexdigest()[:24]
    project = f"skill-eval-{digest}"
    return {
        "COMPOSE_PROJECT_NAME": project,
        "SKILL_EVAL_BUILD_DIR": f"_builds/{project}",
    }


def generate_task(config: dict, setup_task: Path, output: Path, repo: Path) -> Path:
    """Use the same hardware and verifier route as setup; mount the build skill."""
    metadata = tomllib.loads((setup_task / "task.toml").read_text()).get("metadata", {})
    output.mkdir(parents=True, exist_ok=True)
    (output / "instruction.md").write_text(
        "You are running inside a non-interactive evaluation harness. "
        "You are pre-authorized to perform the explicitly requested teardown "
        "autonomously, without asking for confirmation.\n\n" + config["query"] + "\n"
    )
    lines = [
        "[task]",
        'name = "nvidia-vss/vss-build-vision-ai-teardown"',
        'description = "Tear down the recorded evaluation deployment through the build skill"',
        'keywords = ["vss-build-vision-ai", "teardown"]',
        "",
        "[agent]",
        f"timeout_sec = {config['timeout_sec']}.0",
        "",
        "[environment]",
        'skills_dir = "/skills"',
        "",
        "[verifier.env]",
        'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
        'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
        'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
        # Each check's native SDK deadline stays within the verifier phase.
        'JUDGE_PER_CHECK_TIMEOUT_S = "120"',
        "",
        "[metadata]",
        'phase = "teardown"',
    ]
    for key in (
        "gpu_type",
        "gpu_count",
        "min_vram_gb_per_gpu",
        "min_root_disk_gb",
        "min_gpu_driver_version",
        "brev_instance",
        "platform",
    ):
        if key in metadata:
            lines.append(f"{key} = {json.dumps(metadata[key])}")
    (output / "task.toml").write_text("\n".join(lines) + "\n")
    environment = output / "environment"
    environment.mkdir(exist_ok=True)
    (environment / "Dockerfile").write_text("FROM scratch\n")
    tests = output / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "teardown.json").write_text(
        json.dumps({"expects": [config]}, indent=2) + "\n"
    )
    shutil.copyfile(
        repo / ".github/skill-eval/verifiers/generic_judge.py",
        tests / "generic_judge.py",
    )
    (tests / "test.sh").write_text(
        '#!/bin/bash\nset -uo pipefail\nTEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        'python3 "$TEST_DIR/generic_judge.py" --spec "$TEST_DIR/teardown.json" --step 1\n'
    )
    solution = output / "solution"
    solution.mkdir(exist_ok=True)
    (solution / "solve.sh").write_text(
        "#!/bin/bash\necho 'Use the bundled vss-build-vision-ai teardown workflow.'\n"
    )
    skill = output / "skills/vss-build-vision-ai"
    if skill.exists():
        shutil.rmtree(skill)
    shutil.copytree(repo / "skills/vss-build-vision-ai", skill)
    return output
