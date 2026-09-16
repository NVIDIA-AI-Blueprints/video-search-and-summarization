#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate zero-GPU Harbor tasks for profile-vllm-performance specs."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-build-vision-ai` or any other "
    "setup action the trial requires."
)
GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


def test_script(step: int, spec_name: str) -> str:
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
    )


def generate(spec_path: Path, output_root: Path, skill_dir: Path) -> None:
    spec = json.loads(spec_path.read_text())
    platforms = ((spec.get("resources") or {}).get("platforms") or {})
    if platforms != {"ANY": {"gpu_count": 0}}:
        raise ValueError("profile-vllm-performance specs must declare ANY with gpu_count 0")
    expects = spec.get("expects") or []
    if not expects:
        raise ValueError("spec must contain at least one expects entry")

    for step, expect in enumerate(expects, 1):
        task_dir = output_root / spec_path.stem / "any"
        if len(expects) > 1:
            task_dir = task_dir / f"step-{step}"
        task_dir.mkdir(parents=True, exist_ok=True)

        instruction = "\n".join([
            PREAMBLE,
            "",
            expect.get("query", ""),
            "",
            "Use the `profile-vllm-performance` skill and answer from the supplied evidence.",
            "Do not launch profilers, benchmarks, deployments, or GPU work.",
            "",
        ])
        (task_dir / "instruction.md").write_text(instruction)

        suffix = f"-step-{step}" if len(expects) > 1 else ""
        task_toml = "\n".join([
            "[task]",
            f'name = "nvidia-vss/profile-vllm-performance-{spec_path.stem}{suffix}"',
            f'description = "profile-vllm-performance scenario {spec_path.stem}"',
            'keywords = ["profile-vllm-performance", "vllm", "benchmarking"]',
            "",
            "[agent]",
            "timeout_sec = 600.0",
            "",
            "[environment]",
            'skills_dir = "/skills"',
            "",
            "[verifier.env]",
            'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
            'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
            'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
            "",
            "[metadata]",
            'skill = "profile-vllm-performance"',
            'platform = "ANY"',
            'gpu_type = ""',
            'brev_search = ""',
            "min_vram_gb_per_gpu = 0",
            "gpu_count = 0",
            f"step_index = {step}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ])
        (task_dir / "task.toml").write_text(task_toml)

        env_dir = task_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        tests_dir = task_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(test_script(step, spec_path.name))
        shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        shutil.copy(spec_path, tests_dir / spec_path.name)

        solution_dir = task_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(
            "#!/bin/bash\nset -euo pipefail\n"
            "echo 'No service setup is required for this reasoning task.'\n"
        )

        bundled_skill = task_dir / "skills" / "profile-vllm-performance"
        if bundled_skill.exists():
            shutil.rmtree(bundled_skill)
        shutil.copytree(skill_dir, bundled_skill)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--skill-dir", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--platform", choices=("ANY",), default="ANY")
    args = parser.parse_args()

    generate(Path(args.spec), Path(args.output_dir), Path(args.skill_dir))


if __name__ == "__main__":
    main()
