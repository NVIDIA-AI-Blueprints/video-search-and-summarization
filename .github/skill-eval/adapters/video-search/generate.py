#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the video-search skill.

The video-search skill exercises natural-language video archive search via
the VSS search profile (Cosmos Embed1 + Elasticsearch). It requires a
**full-remote deployed VSS search profile** (deploy mode = `remote-all`).
The coordinator chains a deploy task ahead of each trial.

## Directory layout

    datasets/video-search/<spec_stem>/<platform>-<mode>/
        step-1/  step-2/  ...   (one per entry in spec["expects"])
            task.toml
            instruction.md
            tests/test.sh
            tests/generic_judge.py
            tests/<spec_stem>.json
            solution/solve.sh
            skills/video-search/   (full skill copy)
            skills/deploy/         (for agent diagnostics)
            environment/Dockerfile

Usage:
    python3 generate.py --output-dir ../../datasets/video-search \\
        --skill-dir ../../../../../skills/video-search \\
        --deploy-skill-dir ../../../../../skills/deploy \\
        --spec ../../../../../skills/video-search/eval/search.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Preamble — required in every instruction.md so the agent's skill prereq
# bypass clause fires and the agent doesn't stall waiting for confirmation.
# ---------------------------------------------------------------------------

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness.\n"
    "You are pre-authorized to deploy prerequisites autonomously —\n"
    "do not pause to ask for confirmation on `/deploy` or any other\n"
    "setup action the trial requires.\n"
)

# ---------------------------------------------------------------------------
# Platforms
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":         {"short_name": "h100",         "gpu_type": "H100",         "min_vram_per_gpu": 80,  "brev_search": "H100"},
    "L40S":         {"short_name": "l40s",         "gpu_type": "L40S",         "min_vram_per_gpu": 48,  "brev_search": "L40S"},
    "RTXPRO6000BW": {"short_name": "rtxpro6000bw", "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96,  "brev_search": "RTX PRO"},
    "DGX-SPARK":    {"short_name": "spark",        "gpu_type": "GB10",         "min_vram_per_gpu": 96,  "brev_search": "GB10"},
    "IGX-THOR":     {"short_name": "thor",         "gpu_type": "Thor",         "min_vram_per_gpu": 64,  "brev_search": "Thor"},
}

DEFAULT_PLATFORM = "L40S"

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


