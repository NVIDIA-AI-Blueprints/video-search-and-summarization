#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for VSS vss-deploy-test-openshell skill evaluation.

One task per profile, for **this host's** GPU. These legs are placed on
the OpenShell fleet by label and GPU count alone — no SKU — so the spec
does not decide which card runs the trial and cannot: the adapter reads
the card from `nvidia-smi` and generates for it. A card it does not
recognise has no measured `hw-<profile>.env` sizing, so it exits 2 naming
the card instead of guessing. `--platform` overrides detection for local
runs.

OpenShell guests now reach the same remote NIM endpoints as Brev
through egress. Step-1 instructions therefore tell the agent
`LLM_REMOTE_URL` / `VLM_REMOTE_URL` are configured and to use them the
same way the daily operations specs do. `openshell.gpu_count` is the
only trial-level resource hint.

Matrix:
    Profiles : openshell/{base,warehouse,report-rag} plus one spec per
               Skills Eval Daily operational/VDR job, nested under
               evals/<source-skill>/ (stem matches the daily spec)
    Platform : whichever of H100, L40S, RTXPRO6000BW, H200, A40, A16,
               DGX-SPARK, IGX-THOR this guest has (warehouse, search,
               warehouse, search, and vdr_2 are two-GPU jobs; daily
               ask-video is `base_profile_video_understanding`)

Directory layout:
    .github/skill-eval/datasets/vss-deploy-test-openshell/<profile>/<platform_short>/
        step-<N>/instruction.md, task.toml, tests/, solution/, skills/, environment/
    Single-step specs omit the step-<N>/ directory.

Usage from the repository root:
    # Every profile, sized for this host's GPU
    python3 .github/skill-eval/adapters/vss-deploy-test-openshell/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-deploy-test-openshell \\
        --skill-dir skills/vss-deploy-test-openshell

    # One profile
    python3 .github/skill-eval/adapters/vss-deploy-test-openshell/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-deploy-test-openshell \\
        --skill-dir skills/vss-deploy-test-openshell --profile base

    # Override the detected card (local runs only; CI passes none)
    python3 .github/skill-eval/adapters/vss-deploy-test-openshell/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-deploy-test-openshell \\
        --skill-dir skills/vss-deploy-test-openshell --platform RTXPRO6000BW

Run with Harbor:
    export PYTHONPATH="$(pwd)/.github/skill-eval:${PYTHONPATH:-}"
    uvx harbor run --environment-import-path "envs.brev_env:BrevEnvironment" \\
        -p .github/skill-eval/datasets/vss-deploy-test-openshell/base -a claude-code -n 1

On a CI guest this also writes
``/tmp/skill-eval/current-leg-$RUNNER_NAME.json`` so a host-side fleet
probe can read the spec without inferring it from a SKU job-name token.
Local ``generate.py`` runs (no ``RUNNER_NAME`` + spec identity) skip it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

SKILL_EVAL_ROOT = Path(__file__).resolve().parents[2]
GENERIC_JUDGE = SKILL_EVAL_ROOT / "verifiers" / "generic_judge.py"

# This adapter is usually loaded by path (CI runs it as a script, the unit
# tests load it with spec_from_file_location), so the harness root is not on
# sys.path by import machinery alone.
if str(SKILL_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_EVAL_ROOT))

import live_platform  # noqa: E402  (needs the sys.path entry above)

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

# nvidia-smi name tokens → PLATFORMS keys, and the rule that an
# unrecognised card blocks rather than deploying sizing measured for some
# other card, live in the harness module: a count-only leg for any skill
# resolves its sizing the same way this adapter does, so there is one token
# table rather than one per caller.
_LIVE_GPU_TOKENS = live_platform.LIVE_GPU_TOKENS
live_gpu_names = live_platform.live_gpu_names


def detect_live_platform(names: list[str] | None = None) -> str | None:
    """Platform key for this host's GPU, or None when it isn't recognised."""
    return live_platform.detect_platform(
        names if names is not None else live_gpu_names()
    )


