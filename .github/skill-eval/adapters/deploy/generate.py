#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for VSS deploy skill evaluation.

One task per (profile × platform). The adapter does **not** pick LLM/VLM
placement — the `/deploy` skill reads `LLM_REMOTE_URL`/`VLM_REMOTE_URL`
(forwarded by `brev_env.py`) plus what's locally available and decides at
runtime. Specs declare `gpu_count` per platform; that's the only
trial-level resource hint.

The only `mode` concept that survives is the **alerts deploy mode**
(`verification` vs `real-time`), which is a real `/deploy -m <mode>`
argument. It lives on `PROFILES["alerts_cv"]` / `PROFILES["alerts_vlm"]`
as a `deploy_mode` field and is passed through to the instruction —
NOT a per-trial placement dimension.

Matrix:
    Profiles : base, alerts_cv, alerts_vlm, lvs, search
    Platforms: H100, L40S, RTXPRO6000BW, DGX-SPARK, IGX-THOR
               (each spec declares which platforms it runs on)

Directory layout:
    datasets/deploy/<profile>/<platform_short>/
        instruction.md, task.toml, tests/, solution/, skills/, environment/

Usage:
    # Generate every (profile, platform) the specs declare
    python generate.py --output-dir ../../datasets/deploy

    # One profile
    python generate.py --output-dir ../../datasets/deploy --profile base

    # One platform
    python generate.py --output-dir ../../datasets/deploy --platform L40S

Run with Harbor:
    harbor run --env "tools.eval.harbor.envs.brev_env:BrevEnvironment" \\
        -p tools/eval/harbor/datasets/deploy/base -a claude-code -n 1
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fallback only — when the harness can't tell what to sync to (no
# PR_HEAD_SHA forwarded, e.g. a local dev run outside CI), fall back
# to develop. In CI, brev_env.py forwards PR_HEAD_SHA + PR_REPO from
# the workflow step into ~/.eval_env on the instance, and the
# pre-deploy script below resets the working tree to that exact SHA.
# The fallback repo URL is derived from $PR_REPO at script-runtime
# (defaulting to NVIDIA-AI-Blueprints/video-search-and-summarization
# when PR_REPO is unset).
VSS_BRANCH_FALLBACK = "develop"

# ---------------------------------------------------------------------------
# Platform specs
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
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
    "DGX-SPARK": {
        "short_name": "spark",
        "gpu_type": "GB10",
        "min_vram_per_gpu": 96,
        "brev_search": "GB10",
    },
    "IGX-THOR": {
        "short_name": "thor",
        "gpu_type": "Thor",
        "min_vram_per_gpu": 64,
        "brev_search": "Thor",
    },
}

# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------
#
# Eval-profile key (the dict key) is the spec/dataset name. Profile-level
# fields:
#   - description      → human label for task.toml
#   - profile          → underlying `/deploy -p <profile>` arg (default:
#                        the dict key itself when this field is omitted)
#   - deploy_mode      → value of `/deploy -m <mode>` for this eval variant
#                        (only the alerts profile splits this way today)
#
# `gpu_count` is owned by the spec — it's the trial's total GPU need. The
# spec author knows their profile's always-local GPUs (RT-CV for alerts,
# Cosmos Embed1 for search) and writes the total.

PROFILES: dict[str, dict] = {
    "base": {
        "description": "VSS base profile — agent, UI, VST, LLM/VLM NIMs",
    },
    "alerts_cv": {
        "description": "VSS alerts profile, CV mode (`deploy -m verification`)",
        "profile": "alerts",
        "deploy_mode": "verification",
    },
    "alerts_vlm": {
        "description": "VSS alerts profile, VLM mode (`deploy -m real-time`)",
        "profile": "alerts",
        "deploy_mode": "real-time",
    },
    "lvs": {
        "description": "VSS LVS profile — long video summarization",
    },
    "search": {
        "description": "VSS search profile — Cosmos Embed1 semantic search",
    },
}


