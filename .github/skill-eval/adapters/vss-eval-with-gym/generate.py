#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-eval-with-gym skill.

The vss-eval-with-gym skill scores a VSS deployment with NVIDIA NeMo Gym.
Its eval specs exercise two offline capabilities that require NO running
deployment and NO GPU:

1. **delta_adds_only_the_runner** — compose the Gym evaluation delta on a
   Foundation into `_builds/gym-eval-check/` without deploying, and verify
   the delta matches the contract (one extra service key, no modifications
   to checked-in files).

2. **image_gate_rejects_pre_2376** — run the image gate against a known
   pre-#2376 tag (`26.05`) and verify it correctly rejects the tag without
   pulling the image.

Both specs declare `gpu_count: 0` and platform `ANY`, so the task runs on
whichever pool box the coordinator picks (no GPU requirement). The adapter
generates one task per (spec, platform) combination.

Directory layout:
    <output_root>/base/<platform_short>/
        task.toml
        instruction.md
        tests/test.sh
        tests/<spec_name>              (the spec JSON)
        tests/generic_judge.py
        solution/solve.sh
        skills/vss-eval-with-gym/      (full skill copy)
        skills/vss-deploy-profile/     (optional, for agent context)
        environment/Dockerfile

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-eval-with-gym/generate.py \\
        --output-dir /tmp/skill-eval/datasets/<leg-slug>/<run_id> \\
        --skill-dir skills/vss-eval-with-gym \\
        --spec skills/vss-eval-with-gym/evals/delta_adds_only_the_runner.json \\
        --platform ANY
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — gpu_count=0 specs accept any pool box
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "ANY": {
        "short_name": "any",
        "gpu_type": "",
        "gpu_count": 0,
        "min_vram_per_gpu": 0,
        "brev_search": "",
    },
}

DEFAULT_PLATFORM = "ANY"

# Prepended to every instruction.md so the skill's own HITL bypass
# clause fires. Skills default to "ask the user" before /vss-deploy-profile; in CI
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

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for a
    single step's checks. Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# vss-eval-with-gym verifier (step {step}): delegates to the generic\n"
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


def generate_solve_script(platform: str, spec_stem: str) -> str:
    """Gold solution placeholder — these specs are offline checks (no VSS
    deployment needed), so the solve script is a no-op that defers to the
    verifier."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-eval-with-gym/{spec_stem} on {platform}\n"
        "# These specs exercise offline operations (delta composition,\n"
        "# image-gate checking). The verifier drives evaluation directly.\n"
        "set -euo pipefail\n"
        "\n"
        "echo 'Offline spec — verifier will evaluate directly.'\n"
    )


def generate_task(platform: str, spec: dict, output_root: Path,
                  skill_dir: Path, deploy_skill_dir: Path | None) -> None:
    """Emit one Harbor task directory per entry in spec['expects'].
    Single-step specs collapse to a flat `base/<platform_short>/`."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "spec.json")).name or "spec.json"
    spec_stem = Path(spec_name).stem

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / "base" / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md — ONE step's query + environment notes ONLY.
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
            f'name = "nvidia-vss/vss-eval-with-gym-base-{platform_short}{step_suffix}"',
            f'description = "vss-eval-with-gym query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-eval-with-gym", "nemo-gym", "base", "{platform}"]',
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
            f'skill = "vss-eval-with-gym"',
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

        # tests/ — wrapper + generic judge + spec
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
            (tests_dir / spec_name).write_text(json.dumps(spec, indent=2))

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(
            generate_solve_script(platform, spec_stem)
        )

        # skills/ — include vss-eval-with-gym + deploy-profile (for context)
        for src, name in (
            (skill_dir, "vss-eval-with-gym"),
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
        help="Dataset output root",
    )
    parser.add_argument(
        "--skill-dir", required=True,
        help="Path to skills/vss-eval-with-gym",
    )
    parser.add_argument(
        "--deploy-skill-dir", default=None,
        help="Path to skills/vss-deploy-profile (optional — included for agent context)",
    )
    parser.add_argument(
        "--spec", default=None,
        help="Path to the eval spec JSON "
             "(default: auto-detect from <skill-dir>/evals/)",
    )
    parser.add_argument(
        "--platform", default=None,
        choices=list(PLATFORMS.keys()),
        help=f"Generate for this platform only (default: {DEFAULT_PLATFORM})",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None

    if args.spec:
        spec_path = Path(args.spec)
    else:
        # Auto-detect: pick the first spec with the required structure
        evals_dir = skill_dir / "evals"
        if not evals_dir.exists():
            evals_dir = skill_dir / "eval"
        candidates = sorted(evals_dir.glob("*.json")) if evals_dir.exists() else []
        spec_path = None
        for cand in candidates:
            try:
                data = json.loads(cand.read_text())
                if isinstance(data, dict) and "expects" in data and "resources" in data:
                    spec_path = cand
                    break
            except (json.JSONDecodeError, OSError):
                continue
        if not spec_path:
            print("No evaluable spec found under the skill's evals/ directory",
                  file=sys.stderr)
            sys.exit(1)

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())
    # Routing evals (list format) are not harness specs
    if isinstance(spec, list):
        print(f"spec {spec_path} is a routing eval (list), not a harness spec",
              file=sys.stderr)
        sys.exit(1)
    spec["_source_path"] = str(spec_path)

    platform = args.platform or DEFAULT_PLATFORM
    platforms = [platform]

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()
    for plat in platforms:
        task_id = PLATFORMS[plat]["short_name"]
        print(f"  GEN  vss-eval-with-gym/base/{task_id}")
        generate_task(plat, spec, output_root, skill_dir, deploy_skill_dir)
    print()
    print(f"Generated {len(platforms)} task(s) under {output_root}/base/")


if __name__ == "__main__":
    main()
