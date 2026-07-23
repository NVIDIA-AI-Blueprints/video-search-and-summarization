#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-deploy-detection-tracking-3d skill.

The vss-deploy-detection-tracking-3d skill deploys and operates the
RTVI-CV-3D / MV3DT stack (Multi-View 3D Tracking) — per-camera
DeepStream perception plus BEV Fusion over multiple calibrated cameras.

Four eval specs ship with the skill:
  - evals/deploy.json           — Full deploy/verify/teardown flow on the
                                  bundled sample dataset (3 steps)
  - evals/calibration-chain.json — End-to-end custom-data calibration
                                  chain + deploy + teardown (2 steps)
  - evals/routing.json          — CPU-only routing coverage eval; no
                                  containers deployed (4 queries, 1 step
                                  each treated as single-step)
  - evals/evals.json            — Static routing/knowledge tests (no
                                  deploy, JSON array format)

All specs target RTXPRO6000BW. deploy.json and calibration-chain.json
are multi-step (state preserved between steps); routing.json is
single-step (each query is independent, treated as one step with all
queries as checks).

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-deploy-detection-tracking-3d/generate.py \\
        --output-dir /tmp/skill-eval/datasets/<leg-slug>/<run_id> \\
        --skill-dir skills/vss-deploy-detection-tracking-3d \\
        --spec skills/vss-deploy-detection-tracking-3d/evals/deploy.json

    # With platform filter:
    python3 .github/skill-eval/adapters/vss-deploy-detection-tracking-3d/generate.py \\
        --output-dir /tmp/skill-eval/datasets/<leg-slug>/<run_id> \\
        --skill-dir skills/vss-deploy-detection-tracking-3d \\
        --spec skills/vss-deploy-detection-tracking-3d/evals/deploy.json \\
        --platform RTXPRO6000BW
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

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

DEFAULT_PLATFORM = "RTXPRO6000BW"
DEFAULT_SPEC = "deploy.json"

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)


def _substitute_spec(spec: dict, platform: str) -> dict:
    """Substitute {{platform}} and {{repo_root}} in all string fields."""
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


def _platform_modes_from_spec(spec: dict, platform_filter: str | None) -> list[str]:
    """Return list of platforms declared in spec's resources.platforms."""
    declared = ((spec.get("resources") or {}).get("platforms") or {})
    if not declared:
        return [platform_filter or DEFAULT_PLATFORM]

    platforms: list[str] = []
    for platform in declared:
        if platform_filter and platform != platform_filter:
            continue
        if platform not in PLATFORMS:
            continue
        platforms.append(platform)
    return platforms or [platform_filter or DEFAULT_PLATFORM]


def _spec_kind(spec_path: Path) -> str:
    """Derive a dataset-group name from the spec filename."""
    stem = spec_path.stem.lower().replace("-", "_")
    return stem


def _gpu_count_for_platform(spec: dict, platform: str) -> int:
    """Read gpu_count from spec's resources.platforms.<platform>."""
    declared = ((spec.get("resources") or {}).get("platforms") or {})
    cfg = declared.get(platform) or {}
    return int(cfg.get("gpu_count", 1))


def _is_array_spec(spec: dict) -> bool:
    """Detect if the spec is a JSON array (evals.json format)."""
    return isinstance(spec, list)


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
            "# Routing eval is informational — no containers to check.\n"
            "set -euo pipefail\n"
            "echo 'Routing eval — no deploy needed. Verifier judges responses.'\n"
        )
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-deploy-detection-tracking-3d ({kind}) on {platform}\n"
        "# The verifier judges the agent's actions against the spec's checks;\n"
        "# the solver asserts the MV3DT stack is reachable after the agent runs.\n"
        "set -euo pipefail\n"
        "\n"
        "docker ps --format '{{.Names}}' | grep -qx vss-rtvi-cv-mv3dt \\\n"
        "    && echo 'MV3DT is running — deploy succeeded.' \\\n"
        "    || echo 'MV3DT not running — verifier will report the gap.'\n"
    )


def generate_task(
    platform: str,
    spec: dict,
    spec_path: Path,
    output_root: Path,
    skill_dir: Path,
    calibration_skill_dir: Path | None,
) -> None:
    """Emit Harbor task directories for one (spec, platform) pair.

    Multi-step specs get step-<N>/ subdirs; single-step specs get a flat
    layout.
    """
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = spec_path.name
    kind = _spec_kind(spec_path)
    rendered_spec = _substitute_spec(spec, platform)
    gpu_count = _gpu_count_for_platform(spec, platform)

    for idx, expect in enumerate(rendered_spec.get("expects") or [], 1):
        step_dir = output_root / kind / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md
        instruction = [
            PREAMBLE,
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            expect.get("query", ""),
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(instruction) + "\n")

        # task.toml
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-deploy-detection-tracking-3d-{kind}-{platform_short}{step_suffix}"',
            f'description = "MV3DT {kind} query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-deploy-detection-tracking-3d", "mv3dt", "{kind}", "{platform}"]',
            "",
            "[environment]",
            'skills_dir = "/skills"',
            "",
            "[verifier.env]",
            'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
            'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
            'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
            # Higher turn budget for multi-step deploy trials with deep
            # trajectories (AMC chain can exceed 25 turns easily).
            'JUDGE_MAX_TURNS = "50"',
            "",
            "[metadata]",
            f'skill = "vss-deploy-detection-tracking-3d"',
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

        # environment/
        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        # tests/
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform, kind))

        # skills/ — include the primary skill + calibration skill (for
        # the calibration-chain spec which chains to it)
        dst = step_dir / "skills" / "vss-deploy-detection-tracking-3d"
        if dst.exists():
            shutil.rmtree(dst)
        if skill_dir.exists():
            dst.mkdir(parents=True, exist_ok=True)
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                shutil.copy2(skill_md, dst / "SKILL.md")
            refs_src = skill_dir / "references"
            if refs_src.exists():
                shutil.copytree(refs_src, dst / "references")

        if calibration_skill_dir and calibration_skill_dir.exists():
            cal_dst = step_dir / "skills" / "vss-generate-video-calibration"
            if cal_dst.exists():
                shutil.rmtree(cal_dst)
            cal_dst.mkdir(parents=True, exist_ok=True)
            cal_md = calibration_skill_dir / "SKILL.md"
            if cal_md.exists():
                shutil.copy2(cal_md, cal_dst / "SKILL.md")
            cal_refs = calibration_skill_dir / "references"
            if cal_refs.exists():
                shutil.copytree(cal_refs, cal_dst / "references")


