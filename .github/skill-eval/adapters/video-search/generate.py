#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the video-search skill.

The video-search skill exercises semantic video search (Cosmos Embed1 +
Elasticsearch) against a **full-remote deployed VSS search profile**
(deploy mode = `remote-all`; LLM and VLM via remote launchpad endpoints,
Cosmos Embed1 / RTVI-Embed still runs locally on the GPU). A deploy
task must precede this trial in the coordinator's queue.

Because the search profile is GPU-dependent (Cosmos Embed1 runs locally)
but the spec's env says "run on ONE platform only — pick the cheapest
available host (L40S recommended)", this adapter defaults to L40S only.

## Directory layout

    datasets/video-search/<spec_stem>/<platform>-<mode>/
        step-1/
            task.toml
            instruction.md
            tests/test.sh
            tests/generic_judge.py
            tests/<spec_stem>.json
            solution/solve.sh
            skills/video-search/
            skills/vios/
            skills/deploy/
            environment/Dockerfile
        step-2/
            ...

One platform directory, N step subdirs (one per entry in spec['expects']).

Usage:
    python3 generate.py --output-dir ../../datasets/video-search \\
        --skill-dir ../../../../../skills/video-search \\
        --vios-skill-dir ../../../../../skills/vios \\
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
# Preamble — prepended to every instruction.md so the bypass clause in
# SKILL.md fires and the agent does not pause for confirmation on /deploy
# or any other setup action.
# ---------------------------------------------------------------------------

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness.\n"
    "You are pre-authorized to deploy prerequisites autonomously —\n"
    "do not pause to ask for confirmation on `/deploy` or any other\n"
    "setup action the trial requires.\n"
)

# ---------------------------------------------------------------------------
# Platforms — same set as the deploy adapter; video-search spec restricts
# to L40S by default via resources.platforms.
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":         {"short_name": "h100",         "gpu_type": "H100",         "min_vram_per_gpu": 80,  "brev_search": "H100"},
    "L40S":         {"short_name": "l40s",         "gpu_type": "L40S",         "min_vram_per_gpu": 48,  "brev_search": "L40S"},
    "RTXPRO6000BW": {"short_name": "rtxpro6000bw", "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96,  "brev_search": "RTX PRO"},
    "DGX-SPARK":    {"short_name": "spark",        "gpu_type": "GB10",         "min_vram_per_gpu": 96,  "brev_search": "GB10"},
    "IGX-THOR":     {"short_name": "thor",         "gpu_type": "Thor",         "min_vram_per_gpu": 64,  "brev_search": "Thor"},
}

DEFAULT_PLATFORM = "L40S"
DEFAULT_MODE = "remote-all"

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


# ---------------------------------------------------------------------------
# Script generators
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for a
    single step's checks.  Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# video-search verifier (step {step}): delegates to the\n"
        "# generic LLM-as-judge (verifiers/generic_judge.py).\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
        "exit 0\n"
    )


