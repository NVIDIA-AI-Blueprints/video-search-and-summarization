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

The adapter does **not** pick LLM/VLM placement — the
`/vss-deploy-test-openshell` skill reads `LLM_REMOTE_URL`/`VLM_REMOTE_URL`
(forwarded by `brev_env.py`) plus what's locally available and decides at
runtime. `openshell.gpu_count` is the only trial-level resource hint.

Matrix:
    Profiles : one Harbor job per compose profile (base, lvs, warehouse,
               search, alerts-cv, alerts-vlm) whose step-1 deploys.
               Daily operations Harbor exams are their own jobs, named
               after the Daily spec stem (ask-video, summarize, vios,
               report, search-archive, query-analytics, manage-alerts).
               Standalone / VDR specs stay their own jobs.
    Platform : whichever of H100, L40S, RTXPRO6000BW, H200, A40, A16,
               DGX-SPARK, IGX-THOR this guest has (warehouse, search, and
               alerts-cv are two-GPU jobs; alerts-vlm and base/lvs are
               one GPU)

Directory layout:
    .github/skill-eval/datasets/vss-deploy-test-openshell/<profile>/<platform_short>/
        instruction.md, task.toml, tests/, solution/, skills/, environment/

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
import subprocess
import sys
from collections.abc import Mapping
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

# nvidia-smi name tokens → PLATFORMS keys.
#
# An OpenShell leg carries no SKU: placement is fleet labels plus a GPU
# count, so the guest that claims the job owns its own hardware. Sizing
# cannot be guessed from the spec, because `hw-<profile>.env` values are
# per-card — `hw-H200-shared.env` sets NIM_KVCACHE_PERCENT=0.5, the value
# measured to leave 1682 MiB free on an RTX PRO 6000. So the sizing
# profile is read off the live card, and an unrecognised card blocks
# rather than deploying sizing that was never measured for it.
_LIVE_GPU_TOKENS: tuple[tuple[str, str], ...] = (
    ("H200", "H200"),
    ("RTX PRO 6000", "RTXPRO6000BW"),
    ("RTX PRO SERVER 6000", "RTXPRO6000BW"),
    ("H100", "H100"),
    ("L40S", "L40S"),
    ("A40", "A40"),
    ("A16", "A16"),
    ("GB10", "DGX-SPARK"),
    ("THOR", "IGX-THOR"),
)