def resolve_sizing_platform(requested: str | None) -> tuple[str | None, str | None]:
    """Sizing platform for this host, or a reason the leg cannot size.

    `requested` is an operator override (`--platform`) and wins, for local
    runs on a box whose card the tokens above do not cover. A count-only leg
    is handed the platform the guest actually reports — resolved once by the
    workflow step, from this same table — so the value that arrives here is
    already the live card, and an unknown card is a blocker upstream rather
    than a fallback here.
    """
    if requested:
        return requested, None
    return live_platform.resolve_from_names(requested, live_gpu_names())


# Host-side identity for OpenShell fleet probes. Job names are now
# ``gpus-N`` (no SKU), and EVAL_* lives only in the agent process env, so
# a watcher that used to guess the spec from the cohort token has nothing
# left. This file is the adapter-only substitute: other skills never
# write it, and generate_task() never writes it.
_GUEST_LEG_MARKER_DIR = Path("/tmp/skill-eval")
_SAFE_RUNNER_RE = re.compile(r"[^A-Za-z0-9._-]+")


def guest_leg_marker_path(
    runner_name: str, dest_dir: Path | None = None
) -> Path:
    """Stable path a host probe can glob without knowing the spec."""
    safe = _SAFE_RUNNER_RE.sub("_", runner_name).strip("._") or "unknown"
    return (dest_dir or _GUEST_LEG_MARKER_DIR) / f"current-leg-{safe}.json"


