#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the critic skill.

The critic skill exercises the VSS agent's ``POST /api/v1/critic`` endpoint,
which uses a VLM to verify whether a video clip matches a search query. It
runs against a **full-remote-deployed VSS search profile** (deploy mode =
``remote-all``; LLM and VLM both remote; Cosmos Embed1 + Elasticsearch for
prior search). The ``/api/v1/critic`` route is only registered in
``dev-profile-search`` (and profiles derived from it) — base, LVS, and
alerts profiles do NOT expose it.

Because critic is GPU-independent (it submits clips to the already-running
VLM via the agent's HTTP API) and the spec pins ``remote-all`` deploy, this
adapter follows the same single-platform-per-spec shape as the vios /
video-search adapters. Default platform is L40S.

## Directory layout

    datasets/critic/<profile>/<platform>/step-<k>/
        task.toml
        instruction.md
        tests/test.sh
        tests/<spec>.json
        tests/generic_judge.py
        solution/solve.sh
        skills/critic/          (full skill copy)
        skills/video-search/    (critic step 2 exercises search → critic chain)
        skills/vios/            (needed to resolve sensor_id from friendly names)
        skills/deploy/          (for prerequisite diagnostics)
        environment/Dockerfile  (FROM scratch; BrevEnvironment takes over)

``<profile>`` comes from ``spec.profile`` (defaults to ``"search"`` if
absent — the search profile is the only one that exposes ``/api/v1/critic``).
``<k>`` is the 1-based index into ``expects[]``; single-step specs collapse
the step subdir.

Usage::

    python3 generate.py --output-dir ../../datasets/critic \\
        --skill-dir ../../../../../skills/critic \\
        --deploy-skill-dir ../../../../../skills/deploy \\
        --vios-skill-dir ../../../../../skills/vios \\
        --video-search-skill-dir ../../../../../skills/video-search
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — mirrors the vios/video-search adapters so critic runs on the
# same hosts. The spec's ``resources.platforms`` further filters this set.
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":         {"short_name": "h100",         "gpu_type": "H100",         "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S":         {"short_name": "l40s",         "gpu_type": "L40S",         "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW": {"short_name": "rtxpro6000bw", "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96, "brev_search": "RTX PRO"},
    "DGX-SPARK":    {"short_name": "spark",        "gpu_type": "GB10",         "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR":     {"short_name": "thor",         "gpu_type": "Thor",         "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

DEFAULT_PLATFORM = "L40S"

# Prepended to every instruction.md so the skill's own HITL bypass clause
# fires. Skills default to "ask the user" before /deploy; in CI there is no
# user, so without this preamble the agent either stalls or falls through to
# a localhost default (producing false negatives on steps that need a
# deployed profile).
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/deploy` or any other "
    "setup action the trial requires."
)

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for one
    step's checks.  Harbor reads ``/logs/verifier/reward.txt``."""
    return (
        "#!/bin/bash\n"
        f"# critic verifier (step {step}): delegates to the generic\n"
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
    """Gold solution — assumes the search profile is already deployed and
    sample videos are ingested. The verifier drives the critic API
    assertions independently."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: critic on {platform}\n"
        "# The verifier probes /api/v1/critic directly. This solve script\n"
        "# just confirms the agent backend is reachable.\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 "
        "${VSS_AGENT_URL:-http://localhost:8000}/docs "
        ">/dev/null || {\n"
        "    echo 'VSS agent is not deployed — cannot solve critic task'\n"
        "    exit 1\n"
        "}\n"
        "echo 'VSS agent is live — verifier will drive the critic queries.'\n"
    )


def _platforms_from_spec(spec: dict) -> list[str]:
    """Filter PLATFORMS by the spec's ``resources.platforms`` map (if any).
    Spec-declared platform keys not in our table are silently dropped."""
    declared = (spec.get("resources") or {}).get("platforms") or {}
    if not declared:
        return [DEFAULT_PLATFORM]
    return [p for p in declared if p in PLATFORMS] or [DEFAULT_PLATFORM]


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

def generate_task(
    platform: str,
    profile: str,
    spec: dict,
    output_root: Path,
    skill_dir: Path,
    deploy_skill_dir: Path | None,
    vios_skill_dir: Path | None,
    video_search_skill_dir: Path | None,
) -> None:
    """Emit one Harbor task directory per entry in ``spec['expects']`` — i.e.
    ``step-<k>/`` subdirs under ``<profile>/<platform>/`` per AGENTS.md § 4.
    Single-step specs collapse to a flat ``<profile>/<platform>/`` dir."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "critic.json")).name or "critic.json"

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / profile / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md — ONE step's query + environment notes ONLY.
        # Never leak the verifier's ``checks[]`` into the instruction the
        # agent sees — the verifier evaluates them independently.
        lines = [
            PREAMBLE,
            "",
            f"Use the `/critic` skill against the VSS **{profile}** profile "
            f"already running on this `{platform}` host "
            "(`http://localhost:8000/docs` must respond and "
            "`http://localhost:8000/api/v1/critic` must be listed in OpenAPI).",
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
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/critic-{profile}-{platform_short}{step_suffix}"',
            f'description = "critic query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["critic", "{profile}", "{platform}"]',
            "",
            "[environment]",
            'skills_dir = "/skills"',
            "",
            "[verifier.env]",
            'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
            'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
            # ANTHROPIC_MODEL forwards the judge model to the verifier.
            # Forwarding a literal default would bake it in and short-circuit
            # the proxy cascade — always delegate to the env var.
            'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
            "",
            "[metadata]",
            'skill = "critic"',
            f'profile = "{profile}"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            "requires_deployed_vss = true",
            "# Deploy mode is FULL-REMOTE (LLM + VLM both remote) — the critic",
            "# skill submits clips to the agent's /api/v1/critic route, which",
            "# calls the already-running VLM; no local NIM inference on the host.",
            f'prerequisite_deploy_mode = "{spec.get("deploy_mode") or spec.get("prerequisite_deploy_mode", "remote-all")}"',
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
            # Fallback: write the in-memory spec so the verifier can read it.
            (tests_dir / spec_name).write_text(json.dumps(spec, indent=2))

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # skills/ — critic (primary) + vios (sensor-list lookup) +
        #            video-search (step 2 exercises search → critic chain) +
        #            deploy (for prereq diagnostics)
        copies = [
            (skill_dir,             "critic"),
            (vios_skill_dir,        "vios"),
            (video_search_skill_dir, "video-search"),
            (deploy_skill_dir,      "deploy"),
        ]
        for src, name in copies:
            if src and Path(src).exists():
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
        help="Dataset output root (e.g. .github/skill-eval/datasets/critic)",
    )
    parser.add_argument(
        "--skill-dir", required=True,
        help="Path to skills/critic",
    )
    parser.add_argument(
        "--deploy-skill-dir", default=None,
        help="Path to skills/deploy (optional — for prerequisite diagnostics)",
    )
    parser.add_argument(
        "--vios-skill-dir", default=None,
        help="Path to skills/vios (optional — for sensor_id resolution)",
    )
    parser.add_argument(
        "--video-search-skill-dir", default=None,
        help="Path to skills/video-search (optional — step 2 exercises search→critic chain)",
    )
    parser.add_argument(
        "--spec", default=None,
        help="Path to critic.json (default: <skill-dir>/eval/critic.json)",
    )
    parser.add_argument(
        "--platform", default=None, choices=list(PLATFORMS.keys()),
        help="Generate for one platform only (overrides spec.resources.platforms)",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    vios_skill_dir = Path(args.vios_skill_dir) if args.vios_skill_dir else None
    video_search_skill_dir = (
        Path(args.video_search_skill_dir) if args.video_search_skill_dir else None
    )
    spec_path = Path(args.spec) if args.spec else (skill_dir / "eval" / "critic.json")

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)

    # ``profile`` defaults to "search" — the critic endpoint is only available
    # in the search profile (dev-profile-search). If the spec ever ships with
    # a different profile (e.g. a future "critic" profile), it will be read here.
    profile = spec.get("profile") or "search"
    platforms = [args.platform] if args.platform else _platforms_from_spec(spec)

    print("=== Inputs ===")
    print(f"  output_dir        : {output_root}")
    print(f"  skill_dir         : {skill_dir}")
    print(f"  spec              : {spec_path}")
    print(f"  profile           : {profile}")
    print(f"  platforms         : {platforms}")
    print(f"  queries           : {len(spec.get('expects', []))}")
    print(f"  total checks      : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()

    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  critic/{profile}/{task_id}")
        generate_task(
            platform, profile, spec, output_root,
            skill_dir, deploy_skill_dir, vios_skill_dir,
            video_search_skill_dir,
        )

    print()
    print(f"Generated {len(platforms)} platform(s) under {output_root}/{profile}/")
    print()
    print("Note: these tasks assume VSS search profile is already deployed on the")
    print("target Brev instance with /api/v1/critic registered and sample videos")
    print("ingested. The coordinator injects the prerequisite deploy task ahead of")
    print("each critic task in the same subagent queue.")


if __name__ == "__main__":
    main()