def live_gpu_names() -> list[str]:
    """Names `nvidia-smi` reports for this host, or [] when unreadable."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def detect_live_platform(names: list[str] | None = None) -> str | None:
    """Platform key for this host's GPU, or None when it isn't recognised."""
    blob = " | ".join(names if names is not None else live_gpu_names()).upper()
    if not blob:
        return None
    for token, platform in _LIVE_GPU_TOKENS:
        if token in blob:
            return platform
    return None


def resolve_sizing_platform(requested: str | None) -> tuple[str | None, str | None]:
    """Sizing platform for this host, or a reason the leg cannot size.

    `requested` is an operator override (`--platform`) and wins, for local
    runs on a box whose card the tokens above do not cover. CI passes no
    platform — the planner deliberately emits none — so the live card
    decides, and an unknown card is a blocker, not a fallback.
    """
    if requested:
        return requested, None
    names = live_gpu_names()
    platform = detect_live_platform(names)
    if platform:
        return platform, None
    if not names:
        return None, (
            "cannot read this host's GPU: `nvidia-smi --query-gpu=name` "
            "returned nothing. An OpenShell leg sizes NIM from the live "
            "card, so there is no safe profile to generate for. Pass "
            "--platform explicitly to override."
        )
    return None, (
        "unrecognised GPU on this host: "
        + ", ".join(sorted(set(names)))
        + ". No hw-<profile>.env sizing has been measured for it; add it to "
        "_LIVE_GPU_TOKENS and ship the profile files, or pass --platform "
        "explicitly to override."
    )


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

PROFILES: dict[str, dict] = {
    "base": {
        "description": "Base profile deploy smoke",
        "bundled_skills": (),
    },
    "lvs": {
        "description": "LVS profile deploy smoke",
        "bundled_skills": (),
    },
    "warehouse": {
        "description": "Warehouse agents (`bp_wh` 2d) deploy smoke",
        "bundled_skills": (),
    },
    "search": {
        "description": "Search profile deploy smoke — RT-CV, RT-Embed, remote VLM proxy",
        "bundled_skills": (),
    },
    "ask-video": {
        "description": "VSS base profile plus vss-ask-video CLI (`vss vlm run`)",
        "profile": "base",
        "bundled_skills": ("vss-ask-video", "vss-manage-video-io-storage"),
    },
    "base_profile_video_understanding": {
        "description": "Daily vss-ask-video routing exam on a live base stack",
        "profile": "base",
        "bundled_skills": ("vss-ask-video",),
    },
    "direct_vlm_video_understanding": {
        "description": "Daily vss-ask-video standalone fallback then Path A --file",
        "profile": "base",
        "bundled_skills": ("vss-ask-video", "vss-manage-video-io-storage"),
    },
    "lvs_profile_summarize": {
        "description": "Daily vss-summarize-video warehouse_sample end-to-end",
        "profile": "lvs",
        "bundled_skills": ("vss-summarize-video",),
    },
    "lvs_api_ops": {
        "description": "Daily vss-summarize-video API/status smoke (no summarize)",
        "profile": "lvs",
        "bundled_skills": ("vss-summarize-video",),
    },
    "search_archive": {
        "description": "Daily vss-search-archive ingest, search, delete, and contracts",
        "profile": "search",
        "deploy_mode": "remote-all",
        "bundled_skills": ("vss-search-archive", "vss-ask-video"),
    },
    "vios_ops": {
        "description": "Daily vss-manage-video-io-storage standalone VIOS file-sensor exam",
        "profile": "base",
        "bundled_skills": ("vss-manage-video-io-storage",),
    },
    "nvstreamer_ops": {
        "description": "Daily vss-manage-video-io-storage NvStreamer + VIOS handoff",
        "profile": "base",
        "bundled_skills": ("vss-manage-video-io-storage",),
    },
    "base_profile_report": {
        "description": "Daily vss-generate-video-report Mode A clip + incident report",
        "profile": "base",
        "bundled_skills": ("vss-generate-video-report", "vss-query-analytics"),
    },
    "query_analytics": {
        "description": "Daily vss-query-analytics VA-MCP read path on alerts real-time",
        "profile": "alerts",
        "deploy_mode": "real-time",
        "bundled_skills": ("vss-query-analytics",),
    },
    "alerts_vlm_real_time": {
        "description": "Daily vss-manage-alerts real-time onboard + incident query",
        "profile": "alerts",
        "deploy_mode": "real-time",
        "bundled_skills": ("vss-manage-alerts", "vss-query-analytics"),
    },
    "always_on_operate": {
        "description": "Daily vss-manage-alerts always-on operate-not-author",
        "profile": "alerts",
        "deploy_mode": "real-time",
        "bundled_skills": ("vss-manage-alerts",),
    },
    "slack_notify_ops": {
        "description": "Daily vss-manage-alerts Slack notification routing",
        "profile": "alerts",
        "deploy_mode": "real-time",
        "bundled_skills": ("vss-manage-alerts",),
    },
    "subscriptions_lifecycle": {
        "description": "Daily vss-manage-alerts realtime rule create/list/stop",
        "profile": "alerts",
        "deploy_mode": "real-time",
        "bundled_skills": ("vss-manage-alerts", "vss-manage-video-io-storage"),
    },
    "subscriptions_create_phrasings": {
        "description": "Daily vss-manage-alerts create-rule phrasing variants",
        "profile": "alerts",
        "deploy_mode": "real-time",
        "bundled_skills": ("vss-manage-alerts", "vss-manage-video-io-storage"),
    },
    "subscriptions_edge_cases": {
        "description": "Daily vss-manage-alerts unknown-sensor and stop-missing-rule",
        "profile": "alerts",
        "deploy_mode": "real-time",
        "bundled_skills": ("vss-manage-alerts", "vss-manage-video-io-storage"),
    },
    "routing_vlm_c_vs_d": {
        "description": "Daily vss-manage-alerts incident vs active-rules routing",
        "profile": "alerts",
        "deploy_mode": "real-time",
        "bundled_skills": ("vss-manage-alerts",),
    },
    "routing_e_gate_negative": {
        "description": "Daily vss-manage-alerts out-of-scope refuses",
        "profile": "alerts",
        "deploy_mode": "real-time",
        "bundled_skills": ("vss-manage-alerts", "vss-manage-video-io-storage"),
    },
    "cv_mode_gate": {
        "description": "Daily vss-manage-alerts verification-mode incident query",
        "profile": "alerts",
        "deploy_mode": "verification",
        "bundled_skills": ("vss-manage-alerts", "vss-manage-video-io-storage"),
    },
    "ondemand_verification": {
        "description": "Daily vss-manage-alerts on-demand clip verification",
        "profile": "alerts",
        "deploy_mode": "verification",
        "bundled_skills": ("vss-manage-alerts",),
    },
    "verification_flow": {
        "description": "Daily vss-manage-alerts CV verification chain explain + verdicts",
        "profile": "alerts",
        "deploy_mode": "verification",
        "bundled_skills": ("vss-manage-alerts", "vss-manage-video-io-storage"),
    },
    "summarize": {
        "description": "VSS LVS profile plus vss-summarize-video CLI (`vss summarize run`)",
        "profile": "lvs",
        "bundled_skills": ("vss-summarize-video", "vss-manage-video-io-storage"),
    },
    "vios": {
        "description": "VSS base profile plus vss-manage-video-io-storage CLI (`vss vios`)",
        "profile": "base",
        "bundled_skills": ("vss-manage-video-io-storage",),
    },
    "query-analytics": {
        "description": "Warehouse agents plus vss-query-analytics (VA API / VA-MCP read path)",
        "profile": "warehouse",
        "bundled_skills": ("vss-query-analytics",),
    },
    "alerts": {
        "description": "Warehouse agents plus vss-manage-alerts (alert-bridge already in bp_wh)",
        "profile": "warehouse",
        "bundled_skills": ("vss-manage-alerts", "vss-query-analytics"),
    },
    "alerts-cv": {
        "description": "VSS alerts profile verification mode (`MODE=2d_cv`) with remote LLM",
        "profile": "alerts",
        "deploy_mode": "verification",
        "bundled_skills": (),
    },
    "alerts-vlm": {
        "description": "VSS alerts profile real-time mode (`MODE=2d_vlm`) with remote LLM",
        "profile": "alerts",
        "deploy_mode": "real-time",
        "bundled_skills": (),
    },
    "report": {
        "description": "VSS base profile plus vss-generate-video-report",
        "profile": "base",
        "bundled_skills": (
            "vss-generate-video-report",
            "vss-manage-video-io-storage",
            "vss-query-analytics",
        ),
    },
    "report-rag": {
        "description": "VSS LVS profile plus vss-generate-video-report-rag",
        "profile": "lvs",
        "bundled_skills": (
            "vss-generate-video-report-rag",
            "vss-summarize-video",
        ),
    },
    "build-vision-ai": {
        "description": "Compose a vision stack with vss-build-vision-ai (proposal-only smoke)",
        "bundled_skills": ("vss-build-vision-ai",),
    },
    "vdr_1_quickstart_vision_agent": {
        "description": "VDR-1 quickstart: replace the in-stack agent with NemoClaw",
        "bundled_skills": ("vss-build-vision-ai",),
        "build_exam": True,
    },
    "deploy-profile": {
        "description": "Full vss-deploy-profile catalog on OpenShell (base smoke)",
        "profile": "base",
        "bundled_skills": ("vss-deploy-profile",),
    },
    "dense-captioning": {
        "description": "Standalone RT-VLM via vss-deploy-dense-captioning",
        "bundled_skills": ("vss-deploy-dense-captioning",),
    },
    "detection-tracking-2d": {
        "description": "Standalone RT-CV 2D via vss-deploy-detection-tracking-2d",
        "bundled_skills": ("vss-deploy-detection-tracking-2d",),
    },
    "detection-tracking-3d": {
        "description": "Standalone RT-CV 3D / MV3DT via vss-deploy-detection-tracking-3d",
        "bundled_skills": ("vss-deploy-detection-tracking-3d",),
    },
    "video-embedding": {
        "description": "Standalone RT-Embed via vss-deploy-video-embedding",
        "bundled_skills": ("vss-deploy-video-embedding",),
    },
    "setup-behavior-analytics": {
        "description": "Standalone behavior-analytics via vss-setup-behavior-analytics",
        "bundled_skills": ("vss-setup-behavior-analytics",),
    },
    "setup-video-analytics-api": {
        "description": "Standalone Video Analytics API via vss-setup-video-analytics-api",
        "bundled_skills": ("vss-setup-video-analytics-api",),
    },
}

# Always copied into every Harbor task so OpenShell trials can invoke
# `/vss-build-vision-ai` and the listed `skills/deployment/` runbooks, not
# only `skills/operations/*`.
ALWAYS_BUNDLED_SKILLS: tuple[str, ...] = (
    "vss-build-vision-ai",
    "vss-deploy-dense-captioning",
    "vss-deploy-detection-tracking-2d",
    "vss-deploy-detection-tracking-3d",
    "vss-deploy-profile",
    "vss-deploy-video-embedding",
    "vss-setup-behavior-analytics",
    "vss-setup-video-analytics-api",
)


def _find_bundled_skill(skills_root: Path, name: str) -> Path | None:
    """Locate a sibling skill directory by leaf name."""
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
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)


def generate_instruction(
    profile: str,
    platform: str,
    spec_query: str | None = None,
    *,
    spec_env: str = "",
    build_profile: str = "",
    step_idx: int = 1,
    step_count: int = 1,
) -> str:
    """Short, query-style instruction. The `/vss-deploy-test-openshell` skill reads the host
    and env vars and picks the actual LLM/VLM placement.

    When a spec_query is provided (the `expects[0].query` from the eval spec, with
    `{{platform}}` already substituted), it is used verbatim as the body — preserving
    any profile-specific instructions (e.g. `bp_wh_2d`, NGC app-data download, remote
    model env vars) that the generic template would omit.  When spec_query is None
    the generic template is used as a safe fallback.
    """
    if spec_query is not None:
        lines = [PREAMBLE, ""]
        if PROFILES[profile].get("build_exam"):
            lines.extend([
                f"Use the `/vss-build-vision-ai` skill for the "
                f"`{build_profile}` build on `{platform}`.",
                "Work from `$HOME/video-search-and-summarization`.",
                "",
                f"## Query {step_idx} of {step_count}",
                "",
            ])
        lines.append(spec_query)
        if spec_env:
            lines.extend(["", "## Environment notes", "", spec_env])
        lines.extend(["", "Run autonomously without prompting for confirmation."])
        return "\n".join(lines) + "\n"

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

    `step` is 1-based and matches `expects[step-1]` so a multi-query spec
    (base, lvs, warehouse, …) judges the query this Harbor
    directory was generated for.

    No `profile` argument is needed by the script itself anymore — the
    harness used to consume the deployed-profile marker written here
    for instance reuse, but that machinery (active-deploy.txt +
    `_ensure_prerequisite_deployed`) is gone. Each trial deploys
    inside its own agent turn now; nothing reads a marker."""
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