def write_guest_leg_marker(
    extra: Mapping[str, object] | None = None,
    dest_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Publish this OpenShell leg's identity on the guest, or no-op.

    Requires ``RUNNER_NAME`` and either ``EVAL_SPEC_STEM`` or ``EVAL_SLUG``
    so a local generate, a unit test, or any non-CI caller does not drop
    a marker. Never raises: a probe aid must not fail dataset generation.
    """
    env = os.environ if environ is None else environ
    runner = (env.get("RUNNER_NAME") or "").strip()
    spec_stem = (env.get("EVAL_SPEC_STEM") or "").strip()
    slug = (env.get("EVAL_SLUG") or "").strip()
    if not runner or not (spec_stem or slug):
        return None
    payload: dict[str, object] = {
        "runner_name": runner,
        "skill": (env.get("EVAL_SKILL") or "").strip(),
        "spec_stem": spec_stem,
        "spec_path": (env.get("EVAL_SPEC_PATH") or "").strip(),
        "slug": slug,
        "run_id": (env.get("GITHUB_RUN_ID") or "").strip(),
        "eval_platform": (env.get("EVAL_PLATFORM") or "").strip(),
    }
    if extra:
        payload.update(dict(extra))
    path = guest_leg_marker_path(runner, dest_dir=dest_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: guest leg marker write failed: {exc!r}", file=sys.stderr)
        return None
    print(f"=== Guest leg marker: {path} spec={spec_stem or slug} ===")
    return path


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
    "H200": {
        "short_name": "h200",
        "gpu_type": "H200",
        "min_vram_per_gpu": 141,
        "brev_search": "H200",
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
    "A16": {
        "short_name": "a16",
        "gpu_type": "NVIDIA A16",
        "gpu_count": 1,
        "min_vram_per_gpu": 16,
        "brev_search": "A16",
        "min_root_disk_gb": 220,
    },
    "A40": {
        "short_name": "a40",
        "gpu_type": "NVIDIA A40",
        "gpu_count": 1,
        "min_vram_per_gpu": 46,
        "brev_search": "A40",
        "min_root_disk_gb": 220,
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
#   - profile          → underlying `/vss-deploy-test-openshell -p <profile>` arg (default:
#                        the dict key itself when this field is omitted)
#   - deploy_mode      → value of `/vss-deploy-test-openshell -m <mode>` for this eval variant
#                        (only the alerts profile splits this way today)
#
# `gpu_count` is owned by the spec — it's the trial's total GPU need. The
# spec author knows their profile's always-local GPUs (RT-CV for alerts,
# Cosmos Embed1 for search) and writes the total.

ALWAYS_BUNDLED_SKILLS = (
    "vss-build-vision-ai",
    "vss-deploy-dense-captioning",
    "vss-deploy-detection-tracking-2d",
    "vss-deploy-detection-tracking-3d",
    "vss-deploy-video-embedding",
    "vss-deploy-warehouse-helm",
    "vss-setup-behavior-analytics",
    "vss-setup-video-analytics-api",
)

PROFILES: dict[str, dict] = {
    "base": {
        "description": "VSS base profile — agent, UI, VST, LLM/VLM NIMs",
    },
    "warehouse": {
        "description": "VSS warehouse blueprint — RT-DETR 2D (`bp_wh_2d`) with always-local RTVI VLM, agent, UI, behavior analytics, Kafka",
    },
    "report-rag": {
        "description": "VSS LVS profile plus vss-generate-video-report-rag",
        "profile": "lvs",
        "bundled_skills": (
            "vss-generate-video-report-rag",
            "vss-summarize-video",
        ),
    },
    "search": {
        "description": "VSS search profile — RT-CV, RT-Embed, remote VLM proxy, then vss-search-archive CLI",
        "bundled_skills": ("vss-search-archive", "vss-ask-video", "vss-build-vision-ai"),
    },
    # Skills Eval Daily jobs, same spec stems as the operations /
    # vss-build-vision-ai corpus. `search` is already an OpenShell spec.
    "base_profile_video_understanding": {
        "description": "Daily ask-video job — base deploy then vss-ask-video",
        "profile": "base",
        "bundled_skills": ("vss-ask-video", "vss-manage-video-io-storage"),
    },
    "vdr_1_quickstart_vision_agent": {
        "description": "Daily VDR-1 quickstart via vss-build-vision-ai",
        "profile": "base",
        "bundled_skills": ("vss-build-vision-ai",),
    },
    "vdr_2_add_alerting_summarization": {
        "description": "Daily VDR-2 alerting + summarization (two GPU)",
        "profile": "warehouse",
        "bundled_skills": ("vss-build-vision-ai",),
    },
    "base_profile_report": {
        "description": "Daily report job — base deploy then vss-generate-video-report",
        "profile": "base",
        "bundled_skills": (
            "vss-generate-video-report",
            "vss-manage-video-io-storage",
            "vss-query-analytics",
        ),
    },
    "alerts_vlm_real_time": {
        "description": "Daily real-time VLM alerts job",
        "profile": "warehouse",
        "bundled_skills": ("vss-manage-alerts", "vss-query-analytics", "vss-build-vision-ai"),
    },
    "always_on_operate": {
        "description": "Daily always-on alerts operate-not-author job",
        "profile": "warehouse",
        "bundled_skills": ("vss-manage-alerts", "vss-build-vision-ai"),
    },
    "cv_mode_gate": {
        "description": "Daily CV-mode alerts gate job",
        "profile": "warehouse",
        "bundled_skills": (
            "vss-manage-alerts",
            "vss-build-vision-ai",
            "vss-manage-video-io-storage",
        ),
    },
    "ondemand_verification": {
        "description": "Daily on-demand alert verification job",
        "profile": "warehouse",
        "bundled_skills": ("vss-manage-alerts", "vss-build-vision-ai"),
    },
    "routing_e_gate_negative": {
        "description": "Daily alerts routing-E negative gate job",
        "profile": "warehouse",
        "bundled_skills": (
            "vss-manage-alerts",
            "vss-build-vision-ai",
            "vss-manage-video-io-storage",
        ),
    },
    "routing_vlm_c_vs_d": {
        "description": "Daily alerts routing C vs D job",
        "profile": "warehouse",
        "bundled_skills": ("vss-manage-alerts", "vss-build-vision-ai"),
    },
    "slack_notify_ops": {
        "description": "Daily Slack alert-notification job",
        "profile": "warehouse",
        "bundled_skills": ("vss-manage-alerts", "vss-build-vision-ai"),
    },
    "subscriptions_create_phrasings": {
        "description": "Daily alert-subscription phrasing job",
        "profile": "warehouse",
        "bundled_skills": (
            "vss-manage-alerts",
            "vss-build-vision-ai",
            "vss-manage-video-io-storage",
        ),
    },
    "subscriptions_edge_cases": {
        "description": "Daily alert-subscription edge-case job",
        "profile": "warehouse",
        "bundled_skills": (
            "vss-manage-alerts",
            "vss-build-vision-ai",
            "vss-manage-video-io-storage",
        ),
    },
    "subscriptions_lifecycle": {
        "description": "Daily alert-subscription lifecycle job",
        "profile": "warehouse",
        "bundled_skills": (
            "vss-manage-alerts",
            "vss-build-vision-ai",
            "vss-manage-video-io-storage",
        ),
    },
    "verification_flow": {
        "description": "Daily alert verification-flow job",
        "profile": "warehouse",
        "bundled_skills": ("vss-manage-alerts", "vss-build-vision-ai"),
    },
    "nvstreamer_ops": {
        "description": "Daily NvStreamer / VIOS job",
        "profile": "base",
        "bundled_skills": ("vss-manage-video-io-storage",),
    },
    "vios_ops": {
        "description": "Daily VIOS operations job",
        "profile": "base",
        "bundled_skills": ("vss-manage-video-io-storage",),
    },
    "query_analytics": {
        "description": "Daily query-analytics job",
        "profile": "warehouse",
        "bundled_skills": ("vss-query-analytics", "vss-build-vision-ai"),
    },
    "lvs_api_ops": {
        "description": "Daily LVS API operations job",
        "profile": "lvs",
        "bundled_skills": ("vss-summarize-video", "vss-build-vision-ai"),
    },
    "lvs_profile_summarize": {
        "description": "Daily LVS summarization job",
        "profile": "lvs",
        "bundled_skills": ("vss-summarize-video", "vss-build-vision-ai"),
    },
}


def _find_bundled_skill(
    skills_root: Path, name: str, carrier_dir: Path | None = None
) -> Path | None:
    """Locate a bundled snapshot, falling back to the canonical skill tree."""
    if carrier_dir is not None:
        snapshot = carrier_dir / name
        if snapshot.is_dir() and (snapshot / "SKILL.md").is_file():
            return snapshot
    direct = skills_root / name
    if direct.is_dir() and (direct / "SKILL.md").is_file():
        return direct
    for category in ("operations", "deployment", "tools"):
        nested = skills_root / category / name
        if nested.is_dir() and (nested / "SKILL.md").is_file():
            return nested
    return None


def _iter_operations_skills(skills_root: Path) -> list[Path]:
    """Every SKILL.md directory under skills/operations/."""
    operations = skills_root / "operations"
    if not operations.is_dir():
        return []
    found: list[Path] = []
    for child in sorted(operations.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            found.append(child)
    return found


def _copy_skill_dir(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def _copy_carrier_skill_dir(src: Path, dest: Path) -> None:
    """Copy the carrier without embedding its bundled skill snapshots.

    Snapshots live under the carrier in the repository for a self-contained
    OpenShell bundle. Harbor needs each one as a top-level `/skills/<name>`
    directory, so they are copied separately below.
    """
    if dest.exists():
        shutil.rmtree(dest)
    source_root = src.resolve()

    def _ignore(path: str, names: list[str]) -> set[str]:
        if Path(path).resolve() != source_root:
            return set()
        return {
            name
            for name in names
            if (source_root / name / "SKILL.md").is_file()
        }

    shutil.copytree(src, dest, ignore=_ignore)


def deploy_profile(eval_profile: str) -> str:
    """Resolve the eval-profile key to its actual `/vss-deploy-test-openshell -p <profile>`
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
# `envs/brev_env.py::_check_live_resources` on the resolved pool box:
# - The disk default would reject otherwise-eligible smaller-root
#   pool members for trials that end up running fully remote and would
#   actually fit on <220GB. Acceptable today because every `vss-eval-*`
#   pool member has ≥220GB.
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
    "do not pause to ask for confirmation on `/vss-build-vision-ai` or any other "
    "setup action the trial requires. This pre-authorization covers deployment "
    "and setup ONLY. It does not extend to a destructive call — no interactive "
    "user exists to consent to one — so where the skill gates an action behind a "
    "user confirmation, or requires a real operator-supplied credential, follow "
    "the skill up to that gate and stop: ask, and do not perform the gated action "
    "(no user will answer here)."
)


