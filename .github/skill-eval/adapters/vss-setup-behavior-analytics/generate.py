#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-setup-behavior-analytics skill.

This adapter handles specs under `skills/vss-setup-behavior-analytics/evals/`.
The current spec (`standalone_deploy.json`) exercises the skill's
**standalone-deploy** flow — pulling and starting `vss-behavior-analytics`
via the service compose file at
`deploy/docker/services/analytics/behavior-analytics/compose.yml`,
without the full VSS stack.

The spec declares `"resources": {"platforms": {"ANY": {"gpu_count": 0}}}`:

- `gpu_count: 0` → skip GPU-type enforcement in BrevEnvironment; any
  RUNNING+READY `vss-eval-*` box is eligible.
- `ANY` → treated as the task-name suffix (e.g.
  `nvidia-vss/vss-setup-behavior-analytics-standalone-deploy-any`).
  A bare Brev instance is sufficient — `behavior-analytics` is CPU-only.

No `/vss-deploy-profile` prerequisite: the spec omits `profile`, so
`BrevEnvironment._ensure_prerequisite_deployed` runs the box-clean path
(desired = "") and the trial starts on a guaranteed-clean box.

Directory layout (one spec → one task per platform key):
    .github/skill-eval/datasets/vss-setup-behavior-analytics/<spec_stem>/<platform_short>/
        instruction.md
        task.toml
        tests/test.sh
        tests/<spec_stem>.json
        tests/generic_judge.py
        solution/solve.sh
        skills/vss-setup-behavior-analytics/
        environment/Dockerfile

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-setup-behavior-analytics/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-setup-behavior-analytics \\
        --skill-dir skills/vss-setup-behavior-analytics

Run with Harbor:
    export PYTHONPATH="$(pwd)/.github/skill-eval:${PYTHONPATH:-}"
    uvx harbor run --environment-import-path "envs.brev_env:BrevEnvironment" \\
        -p .github/skill-eval/datasets/vss-setup-behavior-analytics/standalone_deploy \\
        --include-task-name any \\
        -a claude-code -n 1
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — ANY (gpu_count=0) uses a CPU-only-capable any-box selector
# ---------------------------------------------------------------------------

# For `gpu_count == 0` specs the platform key is an arbitrary label; the
# brev_env.py GPU-type check is skipped entirely. We map "ANY" to a
# short name "any" used in task names and directory paths.
# If the spec ever grows real platform keys (L40S, RTXPRO6000BW, …),
# extend this dict with proper gpu_type/brev_search entries.
PLATFORMS: dict[str, dict] = {
    "ANY": {
        "short_name": "any",
        "gpu_type": "",       # skipped when gpu_count == 0
        "min_vram_per_gpu": 0,
        "brev_search": "",    # skipped when gpu_count == 0
    },
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
}

# ---------------------------------------------------------------------------
# Preamble — copied verbatim from vss-manage-video-io-storage/generate.py.
# Skills' SKILL.md prereq blocks include a bypass clause keyed on this
# exact wording. Omitting the preamble makes the agent stall waiting for
# user confirmation that never arrives in CI.
# ---------------------------------------------------------------------------
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"

# ---------------------------------------------------------------------------
# Fallback branch (only used when PR_HEAD_SHA is unset — local dev runs)
# ---------------------------------------------------------------------------
VSS_BRANCH_FALLBACK = "develop"


# ---------------------------------------------------------------------------
# Instruction generation
# ---------------------------------------------------------------------------