def generate_build_solve_script(
    platform: str,
    build_profile: str,
    artifact_expected: bool,
) -> str:
    """Validate a `/vss-build-vision-ai` turn without pre-deploying a stock profile."""
    lines = [
        "#!/bin/bash\n",
        f"# Gold solution: vss-build-vision-ai / {build_profile} on {platform}\n",
        "set -euo pipefail\n",
        'REPO_ROOT="${HOME}/video-search-and-summarization"\n',
        f'BUILD_DIR="${{REPO_ROOT}}/_builds/{build_profile}"\n',
    ]
    if not artifact_expected:
        lines.append(
            'echo "No artifact required for this documentation turn; '
            'the verifier checks that it stayed side-effect free."\n'
        )
        return "".join(lines)
    lines.extend([
        "for artifact in override.env compose.yml resolved.yml; do\n",
        '    test -f "${BUILD_DIR}/${artifact}" || { '
        'echo "Build output missing: ${BUILD_DIR}/${artifact}"; exit 1; }\n',
        "done\n",
        'grep -q "^FOUNDATION=" "${BUILD_DIR}/override.env"\n',
        'grep -q "^COMPOSE_PROFILES=" "${BUILD_DIR}/override.env"\n',
        'docker compose -f "${BUILD_DIR}/resolved.yml" config --quiet\n',
        'uv run "${REPO_ROOT}/skills/vss-build-vision-ai/scripts/'
        'validate_resolved_yml.py" "${BUILD_DIR}/resolved.yml" '
        '--repo-root "${REPO_ROOT}"\n',
    ])
    return "".join(lines)


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
    """Write Harbor task directory(ies) for `<profile>/<platform_short>`.

    A spec with one `expects` entry stays flat (the existing OpenShell
    smokes). A spec with several (Daily ask-video routing) writes
    `step-<k>/` so Harbor and generic_judge run one query at a time.

    `gpu_count` is the spec-declared per-platform GPU count plus the
    profile's `local_extras` (RT-CV / Cosmos Embed1 always-local GPUs).
    """
    platform_spec = PLATFORMS[platform]
    task_id = platform_spec["short_name"]
    spec_path = _spec_path_for(profile, skill_dir)
    expected_services: list[str] = []
    expects: list[dict] = []
    raw_spec: dict = {}
    if spec_path is not None:
        try:
            raw_spec = json.loads(spec_path.read_text())
            declared_services = raw_spec.get("expected_services") or []
            if not isinstance(declared_services, list) or any(
                not isinstance(name, str) for name in declared_services
            ):
                raise ValueError("expected_services must be a string list")
            expected_services = declared_services
            raw_expects = raw_spec.get("expects") or []
            if isinstance(raw_expects, list):
                expects = [e for e in raw_expects if isinstance(e, dict)]
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: could not read spec for {profile}: {exc}", file=sys.stderr)

    step_count = max(len(expects), 1)
    rendered_spec = _render_eval_spec(
        raw_spec,
        str(raw_spec.get("profile") or profile),
        platform,
    )
    for idx in range(1, step_count + 1):
        spec_query: str | None = None
        if expects:
            query = expects[idx - 1].get("query")
            if isinstance(query, str):
                spec_query = re.sub(r"\{\{\s*platform\s*\}\}", platform, query)
        dest = output_root / profile / task_id
        if step_count > 1:
            dest = dest / f"step-{idx}"
        _write_openshell_step(
            dest=dest,
            profile=profile,
            platform=platform,
            platform_spec=platform_spec,
            profile_def=profile_def,
            skill_dir=skill_dir,
            spec_path=spec_path,
            spec_query=spec_query,
            spec_env=str(rendered_spec.get("env") or ""),
            build_profile=str(raw_spec.get("profile") or profile),
            artifact_expected=(
                expects[idx - 1].get("artifact_expected", True)
                if expects else True
            ),
            judge_max_turns=int(raw_spec.get("judge_max_turns", 60)),
            expected_services=expected_services,
            gpu_count=gpu_count,
            step_idx=idx,
            step_count=step_count,
        )


