#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vios skill.

The vios skill exercises VIOS (VST) API calls — upload video, extract
snapshot URL, extract clip URL, etc. — against a **full-remote-deployed
VSS base profile** (deploy mode = `remote-all`; LLM and VLM via remote
endpoints, no local NIMs, no GPU inference on the host). It does NOT
deploy VSS itself; the coordinator chains a deploy task in front.

Because VIOS/VST is GPU-independent and the deploy is remote-all, this
adapter targets **ONE platform** by default (L40S — cheapest stoppable
host). Use `--platform <X>` to override or `--all-platforms` if you
really want the fan-out (not what the spec asks for).

## Harbor chaining / dependencies

Harbor has no native mechanism to express inter-task dependencies
(`TaskConfig` lacks a `depends_on` / `prerequisites` field). Each
task is independent — Harbor runs exactly one task per trial on a
clean environment.

Chaining a vios trial *after* a deploy trial is a
**coordinator-level** concern: the coordinator arranges that the
target Brev instance already has VSS running before dispatching the
vios trial. Our eval plan already models this with
`execution_groups[<id>].queue_order` (sequential tasks on the same
instance share state).

To chain: in the run plan, put deploy tasks first in a group's queue,
then vios tasks for the same platform. Each vios task's
`task.toml [metadata]` records `requires_deployed_vss=true` so a
validator can refuse to dispatch it in isolation.

## Directory layout

    datasets/vios/base/<platform>/
        task.toml
        instruction.md
        tests/test.sh
        tests/test_base_profile_ops.py    (copied from skill)
        solution/solve.sh
        skills/vios/                (full skill copy)
        environment/Dockerfile            (FROM scratch; BrevEnvironment takes over)

One task per platform. All platforms share the same verifier — only
the `gpu_type` / `brev_search` / resource hints in task.toml differ,
matching the deploy-adapter convention.

