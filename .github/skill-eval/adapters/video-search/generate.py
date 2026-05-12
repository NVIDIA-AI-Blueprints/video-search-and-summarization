#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the video-search skill evaluation.

The video-search skill exercises the VSS search profile's semantic video
search API (Cosmos Embed1 + Elasticsearch) against a **full-remote deployed
VSS search profile** (deploy mode = `remote-all` — LLM and VLM via remote
launchpad endpoints, no local NIMs; Cosmos Embed1 still runs locally on the
GPU).

The spec declares `profile: "search"` so the coordinator chains a deploy task
ahead of the eval steps. Each step is one of the spec's `expects[]` queries.

## Directory layout

    datasets/video-search/<spec_stem>/<platform>-<mode>/
        step-1/
            task.toml
            instruction.md
            tests/test.sh
            tests/generic_judge.py
            tests/<spec>.json
            solution/solve.sh
            skills/video-search/   (full skill copy)
            environment/Dockerfile
        step-2/ ... step-N/

Usage:
    python3 generate.py \\
        --output-dir /tmp/skill-eval/datasets/video-search \\
        --skill-dir skills/video-search \\
        --spec skills/video-search/eval/search.json

Or let the coordinator call it with its discovered paths.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Preamble — MUST begin every instruction.md
# (mirrors the constant in adapters/vios/generate.py and
#  adapters/deploy/generate.py so the skill's bypass clause fires)
# ---------------------------------------------------------------------------

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/deploy` or any other "
    "setup action the trial requires."
)

# ---------------------------------------------------------------------------
# Platform table — mirrors adapters/vios/generate.py
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":         {"short_name": "h100",         "gpu_type": "H100",         "min_vram_per_gpu": 80,  "brev_search": "H100"},
    "L40S":         {"short_name": "l40s",         "gpu_type": "L40S",         "min_vram_per_gpu": 48,  "brev_search": "L40S"},
    "RTXPRO6000BW": {"short_name": "rtxpro6000bw", "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96,  "brev_search": "RTX PRO"},
    "DGX-SPARK":    {"short_name": "spark",         "gpu_type": "GB10",         "min_vram_per_gpu": 96,  "brev_search": "GB10"},
}

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


# ---------------------------------------------------------------------------
# Script generators
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for one
    step. Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# video-search verifier (step {step}): delegates to the generic\n"
        "# LLM-as-judge (verifiers/generic_judge.py).\n"
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
    """Gold solution stub — assumes VSS search is already deployed; the oracle
    asserts the stack is live then defers to the verifier."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: video-search on {platform} (profile: {profile})\n"
        "# VSS must be deployed before this runs (coordinator injects deploy task).\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 http://localhost:8000/docs >/dev/null || {\n"
        "    echo 'VSS agent not reachable at http://localhost:8000/docs'\n"
        "    exit 1\n"
        "}\n"
        "curl -sf --connect-timeout 5 http://localhost:9200/ >/dev/null || {\n"
        "    echo 'Elasticsearch not reachable at http://localhost:9200'\n"
        "    exit 1\n"
        "}\n"
        "echo 'VSS search profile is live — verifier will drive the queries.'\n"
    )


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_task(
    platform: str,
    mode: str,
    spec: dict,
    spec_name: str,
    output_root: Path,
    skill_dir: Path,
) -> None:
    """Emit one Harbor step-N/ subdir per expects[] entry under
    <output_root>/<spec_stem>/<platform_short>-<mode>/.

    The task name convention matches what --include-task-name expects:
        <platform_short>-<mode>-step-<N>
    e.g. l40s-remote-all-step-1.
    """
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    task_prefix = f"{platform_short}-{mode}"
    expects = spec.get("expects") or []
    profile: str = spec.get("profile", "base")
    deploy_mode: str = spec.get("deploy_mode", "")
    spec_stem = Path(spec.get("_source_path", "search.json")).stem

    platform_dir = output_root / spec_stem / task_prefix
    platform_dir.mkdir(parents=True, exist_ok=True)

    for idx, expect in enumerate(expects, 1):
        step_dir = platform_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # ---- instruction.md -----------------------------------------------
        query = expect.get("query", "")
        env_prose = spec.get("env", "")
        lines = [
            PREAMBLE,
            "",
            f"Use the `/video-search` skill against the VSS **{profile}** profile "
            f"already running on this `{platform}` host "
            "(`http://localhost:8000/docs` must respond, "
            "`http://localhost:9200/` must respond).",
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            query,
            "",
            "## Environment",
            "",
            env_prose,
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(lines) + "\n")

        # ---- task.toml ----------------------------------------------------
        step_suffix = f"-step-{idx}"
        deploy_mode_line = f'prerequisite_deploy_mode = "{deploy_mode}"' if deploy_mode else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/video-search-{task_prefix}{step_suffix}"',
            f'description = "video-search {profile} query {idx}/{len(expects)} on {platform}/{mode}"',
            f'keywords = ["video-search", "{profile}", "{platform}", "{mode}"]',
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
            f'mode = "{mode}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'gpu_count = 1',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            f'brev_search = "{pspec["brev_search"]}"',
            "requires_deployed_vss = true",
        ]
        if deploy_mode_line:
            meta_lines.append(deploy_mode_line)
        meta_lines += [
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ]
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        # ---- environment/ -------------------------------------------------
        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        # ---- tests/ -------------------------------------------------------
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        (tests_dir / "test.sh").chmod(0o755)
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        # Copy spec so the judge can read checks for this step
        spec_src = skill_dir / "eval" / spec_name
        if spec_src.exists():
            shutil.copy(spec_src, tests_dir / spec_name)
        else:
            (tests_dir / spec_name).write_text(json.dumps(spec, indent=2))

        # ---- solution/ ----------------------------------------------------
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform, profile))
        (solution_dir / "solve.sh").chmod(0o755)

        # ---- skills/ ------------------------------------------------------
        dst = step_dir / "skills" / "video-search"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(skill_dir, dst)

    print(f"  GEN  {spec_stem}/{task_prefix}  ({len(expects)} steps)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output-dir", required=True,
                        help="Dataset output root (e.g. /tmp/skill-eval/datasets/video-search)")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/video-search")
    parser.add_argument("--spec", default=None,
                        help="Path to the eval spec JSON (default: <skill-dir>/eval/search.json)")
    parser.add_argument("--platform", default=None, choices=list(PLATFORMS.keys()),
                        help="Generate for this platform only (default: from spec's resources.platforms)")
    parser.add_argument("--mode", default=None,
                        help="Generate for this mode only (default: from spec's resources.platforms)")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    spec_path = Path(args.spec) if args.spec else (skill_dir / "eval" / "search.json")

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)
    spec_name = spec_path.name

    # Validate required fields
    if "resources" not in spec or "platforms" not in spec.get("resources", {}):
        print(f"ERROR: spec is missing resources.platforms — cannot determine platform matrix",
              file=sys.stderr)
        sys.exit(1)

    spec_platforms: dict = spec["resources"]["platforms"]

    # CLI overrides
    if args.platform:
        if args.platform not in spec_platforms:
            print(f"WARNING: --platform {args.platform} not in spec's resources.platforms; "
                  f"using it anyway", file=sys.stderr)
        spec_platforms = {args.platform: spec_platforms.get(args.platform, {})}

    profile = spec.get("profile", "base")
    deploy_mode = spec.get("deploy_mode", "")
    expects = spec.get("expects") or []

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  profile      : {profile}")
    print(f"  deploy_mode  : {deploy_mode or '(not set)'}")
    print(f"  platforms    : {list(spec_platforms.keys())}")
    print(f"  queries      : {len(expects)}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in expects)}")
    print()

    generated = 0
    for platform, plat_cfg in spec_platforms.items():
        if platform not in PLATFORMS:
            print(f"  SKIP {platform} — not in PLATFORMS table", file=sys.stderr)
            continue
        modes = (plat_cfg or {}).get("modes") or ["remote-all"]
        for mode in modes:
            if args.mode and mode != args.mode:
                continue
            generate_task(platform, mode, spec, spec_name, output_root, skill_dir)
            generated += 1

    print()
    print(f"Generated {generated} platform×mode combination(s) under {output_root}/")
    if generated == 0:
        print("ERROR: nothing generated — check platform/mode filters", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
