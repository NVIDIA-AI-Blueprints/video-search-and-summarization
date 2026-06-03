#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-generate-video-calibration skill.

The vss-generate-video-calibration skill deploys and drives AMC (Auto
Multi-Camera Calibration) — a service that calibrates camera extrinsics
from video files, RTSP streams, or the bundled sample dataset. The
current spec (`skills/vss-generate-video-calibration/evals/auto-calibration.json`)
declares a multi-step expects list (11 queries) covering deploy, VGGT,
verify, missing-NGC, videos calibration, ground-truth calibration, RTSP
calibration, RTSP-without-VIOS, sample-dataset, end-to-end, and
MS-down-recovery.

The spec declares one platform: RTXPRO6000BW (gpu_count: 1).

## Deploy model

The spec's first query instructs the agent to deploy AMC using
`/vss-generate-video-calibration`. No `/vss-deploy-profile` prerequisite
is needed — the skill handles its own deployment via
`references/deploy-auto-calibration-service.md`.

## Directory layout

    datasets/vss-generate-video-calibration/auto-calibration/<platform>/
        step-1/  ... step-N/
            task.toml
            instruction.md
            tests/test.sh
            tests/generic_judge.py
            tests/auto-calibration.json
            solution/solve.sh
            skills/vss-generate-video-calibration/  (full skill copy)
            environment/Dockerfile

One step-dir per `expects[]` entry. All steps share the same platform.

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-generate-video-calibration/generate.py \\
        --output-dir /tmp/skill-eval/datasets/<leg>/<run_id> \\
        --skill-dir skills/vss-generate-video-calibration \\
        --spec skills/vss-generate-video-calibration/evals/auto-calibration.json \\
        --platform RTXPRO6000BW
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — from spec resources.platforms
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
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

# Prepended to every instruction.md so the skill's own HITL bypass
# clause fires. Skills default to "ask the user" before deploy actions;
# in CI there's no user, so without this preamble the agent stalls.
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for a
    single step's checks. Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# vss-generate-video-calibration verifier (step {step}): delegates to the generic\n"
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


