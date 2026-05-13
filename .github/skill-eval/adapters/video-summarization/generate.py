#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the video-summarization skill.

The video-summarization skill exercises the LVS microservice on
`http://localhost:38111` against a **full-remote-deployed VSS lvs profile**
(deploy mode = `remote-all`; the agent's LLM and the VLM that LVS calls
are both served via remote launchpad endpoints, no local NIMs). It does
NOT deploy VSS itself; the coordinator chains a deploy task in front and
seeds the sample warehouse video via the vios skill before this trial.

Mirrors the vios adapter — single-task-per-platform, step-chained under
the spec's prerequisite profile name. Default platform is L40S because
summarization is throughput-bound on the remote VLM and the spec pins
this in `resources.platforms`.

## Directory layout

    datasets/video-summarization/<profile>/<platform>/step-<k>/
        task.toml
        instruction.md
        tests/test.sh
        tests/<spec>.json
        tests/generic_judge.py
        solution/solve.sh
        skills/video-summarization/
        skills/deploy/                (for prerequisite diagnostics)
        skills/vios/                  (the spec's env mentions seeding the
                                       sample video via vios upload first)
        environment/Dockerfile        (FROM scratch; BrevEnvironment takes over)

`<profile>` comes from `spec.profile` (here: `lvs`). `<k>` is the
1-based index into `expects[]`; single-step specs collapse the step
subdir.

Usage:
    python3 generate.py --output-dir ../../datasets/video-summarization \\
        --skill-dir ../../../../../skills/video-summarization \\
        --deploy-skill-dir ../../../../../skills/deploy \\
        --vios-skill-dir ../../../../../skills/vios
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
    "H100":          {"short_name": "h100",          "gpu_type": "H100",         "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S":          {"short_name": "l40s",          "gpu_type": "L40S",         "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW":  {"short_name": "rtxpro6000bw",  "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96, "brev_search": "RTX PRO"},
    "DGX-SPARK":     {"short_name": "spark",         "gpu_type": "GB10",         "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR":      {"short_name": "thor",          "gpu_type": "Thor",         "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

DEFAULT_PLATFORM = "L40S"

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/deploy` or any other "
    "setup action the trial requires."
)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    return (
        "#!/bin/bash\n"
        f"# video-summarization verifier (step {step}): delegates to the\n"
        "# generic LLM-as-judge (.github/skill-eval/verifiers/generic_judge.py).\n"
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
    """Gold solution — assumes the lvs profile is already deployed and
    a sample warehouse video is uploaded. Verifier drives the assertions."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: video-summarization on {platform}\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 "
        "${LVS_URL:-http://localhost:38111}/v1/ready "
        ">/dev/null || {\n"
        "    echo 'LVS is not deployed — cannot solve video-summarization task'\n"
        "    exit 1\n"
        "}\n"
        "echo 'LVS is live — verifier will drive the queries.'\n"
    )


GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


def _platforms_from_spec(spec: dict) -> list[str]:
    declared = ((spec.get("resources") or {}).get("platforms") or {})
    if not declared:
        return [DEFAULT_PLATFORM]
    return [p for p in declared if p in PLATFORMS] or [DEFAULT_PLATFORM]


def generate_task(platform: str, profile: str, spec: dict, output_root: Path,
                  skill_dir: Path, deploy_skill_dir: Path | None,
                  vios_skill_dir: Path | None) -> None:
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "spec.json")).name or "spec.json"

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / profile / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            PREAMBLE,
            "",
            f"Use the `/video-summarization` skill against the VSS **{profile}** "
            f"profile already running on this `{platform}` host "
            "(`http://localhost:38111/v1/ready` must respond, and a sample "
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

        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/video-summarization-{profile}-{platform_short}{step_suffix}"',
            f'description = "video-summarization query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["video-summarization", "lvs", "{profile}", "{platform}"]',
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
            'skill = "video-summarization"',
            f'profile = "{profile}"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            "requires_deployed_vss = true",
            f'prerequisite_deploy_mode = "{spec.get("prerequisite_deploy_mode", "remote-all")}"',
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ]
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

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

        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # skills/ — primary + deploy + vios (the spec env mentions seeding
        # the sample video via vios upload before these checks run).
        copies = [(skill_dir, "video-summarization"),
                  (deploy_skill_dir, "deploy"),
                  (vios_skill_dir, "vios")]
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
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", required=True,
                        help="Dataset output root (e.g. .github/skill-eval/datasets/video-summarization)")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/video-summarization")
    parser.add_argument("--deploy-skill-dir", default=None,
                        help="Path to skills/deploy (optional — included for agent debug)")
    parser.add_argument("--vios-skill-dir", default=None,
                        help="Path to skills/vios (optional — referenced by the spec for video upload prerequisite)")
    parser.add_argument("--spec", default=None,
                        help="Path to lvs_profile_summarize.json "
                             "(default: <skill-dir>/eval/lvs_profile_summarize.json)")
    parser.add_argument("--platform", default=None, choices=list(PLATFORMS.keys()),
                        help=f"Generate for one platform only (overrides spec.resources.platforms)")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    vios_skill_dir = Path(args.vios_skill_dir) if args.vios_skill_dir else None
    spec_path = Path(args.spec) if args.spec else (skill_dir / "eval" / "lvs_profile_summarize.json")

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)

    profile = spec.get("profile", "lvs")
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
        print(f"  GEN  video-summarization/{profile}/{task_id}")
        generate_task(platform, profile, spec, output_root, skill_dir,
                      deploy_skill_dir, vios_skill_dir)
    print()
    print(f"Generated {len(platforms)} platform(s) under {output_root}/{profile}/")


if __name__ == "__main__":
    main()