def generate_instruction(spec: dict, spec_stem: str, platform: str) -> str:
    """Build the instruction.md the agent receives.

    Uses the spec's first `expects[].query` as the agent task. The
    `checks[]` list is intentionally NOT shown — the verifier reads
    them independently from tests/<spec_stem>.json. Leaking checks
    into the instruction would let the agent write to the test rather
    than do the actual work.
    """
    expects = spec.get("expects") or []
    query = expects[0].get("query", "Deploy vss-behavior-analytics standalone.") if expects else \
        "Deploy vss-behavior-analytics standalone."
    env_context = spec.get("env", "")

    lines = [
        PREAMBLE,
        "",
        query,
        "",
    ]
    if env_context:
        lines += [
            "## Environment notes",
            "",
            env_context,
            "",
        ]
    lines += [
        "Use the `/vss-setup-behavior-analytics` skill.",
        "Run autonomously without prompting for confirmation.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test script generation
# ---------------------------------------------------------------------------

def generate_test_script(spec_name: str) -> str:
    """Wrapper test.sh that invokes the generic LLM-as-judge verifier
    against the rendered eval spec. Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        "# vss-setup-behavior-analytics verifier: delegates to the generic\n"
        "# LLM-as-judge (.github/skill-eval/verifiers/generic_judge.py).\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step 1\n'
        "exit 0\n"
    )


# ---------------------------------------------------------------------------
# Solution script generation
# ---------------------------------------------------------------------------

def generate_solve_script(spec: dict) -> str:
    """Gold solution: sync repo to PR head, deploy vss-behavior-analytics
    standalone using the service compose file defaults.

    The spec mandates: default entrypoint (`main_analytics_2d_app.py`),
    default config (`warehouse_2d_config.json`), restart policy `always`.
    We use `docker compose run` against the base compose to launch the
    container exactly as the service definition specifies, with no
    entrypoint/config swap.
    """
    return (
        "#!/bin/bash\n"
        "# Gold solution: deploy vss-behavior-analytics standalone\n"
        "set -euo pipefail\n"
        "\n"
        'REPO="$HOME/video-search-and-summarization"\n'
        "\n"
        "# --- Prerequisites ---\n"
        "if ! command -v docker &>/dev/null; then\n"
        "    curl -fsSL https://get.docker.com | sh\n"
        "fi\n"
        "\n"
        "# --- NGC login ---\n"
        'if [ -n "${NGC_CLI_API_KEY:-}" ]; then\n'
        "    docker login nvcr.io -u '\\$oauthtoken' -p \"$NGC_CLI_API_KEY\" 2>/dev/null || true\n"
        "fi\n"
        "\n"
        "# --- Sync repo to PR head ---\n"
        'PR_REPO="${PR_REPO:-NVIDIA-AI-Blueprints/video-search-and-summarization}"\n'
        'PR_HEAD_SHA="${PR_HEAD_SHA:-}"\n'
        'VSS_REPO_URL="https://github.com/${PR_REPO}.git"\n'
        'if [ ! -d "$REPO/.git" ]; then\n'
        '    rm -rf "$REPO"\n'
        f"    git clone --no-checkout --depth=1 --branch {VSS_BRANCH_FALLBACK}"
        ' "$VSS_REPO_URL" "$REPO"\n'
        "fi\n"
        'cd "$REPO"\n'
        'git remote set-url origin "$VSS_REPO_URL"\n'
        'if [ -n "$PR_HEAD_SHA" ]; then\n'
        '    git fetch --depth=1 origin "$PR_HEAD_SHA"\n'
        '    git -c advice.detachedHead=false checkout --force "$PR_HEAD_SHA"\n'
        '    git reset --hard "$PR_HEAD_SHA"\n'
        "else\n"
        f"    git fetch --depth=1 origin {VSS_BRANCH_FALLBACK}\n"
        "    git -c advice.detachedHead=false checkout --force FETCH_HEAD\n"
        "    git reset --hard FETCH_HEAD\n"
        "fi\n"
        "git clean -fdx -e data/ -e .env\n"
        "cd - > /dev/null\n"
        "\n"
        "# --- Deploy vss-behavior-analytics standalone ---\n"
        "# Use the service compose file directly. The base service definition\n"
        "# sets restart: always and the default entrypoint.\n"
        "# The spec checks for container_name vss-behavior-analytics — the\n"
        "# compose file's base service is 'vss-behavior-analytics-base'; the\n"
        "# skill uses 'extends' with a concrete container_name override.\n"
        "# For the gold solution we replicate what the skill does: override\n"
        "# container_name and network_mode to bring the service up with the\n"
        "# right name.\n"
        'COMPOSE_FILE="$REPO/deploy/docker/services/analytics/behavior-analytics/compose.yml"\n'
        "export VSS_APPS_DIR=\"$REPO/deploy/docker\"\n"
        "docker compose -f \"$COMPOSE_FILE\" run -d \\\n"
        "    --name vss-behavior-analytics \\\n"
        "    vss-behavior-analytics-base\n"
        "\n"
        "# --- Verify container is running ---\n"
        "for i in $(seq 1 30); do\n"
        "    docker ps --filter name=^vss-behavior-analytics$ --format '{{.Names}}' \\\n"
        "        | grep -qx vss-behavior-analytics && break\n"
        "    sleep 5\n"
        "done\n"
    )


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_task(
    spec_stem: str,
    platform: str,
    gpu_count: int,
    spec: dict,
    output_root: Path,
    skill_dir: Path,
) -> None:
    """Write one Harbor task directory for <spec_stem>/<platform_short>."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    task_dir = output_root / spec_stem / platform_short
    task_dir.mkdir(parents=True, exist_ok=True)

    spec_name = f"{spec_stem}.json"

    # -- instruction.md --
    (task_dir / "instruction.md").write_text(
        generate_instruction(spec, spec_stem, platform),
    )

    # -- task.toml --
    meta_lines = [
        "[task]",
        f'name = "nvidia-vss/vss-setup-behavior-analytics-{spec_stem}-{platform_short}"',
        f'description = "vss-setup-behavior-analytics {spec_stem} on {platform}"',
        f'keywords = ["vss-setup-behavior-analytics", "{spec_stem}", "{platform}"]',
        "",
        "[environment]",
        "# Harbor copies this into $CLAUDE_CONFIG_DIR/skills so the agent",
        "# can invoke /vss-setup-behavior-analytics via the skill.",
        'skills_dir = "/skills"',
        "",
        "[metadata]",
        'skill = "vss-setup-behavior-analytics"',
        # No `profile` field — this trial runs on a bare Brev instance with
        # no /vss-deploy-profile prerequisite. behavior-analytics is CPU-only
        # and stands up standalone.
        f'platform = "{platform}"',
        f"gpu_count = {gpu_count}",
    ]
    # Only emit gpu_type / brev_search / min_vram when there's a real GPU
    # requirement. For gpu_count==0 (ANY platform), brev_env.py skips the
    # GPU-type check; emitting empty strings would be misleading.
    if gpu_count > 0 and pspec["gpu_type"]:
        meta_lines += [
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
        ]
    meta_lines += [
        # prerequisite_deploy_mode omitted — behavior-analytics doesn't
        # depend on a pre-deployed alerts stack.
        f"step_index = 1",
        f"step_count = 1",
        f"check_count = {len((spec.get('expects') or [{}])[0].get('checks') or [])}",
        "",
        "[verifier.env]",
        'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
        'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
        'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
        "",
    ]
    (task_dir / "task.toml").write_text("\n".join(meta_lines))

    # -- environment/ placeholder --
    env_dir = task_dir / "environment"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "Dockerfile").write_text("FROM scratch\n")

    # -- tests/ --
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    # Render {{platform}} and {{profile}} substitutions in the spec
    rendered_spec = _render_spec(spec, platform)
    (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))
    (tests_dir / "test.sh").write_text(generate_test_script(spec_name))
    if GENERIC_JUDGE.exists():
        shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")

    # -- solution/ --
    solution_dir = task_dir / "solution"
    solution_dir.mkdir(exist_ok=True)
    (solution_dir / "solve.sh").write_text(generate_solve_script(spec))

    # -- skills/ --
    skill_dest = task_dir / "skills" / "vss-setup-behavior-analytics"
    if skill_dest.exists():
        shutil.rmtree(skill_dest)
    shutil.copytree(skill_dir, skill_dest)


