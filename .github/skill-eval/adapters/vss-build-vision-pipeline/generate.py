#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks from the vision-pipeline skill's scenario specs.

Uses the shared Brev environment and generic judge. Does not start instances,
install SDKs, or run GPU workloads. Each spec currently contains one task.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

SKILL = "vss-build-vision-pipeline"
HARNESS = Path(__file__).resolve().parents[2]
REPO = HARNESS.parents[1]
PLATFORMS = {
    "L40S": ("L40S", 48),
    "H100": ("H100", 80),
    "RTXPRO6000BW": ("RTX PRO 6000", 96),
}


def render(value, platform):
    if isinstance(value, str):
        for key, replacement in {
            "platform": platform,
            "repo_root": "$HOME/video-search-and-summarization",
        }.items():
            value = value.replace("{{" + key + "}}", replacement)
        if re.search(r"\{\{.*?\}\}", value):
            raise ValueError("Unresolved template variable")
        return value
    if isinstance(value, list):
        return [render(v, platform) for v in value]
    if isinstance(value, dict):
        return {k: render(v, platform) for k, v in value.items()}
    return value


def generate(spec_path, platform, output_dir, skill_dir):
    spec = json.loads(spec_path.read_text())
    if spec.get("skills") != [SKILL]:
        raise ValueError(f"{spec_path}: expected only {SKILL}")
    if platform not in spec.get("resources", {}).get("platforms", {}):
        raise ValueError(f"{spec_path}: platform {platform} is not declared")
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", spec_path.stem):
        raise ValueError("Unsafe spec filename")
    expects = spec.get("expects", [])
    if len(expects) != 1:
        raise ValueError("This adapter supports the skill's single-step specs only")
    step = expects[0]
    if not isinstance(step.get("query"), str) or not step["query"].strip():
        raise ValueError("Expected a nonempty query")
    checks = step.get("checks")
    if not isinstance(checks, list) or not checks or not all(isinstance(c, str) and c.strip() for c in checks):
        raise ValueError("Expected nonempty natural-language checks")
    config = spec["resources"]["platforms"][platform]
    gpu_count = config.get("gpu_count", 1)
    if type(gpu_count) is not int or gpu_count < 1:
        raise ValueError("gpu_count must be a positive integer")
    spec = render(spec, platform)
    target = output_dir / spec_path.stem / platform.lower()
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite an existing task: {target}")
    for subdir in ("tests", "solution", "environment", "skills"):
        (target / subdir).mkdir(parents=True, exist_ok=True)
    # The test request is passed verbatim; the skill supplies task guidance.
    (target / "instruction.md").write_text(spec["expects"][0]["query"])
    gpu_type, vram = PLATFORMS[platform]
    lines = [
        "[task]", f'name = "nvidia-vss/{SKILL}-{spec_path.stem}-{platform.lower()}"',
        f'description = "Build and verify {spec_path.stem} on {platform}"',
        "", "[agent]", "timeout_sec = 600.0",
        "", "[environment]", 'skills_dir = "/skills"',
        "", "[verifier.env]",
    ]
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"):
        lines.append(f'{name} = "${{{name}}}"')
    lines += [
        'JUDGE_MAX_TURNS = "60"',
        "", "[metadata]", f'skill = "{SKILL}"',
        f'profile = "{spec_path.stem}"', f'platform = "{platform}"',
        f'gpu_type = "{gpu_type}"', f"gpu_count = {gpu_count}",
        f'brev_search = "{gpu_type}"', f"min_vram_gb_per_gpu = {vram}",
        "min_root_disk_gb = 100", "requires_deployed_vss = false",
        "runtime_deploy = true", "step_index = 1", "step_count = 1",
        f"check_count = {len(checks)}", "",
    ]
    (target / "task.toml").write_text("\n".join(lines))
    (target / "environment/Dockerfile").write_text("FROM scratch\n")
    (target / "tests" / spec_path.name).write_text(json.dumps(spec, indent=2) + "\n")
    shutil.copy2(HARNESS / "verifiers/generic_judge.py", target / "tests/generic_judge.py")
    (target / "tests/test.sh").write_text(
        '#!/bin/bash\nset -euo pipefail\n'
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        f'python3 "$TEST_DIR/generic_judge.py" --spec "$TEST_DIR/{spec_path.name}" --step 1\n'
    )
    # A fake oracle must not be mistaken for an implementation of these tasks.
    (target / "solution/solve.sh").write_text(
        '#!/bin/bash\nset -euo pipefail\n'
        'echo "No oracle implementation is supplied; run the agent under test." >&2\nexit 1\n'
    )
    for name in ("tests/test.sh", "solution/solve.sh"):
        (target / name).chmod(0o755)
    shutil.copytree(skill_dir, target / "skills" / SKILL,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skill-dir", type=Path, default=REPO / "skills" / SKILL)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--platform", choices=sorted(PLATFORMS))
    args = parser.parse_args()
    specs = [args.spec] if args.spec else sorted((args.skill_dir / "evals").glob("*.json"))
    if not specs:
        parser.error("No evaluation specs found")
    for path in specs:
        spec = json.loads(path.read_text())
        platforms = [args.platform] if args.platform else list(spec["resources"]["platforms"])
        for platform in platforms:
            generate(path, platform, args.output_dir, args.skill_dir)


if __name__ == "__main__":
    main()