def generate_routing_task(
    platform: str,
    spec: dict,
    spec_path: Path,
    output_root: Path,
    skill_dir: Path,
) -> None:
    """Generate tasks for the routing spec (evals.json-style array or
    routing.json with multiple independent queries).

    For the routing spec (routing.json), each query in `expects` is
    independent — treated as a single-step spec. The framework runs
    them as step-1..N but skip-on-prior-fail is semantically irrelevant
    (each is self-contained).
    """
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = spec_path.name
    kind = _spec_kind(spec_path)
    rendered_spec = _substitute_spec(spec, platform)
    gpu_count = _gpu_count_for_platform(spec, platform)

    for idx, expect in enumerate(rendered_spec.get("expects") or [], 1):
        step_dir = output_root / kind / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md — routing queries are informational only
        instruction = [
            PREAMBLE,
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            expect.get("query", ""),
            "",
            "Answer the question using the skill's documentation. "
            "Do NOT actually deploy any containers or pull images.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(instruction) + "\n")

        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-deploy-detection-tracking-3d-{kind}-{platform_short}{step_suffix}"',
            f'description = "MV3DT routing query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-deploy-detection-tracking-3d", "mv3dt", "{kind}", "{platform}", "routing"]',
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
            f'skill = "vss-deploy-detection-tracking-3d"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f"gpu_count = {gpu_count}",
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            "min_root_disk_gb = 60",
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

        # tests/
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform, kind))

        # skills/
        dst = step_dir / "skills" / "vss-deploy-detection-tracking-3d"
        if dst.exists():
            shutil.rmtree(dst)
        if skill_dir.exists():
            dst.mkdir(parents=True, exist_ok=True)
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                shutil.copy2(skill_md, dst / "SKILL.md")
            refs_src = skill_dir / "references"
            if refs_src.exists():
                shutil.copytree(refs_src, dst / "references")


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
                        help="Path to skills/vss-generate-video-calibration "
                             "(included for calibration-chain spec)")
    parser.add_argument(
        "--spec",
        default=None,
        help=f"Path to spec file (default: <skill-dir>/evals/{DEFAULT_SPEC})",
    )
    parser.add_argument("--platform", default=None, choices=list(PLATFORMS.keys()),
                        help=f"Generate for this platform only (default: from spec)")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    calibration_skill_dir = (
        Path(args.calibration_skill_dir) if args.calibration_skill_dir
        else skill_dir.parent / "vss-generate-video-calibration"
    )
    if not calibration_skill_dir.exists():
        calibration_skill_dir = None

    if args.spec:
        spec_path = Path(args.spec)
    else:
        spec_path = skill_dir / "evals" / DEFAULT_SPEC
        if not spec_path.exists():
            legacy = skill_dir / "eval" / DEFAULT_SPEC
            if legacy.exists():
                spec_path = legacy

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    raw = json.loads(spec_path.read_text())

    # Handle the evals.json array-of-questions format — it's not a
    # standard expects-based spec; skip it (routing coverage is handled
    # by routing.json which has the standard format).
    if _is_array_spec(raw):
        print(f"SKIP: {spec_path.name} is an array-format spec (not expects-based)")
        print("      Use routing.json for the standard routing coverage eval.")
        sys.exit(0)

    spec = raw
    spec["_source_path"] = str(spec_path)
    platforms = _platform_modes_from_spec(spec, args.platform)
    kind = _spec_kind(spec_path)

    print("=== Inputs ===")
    print(f"  output_dir             : {output_root}")
    print(f"  skill_dir              : {skill_dir}")
    print(f"  calibration_skill_dir  : {calibration_skill_dir or '(not found)'}")
    print(f"  spec                   : {spec_path}")
    print(f"  spec kind              : {kind}")
    print(f"  platforms              : {platforms}")
    print(f"  queries                : {len(spec.get('expects', []))}")
    print(f"  total checks           : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()

    for platform in platforms:
        platform_short = PLATFORMS[platform]["short_name"]
        print(f"  GEN  vss-deploy-detection-tracking-3d/{kind}/{platform_short}")
        if kind == "routing":
            generate_routing_task(platform, spec, spec_path, output_root, skill_dir)
        else:
            generate_task(
                platform, spec, spec_path, output_root, skill_dir,
                calibration_skill_dir,
            )

    print()
    print(f"Generated {len(platforms)} task(s) under {output_root}/{kind}/")


if __name__ == "__main__":
    main()
