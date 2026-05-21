#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-generate-video-calibration skill.

The vss-generate-video-calibration skill exercises the Auto-Calibration
Microservice (AMC) — deploying it, running camera calibration (via local
MP4s, RTSP streams, or sample dataset), and verifying the results. It
does NOT require a pre-deployed VSS stack; the skill itself handles
bringing up `vss-auto-calibration` and `vss-auto-calibration-ui` via
Docker Compose.

Spec: skills/vss-generate-video-calibration/eval/auto-calibration.json
  - No `profile` (no /vss-deploy-profile prerequisite needed)
  - Single platform: RTXPRO6000BW (gpu_count: 1)
  - 11 steps (expects[]) covering deploy, verify, calibrate, and error paths

## Directory layout

    .github/skill-eval/datasets/vss-generate-video-calibration/<spec_stem>/<platform>/
        step-<k>/
            task.toml
            instruction.md
            tests/test.sh
            tests/<spec>.json
            tests/generic_judge.py
            solution/solve.sh
            skills/vss-generate-video-calibration/
            environment/Dockerfile      (FROM scratch; BrevEnvironment takes over)

`<spec_stem>` is the spec filename with `.json` dropped.

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-generate-video-calibration/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-generate-video-calibration \\
        --skill-dir skills/vss-generate-video-calibration \\
        [--spec skills/vss-generate-video-calibration/eval/auto-calibration.json]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — same table as other adapters; spec.resources.platforms narrows it.
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":          {"short_name": "h100",          "gpu_type": "H100",         "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S":          {"short_name": "l40s",          "gpu_type": "L40S",         "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW":  {"short_name": "rtxpro6000bw",  "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96, "brev_search": "RTX PRO"},
    "DGX-SPARK":     {"short_name": "spark",         "gpu_type": "GB10",         "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR":      {"short_name": "thor",          "gpu_type": "Thor",         "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

DEFAULT_PLATFORM = "RTXPRO6000BW"

# Prepended to every instruction.md so the skill's own HITL bypass
# clause fires. Skills default to "ask the user" before deployment actions;
# in CI there's no user, so without this preamble the agent either stalls
# or falls through to a localhost default.
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for a
    single step's checks. Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# vss-generate-video-calibration verifier (step {step}): delegates to the\n"
        "# generic LLM-as-judge (.github/skill-eval/verifiers/generic_judge.py).\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
        "exit 0\n"
    )


def generate_solve_script(platform: str) -> str:
    """Gold solution — the AMC trial verifier runs checks independently.
    The solution script asserts the MS is live, then defers to the verifier."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-generate-video-calibration on {platform}\n"
        "set -euo pipefail\n"
        "\n"
        "# The skill manages its own AMC deployment; no external VSS stack required.\n"
        "# Try default AMC port (8010) and env-var override.\n"
        "AMC_PORT=${VSS_AUTO_CALIBRATION_PORT:-8010}\n"
        'curl -sf --connect-timeout 5 "http://localhost:${AMC_PORT}/v1/ready" '
        ">/dev/null || {\n"
        "    echo 'AMC is not running — cannot solve vss-generate-video-calibration task'\n"
        "    exit 1\n"
        "}\n"
        "echo 'AMC is live — verifier will drive the checks.'\n"
    )


GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


def _platforms_from_spec(spec: dict) -> list[str]:
    declared = ((spec.get("resources") or {}).get("platforms") or {})
    if not declared:
        return [DEFAULT_PLATFORM]
    return [p for p in declared if p in PLATFORMS] or [DEFAULT_PLATFORM]


def generate_task(platform: str, spec_stem: str, spec: dict, output_root: Path,
                  skill_dir: Path) -> None:
    """Emit one Harbor task directory per entry in spec['expects']
    (step-<k>/ subdirs under <spec_stem>/<platform>/)."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = f"{spec_stem}.json"

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / spec_stem / platform_short / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md — one step's query + environment notes.
        # Checks are NOT included — they live in tests/ and the verifier
        # evaluates them independently (no teaching to the tape).
        lines = [
            PREAMBLE,
            "",
            f"Use the `/vss-generate-video-calibration` skill on this `{platform}` host.",
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            expect.get("query", ""),
            "",
            "## Environment notes",
            "",
            spec.get("env", ""),
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(lines) + "\n")

        # task.toml
        gpu_count = int(
            ((spec.get("resources") or {}).get("platforms") or {})
            .get(platform, {})
            .get("gpu_count", 1)
            or 1
        )
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-generate-video-calibration-{spec_stem}-{platform_short}-step-{idx}"',
            f'description = "vss-generate-video-calibration query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-generate-video-calibration", "amc", "{platform}"]',
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
            'skill = "vss-generate-video-calibration"',
            # No profile — the skill deploys its own AMC service; no /vss-deploy-profile
            # prerequisite is required. Omitting `profile` tells brev_env.py there
            # is no deploy prerequisite to check.
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            f'gpu_count = {gpu_count}',
            "requires_deployed_vss = false",
            # prerequisite_deploy_mode is alerts-only — not applicable here.
            *([f'prerequisite_deploy_mode = "{spec["prerequisite_deploy_mode"]}"']
              if spec.get("prerequisite_deploy_mode") else []),
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ]
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        # environment/
        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        # tests/ — wrapper + generic judge + spec copy
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        spec_src = skill_dir / "eval" / spec_name
        if spec_src.exists():
            shutil.copy(spec_src, tests_dir / spec_name)
        else:
            (tests_dir / spec_name).write_text(json.dumps(spec, indent=2))

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # skills/ — only vss-generate-video-calibration (the primary skill under test).
        # The spec's `skills` list controls what gets copied; since only
        # vss-generate-video-calibration is listed there is no secondary skill to bundle.
        spec_skills: set[str] = set(spec.get("skills") or [])
        primary = "vss-generate-video-calibration"
        skill_names = {primary} | (spec_skills - {primary})
        # Only copy skills we have paths for.
        skill_paths = {primary: skill_dir}
        for name in skill_names:
            src = skill_paths.get(name)
            if src and src.exists():
                dst = step_dir / "skills" / name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Dataset output root (e.g. .github/skill-eval/datasets/vss-generate-video-calibration)",
    )
    parser.add_argument(
        "--skill-dir", required=True,
        help="Path to skills/vss-generate-video-calibration",
    )
    parser.add_argument(
        "--spec", default=None,
        help="Path to a spec JSON file "
             "(default: <skill-dir>/eval/auto-calibration.json)",
    )
    parser.add_argument(
        "--platform", default=None, choices=list(PLATFORMS.keys()),
        help="Generate for one platform only (overrides spec.resources.platforms)",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    spec_path = Path(args.spec) if args.spec else (skill_dir / "eval" / "auto-calibration.json")

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)
    spec_stem = spec_path.stem  # e.g. "auto-calibration"

    platforms = [args.platform] if args.platform else _platforms_from_spec(spec)

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  spec_stem    : {spec_stem}")
    print(f"  profile      : (none — no deploy prerequisite)")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()
    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  vss-generate-video-calibration/{spec_stem}/{task_id}")
        generate_task(platform, spec_stem, spec, output_root, skill_dir)
    print()
    print(f"Generated {len(platforms)} platform(s) under {output_root}/{spec_stem}/")


if __name__ == "__main__":
    main()