def generate_instruction(
    profile: str,
    platform: str,
    spec_query: str | None = None,
    *,
    source_skill: str = "vss-deploy-test-openshell",
    step_index: int = 1,
    step_count: int = 1,
    environment_notes: str = "",
) -> str:
    """Build one step's instruction using the spec's owning skill.

    Grouped copies use their source skill (for example
    `/vss-build-vision-ai`); native `evals/openshell/` specs use
    `/vss-deploy-test-openshell`. When spec_query is None the generic deploy
    template remains as a fallback for a missing spec.

    Step 1 lands on a bare host, so it names the deploy skill alongside the
    source skill and states the env that deploy reads. Later steps say the
    stack is already up so they do not redeploy — the same split the daily
    adapters make.
    """
    if spec_query is not None:
        if step_index == 1:
            leading = [
                f"Use the `/{source_skill}` skill "
                f"(and `/vss-build-vision-ai` as needed) on this bare "
                f"`{platform}` host.",
                "Docker + NVIDIA Container Toolkit are available and "
                "`NGC_CLI_API_KEY` is set. `LLM_REMOTE_URL` / "
                "`VLM_REMOTE_URL` are configured via OpenShell egress — "
                "use those remote NIM endpoints (`LLM_MODE=remote` / "
                "`VLM_MODE=remote`) the same way Brev daily evals do.",
            ]
        else:
            leading = [
                f"Use the `/{source_skill}` skill on this `{platform}` host.",
                "The profile this spec needs was already deployed by step 1 — "
                "reuse it and do not redeploy.",
            ]
        lines = [
            PREAMBLE,
            "",
            *leading,
            "",
            f"## Query {step_index} of {step_count}",
            "",
            spec_query,
        ]
        if environment_notes:
            lines.extend(["", "## Environment notes", "", environment_notes])
        lines.extend(["", "Run autonomously without prompting for confirmation.", ""])
        return "\n".join(lines)

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
        "Use the `/vss-deploy-test-openshell` skill.",
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