def _render_spec(spec: dict, platform: str) -> dict:
    """Substitute {{platform}} and {{profile}} in all string fields."""
    import re as _re
    _LEGACY_REPO = "/home/ubuntu/video-search-and-summarization"
    _PORTABLE_REPO = "$HOME/video-search-and-summarization"
    pattern = _re.compile(r"\{\{\s*(\w+)\s*\}\}")
    substitutions = {
        "platform": platform,
        "profile": spec.get("profile", ""),
        "repo_root": "$HOME/video-search-and-summarization",
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
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Dataset output root (e.g. .github/skill-eval/datasets/vss-setup-behavior-analytics)",
    )
    parser.add_argument(
        "--skill-dir", required=True,
        help="Path to skills/vss-setup-behavior-analytics",
    )
    parser.add_argument(
        "--spec", default=None,
        help="Path to a specific eval spec JSON (default: all specs under <skill-dir>/evals/)",
    )
    parser.add_argument(
        "--platform", default=None,
        choices=list(PLATFORMS.keys()),
        help="Generate for this platform only (default: all platforms declared in the spec)",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)

    if not skill_dir.exists():
        print(f"ERROR: skill-dir not found: {skill_dir}", file=sys.stderr)
        sys.exit(1)

    # Collect spec files to process
    if args.spec:
        spec_paths = [Path(args.spec)]
    else:
        evals_dir = skill_dir / "evals"
        spec_paths = sorted(evals_dir.glob("*.json")) if evals_dir.exists() else []
        if not spec_paths:
            print(f"ERROR: no spec JSON files found under {evals_dir}", file=sys.stderr)
            sys.exit(1)

    print("=== Inputs ===")
    print(f"  output_dir  : {output_root}")
    print(f"  skill_dir   : {skill_dir}")
    print(f"  specs       : {[str(p) for p in spec_paths]}")
    print(f"  platform    : {args.platform or '(from spec)'}")
    print()

    generated = 0
    skipped_list: list[tuple[str, str, str]] = []

    for spec_path in spec_paths:
        try:
            spec = json.loads(spec_path.read_text())
        except Exception as exc:
            print(f"WARN: failed to parse {spec_path}: {exc}", file=sys.stderr)
            continue

        spec_stem = spec_path.stem

        # Validate required fields
        resources = (spec.get("resources") or {}).get("platforms")
        if not resources:
            msg = f"missing resources.platforms in {spec_path.name}"
            print(f"SKIP {spec_stem}: {msg}", file=sys.stderr)
            skipped_list.append((spec_stem, "-", msg))
            continue

        expects = spec.get("expects")
        if not expects:
            msg = f"missing expects in {spec_path.name}"
            print(f"SKIP {spec_stem}: {msg}", file=sys.stderr)
            skipped_list.append((spec_stem, "-", msg))
            continue

        for platform_key, platform_info in resources.items():
            if args.platform and platform_key != args.platform:
                continue
            platform_info = platform_info or {}
            gpu_count = int(platform_info.get("gpu_count", 1))

            if platform_key not in PLATFORMS:
                msg = f"unknown platform key {platform_key!r}"
                print(f"SKIP {spec_stem}/{platform_key}: {msg}", file=sys.stderr)
                skipped_list.append((spec_stem, platform_key, msg))
                continue

            pspec = PLATFORMS[platform_key]
            print(f"  GEN  {spec_stem}/{pspec['short_name']}  gpu_count={gpu_count}")
            generate_task(
                spec_stem=spec_stem,
                platform=platform_key,
                gpu_count=gpu_count,
                spec=spec,
                output_root=output_root,
                skill_dir=skill_dir,
            )
            generated += 1

    print()
    if skipped_list:
        print(f"=== Skipped ({len(skipped_list)}) ===")
        for s, p, r in skipped_list:
            print(f"  SKIP {s}/{p}: {r}")
        print()

    if generated == 0:
        print("ERROR: no tasks generated.", file=sys.stderr)
        sys.exit(1)

    print(f"Summary: {generated} task(s) generated under {output_root}/")
    print()
    print("Run with Harbor:")
    print(f'  export PYTHONPATH=".github/skill-eval:${{PYTHONPATH:-}}"')
    print(f"  uvx harbor run --environment-import-path 'envs.brev_env:BrevEnvironment' \\")
    print(f"    -p {output_root}/standalone_deploy --include-task-name any -a claude-code -n 1")


if __name__ == "__main__":
    main()
