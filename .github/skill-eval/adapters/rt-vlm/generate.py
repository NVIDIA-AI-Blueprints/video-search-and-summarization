#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the rt-vlm skill.

The rt-vlm skill tests the RTVI VLM microservice API directly. The
current spec deploys only the standalone RT-VLM compose service using
the Docker Compose profile `bp_developer_alerts_2d_vlm`, then probes
the REST API on localhost:8018. It intentionally does not deploy a VSS
profile through `/deploy`.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PLATFORMS: dict[str, dict] = {
    "H100": {"short_name": "h100", "gpu_type": "H100", "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S": {"short_name": "l40s", "gpu_type": "L40S", "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW": {
        "short_name": "rtxpro6000bw",
        "gpu_type": "RTX PRO 6000",
        "min_vram_per_gpu": 96,
        "brev_search": "RTX PRO",
    },
    "DGX-SPARK": {"short_name": "spark", "gpu_type": "GB10", "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR": {"short_name": "thor", "gpu_type": "Thor", "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

DEFAULT_PLATFORM = "L40S"
DEFAULT_SPEC = "standalone_api.json"
COMPOSE_PROFILE = "bp_developer_alerts_2d_vlm"
GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/deploy` or any other "
    "setup action the trial requires."
)


def _substitute_spec(spec: dict, platform: str, mode: str) -> dict:
    substitutions = {
        "platform": platform,
        "mode": mode,
        "repo_root": "$HOME/video-search-and-summarization",
    }
    import re

    pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")

    def _sub(value):
        if isinstance(value, str):
            return pattern.sub(lambda m: str(substitutions.get(m.group(1), m.group(0))), value)
        if isinstance(value, list):
            return [_sub(v) for v in value]
        if isinstance(value, dict):
            return {k: _sub(v) for k, v in value.items()}
        return value

    return _sub(spec)


def _platform_modes_from_spec(spec: dict, platform_filter: str | None) -> list[tuple[str, str]]:
    declared = ((spec.get("resources") or {}).get("platforms") or {})
    if not declared:
        declared = {DEFAULT_PLATFORM: {"modes": ["standalone"]}}

    tasks: list[tuple[str, str]] = []
    for platform, cfg in declared.items():
        if platform_filter and platform != platform_filter:
            continue
        if platform not in PLATFORMS:
            continue
        for mode in (cfg or {}).get("modes") or ["standalone"]:
            tasks.append((platform, mode))
    return tasks or [(platform_filter or DEFAULT_PLATFORM, "standalone")]


def generate_test_script(step: int, spec_name: str) -> str:
    return (
        "#!/bin/bash\n"
        f"# rt-vlm verifier (step {step}): delegates to the generic\n"
        "# LLM-as-judge (.github/skill-eval/verifiers/generic_judge.py).\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
        "exit 0\n"
    )


def generate_solve_script(platform: str, mode: str) -> str:
    return (
        "#!/bin/bash\n"
        f"# Gold solution: rt-vlm on {platform}/{mode}\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 http://localhost:8018/v1/health/ready >/dev/null || {\n"
        "    echo 'RT-VLM is not ready — cannot solve rt-vlm task'\n"
        "    exit 1\n"
        "}\n"
        "echo 'RT-VLM is live — verifier will drive the assertions.'\n"
    )


def generate_task(
    platform: str,
    mode: str,
    spec: dict,
    output_root: Path,
    skill_dir: Path,
    deploy_skill_dir: Path | None,
) -> None:
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", DEFAULT_SPEC)).name or DEFAULT_SPEC
    rendered_spec = _substitute_spec(spec, platform, mode)

    for idx, expect in enumerate(rendered_spec.get("expects") or [], 1):
        step_dir = output_root / "standalone" / f"{platform_short}-{mode}"
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        instruction = [
            PREAMBLE,
            "",
            "Use `/rt-vlm` only. Deploy RT-VLM as a standalone compose service from "
            "`deployments/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`; do not use "
            "`/deploy`, `scripts/dev-profile.sh`, or a full VSS profile. The Docker "
            f"Compose profile that activates the service is `{COMPOSE_PROFILE}`.",
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            expect.get("query", ""),
            "",
            "## Environment notes",
            "",
            rendered_spec.get("env", ""),
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(instruction) + "\n")

        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/rt-vlm-standalone-{platform_short}-{mode}{step_suffix}"',
            f'description = "RT-VLM API query {idx}/{len(expects)} on {platform}/{mode}"',
            f'keywords = ["rt-vlm", "rtvi-vlm", "standalone", "{platform}", "{mode}"]',
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
            'skill = "rt-vlm"',
            'deployment = "standalone"',
            f'compose_profile = "{COMPOSE_PROFILE}"',
            f'platform = "{platform}"',
            f'mode = "{mode}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            # RT-VLM alerts real-time in remote-all still needs one local GPU
            # for the continuous video processor.
            "gpu_count = 1",
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            "min_root_disk_gb = 160",
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ]
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))

        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform, mode))

        for src, name in ((skill_dir, "rt-vlm"),):
            if src and src.exists():
                dst = step_dir / "skills" / name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--skill-dir", required=True)
    parser.add_argument("--deploy-skill-dir", default=None)
    parser.add_argument("--spec", default=None, help=f"Path to {DEFAULT_SPEC}")
    parser.add_argument("--platform", default=None, choices=list(PLATFORMS.keys()))
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    spec_path = Path(args.spec) if args.spec else (skill_dir / "eval" / DEFAULT_SPEC)

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)
    tasks = _platform_modes_from_spec(spec, args.platform)

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  tasks        : {tasks}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()

    for platform, mode in tasks:
        print(f"  GEN  rt-vlm/standalone/{PLATFORMS[platform]['short_name']}-{mode}")
        generate_task(platform, mode, spec, output_root, skill_dir, deploy_skill_dir)

    print()
    print(f"Generated {len(tasks)} task(s) under {output_root}/standalone/")


if __name__ == "__main__":
    main()