def generate_test_script(spec_name: str, profile: str, step: int = 1) -> str:
    """Wrapper test.sh that invokes the generic LLM-as-judge verifier
    against the rendered eval spec shipped alongside it. Harbor reads
    /logs/verifier/reward.txt.

    `step` is the 1-based index into `expects[]`, matching Skills Eval
    Daily adapters so a copied daily spec is judged the same way.
    """
    del profile  # retained in signature for caller compatibility
    return (
        "#!/bin/bash\n"
        "# vss-deploy-test-openshell verifier: delegates to the generic LLM-as-judge\n"
        "# (.github/skill-eval/verifiers/generic_judge.py). Shell-wrapped\n"
        "# checks (curl/docker/grep) never call the LLM — only\n"
        "# trajectory/response-style checks pay the LLM cost.\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
        "\n"
        "exit 0\n"
    )


# ---------------------------------------------------------------------------
# Solution script generation
# ---------------------------------------------------------------------------

def generate_solve_script(profile: str, platform: str) -> str:
    """Gold solution: sync repo to PR head, configure profile .env, deploy.

    No `LLM_MODE`/`VLM_MODE`/`LLM_BASE_URL`/`VLM_BASE_URL` overrides —
    the agent's `/vss-deploy-test-openshell` skill reads the forwarded env vars
    (`LLM_REMOTE_URL`, `VLM_REMOTE_URL`, `NGC_CLI_API_KEY`) and picks
    placement itself.

    warehouse uses a different .env path:
        `<repo>/deploy/docker/industry-profiles/warehouse-operations/.env`
    All other core profiles use:
        `<repo>/deployments/developer-workflow/dev-profile-<profile>/.env`
    """
    env_profile = deploy_profile(profile)
    deploy_flag_m = PROFILES[profile].get("deploy_mode")

    is_warehouse = (env_profile == "warehouse")

    # `platform` is this host's own card, resolved by main(); it selects
    # `nim/<slug>/hw-<profile>(-shared).env`, whose values are measured
    # per SKU. The planner sends no platform for these legs precisely so
    # this cannot be inherited from a scheduling guess.
    nim_profile = platform

    if is_warehouse:
        env_file_line = 'ENV_FILE=$REPO/deploy/docker/industry-profiles/warehouse-operations/.env'
        overrides: dict[str, str] = {
            "HARDWARE_PROFILE": nim_profile,
            "VSS_APPS_DIR": "$REPO/deploy/docker",
            "VSS_DATA_DIR": "$REPO/data",
            "HOST_IP": "$(hostname -I | awk '{print $1}')",
        }
    else:
        env_file_line = 'ENV_FILE=$REPO/deployments/developer-workflow/dev-profile-$PROFILE/.env'
        overrides = {
            "HARDWARE_PROFILE": nim_profile,
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
        '# If $REPO exists but has no .git/, it\'s a stale tarball-style',
        '# checkout from before the repo was a git clone — nuke it.',
        '# `git clone` refuses non-empty target dirs, so without this nuke',
        "# the guard silently falls through to a non-git $REPO and every",
        "# subsequent git command fails with 'fatal: not a git repository'.",
        'if [ ! -d "$REPO/.git" ]; then',
        '    rm -rf "$REPO"',
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
        env_file_line,
        "",
        sed_lines,
        "",
        'if [ -n "${NGC_CLI_API_KEY:-}" ]; then',
        '    sed -i "s|^NGC_CLI_API_KEY=.*|NGC_CLI_API_KEY=$NGC_CLI_API_KEY|" "$ENV_FILE"',
        "fi",
        "",
        f"# --- Deploy ({deploy_args}) ---",
        "cd $REPO/deploy/docker" if is_warehouse else "cd $REPO/deployments",
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
    """Write a Harbor task chain for `<profile>/<platform_short>`.

    One task is emitted per `expects[]` entry. Multi-step specs use
    `step-N/` directories so run_leg executes them in order on the same
    OpenShell guest, matching the Skills Eval Daily adapters.
    """
    platform_spec = PLATFORMS[platform]
    task_id = platform_spec["short_name"]
    platform_dir = output_root / profile / task_id
    if platform_dir.exists():
        shutil.rmtree(platform_dir)

    expected_services: list[str] = []
    spec_path = _spec_path_for(profile, skill_dir)
    rendered: dict | None = None
    expects: list[dict] = [{"query": None, "checks": []}]
    source_skill = "vss-deploy-test-openshell"
    if spec_path is not None:
        raw_spec = json.loads(spec_path.read_text())
        rendered = _render_eval_spec(raw_spec, profile, platform)
        raw_expects = rendered.get("expects")
        if not isinstance(raw_expects, list) or not raw_expects:
            raise ValueError("spec.expects must be a non-empty list")
        if any(not isinstance(expect, dict) for expect in raw_expects):
            raise TypeError("every spec.expects entry must be an object")
        expects = raw_expects
        declared_services = rendered.get("expected_services") or []
        if not isinstance(declared_services, list) or any(
            not isinstance(name, str) for name in declared_services
        ):
            raise ValueError("expected_services must be a string list")
        expected_services = declared_services
        group = spec_path.parent.name
        if group not in ("eval", "evals", "openshell"):
            source_skill = group

    spec_name = spec_path.name if spec_path is not None else f"{profile}.json"
    step_count = len(expects)
    for idx, expect in enumerate(expects, 1):
        task_dir = platform_dir
        if step_count > 1:
            task_dir = platform_dir / f"step-{idx}"
        task_dir.mkdir(parents=True, exist_ok=True)

        query = expect.get("query")
        if query is not None and not isinstance(query, str):
            raise TypeError(f"spec.expects[{idx}].query must be a string")
        (task_dir / "instruction.md").write_text(
            generate_instruction(
                profile,
                platform,
                spec_query=query,
                source_skill=source_skill,
                step_index=idx,
                step_count=step_count,
                environment_notes=str((rendered or {}).get("env") or ""),
            )
        )

        step_suffix = f"-step-{idx}" if step_count > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-deploy-test-openshell-{profile}-{task_id}{step_suffix}"',
            f'description = "{profile_def["description"]} ({idx}/{step_count}) on {platform}"',
            f'keywords = ["vss-deploy-test-openshell", "{source_skill}", "{profile}", "{platform}"]',
            "",
            "[agent]",
            "timeout_sec = 600.0",
            "",
            "[environment]",
            'skills_dir = "/skills"',
            "",
            "[metadata]",
            'skill = "vss-deploy-test-openshell"',
            f'platform = "{platform}"',
            f"expected_services = {json.dumps(expected_services)}",
        ]
        deploy_flag_m = profile_def.get("deploy_mode")
        if deploy_flag_m:
            meta_lines.append(f'deploy_mode = "{deploy_flag_m}"')
        meta_lines += [
            f"step_index = {idx}",
            f"step_count = {step_count}",
            f"check_count = {len(expect.get('checks') or [])}",
            f"gpu_count = {gpu_count}",
            f"min_root_disk_gb = {_DEFAULT_MIN_ROOT_DISK_GB}",
            f'min_gpu_driver_version = "{_DEFAULT_MIN_DRIVER_VERSION}"',
            "",
            "[verifier.env]",
            'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
            'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
            'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
            "",
        ]
        (task_dir / "task.toml").write_text("\n".join(meta_lines))

        env_dir = task_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        tests_dir = task_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        if rendered is not None:
            (tests_dir / spec_name).write_text(json.dumps(rendered, indent=2))
            (tests_dir / "test.sh").write_text(
                generate_test_script(spec_name, profile, step=idx)
            )
            if GENERIC_JUDGE.exists():
                shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        else:
            (tests_dir / "test.sh").write_text(
                "#!/bin/bash\n"
                f"echo 'FAIL: no eval spec named {profile}.json under skills/vss-deploy-test-openshell/evals/' >&2\n"
                "mkdir -p /logs/verifier\n"
                "echo 0 > /logs/verifier/reward.txt\n"
                "exit 0\n"
            )

        solution_dir = task_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(
            generate_solve_script(profile, platform)
        )

        if skill_dir and skill_dir.exists():
            skills_root = skill_dir.parent
            _copy_carrier_skill_dir(
                skill_dir, task_dir / "skills" / "vss-deploy-test-openshell"
            )
            copied: set[str] = {"vss-deploy-test-openshell"}
            for extra in _iter_operations_skills(skills_root):
                src = _find_bundled_skill(
                    skills_root, extra.name, carrier_dir=skill_dir
                )
                if src is None:
                    continue
                _copy_skill_dir(src, task_dir / "skills" / extra.name)
                copied.add(extra.name)
            requested = (
                *ALWAYS_BUNDLED_SKILLS,
                *(profile_def.get("bundled_skills") or ()),
            )
            for extra in requested:
                if extra in copied:
                    continue
                src = _find_bundled_skill(
                    skills_root, extra, carrier_dir=skill_dir
                )
                if src is None:
                    print(
                        f"WARN: bundled skill {extra!r} not found under {skills_root}",
                        file=sys.stderr,
                    )
                    continue
                _copy_skill_dir(src, task_dir / "skills" / extra)
                copied.add(extra)