def generate_solve_script(platform: str, step: int) -> str:
    """Gold solution placeholder — AMC calibration is interactive/API-driven;
    the verifier drives the evaluation."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-generate-video-calibration step {step} on {platform}\n"
        "# The verifier drives evaluation via the generic judge.\n"
        "set -euo pipefail\n"
        "\n"
        "# Check if AMC MS is responding (steps after deploy assume it's up)\n"
        "AMC_PORT=${VSS_AUTO_CALIBRATION_PORT:-8010}\n"
        "curl -sf --connect-timeout 5 http://localhost:${AMC_PORT}/v1/ready >/dev/null 2>&1 || {\n"
        "    echo 'AMC microservice is not running — deploy step may not have completed'\n"
        "    exit 0\n"
        "}\n"
        "echo 'AMC microservice is live — verifier will drive the evaluation.'\n"
    )


def _render_spec(spec: dict, platform: str) -> dict:
    """Substitute {{platform}} and {{repo_root}} in all string fields."""
    import re as _re
    substitutions = {
        "platform": platform,
        "repo_root": "$HOME/video-search-and-summarization",
    }
    pattern = _re.compile(r"\{\{\s*(\w+)\s*\}\}")

    _LEGACY_REPO = "/home/ubuntu/video-search-and-summarization"
    _PORTABLE_REPO = "$HOME/video-search-and-summarization"

    def _sub(value):
        if isinstance(value, str):
            rendered = pattern.sub(
                lambda m: str(substitutions.get(m.group(1), m.group(0))),
                value,
            )
            return rendered.replace(_LEGACY_REPO, _PORTABLE_REPO)
        if isinstance(value, list):
            return [_sub(v) for v in value]
        if isinstance(value, dict):
            return {k: _sub(v) for k, v in value.items()}
        return value

    return _sub(spec)


def generate_task(
    platform: str,
    spec: dict,
    output_root: Path,
    skill_dir: Path,
    deploy_skill_dir: Path | None,
) -> None:
    """Emit one Harbor task directory per entry in spec['expects'] —
    step-<k>/ subdirs under `auto-calibration/<platform>/`."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "auto-calibration.json")).name or "auto-calibration.json"

    # Render the spec with platform substitutions for both the verifier
    # and the instruction.md (queries contain {{platform}} placeholders).
    rendered_spec = _render_spec(spec, platform)
    rendered_expects = rendered_spec.get("expects") or []

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / "auto-calibration" / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # Use the rendered (substituted) query for the instruction
        rendered_query = rendered_expects[idx - 1].get("query", "") if idx <= len(rendered_expects) else expect.get("query", "")

        # instruction.md — ONE step's query + environment notes ONLY.
        # Never leak the verifier's `checks[]` into the instruction.
        lines = [
            PREAMBLE,
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            rendered_query,
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(lines) + "\n")

        # task.toml
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        gpu_count = (spec.get("resources", {}).get("platforms", {})
                     .get(platform, {}).get("gpu_count", 1))
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-generate-video-calibration-auto-calibration-{platform_short}{step_suffix}"',
            f'description = "AMC calibration query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-generate-video-calibration", "auto-calibration", "{platform}"]',
            "",
            "[environment]",
            'skills_dir = "/skills"',
            "",
            "[verifier.env]",
            'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
            'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
            'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
            'JUDGE_MAX_TURNS = "50"',
            "",
            "[metadata]",
            f'skill = "vss-generate-video-calibration"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'gpu_count = {gpu_count}',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            f'brev_search = "{pspec["brev_search"]}"',
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

        # tests/ — wrapper + generic judge + rendered spec
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform, idx))

        # skills/ — include vss-generate-video-calibration + deploy-profile (for diagnostics)
        for src, name in (
            (skill_dir, "vss-generate-video-calibration"),
            (deploy_skill_dir, "vss-deploy-profile"),
        ):
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
        help="Dataset output root (e.g. /tmp/skill-eval/datasets/<leg>/<run_id>)",
    )
    parser.add_argument(
        "--skill-dir", required=True,
        help="Path to skills/vss-generate-video-calibration",
    )
    parser.add_argument(
        "--deploy-skill-dir", default=None,
        help="Path to skills/vss-deploy-profile (optional — included for agent debug)",
    )
    parser.add_argument(
        "--spec", default=None,
        help="Path to auto-calibration.json (default: <skill-dir>/evals/auto-calibration.json)",
    )
    parser.add_argument(
        "--platform", default=None,
        choices=list(PLATFORMS.keys()),
        help="Generate for this platform only (default: read from spec resources.platforms)",
    )
    parser.add_argument(
        "--all-platforms", action="store_true",
        help="Fan out across every platform declared in the spec's resources.platforms",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None

    if args.spec:
        spec_path = Path(args.spec)
    else:
        spec_path = skill_dir / "evals" / "auto-calibration.json"
        if not spec_path.exists():
            legacy = skill_dir / "eval" / "auto-calibration.json"
            if legacy.exists():
                spec_path = legacy

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)

    # Determine platforms from spec or CLI
    spec_platforms = (spec.get("resources", {}).get("platforms") or {})
    if args.platform:
        platforms = [args.platform]
    elif args.all_platforms:
        platforms = [p for p in spec_platforms if p in PLATFORMS]
    else:
        # Default: use whatever the spec declares
        platforms = [p for p in spec_platforms if p in PLATFORMS]
    if not platforms:
        print("No valid platforms found in spec or CLI args", file=sys.stderr)
        sys.exit(1)

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()
    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  vss-generate-video-calibration/auto-calibration/{task_id}")
        generate_task(platform, spec, output_root, skill_dir, deploy_skill_dir)
    print()
    print(f"Generated {len(platforms)} platform(s) under {output_root}/auto-calibration/")
    print()
    print("Note: this spec OMITS `profile`. The trial runs without a")
    print("/vss-deploy-profile prerequisite — the skill handles its own")
    print("deployment via references/deploy-auto-calibration-service.md.")
    print("The agent's first query deploys AMC autonomously (pre-authorized")
    print("per the PREAMBLE).")


if __name__ == "__main__":
    main()
