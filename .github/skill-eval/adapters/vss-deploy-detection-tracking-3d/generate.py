#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-deploy-detection-tracking-3d skill.

The vss-deploy-detection-tracking-3d skill deploys and operates the RTVI-CV-3D
stack (MV3DT / Multi-View 3D Tracking) — per-camera DeepStream perception plus
BEV Fusion over multiple calibrated cameras, using the warehouse blueprint's
compose tree at `deploy/docker/compose.yml` with
`--env-file industry-profiles/warehouse-operations/.env`.

Three eval specs ship with the skill:

  - evals/deploy.json           — Deploy MV3DT on sample data, verify, teardown
                                  (3 steps, RTXPRO6000BW, gpu_count=1)
  - evals/calibration-chain.json— End-to-end custom-data calibration chain
                                  (2 steps, RTXPRO6000BW, gpu_count=1)
  - evals/routing.json          — CPU-only routing/disambiguation queries
                                  (4 steps, RTXPRO6000BW, gpu_count=0)

Each spec's `expects` list contains multiple steps; the adapter emits a
`step-<N>/` subdir per step so Harbor's dispatch loop runs them in order
with skip-on-prior-fail.

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-deploy-detection-tracking-3d/generate.py \\
        --output-dir /tmp/skill-eval/datasets/... \\
        --skill-dir skills/vss-deploy-detection-tracking-3d \\
        --spec skills/vss-deploy-detection-tracking-3d/evals/deploy.json

    # All specs:
    python3 .github/skill-eval/adapters/vss-deploy-detection-tracking-3d/generate.py \\
        --output-dir /tmp/skill-eval/datasets/... \\
        --skill-dir skills/vss-deploy-detection-tracking-3d \\
        --all-specs
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100": {"short_name": "h100", "gpu_type": "H100", "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S": {"short_name": "l40s", "gpu_type": "L40S", "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW": {
        "short_name": "rtxpro6000bw",
        "gpu_type": "RTX PRO 6000",
        "min_vram_per_gpu": 96,
        "brev_search": "RTX PRO",
    },
    "DGX-SPARK": {"short_name": "spark", "gpu_type": "GB10", "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR": {"short_name": "thor", "gpu_type": "Thor", "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)

# Specs that are valid for this adapter (have `expects` arrays).
# `evals.json` is an array of test-case objects (different format) and is excluded.
VALID_SPEC_NAMES = ("deploy.json", "calibration-chain.json", "routing.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _substitute_spec(spec: dict, platform: str) -> dict:
    """Substitute ``{{platform}}`` and ``{{repo_root}}`` in all string fields."""
    substitutions = {
        "platform": platform,
        "repo_root": "$HOME/video-search-and-summarization",
    }
    pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")

    def _sub(value):
        if isinstance(value, str):
            return pattern.sub(lambda m: str(substitutions.get(m.group(1), m.group(0))), value)
        if isinstance(value, list):
            return [_sub(v) for v in value]
        if isinstance(value, dict):
            return {k: _sub(v) for k, v in value.items()}
        return value

    return _sub(spec)


def _spec_kind(spec_path: Path) -> str:
    """Derive the dataset group name from the spec filename."""
    name = spec_path.stem.lower()
    if "calibration" in name:
        return "calibration-chain"
    if "routing" in name:
        return "routing"
    return "deploy"


def _instruction_intro(kind: str, platform: str) -> str:
    """Per-spec-kind intro paragraph for instruction.md."""
    if kind == "routing":
        return (
            f"Answer the following informational question about the "
            f"`vss-deploy-detection-tracking-3d` skill on a `{platform}` host. "
            "This is a routing/coverage eval — do NOT deploy, pull images, or "
            "run any `docker` commands. Load the skill's `SKILL.md` and reason "
            "about the answer from its documentation."
        )
    if kind == "calibration-chain":
        return (
            f"Use the `/vss-deploy-detection-tracking-3d` skill on this `{platform}` host "
            "to deploy MV3DT end-to-end on custom video data that requires calibration. "
            "This trial chains the auto-calibration workflow (AMC via "
            "`vss-generate-video-calibration`) into the MV3DT deploy. Follow the skill's "
            "routing logic and reference docs."
        )
    # deploy
    return (
        f"Use the `/vss-deploy-detection-tracking-3d` skill on this `{platform}` host "
        "to deploy MV3DT (RTVI-CV-3D) on the bundled sample dataset. The sample "
        "dataset ships calibration in-tree so no AMC run is needed. Follow the skill's "
        "routing logic (Q0-Q3) and reference docs for deploy, verify, and teardown."
    )


def _platform_gpu_counts_from_spec(spec: dict, platform_filter: str | None) -> list[tuple[str, int]]:
    """Return list of (platform, gpu_count) from spec's resources.platforms."""
    declared = (spec.get("resources") or {}).get("platforms") or {}
    if not declared:
        return [("RTXPRO6000BW", 1)]

    tasks: list[tuple[str, int]] = []
    for platform, cfg in declared.items():
        if platform_filter and platform != platform_filter:
            continue
        if platform not in PLATFORMS:
            continue
        gpu_count = (cfg or {}).get("gpu_count", 1)
        tasks.append((platform, int(gpu_count)))
    return tasks or [("RTXPRO6000BW", 1)]


# ---------------------------------------------------------------------------
# Test / solution scripts
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier."""
    return (
        "#!/bin/bash\n"
        f"# vss-deploy-detection-tracking-3d verifier (step {step}): delegates to the\n"
        "# generic LLM-as-judge (.github/skill-eval/verifiers/generic_judge.py).\n"
        "set -euo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
    )


def generate_solve_script(platform: str, kind: str) -> str:
    """Gold solution placeholder."""
    if kind == "routing":
        return (
            "#!/bin/bash\n"
            f"# Gold solution: vss-deploy-detection-tracking-3d (routing) on {platform}\n"
            "# Routing queries are informational — the verifier judges the agent's\n"
            "# response against the spec's checks. No deploy actions needed.\n"
            "set -euo pipefail\n"
            "\n"
            "echo 'Routing eval — verifier judges the agent response directly.'\n"
        )
    if kind == "calibration-chain":
        return (
            "#!/bin/bash\n"
            f"# Gold solution: vss-deploy-detection-tracking-3d (calibration-chain) on {platform}\n"
            "# The verifier judges the agent's calibration+deploy actions against the spec.\n"
            "set -euo pipefail\n"
            "\n"
            "docker inspect --format '{{.State.Health.Status}}' vss-rtvi-cv-bev-fusion 2>/dev/null \\\n"
            "    | grep -qx healthy \\\n"
            "    && echo 'MV3DT BEV Fusion healthy — calibration chain succeeded.' \\\n"
            "    || echo 'BEV Fusion not healthy — verifier will report the gap.'\n"
        )
    # deploy
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-deploy-detection-tracking-3d (deploy) on {platform}\n"
        "# The verifier judges the agent's deploy/verify/teardown actions.\n"
        "set -euo pipefail\n"
        "\n"
        "docker inspect --format '{{.State.Health.Status}}' vss-rtvi-cv-bev-fusion 2>/dev/null \\\n"
        "    | grep -qx healthy \\\n"
        "    && echo 'MV3DT BEV Fusion healthy — deploy succeeded.' \\\n"
        "    || echo 'BEV Fusion not healthy — verifier will report the gap.'\n"
    )


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_task(
    platform: str,
    gpu_count: int,
    spec: dict,
    spec_path: Path,
    output_root: Path,
    skill_dir: Path,
    calibration_skill_dir: Path | None,
    deploy_profile_skill_dir: Path | None,
) -> None:
    """Emit one Harbor task directory per step in the spec's expects list."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = spec_path.name
    kind = _spec_kind(spec_path)
    rendered_spec = _substitute_spec(spec, platform)
    dataset_group = kind

    for idx, expect in enumerate(rendered_spec.get("expects") or [], 1):
        step_dir = output_root / dataset_group / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # -- instruction.md --
        instruction = [
            PREAMBLE,
            "",
            _instruction_intro(kind, platform),
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            expect.get("query", ""),
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(instruction) + "\n")

        # -- task.toml --
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-deploy-detection-tracking-3d-{dataset_group}-{platform_short}{step_suffix}"',
            f'description = "MV3DT {kind} query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-deploy-detection-tracking-3d", "mv3dt", "{dataset_group}", "{platform}"]',
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
            'skill = "vss-deploy-detection-tracking-3d"',
            f'deployment = "{kind}"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f"gpu_count = {gpu_count}",
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            "min_root_disk_gb = 120",
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
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))

        # -- solution/ --
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform, kind))

        # -- skills/ — include the primary skill + related skills --
        skills_to_copy: list[tuple[Path | None, str]] = [
            (skill_dir, "vss-deploy-detection-tracking-3d"),
            (calibration_skill_dir, "vss-generate-video-calibration"),
            (deploy_profile_skill_dir, "vss-deploy-profile"),
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
                        help="Dataset output root")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/vss-deploy-detection-tracking-3d")
    parser.add_argument("--calibration-skill-dir", default=None,
                        help="Path to skills/vss-generate-video-calibration (optional)")
    parser.add_argument("--deploy-skill-dir", default=None,
                        help="Path to skills/vss-deploy-profile (optional)")
    parser.add_argument("--spec", default=None,
                        help="Path to a specific spec file (default: auto-detect)")
    parser.add_argument("--all-specs", action="store_true",
                        help="Generate tasks for all valid specs in evals/")
    parser.add_argument("--platform", default=None, choices=list(PLATFORMS.keys()),
                        help="Generate for this platform only")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    calibration_skill_dir = Path(args.calibration_skill_dir) if args.calibration_skill_dir else None
    deploy_profile_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None

    # Resolve spec(s) to generate
    if args.spec:
        spec_paths = [Path(args.spec)]
    elif args.all_specs:
        evals_dir = skill_dir / "evals"
        if not evals_dir.exists():
            evals_dir = skill_dir / "eval"
        spec_paths = sorted(
            p for p in evals_dir.glob("*.json")
            if p.name in VALID_SPEC_NAMES
        )
    else:
        # Default to deploy.json
        spec_paths = [skill_dir / "evals" / "deploy.json"]

    if not spec_paths:
        print("No valid specs found.", file=sys.stderr)
        sys.exit(1)

    print("=== Inputs ===")
    print(f"  output_dir             : {output_root}")
    print(f"  skill_dir              : {skill_dir}")
    print(f"  calibration_skill_dir  : {calibration_skill_dir or '(none)'}")
    print(f"  deploy_skill_dir       : {deploy_profile_skill_dir or '(none)'}")
    print(f"  specs                  : {[p.name for p in spec_paths]}")
    print(f"  platform filter        : {args.platform or '(all declared)'}")
    print()

    total_tasks = 0
    for spec_path in spec_paths:
        if not spec_path.exists():
            print(f"  SKIP {spec_path.name} (not found)", file=sys.stderr)
            continue

        spec = json.loads(spec_path.read_text())
        # Skip non-expects specs (e.g. evals.json which is an array)
        if not isinstance(spec, dict) or "expects" not in spec:
            print(f"  SKIP {spec_path.name} (no 'expects' field)")
            continue

        spec["_source_path"] = str(spec_path)
        kind = _spec_kind(spec_path)
        tasks = _platform_gpu_counts_from_spec(spec, args.platform)

        print(f"  === {spec_path.name} (kind={kind}) ===")
        print(f"      queries      : {len(spec.get('expects', []))}")
        print(f"      total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
        print(f"      platforms    : {tasks}")

        for platform, gpu_count in tasks:
            platform_short = PLATFORMS[platform]["short_name"]
            print(f"      GEN  {kind}/{platform_short}")
            generate_task(
                platform, gpu_count, spec, spec_path,
                output_root, skill_dir,
                calibration_skill_dir, deploy_profile_skill_dir,
            )
            total_tasks += 1
        print()

    print(f"Generated {total_tasks} task(s) under {output_root}/")


if __name__ == "__main__":
    main()