# ---------------------------------------------------------------------------
# Spec → matrix
# ---------------------------------------------------------------------------

def _spec_path_for(profile: str, skill_dir: Path | None) -> Path | None:
    """`evals/<profile>.json` or `evals/<group>/<profile>.json`.

    The OpenShell pack nests daily jobs under the source skill folder.
    """
    if skill_dir is None:
        return None
    matches: list[Path] = []
    for sub in ("evals", "eval"):
        root = skill_dir / sub
        if not root.is_dir():
            continue
        for candidate in root.rglob(f"{profile}.json"):
            if len(candidate.relative_to(root).parts) > 2:
                continue
            matches.append(candidate)
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(
            f"duplicate spec stem {profile!r}: "
            + ", ".join(m.as_posix() for m in matches)
        )
    return matches[0]


def _spec_platforms_for(profile: str, skill_dir: Path | None) -> dict[str, int] | None:
    """Read `evals/<profile>.json` (legacy `eval/<profile>.json` accepted)
    and return `{platform: gpu_count}`.
    Return None if the spec doesn't declare `resources.platforms` (the
    spec is required to ship a `gpu_count` per platform — there is no
    adapter-side fallback matrix any more).

    Legacy specs that still carry a `modes` array are accepted: the
    array is ignored and a one-shot trial runs per declared platform.
    A warning is printed so authors notice the dead field."""
    spec_path = _spec_path_for(profile, skill_dir)
    if spec_path is None:
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