def _write_openshell_step(
    *,
    dest: Path,
    profile: str,
    platform: str,
    platform_spec: dict,
    profile_def: dict,
    skill_dir: Path | None,
    spec_path: Path | None,
    spec_query: str | None,
    spec_env: str,
    build_profile: str,
    artifact_expected: bool,
    judge_max_turns: int,
    expected_services: list[str],
    gpu_count: int,
    step_idx: int,
    step_count: int,
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    task_id = platform_spec["short_name"]
    step_suffix = f"-step-{step_idx}" if step_count > 1 else ""

    if not isinstance(artifact_expected, bool):
        raise ValueError(
            f"expects[{step_idx}].artifact_expected must be a JSON boolean"
        )
    rendered_env = re.sub(r"\{\{\s*platform\s*\}\}", platform, spec_env)
    (dest / "instruction.md").write_text(generate_instruction(
        profile,
        platform,
        spec_query=spec_query,
        spec_env=rendered_env,
        build_profile=build_profile,
        step_idx=step_idx,
        step_count=step_count,
    ))

    meta_lines = [
        "[task]",
        f'name = "nvidia-vss/vss-deploy-test-openshell-{profile}-{task_id}{step_suffix}"',
        f'description = "{profile_def["description"]} on {platform}"',
        f'keywords = ["vss-deploy-test-openshell", "{profile}", "{platform}"]',
        "",
        "[agent]",
        "timeout_sec = 600.0",
        "",
        "[environment]",
        '# Harbor copies this into $CLAUDE_CONFIG_DIR/skills so the agent',
        '# can invoke /vss-deploy-test-openshell via the skill.',
        'skills_dir = "/skills"',
        "",
        "[metadata]",
        f'platform = "{platform}"',
        f"expected_services = {json.dumps(expected_services)}",
    ]
    deploy_flag_m = profile_def.get("deploy_mode")
    if deploy_flag_m:
        meta_lines.append(f'deploy_mode = "{deploy_flag_m}"')
    meta_lines += [
        "# OpenShell vss-deploy-test-openshell: GitHub labels + gpu_count.",
        "# Do not emit gpu_type / min_vram / brev_search — any SKU that",
        "# satisfies gpus-N is acceptable.",
        f'gpu_count = {gpu_count}',
        f"step_index = {step_idx}",
        f"step_count = {step_count}",
        f'min_root_disk_gb = {_DEFAULT_MIN_ROOT_DISK_GB}',
        f'min_gpu_driver_version = "{_DEFAULT_MIN_DRIVER_VERSION}"',
        "",
        "[verifier.env]",
        'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
        'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
        'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
        f'JUDGE_MAX_TURNS = "{judge_max_turns}"',
        "",
    ]
    (dest / "task.toml").write_text("\n".join(meta_lines))

    env_dir = dest / "environment"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "Dockerfile").write_text("FROM scratch\n")

    tests_dir = dest / "tests"
    tests_dir.mkdir(exist_ok=True)
    if spec_path and spec_path.exists():
        raw_spec = json.loads(spec_path.read_text())
        rendered = _render_eval_spec(raw_spec, profile, platform)
        spec_name = spec_path.name
        (tests_dir / spec_name).write_text(json.dumps(rendered, indent=2))
        (tests_dir / "test.sh").write_text(
            generate_test_script(spec_name, profile, step=step_idx)
        )
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
    else:
        (tests_dir / "test.sh").write_text(
            "#!/bin/bash\n"
            f"echo 'FAIL: no eval spec at skills/vss-deploy-test-openshell/evals/{profile}.json' >&2\n"
            "mkdir -p /logs/verifier\n"
            "echo 0 > /logs/verifier/reward.txt\n"
            "exit 0\n"
        )

    solution_dir = dest / "solution"
    solution_dir.mkdir(exist_ok=True)
    if profile_def.get("build_exam"):
        (solution_dir / "solve.sh").write_text(
            generate_build_solve_script(
                platform, build_profile, artifact_expected
            ),
        )
    elif step_idx == 1:
        (solution_dir / "solve.sh").write_text(
            generate_solve_script(profile, platform),
        )
    else:
        (solution_dir / "solve.sh").write_text(
            "#!/bin/bash\n"
            "# Later Harbor steps reuse the stack from step-1.\n"
            "exit 0\n"
        )

    if skill_dir and skill_dir.exists():
        skills_root = skill_dir.parent
        _copy_skill_dir(skill_dir, dest / "skills" / "vss-deploy-test-openshell")
        copied: set[str] = {"vss-deploy-test-openshell"}
        for extra in _iter_operations_skills(skills_root):
            _copy_skill_dir(extra, dest / "skills" / extra.name)
            copied.add(extra.name)
        extras = list(ALWAYS_BUNDLED_SKILLS)
        extras.extend(
            name for name in (profile_def.get("bundled_skills") or ()) if name not in extras
        )
        for extra in extras:
            if extra in copied:
                continue
            src = _find_bundled_skill(skills_root, extra)
            if src is None:
                print(
                    f"WARN: bundled skill {extra!r} not found under {skills_root}",
                    file=sys.stderr,
                )
                continue
            _copy_skill_dir(src, dest / "skills" / extra)
            copied.add(extra)


# ---------------------------------------------------------------------------
# Spec → matrix
# ---------------------------------------------------------------------------

def _spec_path_for(profile: str, skill_dir: Path | None) -> Path | None:
    """`evals/<profile>.json`, accepting the legacy `eval/` directory."""
    if skill_dir is None:
        return None
    for sub in ("evals", "eval"):
        candidate = skill_dir / sub / f"{profile}.json"
        if candidate.exists():
            return candidate
    return None


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
            "no spec at skills/vss-deploy-test-openshell/evals/"
            f"{profile}.json"
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