def deploy_profile(eval_profile: str) -> str:
    """Resolve the eval-profile key to its actual `/deploy -p <profile>`
    argument. Eval keys like `alerts_cv` map to `-p alerts`; plain keys
    like `base` map to themselves."""
    override = PROFILES.get(eval_profile, {}).get("profile")
    return override or eval_profile


# ---------------------------------------------------------------------------
# Resource estimates (worst-case)
# ---------------------------------------------------------------------------
#
# Without a fixed placement at task-generation time, we always reserve disk
# and require a current driver. Reasoning: a deploy that ends up using
# remote LLM + remote VLM has zero local-NIM footprint, but the eval pool
# instances already have headroom for these defaults, and trying to
# negotiate dynamically would just leak the placement decision back into
# the adapter. Cost of over-reserving disk on a stoppable instance is
# negligible compared to a failed pull.

_DEFAULT_MIN_ROOT_DISK_GB = 220        # base stack ~80GB + 2 local NIMs ~70GB each
_DEFAULT_MIN_DRIVER_VERSION = "580.95" # cosmos-reason2-8b:1.6.0 floor
# Caveats — both defaults are enforced unconditionally by
# `envs/brev_env.py::_check_live_resources` / `_find_cheapest_matching_type`:
# - The disk default would reject otherwise-eligible smaller-root
#   providers (e.g. some launchpad boxes) for trials that end up
#   running fully remote and would actually fit on <220GB. Acceptable
#   today because every `vss-eval-*` pool member has ≥220GB.
# - The driver default is skipped when `nvidia-smi` is absent (the
#   resource check warns instead of erroring), so CPU-only boxes still
#   pass; don't tighten that branch to a hard error without revisiting
#   this default.


