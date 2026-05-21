#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-generate-video-calibration skill.

The vss-generate-video-calibration skill handles AMC (AutoMagicCalib)
deployment and calibration workflows — from local MP4 files, RTSP
streams, or the bundled sample dataset. It does NOT require an external
VSS stack (no /vss-deploy-profile prerequisite); the skill itself brings
up `vss-auto-calibration` + `vss-auto-calibration-ui` from
`nvcr.io/nvstaging/vss-core/`.

One task per (spec_stem × platform). Each `expects` entry in the spec
becomes a `step-<N>/` subdir under `<spec_stem>/<platform_short>/`.
Multi-step specs are dispatched in order by the coordinator so that
state from step N is available to step N+1 checks.

No `profile` in the spec → no prerequisite deploy → BrevEnvironment
runs the clean-state path before the trial.

Matrix:
    Spec stems : auto-calibration (one spec today)
    Platforms  : RTXPRO6000BW (as declared in auto-calibration.json)

Directory layout:
    .github/skill-eval/datasets/vss-generate-video-calibration/<spec_stem>/<platform_short>/
        [step-<N>/]            # present when len(expects) > 1
            instruction.md
            task.toml
            tests/test.sh
            tests/generic_judge.py
            tests/<spec_stem>.json
            solution/solve.sh
            skills/vss-generate-video-calibration/
            environment/Dockerfile

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-generate-video-calibration/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-generate-video-calibration \\
        --skill-dir skills/vss-generate-video-calibration

    # One spec
    python3 .github/skill-eval/adapters/vss-generate-video-calibration/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-generate-video-calibration \\
        --skill-dir skills/vss-generate-video-calibration \\
        --spec skills/vss-generate-video-calibration/eval/auto-calibration.json

Run with Harbor (example for RTXPRO6000BW, step 1 of 11):
    export PYTHONPATH="$(pwd)/.github/skill-eval:${PYTHONPATH:-}"
    uvx harbor run --environment-import-path "envs.brev_env:BrevEnvironment" \\
        -p .github/skill-eval/datasets/vss-generate-video-calibration/auto-calibration \\
        --include-task-name "rtxpro6000bw-step-1" \\
        -a claude-code -n 1
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — mirrors the vss-deploy-profile adapter so pool members match
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":         {"short_name": "h100",         "gpu_type": "H100",         "min_vram_per_gpu": 80,  "brev_search": "H100"},
    "L40S":         {"short_name": "l40s",         "gpu_type": "L40S",         "min_vram_per_gpu": 48,  "brev_search": "L40S"},
    "RTXPRO6000BW": {"short_name": "rtxpro6000bw", "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96,  "brev_search": "RTX PRO"},
    "DGX-SPARK":    {"short_name": "spark",        "gpu_type": "GB10",         "min_vram_per_gpu": 96,  "brev_search": "GB10"},
    "IGX-THOR":     {"short_name": "thor",         "gpu_type": "Thor",         "min_vram_per_gpu": 64,  "brev_search": "Thor"},
}

# Disk + driver minimums — AMC pulls ~2 GB from nvcr.io/nvstaging/vss-core/.
# No local LLM/VLM NIMs, so 50 GB headroom is enough. Keep the driver
# minimum at the same level as vss-deploy-profile to guarantee NVIDIA
# Container Toolkit works on the pool instance.
_DEFAULT_MIN_ROOT_DISK_GB = 60
_DEFAULT_MIN_DRIVER_VERSION = "580.95"

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"

# Prepended to every instruction.md so the skill's own HITL bypass clause
# fires. Skills default to "ask the user" before any deploy action; in CI
# there's no user, so without this preamble the agent stalls.
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)


# ---------------------------------------------------------------------------
# Spec substitution
# ---------------------------------------------------------------------------

def _render_spec(spec: dict, platform: str) -> dict:
    """Substitute `{{platform}}` and `{{repo_root}}` in every string field."""
    import re as _re
    _LEGACY_REPO = "/home/ubuntu/video-search-and-summarization"
    _PORTABLE_REPO = "$HOME/video-search-and-summarization"
    pattern = _re.compile(r"\{\{\s*(\w+)\s*\}\}")
    substitutions = {
        "platform": platform,
        "repo_root": _PORTABLE_REPO,
    }

    def _sub(value):
        if isinstance(value, str):
            rendered = pattern.sub(
                lambda m: str(substitutions.get(m.group(1), m.group(0))),
                value,
            )
            return rendered.replace(_LEGACY_REPO, _PORTABLE_REPO)
        if isinstance(value, list):
            return [_sub(v) for v in value]
        if isinstance(value, dict):
            return {k: _sub(v) for k, v in value.items()}
        return value

    return _sub(spec)


