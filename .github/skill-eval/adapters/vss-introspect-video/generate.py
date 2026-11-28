#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate deterministic Harbor tasks for vss-introspect-video eval specs.

The mocked-loop spec exercises skill orchestration without deploying VSS or
contacting a backend. The prompt supplies deterministic planner, memory, and
bounded-VLM fixture outputs; the agent must still use the skill's canonical
budget and ledger contracts and produce auditable artifacts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

PLATFORMS: dict[str, dict[str, Any]] = {
    "H100": {
        "short_name": "h100",
        "gpu_type": "H100",
        "min_vram_per_gpu": 80,
        "brev_search": "H100",
    },
    "L40S": {
        "short_name": "l40s",
        "gpu_type": "L40S",
        "min_vram_per_gpu": 48,
        "brev_search": "L40S",
    },
    "RTXPRO6000BW": {
        "short_name": "rtxpro6000bw",
        "gpu_type": "RTX PRO 6000",
        "min_vram_per_gpu": 96,
        "brev_search": "RTX PRO",
    },
    "DGX-SPARK": {
        "short_name": "spark",
        "gpu_type": "GB10",
        "min_vram_per_gpu": 96,
        "brev_search": "GB10",
    },
    "IGX-THOR": {
        "short_name": "thor",
        "gpu_type": "Thor",
        "min_vram_per_gpu": 64,
        "brev_search": "Thor",
    },
}

DEFAULT_PLATFORM = "L40S"
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-build-vision-ai` or any other "
    "setup action the trial requires."
)
MOCKED_LOOP_CLAUSE = (
    " This is a fixture-backed orchestration evaluation. Do not deploy VSS, "
    "contact any service, or replace fixture outputs with live calls. Use only "
    "the bundled skill contracts and standard-library ledger utility. Preserve "
    "the distinction between declared ordinary VSS command fixtures and actual "
    "backend execution."
)
GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


def generate_test_script(step: int, spec_name: str) -> str:
    """Return the deterministic generic-judge wrapper for one spec step."""
    return (
        "#!/bin/bash\n"
        f"# vss-introspect-video verifier (step {step})\n"
        "set -uo pipefail\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
        "exit 0\n"
    )


def generate_solve_script(platform: str) -> str:
    """Return a gold-script smoke check that never contacts VSS."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-introspect-video mocked loop on {platform}\n"
        "set -euo pipefail\n"
        'SKILL_DIR="${SKILL_DIR:-/skills/vss-introspect-video}"\n'
        'test -f "${SKILL_DIR}/config/ledger-budgets.json"\n'
        'test -f "${SKILL_DIR}/scripts/evidence_ledger.py"\n'
        'python3 "${SKILL_DIR}/scripts/evidence_ledger.py" --help >/dev/null\n'
        "echo 'Deterministic ledger contracts are available; no VSS backend contacted.'\n"
    )


def _platforms_from_spec(spec: dict[str, Any]) -> list[str]:
    declared = (spec.get("resources") or {}).get("platforms") or {}
    return [name for name in declared if name in PLATFORMS] or [DEFAULT_PLATFORM]


def _render(value: Any, *, platform: str) -> Any:
    if isinstance(value, str):
        return value.replace("{{platform}}", platform)
    if isinstance(value, list):
        return [_render(item, platform=platform) for item in value]
    if isinstance(value, dict):
        return {key: _render(item, platform=platform) for key, item in value.items()}
    return value


def generate_task(
    platform: str,
    profile: str,
    spec: dict[str, Any],
    output_root: Path,
    skill_dir: Path,
    planner_skill_dir: Path | None,
) -> None:
    """Render one deterministic Harbor task directory per expects entry."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    rendered = _render(spec, platform=platform)
    rendered_for_tests = dict(rendered)
    rendered_for_tests.pop("_source_path", None)
    expects = rendered.get("expects") or []
    spec_name = Path(spec.get("_source_path", "mocked_loop.json")).name
    declared_platform = ((spec.get("resources") or {}).get("platforms") or {}).get(
        platform
    ) or {}
    gpu_count = int(declared_platform.get("gpu_count", 0))

    for index, expect in enumerate(expects, 1):
        step_dir = output_root / profile / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{index}"
        step_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"-step-{index}" if len(expects) > 1 else ""

        instruction = "\n".join(
            [
                PREAMBLE + MOCKED_LOOP_CLAUSE,
                "",
                f"## Query {index} of {len(expects)}",
                "",
                expect.get("query", ""),
                "",
                "Run autonomously without prompting for confirmation.",
                "",
            ]
        )
        (step_dir / "instruction.md").write_text(instruction + "\n", encoding="utf-8")

        task_toml = "\n".join(
            [
                "[task]",
                f'name = "nvidia-vss/vss-introspect-video-{profile}-{platform_short}{suffix}"',
                f'description = "vss-introspect-video mocked loop {index}/{len(expects)} on {platform}"',
                f'keywords = ["vss-introspect-video", "evidence-ledger", "mocked-loop", "{platform}"]',
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
                'skill = "vss-introspect-video"',
                f'platform = "{platform}"',
                f'gpu_type = "{pspec["gpu_type"]}"',
                f'brev_search = "{pspec["brev_search"]}"',
                f"min_vram_gb_per_gpu = {pspec['min_vram_per_gpu']}",
                f"gpu_count = {gpu_count}",
                f"step_index = {index}",
                f"step_count = {len(expects)}",
                f"check_count = {len(expect.get('checks') or [])}",
                "",
            ]
        )
        (step_dir / "task.toml").write_text(task_toml, encoding="utf-8")

        environment = step_dir / "environment"
        environment.mkdir(exist_ok=True)
        (environment / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

        tests = step_dir / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "test.sh").write_text(
            generate_test_script(index, spec_name), encoding="utf-8"
        )
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests / "generic_judge.py")
        (tests / spec_name).write_text(
            json.dumps(rendered_for_tests, indent=2) + "\n", encoding="utf-8"
        )

        solution = step_dir / "solution"
        solution.mkdir(exist_ok=True)
        (solution / "solve.sh").write_text(
            generate_solve_script(platform), encoding="utf-8"
        )

        for source, name in (
            (skill_dir, "vss-introspect-video"),
            (planner_skill_dir, "vss-generate-evidence-plan"),
        ):
            if source and source.exists():
                destination = step_dir / "skills" / name
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(source, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--skill-dir", required=True)
    parser.add_argument("--planner-skill-dir", default=None)
    parser.add_argument("--spec", default=None)
    parser.add_argument("--platform", choices=list(PLATFORMS), default=None)
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    planner_skill_dir = Path(args.planner_skill_dir) if args.planner_skill_dir else None
    spec_path = (
        Path(args.spec) if args.spec else skill_dir / "evals" / "mocked_loop.json"
    )
    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        raise SystemExit(1)

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["_source_path"] = str(spec_path)
    profile = spec.get("profile", "mocked-loop")
    platforms = [args.platform] if args.platform else _platforms_from_spec(spec)

    for platform in platforms:
        generate_task(
            platform,
            profile,
            spec,
            output_root,
            skill_dir,
            planner_skill_dir,
        )
        print(f"GEN vss-introspect-video/{profile}/{PLATFORMS[platform]['short_name']}")


if __name__ == "__main__":
    main()