Usage:
    python3 generate.py --output-dir ../../datasets/vios \\
        --skill-dir ../../../../../skills/vios \\
        --deploy-skill-dir ../../../../../skills/deploy \\
        --video-url https://videos.pexels.com/video-files/6079421/6079421-sd_640_360_24fps.mp4
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — mirrors the deploy adapter so vios runs on the same hosts
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":          {"short_name": "h100",          "gpu_type": "H100",         "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S":          {"short_name": "l40s",          "gpu_type": "L40S",         "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW":  {"short_name": "rtxpro6000bw",  "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96, "brev_search": "RTX PRO"},
    "DGX-SPARK":     {"short_name": "spark",         "gpu_type": "GB10",         "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR":      {"short_name": "thor",          "gpu_type": "Thor",         "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

# The vios skill exercises VIOS/VST, which is GPU-independent. The spec's
# env field mandates a `remote-all` deploy (both LLM and VLM via remote
# endpoints), so there's no value in fanning out to multiple platforms.
# Default to the single cheapest host.
DEFAULT_PLATFORM = "L40S"

DEFAULT_VIDEO_URL = (
    "https://videos.pexels.com/video-files/6079421/6079421-sd_640_360_24fps.mp4"
)
DEFAULT_VIDEO_NAME = "warehouse_forklift_pexels_6079421"


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for a
    single step's checks. Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# vios verifier (step {step}): delegates to the generic\n"
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


def generate_solve_script(platform: str) -> str:
    """Gold solution — assumes VSS is already deployed; the oracle just
    re-runs the verifier (there's no separate 'solve' action for a
    probe-style task since the agent's job is driving the API, which
    the verifier does independently)."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vios on {platform}\n"
        "# The verifier drives the VIOS queries directly — the solution\n"
        "# script simply asserts VSS is live, then defers to the verifier.\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 "
        "${VST_URL:-http://localhost:30888}/vst/api/v1/sensor/version "
        ">/dev/null || {\n"
        "    echo 'VSS is not deployed — cannot solve vios task'\n"
        "    exit 1\n"
        "}\n"
        "echo 'VSS is live — verifier will drive the queries.'\n"
    )


GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


def generate_task(platform: str, spec: dict, output_root: Path,
                  skill_dir: Path, deploy_skill_dir: Path | None,
                  video_url: str) -> None:
    """Emit one Harbor task directory per entry in spec['expects'] — i.e.
    step-<k>/ subdirs under `base/<platform>/` per AGENTS.md § 4.
    Single-step specs collapse to a flat `base/<platform>/`."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "spec.json")).name or "spec.json"

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / "base" / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md — ONE step's query + environment notes ONLY.
        # Never leak the verifier's `checks[]` into the instruction the agent
        # sees — they live in the spec, are copied into tests/, and the
        # verifier evaluates them independently. If the agent sees the checks
        # it can write to the test rather than do the work.
        lines = [
            f"Use the `/vios` skill against the VSS base profile "
            f"already running on this `{platform}` host "
            "(`http://localhost:30888/vst/api/v1/sensor/version` must respond).",
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
            f'name = "nvidia-vss/vios-base-{platform_short}{step_suffix}"',
            f'description = "VIOS API query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vios", "vst", "base", "{platform}"]',
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
            'skill = "vios"',
            f'profile = "{spec.get("profile", "base")}"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            "requires_deployed_vss = true",
            "# Deploy mode is FULL-REMOTE (LLM + VLM both remote) — vios",
            "# exercises VIOS/VST only, so there's no benefit to running local NIMs.",
            f'prerequisite_deploy_mode = "{spec.get("prerequisite_deploy_mode", "remote-all")}"',
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
        spec_src = skill_dir / "eval" / spec_name
        if spec_src.exists():
            shutil.copy(spec_src, tests_dir / spec_name)
        else:
            # write a copy of the spec even if the source file path differs
            (tests_dir / "base_profile_ops.json").write_text(
                json.dumps(spec, indent=2)
            )

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # skills/ — include vios + deploy (so agent can diagnose
        # if VSS isn't live).
        for src, name in ((skill_dir, "vios"), (deploy_skill_dir, "deploy")):
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
                        help="Dataset output root (e.g. tools/eval/harbor/datasets/vios)")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/vios")
    parser.add_argument("--deploy-skill-dir", default=None,
                        help="Path to skills/deploy (optional — included for agent debug)")
    parser.add_argument("--spec", default=None,
                        help="Path to base_profile_ops.json "
                             "(default: <skill-dir>/eval/base_profile_ops.json)")
    parser.add_argument("--platform", default=None,
                        choices=list(PLATFORMS.keys()),
                        help=f"Generate for this platform only "
                             f"(default: {DEFAULT_PLATFORM}; pass --all-platforms "
                             "to generate across every platform)")
    parser.add_argument("--all-platforms", action="store_true",
                        help="Fan out across every platform in PLATFORMS — "
                             "only useful for skills whose spec explicitly "
                             "asks for a multi-platform matrix. VIOS "
                             "does NOT: the base_profile_ops.json env says "
                             "run on ONE platform.")
    parser.add_argument("--video-url", default=DEFAULT_VIDEO_URL,
                        help="Public .mp4 URL used by the verifier "
                             "(default: Pexels warehouse forklift)")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    spec_path = Path(args.spec) if args.spec else (skill_dir / "eval" / "base_profile_ops.json")

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
    print(f"=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  video_url    : {args.video_url}")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()
    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  vios/base/{task_id}")
        generate_task(platform, spec, output_root, skill_dir,
                      deploy_skill_dir, args.video_url)
    print()
    print(f"Generated {len(platforms)} task(s) under {output_root}/base/")
    print()
    print("Note: these tasks assume VSS base is already deployed on the target")
    print("Brev instance. The coordinator (see tools/eval/harbor/AGENTS.md) is")
    print("responsible for injecting a matching deploy task ahead of each")
    print("vios task in the same subagent queue.")


if __name__ == "__main__":
    main()