# ---------------------------------------------------------------------------
# Test script
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for a
    single step's checks. Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# vss-generate-video-calibration verifier (step {step}): delegates to generic judge\n"
        "# (.github/skill-eval/verifiers/generic_judge.py).\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
        "exit 0\n"
    )


# ---------------------------------------------------------------------------
# Solution script
# ---------------------------------------------------------------------------

def generate_solve_script(platform: str) -> str:
    """Gold solution: sync repo to PR head, start AMC containers, then run the
    sample calibration test end-to-end via the calibration API."""
    lines = [
        "#!/bin/bash",
        f"# Gold solution: vss-generate-video-calibration on {platform}",
        "set -euo pipefail",
        "",
        'REPO="$HOME/video-search-and-summarization"',
        "",
        "# --- Prerequisites ---",
        "if ! command -v docker &>/dev/null; then",
        "    curl -fsSL https://get.docker.com | sh",
        "fi",
        "",
        "# --- NGC login ---",
        'if [ -n "${NGC_CLI_API_KEY:-}" ]; then',
        "    docker login nvcr.io -u '\\$oauthtoken' -p \"$NGC_CLI_API_KEY\" 2>/dev/null || true",
        "fi",
        "",
        "# --- Sync repo to PR head ---",
        'PR_REPO="${PR_REPO:-NVIDIA-AI-Blueprints/video-search-and-summarization}"',
        'PR_HEAD_SHA="${PR_HEAD_SHA:-}"',
        'VSS_REPO_URL="https://github.com/${PR_REPO}.git"',
        'if [ ! -d "$REPO/.git" ]; then',
        '    rm -rf "$REPO"',
        '    git clone --no-checkout --depth=1 --branch develop "$VSS_REPO_URL" "$REPO"',
        "fi",
        'cd "$REPO"',
        'git remote set-url origin "$VSS_REPO_URL"',
        'if [ -n "$PR_HEAD_SHA" ]; then',
        '    git fetch --depth=1 origin "$PR_HEAD_SHA"',
        '    git -c advice.detachedHead=false checkout --force "$PR_HEAD_SHA"',
        '    git reset --hard "$PR_HEAD_SHA"',
        "else",
        "    git fetch --depth=1 origin develop",
        "    git -c advice.detachedHead=false checkout --force FETCH_HEAD",
        "    git reset --hard FETCH_HEAD",
        "fi",
        "git clean -fdx -e data/ -e .env",
        "cd - > /dev/null",
        "",
        "# --- Set environment variables ---",
        "ENV_FILE=$REPO/deploy/docker/industry-profiles/warehouse-operations/.env",
        'HOST_IP=$(hostname -I | awk \'{print $1}\')',
        'sed -i "s|^HOST_IP=.*|HOST_IP=$HOST_IP|" "$ENV_FILE" 2>/dev/null || true',
        'sed -i "s|^VSS_APPS_DIR=.*|VSS_APPS_DIR=$REPO/deploy/docker|" "$ENV_FILE" 2>/dev/null || true',
        'sed -i "s|^VSS_DATA_DIR=.*|VSS_DATA_DIR=$REPO/data|" "$ENV_FILE" 2>/dev/null || true',
        'mkdir -p "$REPO/data/auto-calib/vggt"',
        "",
        "# --- Deploy AMC containers ---",
        "cd $REPO/deploy/docker",
        "docker compose --env-file industry-profiles/warehouse-operations/.env \\",
        "  -f services/auto-calibration/ms/compose.yml \\",
        "  -f services/auto-calibration/ui/compose.yml \\",
        "  --profile auto_calib up -d",
        "",
        "# --- Wait for MS readiness ---",
        "MS_PORT=$(grep ^VSS_AUTO_CALIBRATION_PORT $REPO/deploy/docker/industry-profiles/warehouse-operations/.env 2>/dev/null | cut -d= -f2 || echo 8010)",
        "for i in $(seq 1 60); do",
        "    curl -sf --max-time 5 http://localhost:${MS_PORT}/v1/ready | grep -q '\"code\":0' && break",
        "    sleep 5",
        "done",
        "",
        "echo 'AMC ready — verifier will drive calibration queries.'",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_task(platform: str, spec: dict, spec_stem: str,
                  output_root: Path, skill_dir: Path) -> None:
    """Emit one Harbor task directory per entry in spec['expects'] under
    `<spec_stem>/<platform_short>/[step-<N>/]`."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = f"{spec_stem}.json"

    # Resolve gpu_count from the spec's platforms declaration
    gpu_count = (spec.get("resources", {}).get("platforms", {})
                     .get(platform, {}) or {}).get("gpu_count", 1)

    rendered_spec = _render_spec(spec, platform)
    rendered_expects = rendered_spec.get("expects") or []

    for idx, expect in enumerate(rendered_expects, 1):
        step_dir = output_root / spec_stem / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # -- instruction.md --
        # Never leak verifier checks into the agent instruction.
        lines = [
            PREAMBLE,
            "",
            f"Use the `/vss-generate-video-calibration` skill on this `{platform}` host.",
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            expect.get("query", ""),
            "",
            "## Environment notes",
            "",
            rendered_spec.get("env", ""),
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(lines) + "\n")

        # -- task.toml --
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-generate-video-calibration-{spec_stem}-{platform_short}{step_suffix}"',
            f'description = "AMC calibration query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-generate-video-calibration", "amc", "{spec_stem}", "{platform}"]',
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
            'skill = "vss-generate-video-calibration"',
            # No `profile` field — this skill brings up its own containers;
            # there is no /vss-deploy-profile prerequisite. BrevEnvironment
            # treats absent `profile` as desired="" and runs the clean-state
            # path (wipes containers/networks/volumes) before the trial.
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'gpu_count = {gpu_count}',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_root_disk_gb = {_DEFAULT_MIN_ROOT_DISK_GB}',
            f'min_gpu_driver_version = "{_DEFAULT_MIN_DRIVER_VERSION}"',
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ]
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        # -- environment/ placeholder --
        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        # -- tests/: wrapper + generic judge + rendered spec --
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        # Write the rendered spec ({{platform}} substituted)
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))

        # -- solution/solve.sh --
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # -- skills/ --
        if skill_dir and skill_dir.exists():
            skill_dest = step_dir / "skills" / "vss-generate-video-calibration"
            if skill_dest.exists():
                shutil.rmtree(skill_dest)
            shutil.copytree(skill_dir, skill_dest)


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
                        help="Path to skills/vss-generate-video-calibration")
    parser.add_argument("--spec", default=None,
                        help="Path to a specific eval JSON spec "
                             "(default: all specs under <skill-dir>/eval/*.json)")
    parser.add_argument("--platform", default=None,
                        choices=list(PLATFORMS.keys()),
                        help="Generate for this platform only (default: per spec)")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)

    # Collect specs
    if args.spec:
        spec_paths = [Path(args.spec)]
    else:
        eval_dir = skill_dir / "eval"
        if not eval_dir.is_dir():
            print(f"No eval/ dir found at {eval_dir}", file=sys.stderr)
            sys.exit(1)
        spec_paths = sorted(eval_dir.glob("*.json"))
        if not spec_paths:
            print(f"No *.json specs found under {eval_dir}", file=sys.stderr)
            sys.exit(1)

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  specs        : {[str(p) for p in spec_paths]}")
    print(f"  platform     : {args.platform or '(from spec)'}")
    print()

    total_tasks = 0
    for spec_path in spec_paths:
        spec = json.loads(spec_path.read_text())
        spec_stem = spec_path.stem
        platforms_decl = (spec.get("resources") or {}).get("platforms") or {}
        if not platforms_decl:
            print(f"SKIP {spec_stem}: no resources.platforms declared", file=sys.stderr)
            continue

        for platform, plat_cfg in platforms_decl.items():
            if args.platform and platform != args.platform:
                continue
            if platform not in PLATFORMS:
                print(f"SKIP {spec_stem}/{platform}: unknown platform", file=sys.stderr)
                continue
            platform_short = PLATFORMS[platform]["short_name"]
            n_expects = len(spec.get("expects") or [])
            print(f"  GEN  {spec_stem}/{platform_short}  steps={n_expects}")
            generate_task(platform, spec, spec_stem, output_root, skill_dir)
            total_tasks += 1

    if total_tasks == 0:
        print("No tasks generated.", file=sys.stderr)
        sys.exit(1)

    print()
    print(f"Generated {total_tasks} task set(s) under {output_root}/")
    print()
    print("Run step 1 of the auto-calibration spec on RTXPRO6000BW:")
    print(f'  export PYTHONPATH="$(pwd)/.github/skill-eval:${{PYTHONPATH:-}}"')
    print(f"  uvx harbor run --environment-import-path 'envs.brev_env:BrevEnvironment' \\")
    print(f"    -p {output_root}/auto-calibration/rtxpro6000bw \\")
    print(f"    --include-task-name 'rtxpro6000bw-step-1' -a claude-code -n 1")


if __name__ == "__main__":
    main()