def _spec_gpu_count(
    profile: str, skill_dir: Path | None
) -> tuple[int | None, str | None]:
    """The trial's GPU demand, which is all an OpenShell spec decides.

    `openshell.gpu_count` is the contract the planner places on. The
    per-platform counts are only a fallback for a spec that predates it,
    and they must agree — a spec asking for 1 GPU on one card and 2 on
    another has no single demand to place, and says so instead of
    picking one.
    """
    spec_path = _spec_path_for(profile, skill_dir)
    if spec_path is None:
        return None, (
            "no spec named "
            f"{profile}.json under skills/vss-deploy-test-openshell/evals/"
        )
    try:
        spec = json.loads(spec_path.read_text())
    except Exception as exc:  # noqa: BLE001
        return None, f"failed to parse {spec_path.name}: {exc}"
    declared = ((spec.get("openshell") or {}) if isinstance(spec, dict) else {}).get(
        "gpu_count"
    )
    if isinstance(declared, int) and not isinstance(declared, bool) and declared > 0:
        return declared, None
    counts = set((_spec_platforms_for(profile, skill_dir) or {}).values())
    if len(counts) == 1:
        return counts.pop(), None
    if not counts:
        return None, f"{spec_path.name} declares no openshell.gpu_count"
    return None, (
        f"{spec_path.name} has conflicting GPU demand "
        + "/".join(str(c) for c in sorted(counts))
        + "; declare one openshell.gpu_count"
    )