# ---------------------------------------------------------------------------
# Instruction template
# ---------------------------------------------------------------------------

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/deploy` or any other "
    "setup action the trial requires."
)


def generate_instruction(profile: str, platform: str) -> str:
    """Short, query-style instruction. The `/deploy` skill reads the host
    and env vars and picks the actual LLM/VLM placement.

    Shape: "Deploy the <profile> profile (on <platform>) [in <mode> mode]
    autonomously — do not ask for confirmation before running."
    """
    profile_def = PROFILES[profile]
    underlying = deploy_profile(profile)
    deploy_flag_m = profile_def.get("deploy_mode")

    verb_phrase = f"Deploy the **{underlying}** profile"
    if deploy_flag_m:
        verb_phrase += f" in **{deploy_flag_m}** mode"
    verb_phrase += f" on {platform} autonomously — do not ask for confirmation before running."

    return "\n".join([
        PREAMBLE,
        "",
        verb_phrase,
        "",
        "Use the `/deploy` skill.",
    ]) + "\n"


# ---------------------------------------------------------------------------
# Spec rendering
# ---------------------------------------------------------------------------

def _render_eval_spec(spec: dict, profile: str, platform: str) -> dict:
    """Substitute `{{platform}}`, `{{profile}}`, and `{{repo_root}}` into
    every string field of the spec. Returns a fully-resolved spec ready to
    ship to the task's tests/ dir.

    `{{repo_root}}` is `$HOME/video-search-and-summarization` — a shell-
    expansion that matches whichever default user the Brev provider assigns
    (Crusoe → `ubuntu`, Massed Compute → `shadeform`, etc.).
    """
    substitutions = {
        "profile": profile,
        "platform": platform,
        "repo_root": "$HOME/video-search-and-summarization",
    }
    import re as _re
    pattern = _re.compile(r"\{\{\s*(\w+)\s*\}\}")

    # Back-compat: rewrite the old hardcoded Crusoe path so existing specs
    # survive the CSP change without author-side edits.
    _LEGACY_REPO = "/home/ubuntu/video-search-and-summarization"
    _PORTABLE_REPO = "$HOME/video-search-and-summarization"

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
# Test script generation
# ---------------------------------------------------------------------------

def generate_test_script(spec_name: str, profile: str) -> str:
    """Wrapper test.sh that invokes the generic LLM-as-judge verifier
    against the rendered eval spec shipped alongside it. Harbor reads
    /logs/verifier/reward.txt.

    On a full-pass (reward == 1.0), OVERWRITES the canonical active
    marker `/tmp/skill-eval/active-deploy.txt` with this trial's
    `<underlying_profile>-<deploy_mode>` so dependent trials (vios,
    video-*) reading the marker via
    `BrevEnvironment._ensure_prerequisite_deployed` see what is currently
    RUNNING on the box. See specs/stale-marker.spec.

    The marker format `<profile>-<mode>` is the producer↔consumer contract
    shared with `envs/brev_env.py::_ensure_prerequisite_deployed` and
    every non-deploy adapter's `prerequisite_deploy_mode` field. After
    placement modes were dropped from the deploy adapter, non-alerts
    profiles get the default token `remote-all` (which every downstream
    adapter still writes as its `prerequisite_deploy_mode`), so the
    contract stays intact without forcing downstream changes. Changing
    the token here means updating every downstream adapter + the
    consumer at the same time."""
    underlying_profile = deploy_profile(profile)
    # Alerts splits on /deploy -m verification|real-time; every other
    # profile defaults to "remote-all" to match what downstream adapters
    # pass via `prerequisite_deploy_mode` in their task.toml.
    deploy_mode_token = PROFILES[profile].get("deploy_mode") or "remote-all"
    marker_token = f"{underlying_profile}-{deploy_mode_token}"
    return (
        "#!/bin/bash\n"
        "# deploy verifier: delegates to the generic LLM-as-judge\n"
        "# (tools/eval/harbor/verifiers/generic_judge.py). Shell-wrapped\n"
        "# checks (curl/docker/grep) never call the LLM — only\n"
        "# trajectory/response-style checks pay the LLM cost.\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step 1\n'
        "\n"
        "# On full pass, overwrite the canonical active-deploy marker so\n"
        "# downstream trials (vios/video-*) reuse the running deployment\n"
        "# instead of re-running /deploy. Overwrite, never append — the\n"
        "# marker is what is currently RUNNING, not a deploy log.\n"
        'reward="$(cat /logs/verifier/reward.txt 2>/dev/null || echo 0)"\n'
        f'if [ "$reward" = "1.0" ] || [ "$reward" = "1" ]; then\n'
        f'  mkdir -p /tmp/skill-eval && '
        f"printf '%s\\n' '{marker_token}' "
        f"> /tmp/skill-eval/active-deploy.txt\n"
        "fi\n"
        "exit 0\n"
    )


# ---------------------------------------------------------------------------
# Solution script generation
# ---------------------------------------------------------------------------

def generate_solve_script(profile: str, platform: str) -> str:
    """Gold solution: sync repo to PR head, configure profile .env, deploy.

    No `LLM_MODE`/`VLM_MODE`/`LLM_BASE_URL`/`VLM_BASE_URL` overrides —
    the agent's `/deploy` skill reads the forwarded env vars
    (`LLM_REMOTE_URL`, `VLM_REMOTE_URL`, `NGC_CLI_API_KEY`) and picks
    placement itself.
    """
    env_profile = deploy_profile(profile)
    deploy_flag_m = PROFILES[profile].get("deploy_mode")

    overrides: dict[str, str] = {
        "HARDWARE_PROFILE": platform,
        "VSS_APPS_DIR": "$REPO/deployments",
        "VSS_DATA_DIR": "$REPO/data",
        "HOST_IP": "$(hostname -I | awk '{print $1}')",
    }
    sed_lines = "\n".join(
        'sed -i "s|^' + k + "=.*|" + k + "=" + v + '|" "$ENV_FILE"'
        for k, v in overrides.items()
    )

    deploy_args = f"-p {env_profile}"
    if deploy_flag_m:
        deploy_args += f" -m {deploy_flag_m}"

    lines = [
        "#!/bin/bash",
        f"# Gold solution: deploy {profile} on {platform}",
        "set -euo pipefail",
        "",
        'REPO="$HOME/video-search-and-summarization"',
        "",
        "# --- Prerequisites ---",
        "if ! command -v docker &>/dev/null; then",
        "    curl -fsSL https://get.docker.com | sh",
        "fi",
        "sudo sysctl -w vm.max_map_count=262144 2>/dev/null || true",
        "sudo sysctl -w net.core.rmem_max=5242880 2>/dev/null || true",
        "sudo sysctl -w net.core.wmem_max=5242880 2>/dev/null || true",
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
        "    git clone --no-checkout --depth=1 --branch " + VSS_BRANCH_FALLBACK + ' "$VSS_REPO_URL" "$REPO"',
        "fi",
        'cd "$REPO"',
        'git remote set-url origin "$VSS_REPO_URL"',
        'if [ -n "$PR_HEAD_SHA" ]; then',
        '    git fetch --depth=1 origin "$PR_HEAD_SHA"',
        '    git -c advice.detachedHead=false checkout --force "$PR_HEAD_SHA"',
        '    git reset --hard "$PR_HEAD_SHA"',
        "else",
        "    git fetch --depth=1 origin " + VSS_BRANCH_FALLBACK,
        "    git -c advice.detachedHead=false checkout --force FETCH_HEAD",
        "    git reset --hard FETCH_HEAD",
        "fi",
        "git clean -fdx -e data/ -e .env",
        "cd - > /dev/null",
        'mkdir -p "$REPO/data"',
        "",
        "# --- Configure .env ---",
        f"PROFILE={env_profile}",
        'ENV_FILE=$REPO/deployments/developer-workflow/dev-profile-$PROFILE/.env',
        "",
        sed_lines,
        "",
        'if [ -n "${NGC_CLI_API_KEY:-}" ]; then',
        '    sed -i "s|^NGC_CLI_API_KEY=.*|NGC_CLI_API_KEY=$NGC_CLI_API_KEY|" "$ENV_FILE"',
        "fi",
        "",
        f"# --- Deploy ({deploy_args}) ---",
        "cd $REPO/deployments",
        "docker compose --env-file $ENV_FILE config 2>/dev/null > resolved.yml",
        "docker compose -f resolved.yml up -d",
        "",
        "# --- Wait for Agent API ---",
        "for i in $(seq 1 90); do",
        "    curl -sf -o /dev/null --max-time 5 http://localhost:8000/docs 2>/dev/null && break",
        "    sleep 10",
        "done",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_task(
    profile: str,
    platform: str,
    profile_def: dict,
    output_root: Path,
    skill_dir: Path | None,
    gpu_count: int,
) -> None:
    """Write one Harbor task directory for `<profile>/<platform_short>`.

    `gpu_count` is the spec-declared per-platform GPU count plus the
    profile's `local_extras` (RT-CV / Cosmos Embed1 always-local GPUs).
    """
    platform_spec = PLATFORMS[platform]
    task_id = platform_spec["short_name"]
    task_dir = output_root / profile / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # -- instruction.md --
    (task_dir / "instruction.md").write_text(
        generate_instruction(profile, platform),
    )

    # -- task.toml --
    meta_lines = [
        "[task]",
        f'name = "nvidia-vss/deploy-{profile}-{task_id}"',
        f'description = "{profile_def["description"]} on {platform}"',
        f'keywords = ["deploy", "{profile}", "{platform}"]',
        "",
        "[environment]",
        '# Harbor copies this into $CLAUDE_CONFIG_DIR/skills so the agent',
        '# can invoke /deploy via the skill.',
        'skills_dir = "/skills"',
        "",
        "[metadata]",
        f'profile = "{profile}"',
        f'platform = "{platform}"',
    ]
    deploy_flag_m = profile_def.get("deploy_mode")
    if deploy_flag_m:
        meta_lines.append(f'deploy_mode = "{deploy_flag_m}"')
    meta_lines += [
        "# GPU requirements — BrevEnvironment checks these against the",
        "# instance's actual GPU capacity before the trial runs.",
        f'gpu_type = "{platform_spec["gpu_type"]}"',
        f'gpu_count = {gpu_count}',
        f'min_vram_gb_per_gpu = {platform_spec["min_vram_per_gpu"]}',
        f'brev_search = "{platform_spec["brev_search"]}"',
        "# Disk + driver requirements — worst-case (covers a deploy with",
        "# both LLM and VLM running as local NIMs). The /deploy skill",
        "# decides actual placement from forwarded env (LLM_REMOTE_URL,",
        "# VLM_REMOTE_URL); we don't try to second-guess it here.",
        f'min_root_disk_gb = {_DEFAULT_MIN_ROOT_DISK_GB}',
        f'min_gpu_driver_version = "{_DEFAULT_MIN_DRIVER_VERSION}"',
        "",
        "[verifier.env]",
        'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
        'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
        # ANTHROPIC_MODEL gives the verifier's judge model cascade
        # (JUDGE_MODEL → ANTHROPIC_MODEL → literal) a working
        # fallback when JUDGE_MODEL is unset.
        'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
        "",
    ]
    (task_dir / "task.toml").write_text("\n".join(meta_lines))

    # -- environment/ placeholder (not used with BrevEnvironment) --
    env_dir = task_dir / "environment"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "Dockerfile").write_text("FROM scratch\n")

    # -- tests/: wrapper + generic judge + rendered eval spec --
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    spec_path = skill_dir / "eval" / f"{profile}.json" if skill_dir else None
    if spec_path and spec_path.exists():
        raw_spec = json.loads(spec_path.read_text())
        rendered = _render_eval_spec(raw_spec, profile, platform)
        spec_name = spec_path.name
        (tests_dir / spec_name).write_text(json.dumps(rendered, indent=2))
        (tests_dir / "test.sh").write_text(generate_test_script(spec_name, profile))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
    else:
        (tests_dir / "test.sh").write_text(
            "#!/bin/bash\n"
            f"echo 'FAIL: no eval spec at skills/deploy/eval/{profile}.json' >&2\n"
            "mkdir -p /logs/verifier\n"
            "echo 0 > /logs/verifier/reward.txt\n"
            "exit 0\n"
        )

    # -- solution/solve.sh --
    solution_dir = task_dir / "solution"
    solution_dir.mkdir(exist_ok=True)
    (solution_dir / "solve.sh").write_text(
        generate_solve_script(profile, platform),
    )

    # -- skills/deploy/ --
    if skill_dir and skill_dir.exists():
        skill_dest = task_dir / "skills" / "deploy"
        if skill_dest.exists():
            shutil.rmtree(skill_dest)
        shutil.copytree(skill_dir, skill_dest)


# ---------------------------------------------------------------------------
# Spec → matrix
# ---------------------------------------------------------------------------

def _spec_platforms_for(profile: str, skill_dir: Path | None) -> dict[str, int] | None:
    """Read `eval/<profile>.json` and return `{platform: gpu_count}`.
    Return None if the spec doesn't declare `resources.platforms` (the
    spec is required to ship a `gpu_count` per platform — there is no
    adapter-side fallback matrix any more).

    Legacy specs that still carry a `modes` array are accepted: the
    array is ignored and a one-shot trial runs per declared platform.
    A warning is printed so authors notice the dead field."""
    if skill_dir is None:
        return None
    spec_path = skill_dir / "eval" / f"{profile}.json"
    if not spec_path.exists():
        return None
    try:
        spec = json.loads(spec_path.read_text())
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: failed to parse {spec_path}: {exc}", file=sys.stderr)
        return None
    resources = (spec.get("resources") or {}).get("platforms")
    if not isinstance(resources, dict) or not resources:
        return None
    out: dict[str, int] = {}
    for p, v in resources.items():
        v = v or {}
        if "modes" in v:
            print(
                f"WARN: {spec_path.name} platform {p!r} declares 'modes' — "
                "the placement-mode matrix was removed; the field is ignored. "
                "Drop it from the spec.",
                file=sys.stderr,
            )
        gpu_count = v.get("gpu_count")
        if gpu_count is None:
            print(
                f"WARN: {spec_path.name} platform {p!r} is missing 'gpu_count' — "
                "defaulting to 1. Declare it explicitly to silence this warning.",
                file=sys.stderr,
            )
            gpu_count = 1
        out[p] = int(gpu_count)
    return out


def expand_matrix(
    profile_filter: str | None,
    platform_filter: str | None,
    skill_dir: Path | None = None,
) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, str]]]:
    """Return (included, skipped) where:
        included = list of (profile, platform, gpu_count) tuples
        skipped  = list of (profile, platform, reason) tuples
    """
    included: list[tuple[str, str, int]] = []
    skipped: list[tuple[str, str, str]] = []
    for profile, profile_def in PROFILES.items():
        if profile_filter and profile != profile_filter:
            continue
        spec_matrix = _spec_platforms_for(profile, skill_dir)
        if spec_matrix is None:
            skipped.append((profile, "-", "no spec at skills/deploy/eval/"
                                          f"{profile}.json with resources.platforms"))
            continue
        for platform, spec_gpu_count in spec_matrix.items():
            if platform_filter and platform != platform_filter:
                continue
            if platform not in PLATFORMS:
                skipped.append((profile, platform, f"unknown platform {platform!r}"))
                continue
            included.append((profile, platform, spec_gpu_count))
    return included, skipped


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="Dataset output root")
    parser.add_argument("--skill-dir", default=None, help="Path to skills/deploy")
    parser.add_argument("--profile", default=None, choices=list(PROFILES.keys()))
    parser.add_argument("--platform", default=None, choices=list(PLATFORMS.keys()))
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir) if args.skill_dir else None

    print("=== Inputs ===")
    print(f"  output_dir       : {output_root}")
    print(f"  skill_dir        : {skill_dir or '(none)'}")
    print(f"  filter profile   : {args.profile or '(all)'}")
    print(f"  filter platform  : {args.platform or '(all)'}")
    print()

    included, skipped = expand_matrix(
        args.profile, args.platform, skill_dir=skill_dir,
    )

    if skipped:
        print(f"=== Skipped ({len(skipped)}) ===")
        for profile, platform, reason in skipped:
            print(f"  SKIP {profile}/{platform}   reason: {reason}")
        print()

    if not included:
        print("No (profile, platform) combinations match filters.", file=sys.stderr)
        sys.exit(1)

    print(f"=== Generating ({len(included)}) ===")
    for profile, platform, gpu_count in included:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  {profile}/{task_id}   gpu_count={gpu_count}")
        generate_task(
            profile, platform,
            PROFILES[profile], output_root, skill_dir,
            gpu_count=gpu_count,
        )

    print()
    print(f"Summary: {len(included)} generated, {len(skipped)} skipped.")
    print()
    print("Coverage:")
    by_profile: dict[str, list[str]] = {}
    for p, plat, _ in included:
        by_profile.setdefault(p, []).append(PLATFORMS[plat]["short_name"])
    for p, tasks in by_profile.items():
        print(f"  {p}: {', '.join(tasks)}")
    print()
    print("Run a profile's tasks with:")
    first_profile = list(by_profile.keys())[0]
    print(f"  harbor run --env 'tools.eval.harbor.envs.brev_env:BrevEnvironment' \\")
    print(f"    -p {output_root}/{first_profile} -a claude-code -n 1")


if __name__ == "__main__":
    main()
