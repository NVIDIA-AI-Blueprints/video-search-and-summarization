# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-ask-video skill.

The skill routes video questions through hot context, harness-owned OpenClaw
Markdown recall, structured VSS memory, configured bounded introspection, or a
direct fresh ``vss vlm run``. All VSS operations use the host checkout's
project-local CLI. The adapter never substitutes direct judge, Elasticsearch,
VIOS, RT-VLM, or VSS Agent HTTP calls for that contract.

The spec targets one platform by default (L40S). Override with ``--platform``.

## Directory layout

    .github/skill-eval/datasets/vss-ask-video/<profile>/<platform>/   (multi-step spec)
        step-1/
            task.toml, instruction.md, tests/, solution/, skills/, environment/
        step-2/
            ...
        step-N/
            ...

``<profile>`` comes from ``spec.profile`` (here: ``base``).

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-ask-video/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-ask-video \\
        --skill-dir skills/operations/vss-ask-video \\
        --deploy-skill-dir skills/deployment/vss-deploy-profile \\
        --video-io-skill-dir skills/operations/vss-manage-video-io-storage \\
        --spec skills/operations/vss-ask-video/evals/base_profile_video_understanding.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — same table as the other adapters; spec.resources.platforms
# narrows it down further.
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

DEFAULT_PLATFORM = "L40S"

# Prepended to every instruction.md so the skill's own HITL bypass clause
# fires.  Skills default to "ask the user" before /vss-deploy-profile; in CI there is no
# user, so without this preamble the agent stalls or falls through to a
# localhost default.
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------


def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for
    a single step's checks.  Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# vss-ask-video verifier (step {step}): delegates to the generic\n"
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
    """Gold solution verifies only the project-local CLI prerequisite."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-ask-video on {platform}\n"
        "# The skill owns routing and invokes VSS only through the project-local CLI.\n"
        "# This script does not call the judge, VLM, Elasticsearch, VIOS, or Agent directly.\n"
        "set -euo pipefail\n"
        "\n"
        'VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"\n'
        'test -f "${VSS_REPO_ROOT}/services/agent/pyproject.toml"\n'
        'VSS=(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss)\n'
        '"${VSS[@]}" --version\n'
        "echo 'Project-local VSS CLI is available; the verifier evaluates the routing trajectory.'\n"
    )


def _platforms_from_spec(spec: dict) -> list[str]:
    declared = (spec.get("resources") or {}).get("platforms") or {}
    if not declared:
        return [DEFAULT_PLATFORM]
    return [p for p in declared if p in PLATFORMS] or [DEFAULT_PLATFORM]


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------


def generate_task(
    platform: str,
    profile: str,
    spec: dict,
    output_root: Path,
    skill_dir: Path,
    deploy_skill_dir: Path | None,
    video_io_skill_dir: Path | None,
) -> None:
    """Emit one Harbor task directory per entry in spec['expects'] — i.e.
    step-<k>/ subdirs under ``<profile>/<platform_short>/`` per AGENTS.md § 4.
    Single-step specs collapse to a flat ``<profile>/<platform_short>/``."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "spec.json")).name or "spec.json"

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / profile / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md — ONE step's query + environment notes ONLY.
        # Never leak the verifier's checks[] into the instruction so the
        # agent can't write to the test rather than do the actual work.
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        lines = [
            PREAMBLE,
            "",
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
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-ask-video-{profile}-{platform_short}{step_suffix}"',
            f'description = "vss-ask-video query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-ask-video", "memory", "introspection", "openclaw", "{profile}", "{platform}"]',
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
            # ANTHROPIC_MODEL gives the verifier's judge model cascade
            # (JUDGE_MODEL → ANTHROPIC_MODEL → literal) a working fallback
            # when JUDGE_MODEL is unset. Forwarding a literal default for
            # JUDGE_MODEL would bake it in and short-circuit the cascade.
            'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
            "",
            "[metadata]",
            'skill = "vss-ask-video"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f"min_vram_gb_per_gpu = {pspec['min_vram_per_gpu']}",
            "# vss-ask-video uses the project-local CLI for structured memory,",
            "# configured introspection, and explicitly scoped fresh VLM jobs.",
            "# OpenClaw Markdown recall is harness-owned and may be fixture-backed here.",
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ]
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        # environment/ placeholder (BrevEnvironment takes over)
        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        # tests/ — wrapper + generic judge + spec copy
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
            # Fallback: write the in-memory spec so tests/ is complete
            (tests_dir / spec_name).write_text(json.dumps(spec, indent=2))

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # skills/ — vss-ask-video plus deployment and VIOS prerequisites.
        copies = [
            (skill_dir, "vss-ask-video"),
            (deploy_skill_dir, "vss-deploy-profile"),
            (video_io_skill_dir, "vss-manage-video-io-storage"),
        ]
        for src, name in copies:
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
        "--output-dir",
        required=True,
        help="Dataset output root (e.g. .github/skill-eval/datasets/vss-ask-video)",
    )
    parser.add_argument(
        "--skill-dir",
        required=True,
        help="Path to skills/operations/vss-ask-video",
    )
    parser.add_argument(
        "--deploy-skill-dir",
        default=None,
        help="Path to skills/deployment/vss-deploy-profile (optional — included for agent diagnosis)",
    )
    parser.add_argument(
        "--video-io-skill-dir",
        dest="video_io_skill_dir",
        default=None,
        help="Path to skills/operations/vss-manage-video-io-storage (optional — spec env references VIOS video upload)",
    )
    parser.add_argument(
        "--vios-skill-dir", dest="video_io_skill_dir", help=argparse.SUPPRESS
    )
    if any(
        arg == "--vios-skill-dir" or arg.startswith("--vios-skill-dir=")
        for arg in sys.argv[1:]
    ):
        print(
            "WARNING: --vios-skill-dir is deprecated; use --video-io-skill-dir.",
            file=sys.stderr,
        )
    parser.add_argument(
        "--spec",
        default=None,
        help="Path to spec JSON "
        "(default: <skill-dir>/evals/base_profile_video_understanding.json)",
    )
    parser.add_argument(
        "--platform",
        default=None,
        choices=list(PLATFORMS.keys()),
        help=f"Generate for one platform only (overrides spec.resources.platforms; "
        f"default: {DEFAULT_PLATFORM})",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    video_io_skill_dir = (
        Path(args.video_io_skill_dir) if args.video_io_skill_dir else None
    )
    spec_path = (
        Path(args.spec)
        if args.spec
        else (skill_dir / "evals" / "base_profile_video_understanding.json")
    )

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)

    profile = spec.get("profile", "base")
    platforms = [args.platform] if args.platform else _platforms_from_spec(spec)

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  profile      : {profile}")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(
        f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}"
    )
    print()
    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  vss-ask-video/{profile}/{task_id}")
        generate_task(
            platform,
            profile,
            spec,
            output_root,
            skill_dir,
            deploy_skill_dir,
            video_io_skill_dir,
        )
    print()
    print(f"Generated {len(platforms)} platform(s) under {output_root}/{profile}/")
    print()
    print("Note: task queries declare any deployment and fixture prerequisites.")
    print("All VSS operations are evaluated through the project-local CLI contract.")


if __name__ == "__main__":
    main()
