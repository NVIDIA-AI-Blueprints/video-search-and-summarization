#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the video-search skill.

The video-search skill exercises semantic / fusion video-search against a
**full-remote deployed VSS search profile** (deploy mode = `remote-all` —
LLM and VLM both via remote launchpad endpoints, Cosmos Embed1 runs locally
on the GPU, Elasticsearch runs locally). Because the spec env field says
to run on ONE platform only (L40S, cheapest stoppable host), this adapter
generates a single platform by default.

The spec (`skills/video-search/eval/search.json`) contains N `expects`
entries; each becomes a `step-<k>/` sub-directory under
`<platform>-<mode>/` so Harbor's per-step chaining works.

## Directory layout

    datasets/video-search/search/<platform>-<mode>/
        step-1/
            task.toml
            instruction.md
            tests/test.sh, generic_judge.py, search.json
            solution/solve.sh
            skills/video-search/
            environment/Dockerfile
        step-2/ ...

Usage:
    python3 generate.py \\
        --output-dir /tmp/skill-eval/datasets/video-search \\
        --skill-dir skills/video-search \\
        --deploy-skill-dir skills/deploy \\
        --spec skills/video-search/eval/search.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# PREAMBLE — must appear at the top of every instruction.md.
# Skills' SKILL.md prereq blocks fire the bypass clause on this exact wording.
# ---------------------------------------------------------------------------
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/deploy` or any other "
    "setup action the trial requires."
)

# ---------------------------------------------------------------------------
# Platform table (mirrors vios/deploy adapters)
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":         {"short_name": "h100",         "gpu_type": "H100",        "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S":         {"short_name": "l40s",         "gpu_type": "L40S",        "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW": {"short_name": "rtxpro6000bw", "gpu_type": "RTX PRO 6000","min_vram_per_gpu": 96, "brev_search": "RTX PRO"},
    "DGX-SPARK":    {"short_name": "spark",        "gpu_type": "GB10",        "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR":     {"short_name": "thor",         "gpu_type": "Thor",        "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

DEFAULT_PLATFORM = "L40S"

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------

def _task_dir_name(platform: str, mode: str) -> str:
    """Harbor task directory name, e.g. `l40s-remote-all`."""
    short = PLATFORMS[platform]["short_name"]
    return f"{short}-{mode}"


def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for a
    single step. Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# video-search verifier (step {step}): delegates to the generic\n"
        "# LLM-as-judge (generic_judge.py).\n"
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
    """Gold solution stub — the eval pre-deploys VSS search; the solution
    script just confirms the stack is live and defers to the verifier."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: video-search on {platform}/{mode}\n"
        "# The stack should already be deployed with the search profile.\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 http://localhost:8000/docs >/dev/null || {\n"
        "    echo 'VSS agent not reachable — cannot solve video-search task'\n"
        "    exit 1\n"
        "}\n"
        "curl -sf --connect-timeout 5 http://localhost:9200/ >/dev/null || {\n"
        "    echo 'Elasticsearch not reachable — cannot solve video-search task'\n"
        "    exit 1\n"
        "}\n"
        "echo 'VSS search stack is live — verifier will drive the queries.'\n"
    )


def generate_task(
    platform: str,
    mode: str,
    spec: dict,
    output_root: Path,
    skill_dir: Path,
    deploy_skill_dir: Path | None,
) -> None:
    """Emit one Harbor task directory per `expects` entry (step-<k>) under
    `<output_root>/<spec_stem>/<platform>-<mode>/`.

    For a spec with N expects:
        <output_root>/search/l40s-remote-all/step-1/
        <output_root>/search/l40s-remote-all/step-2/
        ...
    """
    pspec = PLATFORMS[platform]
    expects = spec.get("expects") or []
    spec_stem = Path(spec.get("_source_path", "search.json")).stem  # e.g. "search"
    spec_name = Path(spec.get("_source_path", "search.json")).name  # e.g. "search.json"

    task_id = _task_dir_name(platform, mode)
    task_root = output_root / spec_stem / task_id

    profile = spec.get("profile", "search")
    deploy_mode = spec.get("deploy_mode", "remote-all")

    for idx, expect in enumerate(expects, 1):
        step_dir = task_root / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # -- instruction.md --
        query = expect.get("query", "")
        env_notes = spec.get("env", "")
        lines = [
            PREAMBLE,
            "",
            f"Use the `/video-search` skill against the VSS **{profile}** profile "
            f"already running on this `{platform}` host "
            "(VSS agent reachable at `http://localhost:8000/docs`, "
            "Elasticsearch at `http://localhost:9200/`). "
            "Agent backend is on localhost.",
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            query,
            "",
            "## Environment notes",
            "",
            env_notes,
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(lines) + "\n")

        # -- task.toml --
        step_suffix = f"-step-{idx}"
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/video-search-{spec_stem}-{task_id}{step_suffix}"',
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
            f'skill = "video-search"',
            f'profile = "{profile}"',
            f'deploy_mode = "{deploy_mode}"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'gpu_count = 1',
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
        # Copy the spec so the judge can find it at tests/search.json
        spec_src = skill_dir / "eval" / spec_name
        if spec_src.exists():
            shutil.copy(spec_src, tests_dir / spec_name)
        else:
            (tests_dir / spec_name).write_text(json.dumps(spec, indent=2))

        # -- solution/ --
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform, mode))

        # -- skills/ — include video-search + deploy (for prereq deploy) --
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
                        help="Dataset output root (e.g. /tmp/skill-eval/datasets/video-search)")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/video-search")
    parser.add_argument("--deploy-skill-dir", default=None,
                        help="Path to skills/deploy (optional — included for agent prereq deploy)")
    parser.add_argument("--spec", default=None,
                        help="Path to the eval spec JSON "
                             "(default: <skill-dir>/eval/search.json)")
    parser.add_argument("--platform", default=None,
                        choices=list(PLATFORMS.keys()),
                        help=f"Generate for this platform only (default: {DEFAULT_PLATFORM})")
    parser.add_argument("--mode", default="remote-all",
                        help="Deploy mode (default: remote-all)")
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
    spec["_source_path"] = str(spec_path)

    # Determine platforms from spec.resources.platforms or CLI
    spec_platforms: dict[str, list[str]] = {}
    resources_platforms = (spec.get("resources") or {}).get("platforms") or {}
    if resources_platforms:
        for p, v in resources_platforms.items():
            spec_platforms[p] = list((v or {}).get("modes") or [args.mode])

    if args.all_platforms:
        platforms = list(PLATFORMS.keys())
        modes_for: dict[str, list[str]] = {p: [args.mode] for p in platforms}
    elif args.platform:
        platforms = [args.platform]
        modes_for = {args.platform: spec_platforms.get(args.platform) or [args.mode]}
    elif spec_platforms:
        platforms = list(spec_platforms.keys())
        modes_for = spec_platforms
    else:
        platforms = [DEFAULT_PLATFORM]
        modes_for = {DEFAULT_PLATFORM: [args.mode]}

    print("=== Inputs ===")
    print(f"  output_dir  : {output_root}")
    print(f"  skill_dir   : {skill_dir}")
    print(f"  spec        : {spec_path}")
    print(f"  platforms   : {platforms}")
    print(f"  queries     : {len(spec.get('expects', []))}")
    print(f"  total checks: {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()

    for platform in platforms:
        if platform not in PLATFORMS:
            print(f"  SKIP unknown platform {platform!r}", file=sys.stderr)
            continue
        for mode in modes_for.get(platform, [args.mode]):
            task_id = _task_dir_name(platform, mode)
            print(f"  GEN  video-search/search/{task_id}")
            generate_task(platform, mode, spec, output_root, skill_dir, deploy_skill_dir)

    print()
    print(f"Generated tasks under {output_root}/search/")
    print()
    print("Note: these tasks assume VSS search profile is already deployed on the")
    print("target Brev instance, and sample videos are pre-ingested per the spec env.")
    print("The coordinator chains a deploy task ahead of each video-search task.")


if __name__ == "__main__":
    main()
