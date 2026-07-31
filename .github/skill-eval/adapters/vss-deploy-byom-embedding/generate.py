#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor routing tasks for the vss-deploy-byom-embedding skill."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

PLATFORMS = {
    "RTXPRO6000BW": {
        "short_name": "rtxpro6000bw",
        "gpu_type": "RTX PRO 6000",
        "min_vram_per_gpu": 0,
        "brev_search": "RTX PRO",
    },
}

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


def _render_spec(spec: dict, platform: str) -> dict:
    substitutions = {
        "platform": platform,
        "repo_root": "$HOME/video-search-and-summarization",
    }
    pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")

    def sub(value):
        if isinstance(value, str):
            return pattern.sub(lambda m: str(substitutions.get(m.group(1), m.group(0))), value)
        if isinstance(value, list):
            return [sub(v) for v in value]
        if isinstance(value, dict):
            return {k: sub(v) for k, v in value.items()}
        return value

    return sub(spec)


def _platforms_from_spec(spec: dict, platform_override: str | None) -> list[str]:
    declared = spec.get("resources", {}).get("platforms", {})
    platforms = [platform_override] if platform_override else sorted(declared)
    unknown = [platform for platform in platforms if platform not in PLATFORMS]
    if unknown:
        raise ValueError(f"unsupported platform(s): {unknown}")
    return platforms


def _gpu_count_for_platform(spec: dict, platform: str) -> int:
    value = spec.get("resources", {}).get("platforms", {}).get(platform, {}).get("gpu_count", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def generate_test_script(step: int, spec_name: str) -> str:
    return (
        "#!/bin/bash\n"
        "# vss-deploy-byom-embedding verifier delegates to the generic LLM-as-judge.\n"
        "set -euo pipefail\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
    )


def generate_solve_script() -> str:
    return "\n".join(
        [
            "#!/bin/bash",
            "# Gold solution placeholder: routing evals are judged from the agent response.",
            "set -euo pipefail",
            'echo "Use vss-deploy-byom-embedding for VideoPrism BYOM and vss-deploy-video-embedding for default RT-Embed deployment."',
            "",
        ]
    )


def copy_skill(skill_dir: Path, task_dir: Path) -> None:
    dst = task_dir / "skills" / "vss-deploy-byom-embedding"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("SKILL.md", "skill-card.md"):
        src = skill_dir / name
        if src.exists():
            shutil.copy2(src, dst / name)
    for name in ("references",):
        src = skill_dir / name
        if src.exists():
            shutil.copytree(src, dst / name)


def copy_generic_judge(tests_dir: Path) -> None:
    if not GENERIC_JUDGE.exists():
        raise FileNotFoundError(f"generic judge not found: {GENERIC_JUDGE}")
    shutil.copy2(GENERIC_JUDGE, tests_dir / "generic_judge.py")


def generate_task(platform: str, spec: dict, spec_path: Path, output_root: Path, skill_dir: Path) -> None:
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    rendered_spec = _render_spec(spec, platform)
    expects = rendered_spec.get("expects") or []
    gpu_count = _gpu_count_for_platform(spec, platform)
    spec_name = spec_path.name

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / "routing" / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        instruction = [
            PREAMBLE,
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            expect.get("query", ""),
            "",
            "Answer from the VSS skill documentation only. Do NOT deploy containers, "
            "pull images, or use credentials.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(instruction) + "\n")

        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        (step_dir / "task.toml").write_text(
            "\n".join(
                [
                    "[task]",
                    f'name = "nvidia-vss/vss-deploy-byom-embedding-routing-{platform_short}{step_suffix}"',
                    f'description = "RT-Embed BYOM routing query {idx}/{len(expects)} on {platform}"',
                    f'keywords = ["vss-deploy-byom-embedding", "rtvi-embed", "videoprism", "{platform}", "routing"]',
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
                    'skill = "vss-deploy-byom-embedding"',
                    f'platform = "{platform}"',
                    f'gpu_type = "{pspec["gpu_type"]}"',
                    f'brev_search = "{pspec["brev_search"]}"',
                    f"gpu_count = {gpu_count}",
                    f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
                    "min_root_disk_gb = 60",
                    f"step_index = {idx}",
                    f"step_count = {len(expects)}",
                    f"check_count = {len(expect.get('checks') or [])}",
                    "",
                ]
            )
        )

        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        test_script = tests_dir / "test.sh"
        test_script.write_text(generate_test_script(idx, spec_name))
        test_script.chmod(0o755)
        copy_generic_judge(tests_dir)
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))

        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        solve_script = solution_dir / "solve.sh"
        solve_script.write_text(generate_solve_script())
        solve_script.chmod(0o755)

        copy_skill(skill_dir, step_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="Dataset output root")
    parser.add_argument("--skill-dir", required=True, help="Path to skills/vss-deploy-byom-embedding")
    parser.add_argument("--spec", default=None, help="Path to routing spec")
    parser.add_argument("--platform", default=None, choices=list(PLATFORMS), help="Generate one platform")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    spec_path = Path(args.spec) if args.spec else skill_dir / "evals" / "routing.json"

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    spec = json.loads(spec_path.read_text())
    platforms = _platforms_from_spec(spec, args.platform)

    print("=== Inputs ===")
    print(f"  output_dir : {output_root}")
    print(f"  skill_dir  : {skill_dir}")
    print(f"  spec       : {spec_path}")
    print(f"  platforms  : {platforms}")
    print()

    for platform in platforms:
        print(f"  GEN  vss-deploy-byom-embedding/routing/{PLATFORMS[platform]['short_name']}")
        generate_task(platform, spec, spec_path, output_root, skill_dir)

    print()
    print(f"Generated {len(platforms)} task(s) under {output_root}/routing/")


if __name__ == "__main__":
    main()
