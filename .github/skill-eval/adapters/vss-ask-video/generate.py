#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-ask-video skill.

The vss-ask-video skill answers visual questions about a named sensor
by calling ``POST /generate`` on the VSS agent, instructing it to invoke the
``video_understanding`` tool.  It requires a **full-remote deployed VSS base
profile** (deploy mode = ``remote-all``; both LLM and VLM via remote
launchpad endpoints, no local NIMs required). It does NOT deploy VSS itself;
the coordinator chains a deploy task in front, plus a VIOS seed step to
upload the sample warehouse video.

Because vss-ask-video is a thin wrapper around POST /generate — purely
HTTP, GPU-independent at the harness level — the spec targets **ONE platform**
by default (L40S — cheapest available host).  Override with ``--platform``.

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
        --skill-dir skills/vss-ask-video \\
        --deploy-skill-dir skills/vss-deploy-profile \\
        --video-io-skill-dir skills/vss-manage-video-io-storage \\
        --spec skills/vss-ask-video/eval/base_profile_video_understanding.json
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
    "H100":         {"short_name": "h100",         "gpu_type": "H100",         "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S":         {"short_name": "l40s",         "gpu_type": "L40S",         "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW": {"short_name": "rtxpro6000bw", "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96, "brev_search": "RTX PRO"},
    "DGX-SPARK":    {"short_name": "spark",        "gpu_type": "GB10",         "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR":     {"short_name": "thor",         "gpu_type": "Thor",         "min_vram_per_gpu": 64, "brev_search": "Thor"},
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
    """Gold solution — assumes VSS base profile is already deployed and a
    sample warehouse video is already uploaded via VIOS.  The verifier drives
    the POST /generate assertion; the solution script asserts the agent is
    live, then defers."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-ask-video on {platform}\n"
        "# The verifier calls POST /generate directly — the solution script\n"
        "# just asserts VSS agent is reachable then defers to the verifier.\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 "
        "${VSS_AGENT_URL:-http://localhost:8000}/docs "
        ">/dev/null || {\n"
        "    echo 'VSS agent is not deployed — cannot solve vss-ask-video task'\n"
        "    exit 1\n"
        "}\n"
        "echo 'VSS agent is live — verifier will drive POST /generate.'\n"
    )


def _platforms_from_spec(spec: dict) -> list[str]:
    declared = ((spec.get("resources") or {}).get("platforms") or {})
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
    vios_skill_dir: Path | None,
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
            f"Use the `/vss-ask-video` skill against the VSS **{profile}** "
            f"profile already running on this `{platform}` host "
            "(`http://localhost:8000/docs` must respond, and a sample "
            "warehouse video must already be uploaded per the env notes below).",
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
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-ask-video-{profile}-{platform_short}{step_suffix}"',
            f'description = "vss-ask-video query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-ask-video", "generate", "{profile}", "{platform}"]',
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
            f'profile = "{spec.get("profile", "base")}"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            "requires_deployed_vss = true",
            "# Deploy mode is FULL-REMOTE (LLM + VLM both remote) — vss-ask-video",
            "# exercises POST /generate only, so there is no benefit to local NIMs.",
            # prerequisite_deploy_mode is alerts-only — the deploy marker
            # is profile-name only for base/lvs/search; the consumer
            # (envs/brev_env.py::_ensure_prerequisite_deployed) matches
            # on profile alone when this field is absent. Set it only if
            # this spec needs a specific alerts stack (verification vs
            # real-time).
            *([f'prerequisite_deploy_mode = "{spec["prerequisite_deploy_mode"]}"'] if spec.get("prerequisite_deploy_mode") else []),
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
        spec_src = skill_dir / "eval" / spec_name
        if spec_src.exists():
            shutil.copy(spec_src, tests_dir / spec_name)
        else:
            # Fallback: write the in-memory spec so tests/ is complete
            (tests_dir / spec_name).write_text(json.dumps(spec, indent=2))

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # skills/ — vss-ask-video + deploy + VIOS (the spec env mentions
        # pre-uploading a sample warehouse video via VIOS before running checks).
        copies = [
            (skill_dir,        "vss-ask-video"),
            (deploy_skill_dir, "vss-deploy-profile"),
            (vios_skill_dir,   "vss-manage-video-io-storage"),
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
        "--output-dir", required=True,
        help="Dataset output root (e.g. .github/skill-eval/datasets/vss-ask-video)",
    )
    parser.add_argument(
        "--skill-dir", required=True,
        help="Path to skills/vss-ask-video",
    )
    parser.add_argument(
        "--deploy-skill-dir", default=None,
        help="Path to skills/vss-deploy-profile (optional — included for agent diagnosis)",
    )
    parser.add_argument(
        "--video-io-skill-dir", dest="vios_skill_dir", default=None,
        help="Path to skills/vss-manage-video-io-storage (optional — spec env references VIOS video upload)",
    )
    parser.add_argument("--vios-skill-dir", dest="vios_skill_dir", help=argparse.SUPPRESS)
    parser.add_argument(
        "--spec", default=None,
        help="Path to spec JSON "
             "(default: <skill-dir>/eval/base_profile_video_understanding.json)",
    )
    parser.add_argument(
        "--platform", default=None, choices=list(PLATFORMS.keys()),
        help=f"Generate for one platform only (overrides spec.resources.platforms; "
             f"default: {DEFAULT_PLATFORM})",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    vios_skill_dir = Path(args.vios_skill_dir) if args.vios_skill_dir else None
    spec_path = (
        Path(args.spec)
        if args.spec
        else (skill_dir / "eval" / "base_profile_video_understanding.json")
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
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()
    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  vss-ask-video/{profile}/{task_id}")
        generate_task(
            platform, profile, spec, output_root, skill_dir,
            deploy_skill_dir, vios_skill_dir,
        )
    print()
    print(f"Generated {len(platforms)} platform(s) under {output_root}/{profile}/")
    print()
    print("Note: these tasks assume VSS base is already deployed on the target")
    print("Brev instance and a sample warehouse video has been uploaded via VIOS.")
    print("The coordinator is responsible for chaining those prerequisites ahead")
    print("of each vss-ask-video task in the same subagent queue.")


if __name__ == "__main__":
    main()
