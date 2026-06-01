#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-manage-video-io-storage skill.

The vss-manage-video-io-storage skill exercises VIOS (VST) API calls — upload video, extract
snapshot URL, extract clip URL, etc. The current spec
([`skills/vss-manage-video-io-storage/evals/vios_ops.json`]) **omits the `profile` field
by design** — the agent is expected to stand VIOS up standalone via the
skill's bundled `references/deploy-vios-service.md` runbook before
exercising the API. Per `.github/skill-eval/AGENTS.md` § 2, an absent
`profile` is the supported signal to the harness that no
`/vss-deploy-profile` prerequisite should be prepended; the trial runs
directly on a bare Brev instance.

Because VIOS/VST is mostly GPU-independent (only `streamprocessing-ms`
uses NVDEC/NVENC, and no single-stream eval saturates one GPU), this
adapter targets **ONE platform** by default (L40S — cheapest stoppable
host with a working `nvidia-container-toolkit`). Use `--platform <X>`
to override or `--all-platforms` if you really want the fan-out (not
what the spec asks for).

## Harbor chaining / dependencies

Harbor has no native mechanism to express inter-task dependencies
(`TaskConfig` lacks a `depends_on` / `prerequisites` field). Each
task is independent — Harbor runs exactly one task per trial on a
clean environment.

The adapter supports two modes, controlled by the spec's `profile` field:

1. **Profile-less spec (current vios_ops.json — `profile` absent):**
   `task.toml [metadata]` emits `requires_deployed_vss = false` and
   no `profile = ...` line. The trial runs on a bare Brev instance
   and the agent stands VIOS up itself via the skill's bundled
   `references/deploy-vios-service.md` runbook (pre-authorized
   per the skill's "Pre-authorized autonomous mode" branch). No
   chaining; no coordinator-level deploy injection.

2. **Profile-bound spec (e.g. `profile: "base"`):** `task.toml
   [metadata]` emits `profile = "<value>"` and
   `requires_deployed_vss = true`. The coordinator
   (see `.github/skill-eval/AGENTS.md` § 2) arranges that the
   target Brev instance already has VSS running on the requested
   profile before dispatching the vss-manage-video-io-storage
   trial — via `execution_groups[<id>].queue_order` (sequential
   tasks on the same instance share state). To chain: put a
   `/vss-deploy-profile -p <profile>` task first in the group's
   queue, then the vss-manage-video-io-storage tasks for the
   same platform.

## Directory layout

    .github/skill-eval/datasets/vss-manage-video-io-storage/base/<platform>/
        task.toml
        instruction.md
        tests/test.sh
        tests/vios_ops.json               (copied from skill)
        solution/solve.sh
        skills/vss-manage-video-io-storage/                (full skill copy)
        environment/Dockerfile            (FROM scratch; BrevEnvironment takes over)

One task per platform. All platforms share the same verifier — only
the `gpu_type` / `brev_search` / resource hints in task.toml differ,
matching the deploy-adapter convention.

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-manage-video-io-storage/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-manage-video-io-storage \\
        --skill-dir skills/vss-manage-video-io-storage \\
        --deploy-skill-dir skills/vss-deploy-profile \\
        --video-url https://videos.pexels.com/video-files/6079421/6079421-sd_640_360_24fps.mp4
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — mirrors the vss-deploy-profile adapter so vss-manage-video-io-storage runs on the same hosts
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":          {"short_name": "h100",          "gpu_type": "H100",         "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S":          {"short_name": "l40s",          "gpu_type": "L40S",         "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW":  {"short_name": "rtxpro6000bw",  "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96, "brev_search": "RTX PRO"},
    "DGX-SPARK":     {"short_name": "spark",         "gpu_type": "GB10",         "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR":      {"short_name": "thor",          "gpu_type": "Thor",         "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

# The vss-manage-video-io-storage skill exercises VIOS/VST. Only `streamprocessing-ms`
# needs a GPU (NVDEC/NVENC); the rest is CPU. A single-stream eval is
# trivially within one GPU. The current spec is also profile-less (the
# agent stands VIOS up standalone per the skill's deploy contract), so
# there is no value in fanning out to multiple platforms. Default to
# the single cheapest GPU host.
DEFAULT_PLATFORM = "L40S"

DEFAULT_VIDEO_URL = (
    "https://videos.pexels.com/video-files/6079421/6079421-sd_640_360_24fps.mp4"
)
DEFAULT_VIDEO_NAME = "warehouse_forklift_pexels_6079421"

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

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for a
    single step's checks. Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# vss-manage-video-io-storage verifier (step {step}): delegates to the generic\n"
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
    """Gold solution — assumes VSS is already deployed; the oracle just
    re-runs the verifier (there's no separate 'solve' action for a
    probe-style task since the agent's job is driving the API, which
    the verifier does independently)."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-manage-video-io-storage on {platform}\n"
        "# The verifier drives the VIOS queries directly — the solution\n"
        "# script simply asserts VSS is live, then defers to the verifier.\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 "
        "${VST_URL:-http://localhost:30888}/vst/api/v1/sensor/version "
        ">/dev/null || {\n"
        "    echo 'VSS is not deployed — cannot solve vss-manage-video-io-storage task'\n"
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
            PREAMBLE,
            "",
            f"Use the `/vss-manage-video-io-storage` skill against the VSS base profile "
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
            f'name = "nvidia-vss/vss-manage-video-io-storage-base-{platform_short}{step_suffix}"',
            f'description = "VIOS API query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-manage-video-io-storage", "vst", "base", "{platform}"]',
            "",
            "[environment]",
            'skills_dir = "/skills"',
            "",
            "[verifier.env]",
            'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
            'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
            # ANTHROPIC_MODEL gives the verifier's judge model cascade
            # (JUDGE_MODEL → ANTHROPIC_MODEL → literal) a working
            # fallback when JUDGE_MODEL is unset. Forwarding a literal
            # default for JUDGE_MODEL would bake it in and short-circuit
            # the cascade — the proxy 401s the literal default outright.
            'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
            # JUDGE_MAX_TURNS bumped from the generic_judge.py:276 default
            # of 25 because VIOS step trajectories run 6+ MB on a full
            # 13-step thread (upload + sensor probes + replay + record).
            # Checks that need to resolve a placeholder (e.g. <streamId>)
            # from deep in the trajectory before issuing a live probe
            # have been observed to exhaust the 25-turn budget — see the
            # step-2 check 1 fail on PR #516's first eval run. 50 turns
            # gives the per-check judge enough headroom to navigate the
            # trajectory and emit a verdict without changing other
            # skills' defaults.
            'JUDGE_MAX_TURNS = "50"',
            "",
            "[metadata]",
            'skill = "vss-manage-video-io-storage"',
            # `profile` is emitted ONLY when the spec declares one. The
            # current vios_ops.json omits `profile` by design — the trial
            # then runs without a /vss-deploy-profile prerequisite (per
            # `.github/skill-eval/AGENTS.md` § 2) and the agent stands
            # VIOS up standalone via the skill's deploy contract.
            # Defaulting to "base" here would resurrect the wrong
            # prerequisite-deploy behaviour silently.
            *([f'profile = "{spec["profile"]}"'] if spec.get("profile") else []),
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            # requires_deployed_vss tracks whether the trial assumes a
            # pre-deployed VSS stack. With the current profile-less
            # spec the agent is responsible for the deploy, so this is
            # false; if a future spec re-introduces `profile`, flip
            # this back to true (the coordinator gates dispatch on it).
            f"requires_deployed_vss = {'true' if spec.get('profile') else 'false'}",
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
        if spec_src.exists():
            shutil.copy(spec_src, tests_dir / spec_name)
        else:
            # write a copy of the spec even if the source file path differs
            (tests_dir / "vios_ops.json").write_text(
                json.dumps(spec, indent=2)
            )

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # skills/ — include vss-manage-video-io-storage + deploy (so agent can diagnose
        # if VSS isn't live).
        for src, name in ((skill_dir, "vss-manage-video-io-storage"), (deploy_skill_dir, "vss-deploy-profile")):
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
                        help="Dataset output root (e.g. .github/skill-eval/datasets/vss-manage-video-io-storage)")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/vss-manage-video-io-storage")
    parser.add_argument("--deploy-skill-dir", default=None,
                        help="Path to skills/vss-deploy-profile (optional — included for agent debug)")
    parser.add_argument("--spec", default=None,
                        help="Path to vios_ops.json "
                             "(default: <skill-dir>/evals/vios_ops.json)")
    parser.add_argument("--platform", default=None,
                        choices=list(PLATFORMS.keys()),
                        help=f"Generate for this platform only "
                             f"(default: {DEFAULT_PLATFORM}; pass --all-platforms "
                             "to generate across every platform)")
    parser.add_argument("--all-platforms", action="store_true",
                        help="Fan out across every platform in PLATFORMS — "
                             "only useful for skills whose spec explicitly "
                             "asks for a multi-platform matrix. VIOS "
                             "does NOT: the vios_ops.json env says "
                             "run on ONE platform.")
    parser.add_argument("--video-url", default=DEFAULT_VIDEO_URL,
                        help="Public .mp4 URL used by the verifier "
                             "(default: Pexels warehouse forklift)")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    spec_path = Path(args.spec) if args.spec else (skill_dir / "evals" / "vios_ops.json")

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
        print(f"  GEN  vss-manage-video-io-storage/base/{task_id}")
        generate_task(platform, spec, output_root, skill_dir,
                      deploy_skill_dir, args.video_url)
    print()
    print(f"Generated {len(platforms)} task(s) under {output_root}/base/")
    print()
    if spec.get("profile"):
        print("Note: this spec declares a `profile` — the coordinator (see")
        print(".github/skill-eval/AGENTS.md § 2) will inject a matching")
        print("/vss-deploy-profile task ahead of each vss-manage-video-io-storage task in the same")
        print("subagent queue.")
    else:
        print("Note: this spec OMITS `profile`. The trial runs on a bare Brev")
        print("instance — no /vss-deploy-profile prerequisite is injected. The agent is")
        print("expected to stand VIOS up standalone via the skill's bundled")
        print("references/deploy-vios-service.md runbook (documents both")
        print("direct-routing and SDRC-routed modes — either is acceptable) before")
        print("exercising the API.")


if __name__ == "__main__":
    main()
