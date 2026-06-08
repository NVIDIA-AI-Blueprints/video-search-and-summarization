#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-generate-video-calibration skill.

The vss-generate-video-calibration skill exercises the AutoMagicCalib (AMC)
microservice REST API -- deploy, calibrate from local videos / RTSP / sample
dataset, and export results in MV3DT format.

The current spec (`skills/vss-generate-video-calibration/evals/auto-calibration.json`)
**omits the `profile` field by design** -- the agent is expected to deploy
AMC standalone via the skill's bundled
`references/deploy-auto-calibration-service.md` runbook before exercising
the calibration API. Per `.github/skill-eval/AGENTS.md` section 2, an absent
`profile` is the supported signal to the harness that no
`/vss-deploy-profile` prerequisite should be prepended; the trial runs
directly on a bare Brev instance.

## Deploy modes

Harbor runs exactly one task per trial on a clean environment, and the
skill-eval harness no longer pre-deploys any VSS profile on the agent's
behalf. So the **agent's first turn** is responsible for bringing up
whatever it needs:

1. **Profile-less spec (current `auto-calibration.json` -- `profile` absent):**
   the instruction.md tells the agent to stand AMC up standalone via the
   skill's bundled `references/deploy-auto-calibration-service.md` runbook
   (pre-authorized per the PREAMBLE bypass). No `/vss-deploy-profile`.

## Directory layout

    .github/skill-eval/datasets/vss-generate-video-calibration/base/<platform>/
        step-1/
            task.toml
            instruction.md
            tests/test.sh
            tests/generic_judge.py
            tests/auto-calibration.json
            solution/solve.sh
            skills/vss-generate-video-calibration/   (full skill copy)
            skills/vss-deploy-profile/               (optional, for agent debug)
            environment/Dockerfile
        step-2/
            ...
        ...

One task per step per platform. All platforms share the same verifier --
only the `gpu_type` / `brev_search` / resource hints in task.toml differ.

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-generate-video-calibration/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-generate-video-calibration \\
        --skill-dir skills/vss-generate-video-calibration \\
        --deploy-skill-dir skills/vss-deploy-profile
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms -- mirrors the spec's resources.platforms declaration
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "RTXPRO6000BW": {
        "short_name": "rtxpro6000bw",
        "gpu_type": "RTX PRO 6000",
        "gpu_count": 1,
        "min_vram_per_gpu": 96,
        "brev_search": "RTX PRO",
    },
}

DEFAULT_PLATFORM = "RTXPRO6000BW"

# Prepended to every instruction.md so the skill's own HITL bypass
# clause fires. Skills default to "ask the user" before deployment; in CI
# there's no user, so without this preamble the agent either stalls or
# falls through to a localhost default.
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)


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


def generate_solve_script(platform: str) -> str:
    """Gold solution -- assumes AMC is already deployed; the oracle just
    re-runs the verifier (there's no separate 'solve' action for a
    probe-style task since the agent's job is driving the API, which
    the verifier does independently)."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-generate-video-calibration on {platform}\n"
        "# The verifier drives the AMC queries directly -- the solution\n"
        "# script simply asserts AMC is live, then defers to the verifier.\n"
        "set -euo pipefail\n"
        "\n"
        "AMC_PORT=${VSS_AUTO_CALIBRATION_PORT:-8010}\n"
        'curl -sf --connect-timeout 5 '
        '"http://localhost:${AMC_PORT}/v1/ready" '
        ">/dev/null || {\n"
        "    echo 'AMC microservice is not deployed -- cannot solve vss-generate-video-calibration task'\n"
        "    exit 1\n"
        "}\n"
        "echo 'AMC is live -- verifier will drive the queries.'\n"
    )


GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


def generate_task(platform: str, spec: dict, output_root: Path,
                  skill_dir: Path, deploy_skill_dir: Path | None) -> None:
    """Emit one Harbor task directory per entry in spec['expects'] -- i.e.
    step-<k>/ subdirs under `base/<platform>/` per AGENTS.md section 4.
    Single-step specs collapse to a flat `base/<platform>/`."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "auto-calibration.json")).name or "auto-calibration.json"

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / "base" / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md -- ONE step's query + environment notes ONLY.
        # Never leak the verifier's `checks[]` into the instruction the agent
        # sees -- they live in the spec, are copied into tests/, and the
        # verifier evaluates them independently.
        lines = [
            PREAMBLE,
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            expect.get("query", ""),
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(lines) + "\n")

        # task.toml
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-generate-video-calibration-base-{platform_short}{step_suffix}"',
            f'description = "AMC calibration query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-generate-video-calibration", "amc", "auto-calibration", "base", "{platform}"]',
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
            f'gpu_count = {pspec["gpu_count"]}',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
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

        # tests/ -- wrapper + generic judge + spec
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        spec_src = skill_dir / "evals" / spec_name
        if not spec_src.exists():
            legacy = skill_dir / "eval" / spec_name
            if legacy.exists():
                spec_src = legacy
        if spec_src.exists():
            shutil.copy(spec_src, tests_dir / spec_name)
        else:
            # write a copy of the spec even if the source file path differs
            (tests_dir / "auto-calibration.json").write_text(
                json.dumps(spec, indent=2)
            )

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # skills/ -- include vss-generate-video-calibration + deploy (so agent
        # can diagnose if AMC isn't live).
        for src, name in ((skill_dir, "vss-generate-video-calibration"),
                          (deploy_skill_dir, "vss-deploy-profile")):
            if src and src.exists():
                dst = step_dir / "skills" / name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", required=True,
                        help="Dataset output root (e.g. .github/skill-eval/datasets/vss-generate-video-calibration)")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/vss-generate-video-calibration")
    parser.add_argument("--deploy-skill-dir", default=None,
                        help="Path to skills/vss-deploy-profile (optional -- included for agent debug)")
    parser.add_argument("--spec", default=None,
                        help="Path to auto-calibration.json "
                             "(default: <skill-dir>/evals/auto-calibration.json)")
    parser.add_argument("--platform", default=None,
                        choices=list(PLATFORMS.keys()),
                        help=f"Generate for this platform only "
                             f"(default: {DEFAULT_PLATFORM})")
    parser.add_argument("--all-platforms", action="store_true",
                        help="Fan out across every platform in PLATFORMS")
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

    if args.platform:
        platforms = [args.platform]
    elif args.all_platforms:
        platforms = list(PLATFORMS.keys())
    else:
        platforms = [DEFAULT_PLATFORM]

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
        print(f"  GEN  vss-generate-video-calibration/base/{task_id}")
        generate_task(platform, spec, output_root, skill_dir, deploy_skill_dir)
    print()
    print(f"Generated {len(platforms)} task(s) under {output_root}/base/")
    print()
    print("Note: this spec OMITS `profile`. The trial runs on a bare Brev")
    print("instance -- no /vss-deploy-profile prerequisite is injected. The agent is")
    print("expected to stand AMC up standalone via the skill's bundled")
    print("references/deploy-auto-calibration-service.md runbook before")
    print("exercising the calibration API.")


if __name__ == "__main__":
    main()
