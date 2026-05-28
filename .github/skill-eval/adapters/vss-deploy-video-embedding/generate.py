#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-deploy-video-embedding skill.

The vss-deploy-video-embedding skill brings up the VSS Video Embedding
microservice (legacy name RT-Embed, Compose service `rtvi-embed`) as a
standalone Docker Compose deployment. Unlike skills that probe an
existing VSS deployment, this trial *is* the deploy — it runs from a
bare Brev L40S instance and stands the service up itself. No
`/vss-deploy-profile` prerequisite is required; the spec explicitly
forbids invoking it.

## Platform

L40S only — the spec's `resources.platforms` declares L40S with mode
`standalone`. The skill's `rtvi-embed` service is a single GPU
microservice; fanning out across more expensive hardware would just burn
budget without exercising new code paths.

## Spec shape

`skills/vss-deploy-video-embedding/evals/standalone_deploy.json` has a
single `expects` entry (one bring-up query + 9 checks). The adapter
therefore emits a flat `base/<platform_short>/` task directory rather
than a step-chain.

## Directory layout

    .github/skill-eval/datasets/vss-deploy-video-embedding/base/l40s/
        task.toml
        instruction.md
        tests/test.sh
        tests/generic_judge.py
        tests/standalone_deploy.json
        solution/solve.sh
        skills/vss-deploy-video-embedding/    (full skill copy)
        environment/Dockerfile                 (FROM scratch; BrevEnvironment takes over)

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-deploy-video-embedding/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-deploy-video-embedding \\
        --skill-dir skills/vss-deploy-video-embedding
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — single-platform skill; only L40S is declared by the spec.
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "L40S": {"short_name": "l40s", "gpu_type": "L40S", "min_vram_per_gpu": 48, "brev_search": "L40S"},
}

DEFAULT_PLATFORM = "L40S"

# Prepended to every instruction.md so the skill's HITL bypass clause
# fires. Skills default to "ask the user" before deploying; in CI there
# is no user, so without this preamble the agent either stalls or falls
# through to a default that doesn't match the trial's setup.
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
        f"# vss-deploy-video-embedding verifier (step {step}): delegates to the\n"
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
    """Gold solution: bring up rtvi-embed standalone from the PR head.

    - Sync the repo to PR_HEAD_SHA so the deploy validates the PR's
      actual compose file, not a stale tarball.
    - Stage a writable VSS_DATA_DIR with the unconditional
      `data_log/vst/clip_storage` subdirectory pre-created, otherwise the
      bind mount resolves to `/data_log/vst/clip_storage` from the root.
    - Set RTVI_EMBED_PORT=8017 and activate the
      `bp_developer_search_2d` Compose profile.
    - Disable Kafka and Redis error messages (no broker / Redis peer is
      started alongside the standalone microservice).
    - Wait up to 25 minutes for `/v1/ready` — first boot pulls the
      Cosmos-Embed1 weights and builds the Triton model repo
      (`start_period: 1200s` in the Compose healthcheck).
    """
    return "\n".join([
        "#!/bin/bash",
        f"# Gold solution: vss-deploy-video-embedding standalone on {platform}",
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
        "# PR_HEAD_SHA + PR_REPO are forwarded from the workflow step by",
        "# brev_env.py. On a warm-pool box, $REPO usually already exists",
        "# from a prior trial — fetch + reset to the PR SHA instead of",
        "# re-cloning so the deploy step always validates the PR's actual",
        "# code, never a stale checkout from a previous trial.",
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
        '    git -c advice.detachedHead=false checkout --force FETCH_HEAD',
        "    git reset --hard FETCH_HEAD",
        "fi",
        "cd - > /dev/null",
        "",
        "# --- Stage standalone VSS_DATA_DIR ---",
        "# The rtvi-embed compose file unconditionally bind-mounts",
        "# ${VSS_DATA_DIR}/data_log/vst/clip_storage. If VSS_DATA_DIR is",
        "# empty, the mount resolves to /data_log/vst/clip_storage from",
        "# the filesystem root. Stage a temp dir with the subpath created.",
        'VSS_DATA_DIR="$(mktemp -d -t rtvi-embed-data-XXXXXX)"',
        'mkdir -p "$VSS_DATA_DIR/data_log/vst/clip_storage"',
        'export VSS_DATA_DIR',
        "",
        "# --- Standalone env ---",
        "export RTVI_EMBED_PORT=8017",
        "export RTVI_EMBED_KAFKA_ENABLED=false",
        "export ENABLE_REDIS_ERROR_MESSAGES=false",
        'export NGC_API_KEY="${NGC_API_KEY:-${NGC_CLI_API_KEY:-}}"',
        '# HF_TOKEN is optional but lifts the Hugging Face 429 ceiling on',
        '# cold rtvi-hf-cache pulls — forward if the harness provided it.',
        'export HF_TOKEN="${HF_TOKEN:-}"',
        "",
        "# --- Bring up rtvi-embed standalone ---",
        'COMPOSE_FILE="$REPO/deploy/docker/services/rtvi/rtvi-embed/rtvi-embed-docker-compose.yml"',
        'cd "$(dirname "$COMPOSE_FILE")"',
        'docker compose -f "$COMPOSE_FILE" --profile bp_developer_search_2d up -d rtvi-embed',
        "",
        "# --- Wait for /v1/ready (up to 25 minutes; start_period is 1200s) ---",
        "for i in $(seq 1 150); do",
        "    curl -sf --max-time 5 http://localhost:8017/v1/ready >/dev/null 2>&1 && break",
        "    sleep 10",
        "done",
        "",
        "curl -sf --max-time 15 http://localhost:8017/v1/ready >/dev/null",
        "",
    ]) + "\n"


GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


def generate_task(platform: str, spec: dict, output_root: Path,
                  skill_dir: Path) -> None:
    """Emit one Harbor task directory.

    The spec has a single `expects` entry, so this collapses to a flat
    `base/<platform_short>/` (no step-<k>/ subdirs)."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "standalone_deploy.json")).name or "standalone_deploy.json"

    if len(expects) != 1:
        print(
            f"ERROR: vss-deploy-video-embedding adapter expects exactly one "
            f"`expects` entry in {spec_name}; got {len(expects)}. "
            "Switch to a step-chain layout if multi-step.",
            file=sys.stderr,
        )
        sys.exit(1)

    expect = expects[0]
    step_dir = output_root / "base" / platform_short
    step_dir.mkdir(parents=True, exist_ok=True)

    # instruction.md — single-step query + environment notes. Do NOT
    # leak the verifier's `checks[]` into the instruction; the verifier
    # evaluates those independently from the spec shipped in tests/.
    lines = [
        PREAMBLE,
        "",
        f"Use the `/vss-deploy-video-embedding` skill on this bare `{platform}` host "
        "to bring up the RT-Embed microservice standalone via Docker Compose. "
        "Do not run `/vss-deploy-profile` or `scripts/dev-profile.sh`.",
        "",
        "## Query",
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

    # task.toml — no profile / no prerequisite_deploy_mode: this trial
    # IS the deploy, and the spec has no profile field.
    meta_lines = [
        "[task]",
        f'name = "nvidia-vss/vss-deploy-video-embedding-standalone-{platform_short}"',
        f'description = "Bring up RT-Embed (rtvi-embed) standalone via Docker Compose on {platform}"',
        f'keywords = ["vss-deploy-video-embedding", "rtvi-embed", "standalone", "{platform}"]',
        "",
        "[environment]",
        "# Harbor copies this into $CLAUDE_CONFIG_DIR/skills so the agent",
        "# can invoke /vss-deploy-video-embedding via the skill.",
        'skills_dir = "/skills"',
        "",
        "[verifier.env]",
        'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
        'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
        # ANTHROPIC_MODEL gives the verifier's judge model cascade
        # (JUDGE_MODEL → ANTHROPIC_MODEL → literal) a working fallback
        # when JUDGE_MODEL is unset.
        'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
        "",
        "[metadata]",
        'skill = "vss-deploy-video-embedding"',
        # No profile / no requires_deployed_vss / no prerequisite_deploy_mode.
        # The trial IS the deploy; it brings up rtvi-embed standalone
        # from a bare instance, not against an existing VSS profile.
        f'platform = "{platform}"',
        f'gpu_type = "{pspec["gpu_type"]}"',
        f'brev_search = "{pspec["brev_search"]}"',
        f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
        f"check_count = {len(expect.get('checks') or [])}",
        "",
    ]
    (step_dir / "task.toml").write_text("\n".join(meta_lines))

    # environment/ placeholder (not used with BrevEnvironment)
    env_dir = step_dir / "environment"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "Dockerfile").write_text("FROM scratch\n")

    # tests/ — wrapper + generic judge + spec
    tests_dir = step_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test.sh").write_text(generate_test_script(1, spec_name))
    if GENERIC_JUDGE.exists():
        shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
    spec_src = skill_dir / "evals" / spec_name
    if spec_src.exists():
        shutil.copy(spec_src, tests_dir / spec_name)
    else:
        (tests_dir / spec_name).write_text(json.dumps(spec, indent=2))

    # solution/
    solution_dir = step_dir / "solution"
    solution_dir.mkdir(exist_ok=True)
    (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

    # skills/vss-deploy-video-embedding/ — full copy so the agent has the
    # whole reference set available at runtime.
    if skill_dir and skill_dir.exists():
        skill_dest = step_dir / "skills" / "vss-deploy-video-embedding"
        if skill_dest.exists():
            shutil.rmtree(skill_dest)
        shutil.copytree(skill_dir, skill_dest)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", required=True,
                        help="Dataset output root "
                             "(e.g. .github/skill-eval/datasets/vss-deploy-video-embedding)")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/vss-deploy-video-embedding")
    parser.add_argument("--spec", default=None,
                        help="Path to standalone_deploy.json "
                             "(default: <skill-dir>/evals/standalone_deploy.json)")
    parser.add_argument("--platform", default=None,
                        choices=list(PLATFORMS.keys()),
                        help=f"Generate for this platform only "
                             f"(default: {DEFAULT_PLATFORM})")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    spec_path = Path(args.spec) if args.spec else (skill_dir / "evals" / "standalone_deploy.json")

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)

    platforms = [args.platform] if args.platform else [DEFAULT_PLATFORM]

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()
    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  vss-deploy-video-embedding/base/{task_id}")
        generate_task(platform, spec, output_root, skill_dir)
    print()
    print(f"Generated {len(platforms)} task(s) under {output_root}/base/")


if __name__ == "__main__":
    main()