def generate_solve_script(platform: str, profile: str, mode: str) -> str:
    """Gold solution — asserts the search stack is live, then defers to
    the verifier.  The verifier is the oracle here; the agent's job is
    driving the REST API, not running shell probes."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: video-search on {platform}/{mode} (profile: {profile})\n"
        "# Verifier drives the search queries — solution just confirms the stack.\n"
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
        "curl -sf --connect-timeout 5 http://localhost:30888/vst/api/v1/sensor/version "
        ">/dev/null || {\n"
        "    echo 'VST not reachable at http://localhost:30888'\n"
        "    exit 1\n"
        "}\n"
        "echo 'Search stack is live — verifier will drive the queries.'\n"
    )


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_task(
    platform: str,
    mode: str,
    spec: dict,
    spec_stem: str,
    output_root: Path,
    skill_dir: Path,
    vios_skill_dir: Path | None,
    deploy_skill_dir: Path | None,
) -> None:
    """Emit one Harbor step directory per entry in spec['expects'] under
    `<spec_stem>/<platform_short>-<mode>/step-<N>/`.
    Single-step specs collapse to a flat directory (no step-N sub-level)."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    profile = spec.get("profile", "search")
    deploy_mode = spec.get("deploy_mode", mode)
    expects = spec.get("expects") or []
    spec_name = f"{spec_stem}.json"

    platform_dir = output_root / spec_stem / f"{platform_short}-{mode}"

    for idx, expect in enumerate(expects, 1):
        if len(expects) > 1:
            step_dir = platform_dir / f"step-{idx}"
        else:
            step_dir = platform_dir
        step_dir.mkdir(parents=True, exist_ok=True)

        # -- instruction.md --
        # Begins with PREAMBLE so the skill's bypass clause fires.
        step_suffix = f" (step {idx}/{len(expects)})" if len(expects) > 1 else ""
        lines = [
            PREAMBLE,
            f"Use the `/video-search` skill against the VSS **{profile}** profile "
            f"already running on this `{platform}` host{step_suffix}.",
            "",
            "Required services (all on localhost):",
            "- VSS agent: `http://localhost:8000/docs` (OpenAPI visible)",
            "- Elasticsearch: `http://localhost:9200`",
            "- VST: `http://localhost:30888/vst/api/v1`",
            "",
            f"## Query{step_suffix}",
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

        # -- task.toml --
        step_suffix_id = f"-step-{idx}" if len(expects) > 1 else ""
        # gpu_count for search/remote-all: Cosmos Embed1 runs locally so
        # gpu_count=1 (one GPU for the embed model); the LLM/VLM are remote.
        gpu_count = 1 if mode != "remote-all" else 1  # Embed1 always local on GPU
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/video-search-{spec_stem}-{platform_short}-{mode}{step_suffix_id}"',
            f'description = "Video search ({spec_stem}) query {idx}/{len(expects)} on {platform}/{mode}"',
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
            f'skill = "video-search"',
            f'profile = "{profile}"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'gpu_count = {gpu_count}',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            f'brev_search = "{pspec["brev_search"]}"',
            "requires_deployed_vss = true",
            f'prerequisite_deploy_mode = "{deploy_mode}"',
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ]
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        # -- environment/ --
        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        # -- tests/ --
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        # Ship the spec so the judge can read checks
        spec_src = skill_dir / "eval" / spec_name
        if spec_src.exists():
            shutil.copy(spec_src, tests_dir / spec_name)
        else:
            (tests_dir / spec_name).write_text(json.dumps(spec, indent=2))

        # -- solution/ --
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(
            generate_solve_script(platform, profile, mode)
        )

        # -- skills/ — include video-search, vios (for source listing),
        #              and deploy (for diagnostic / deploy prereq) --
        skills_to_copy = [
            (skill_dir,        "video-search"),
            (vios_skill_dir,   "vios"),
            (deploy_skill_dir, "deploy"),
        ]
        for src, name in skills_to_copy:
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
    parser.add_argument("--vios-skill-dir", default=None,
                        help="Path to skills/vios (included so agent can list sources)")
    parser.add_argument("--deploy-skill-dir", default=None,
                        help="Path to skills/deploy (included for diagnostic / deploy prereq)")
    parser.add_argument("--spec", default=None,
                        help="Path to the eval spec JSON "
                             "(default: <skill-dir>/eval/search.json)")
    parser.add_argument("--platform", default=None,
                        choices=list(PLATFORMS.keys()),
                        help=f"Generate for this platform only (default: {DEFAULT_PLATFORM})")
    parser.add_argument("--mode", default=None,
                        help=f"Deploy mode (default: {DEFAULT_MODE})")
    parser.add_argument("--all-platforms", action="store_true",
                        help="Fan out across every platform in PLATFORMS "
                             "(the spec says one platform only; use with care)")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    vios_skill_dir = Path(args.vios_skill_dir) if args.vios_skill_dir else None
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    spec_path = Path(args.spec) if args.spec else (skill_dir / "eval" / "search.json")

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)
    spec_stem = spec_path.stem  # e.g. "search"

    # Determine platforms/modes from spec resources.platforms or CLI override.
    resources_platforms: dict = (spec.get("resources") or {}).get("platforms") or {}

    if args.platform:
        platform_modes = {args.platform: [args.mode or DEFAULT_MODE]}
    elif args.all_platforms:
        platform_modes = {p: list((v or {}).get("modes") or [DEFAULT_MODE])
                          for p, v in resources_platforms.items()} \
                         if resources_platforms else \
                         {p: [DEFAULT_MODE] for p in PLATFORMS}
    elif resources_platforms:
        platform_modes = {p: list((v or {}).get("modes") or [DEFAULT_MODE])
                          for p, v in resources_platforms.items()}
    else:
        platform_modes = {DEFAULT_PLATFORM: [DEFAULT_MODE]}

    print("=== Inputs ===")
    print(f"  output_dir     : {output_root}")
    print(f"  skill_dir      : {skill_dir}")
    print(f"  vios_skill_dir : {vios_skill_dir or '(not provided)'}")
    print(f"  deploy_skill_dir: {deploy_skill_dir or '(not provided)'}")
    print(f"  spec           : {spec_path}")
    print(f"  spec_stem      : {spec_stem}")
    print(f"  profile        : {spec.get('profile', 'search')}")
    print(f"  platform_modes : {platform_modes}")
    print(f"  queries        : {len(spec.get('expects', []))}")
    total_checks = sum(len(q.get("checks", [])) for q in spec.get("expects", []))
    print(f"  total checks   : {total_checks}")
    print()

    generated = 0
    for platform, modes in platform_modes.items():
        if platform not in PLATFORMS:
            print(f"  SKIP unknown platform: {platform}", file=sys.stderr)
            continue
        for mode in modes:
            task_id = f"{PLATFORMS[platform]['short_name']}-{mode}"
            print(f"  GEN  video-search/{spec_stem}/{task_id}")
            generate_task(
                platform=platform,
                mode=mode,
                spec=spec,
                spec_stem=spec_stem,
                output_root=output_root,
                skill_dir=skill_dir,
                vios_skill_dir=vios_skill_dir,
                deploy_skill_dir=deploy_skill_dir,
            )
            generated += 1

    print()
    print(f"Generated {generated} platform-mode task(s) under {output_root}/{spec_stem}/")
    print()
    print("Note: these tasks assume the VSS search profile is already deployed on the")
    print("target Brev instance.  The coordinator must chain a /deploy -p search task")
    print("ahead of each video-search task in the same subagent queue.")


if __name__ == "__main__":
    main()