# ---------------------------------------------------------------------------
# Script generators
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier."""
    return (
        "#!/bin/bash\n"
        f"# video-search verifier (step {step}): delegates to the generic\n"
        "# LLM-as-judge (tools/eval/harbor/verifiers/generic_judge.py).\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
        "exit 0\n"
    )


def generate_solve_script(platform: str, profile: str) -> str:
    """Gold solution — asserts the search stack is live."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: video-search ({profile} profile) on {platform}\n"
        "# The verifier drives the search queries directly — the solution\n"
        "# script just asserts the required endpoints are live.\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 http://localhost:8000/docs >/dev/null || {\n"
        "    echo 'VSS Agent API is not reachable — cannot solve video-search task'\n"
        "    exit 1\n"
        "}\n"
        "curl -sf --connect-timeout 5 http://localhost:9200/ >/dev/null || {\n"
        "    echo 'Elasticsearch is not reachable — cannot solve video-search task'\n"
        "    exit 1\n"
        "}\n"
        "echo 'VSS search stack is live — verifier will drive the queries.'\n"
    )


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_task(platform: str, spec: dict, output_root: Path,
                  skill_dir: Path, deploy_skill_dir: Path | None,
                  spec_stem: str, mode: str) -> None:
    """Emit one Harbor task directory per entry in spec['expects'] — i.e.
    step-<k>/ subdirs under `<spec_stem>/<platform_short>-<mode>/`."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = f"{spec_stem}.json"
    profile = spec.get("profile", "search")
    deploy_mode = spec.get("deploy_mode", "remote-all")

    task_dir_root = output_root / spec_stem / f"{platform_short}-{mode}"

    for idx, expect in enumerate(expects, 1):
        step_dir = task_dir_root
        if len(expects) > 1:
            step_dir = task_dir_root / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md — begins with PREAMBLE, then step query + env notes
        step_suffix = f"step {idx}/{len(expects)}: " if len(expects) > 1 else ""
        lines = [
            PREAMBLE,
            f"Use the `/video-search` skill against the VSS **{profile}** profile "
            f"already running on this `{platform}` host "
            "(VSS agent must be reachable at `http://localhost:8000/docs` "
            "and Elasticsearch at `http://localhost:9200/`).",
            "",
            f"## {step_suffix}Query",
            "",
            expect.get("query", ""),
            "",
            "## Environment notes",
            "",
            spec.get("env", ""),
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(lines) + "\n")

        # task.toml
        step_suffix_id = f"-step-{idx}" if len(expects) > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/video-search-{spec_stem}-{platform_short}-{mode}{step_suffix_id}"',
            f'description = "video-search {spec_stem} query {idx}/{len(expects)} on {platform}/{mode}"',
            f'keywords = ["video-search", "{spec_stem}", "{platform}", "{mode}"]',
            "",
            "[environment]",
            'skills_dir = "/skills"',
            "",
            "[verifier.env]",
            'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
            'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
            'JUDGE_MODEL = "${JUDGE_MODEL:-claude-haiku-4-5}"',
            "",
            "[metadata]",
            'skill = "video-search"',
            f'profile = "{profile}"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            "requires_deployed_vss = true",
            f'prerequisite_deploy_mode = "{deploy_mode}"',
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

        # tests/
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
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform, profile))

        # skills/
        for src, name in ((skill_dir, "video-search"), (deploy_skill_dir, "deploy")):
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
    parser.add_argument("--output-dir", required=True,
                        help="Dataset output root (e.g. .github/skill-eval/datasets/video-search)")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/video-search")
    parser.add_argument("--deploy-skill-dir", default=None,
                        help="Path to skills/deploy (optional — included for agent debug)")
    parser.add_argument("--spec", default=None,
                        help="Path to eval spec JSON (default: <skill-dir>/eval/search.json)")
    parser.add_argument("--platform", default=None, choices=list(PLATFORMS.keys()),
                        help=f"Generate for this platform only (default: {DEFAULT_PLATFORM})")
    parser.add_argument("--all-platforms", action="store_true",
                        help="Fan out across every platform in PLATFORMS")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None

    spec_path = Path(args.spec) if args.spec else (skill_dir / "eval" / "search.json")
    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    spec = json.loads(spec_path.read_text())
    spec_stem = spec_path.stem

    if args.platform:
        platforms = [args.platform]
    elif args.all_platforms:
        platforms = list(PLATFORMS.keys())
    else:
        # Use platforms declared in spec.resources.platforms; fall back to DEFAULT_PLATFORM
        spec_platforms = list((spec.get("resources") or {}).get("platforms") or {})
        platforms = spec_platforms if spec_platforms else [DEFAULT_PLATFORM]

    expects = spec.get("expects") or []
    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  spec_stem    : {spec_stem}")
    print(f"  profile      : {spec.get('profile', 'search')}")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(expects)}")
    print(f"  total checks : {sum(len(q.get('checks') or []) for q in expects)}")
    print()

    for platform in platforms:
        # Determine modes from spec
        spec_modes = list(
            ((spec.get("resources") or {}).get("platforms") or {})
            .get(platform, {})
            .get("modes") or ["remote-all"]
        )
        for mode in spec_modes:
            pshort = PLATFORMS[platform]["short_name"]
            print(f"  GEN  video-search/{spec_stem}/{pshort}-{mode}")
            generate_task(platform, spec, output_root, skill_dir,
                          deploy_skill_dir, spec_stem, mode)

    print()
    print(f"Generated tasks under {output_root}/{spec_stem}/")
    print()
    print("Note: these tasks assume VSS search profile is already deployed on the")
    print("target Brev instance. The coordinator is responsible for injecting a")
    print("matching deploy task ahead of each video-search task.")


if __name__ == "__main__":
    main()