def expand_matrix(
    profile_filter: str | None,
    platform: str,
    skill_dir: Path | None = None,
) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, str]]]:
    """Return (included, skipped) where:
        included = list of (profile, platform, gpu_count) tuples
        skipped  = list of (profile, platform, reason) tuples

    One task per profile, for the single `platform` this host sizes for.
    The spec's `resources.platforms` keys do not gate generation: an
    OpenShell leg runs on whatever guest claimed its labels, and refusing
    to generate for that guest's card would only turn a supported
    deployment into a phantom failure.
    """
    included: list[tuple[str, str, int]] = []
    skipped: list[tuple[str, str, str]] = []
    for profile in PROFILES:
        if profile_filter and profile != profile_filter:
            continue
        gpu_count, reason = _spec_gpu_count(profile, skill_dir)
        if gpu_count is None:
            skipped.append((profile, platform, reason or "no GPU demand declared"))
            continue
        included.append((profile, platform, gpu_count))
    return included, skipped


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", required=True, help="Dataset output root")
    parser.add_argument("--skill-dir", default=None, help="Path to skills/vss-deploy-test-openshell")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--profile", default=None, choices=list(PROFILES.keys()))
    selector.add_argument(
        "--spec",
        default=None,
        help="Eval spec path; its filename stem selects the profile",
    )
    # "" is accepted because the planner emits an empty `matrix.platform`
    # for OpenShell legs and the workflow forwards it verbatim; it means
    # "the guest decides", same as omitting the flag.
    parser.add_argument(
        "--platform", default=None, choices=[*PLATFORMS, ""],
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir) if args.skill_dir else None
    profile = args.profile
    if args.spec:
        spec_path = Path(args.spec)
        profile = spec_path.stem
        if profile not in PROFILES:
            parser.error(
                f"spec stem {profile!r} does not name a supported profile: "
                + ", ".join(PROFILES)
            )

    write_guest_leg_marker()
    platform, sizing_error = resolve_sizing_platform(args.platform or None)
    if platform is None:
        write_guest_leg_marker(extra={"sizing_error": sizing_error})
        parser.exit(2, f"ERROR: {sizing_error}\n")
    if not args.platform:
        print(f"=== Sizing profile from live GPU: {platform} ===")
    os.environ["HARDWARE_PROFILE"] = platform
    write_guest_leg_marker(extra={"hardware_profile": platform})

    print("=== Inputs ===")
    print(f"  output_dir       : {output_root}")
    print(f"  skill_dir        : {skill_dir or '(none)'}")
    print(f"  filter profile   : {profile or '(all)'}")
    print(f"  sizing platform  : {platform}")
    print()

    included, skipped = expand_matrix(
        profile, platform, skill_dir=skill_dir,
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
    skill_eval_root = Path(__file__).resolve().parents[2]
    first_profile_root = (output_root / first_profile).resolve()
    print(f'  export PYTHONPATH="{skill_eval_root}:${{PYTHONPATH:-}}"')
    print(f"  uvx harbor run --environment-import-path 'envs.brev_env:BrevEnvironment' \\")
    print(f"    -p {first_profile_root} -a claude-code -n 1")


if __name__ == "__main__":
    main()
