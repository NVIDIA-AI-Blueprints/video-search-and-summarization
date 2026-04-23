#!/usr/bin/env python3
"""Generate Harbor tasks for VSS deploy skill evaluation.

For each profile × platform × mode combination, generates a task that
asks the agent to deploy the profile using local LLM/VLM NIMs.

Matrix:
    Profiles: base, alerts, lvs, search
    Platforms: H100 (80GB), L40S (48GB), RTXPRO6000BW (48GB)
    Modes:
        shared     — LLM + VLM share a single GPU (local_shared)
        dedicated  — LLM on device 0, VLM on device 1 (two GPUs)

Directory layout:
    datasets/deploy/<profile>/<platform>-<mode>/
        instruction.md, task.toml, tests/, solution/, skills/, environment/

Usage:
    # Generate all profiles × platforms × modes
    python generate.py --output-dir ../../datasets/deploy

    # Single profile
    python generate.py --output-dir ../../datasets/deploy --profile base

    # Single platform
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

VSS_REPO_URL = "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git"
VSS_BRANCH = "feat/skills"

# ---------------------------------------------------------------------------
# Platform / GPU specs
# ---------------------------------------------------------------------------
#
# min_vram_per_gpu: minimum VRAM per GPU in GB required to run VSS NIMs
# brev_search:      substring to match in `brev search --json` gpu_name field

PLATFORMS: dict[str, dict] = {
    "H100": {
        "short_name": "h100",
        "gpu_type": "H100",
        "min_vram_per_gpu": 80,
        "brev_search": "H100",
        "supported_modes": ["shared", "dedicated", "remote-all", "remote-llm", "remote-vlm"],
        "default_mode": None,
    },
    "L40S": {
        "short_name": "l40s",
        "gpu_type": "L40S",
        "min_vram_per_gpu": 48,
        "brev_search": "L40S",
        # 48 GB is not enough for LLM + VLM on the same GPU → no shared
        "supported_modes": ["dedicated", "remote-all", "remote-llm", "remote-vlm"],
        "default_mode": None,
    },
    "RTXPRO6000BW": {
        "short_name": "rtxpro6000bw",
        "gpu_type": "RTX PRO 6000",
        "min_vram_per_gpu": 96,
        "brev_search": "RTX PRO",
        "supported_modes": ["shared", "dedicated", "remote-all", "remote-llm", "remote-vlm"],
        "default_mode": None,
    },
    # Edge platforms — single GPU; default config offloads the LLM.
    "DGX-SPARK": {
        "short_name": "spark",
        "gpu_type": "GB10",
        "min_vram_per_gpu": 96,   # unified memory on GB10
        "brev_search": "GB10",
        "supported_modes": ["shared", "remote-llm"],
        "default_mode": "remote-llm",  # bare "spark" task id
    },
    "IGX-THOR": {
        "short_name": "thor",
        "gpu_type": "Thor",
        "min_vram_per_gpu": 64,
        "brev_search": "Thor",
        "supported_modes": ["shared", "remote-llm"],
        "default_mode": "remote-llm",  # bare "thor" task id
    },
}

# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

MODES: dict[str, dict] = {
    "shared": {
        "llm_mode": "local_shared",
        "vlm_mode": "local_shared",
        "gpus_needed": 1,
        "description": "LLM and VLM share a single GPU",
    },
    "dedicated": {
        "llm_mode": "local",
        "vlm_mode": "local",
        "gpus_needed": 2,
        "description": "LLM on GPU 0, VLM on GPU 1",
    },
    "remote-all": {
        "llm_mode": "remote",
        "vlm_mode": "remote",
        "gpus_needed": 0,
        "description": "Both LLM and VLM via remote endpoints (no local GPU used)",
    },
    "remote-llm": {
        "llm_mode": "remote",
        "vlm_mode": "local_shared",
        "gpus_needed": 1,
        "description": "Remote LLM, local VLM on a single GPU",
    },
    "remote-vlm": {
        "llm_mode": "local_shared",
        "vlm_mode": "remote",
        "gpus_needed": 1,
        "description": "Local LLM on a single GPU, remote VLM",
    },
}

# ---------------------------------------------------------------------------
# Resource estimates
# ---------------------------------------------------------------------------
#
# VSS base stack (agent, UI, VST, phoenix, redis, kafka, centralizedb) is
# ~60 GB of image pulls.  Each local NIM image is ~60-70 GB.  Add 20 GB
# docker metadata + buffer.
#
# min_gpu_driver_version is keyed to the default NIM image tags shipped
# with the skill: cosmos-reason2-8b:1.6.0 requires driver 580.95+.  If the
# mode uses only remote inference (remote-all), there is no local driver
# requirement.

_BASE_STACK_GB = 80
_PER_LOCAL_NIM_GB = 70
_LOCAL_NIM_MIN_DRIVER = "580.95"


def _min_root_disk_gb(mode_spec: dict) -> int:
    """Estimated root disk (GB) needed for this mode's docker workload."""
    n = int(mode_spec["llm_mode"] != "remote") + int(mode_spec["vlm_mode"] != "remote")
    return _BASE_STACK_GB + _PER_LOCAL_NIM_GB * n


def _min_gpu_driver_version(mode_spec: dict) -> str | None:
    """Minimum NVIDIA driver version. None if no local NIMs."""
    if mode_spec["llm_mode"] == "remote" and mode_spec["vlm_mode"] == "remote":
        return None
    return _LOCAL_NIM_MIN_DRIVER


# Edge platforms that don't have an arm64 NIM for the Nano 9B LLM —
# shared mode on these must use the Edge 4B model via a standalone vLLM
# container, which the blueprint deploys with LLM_MODE=remote. See
# skills/deploy/references/edge.md.
_EDGE_PLATFORMS_WITHOUT_LOCAL_LLM_NIM = ("DGX-SPARK", "IGX-THOR", "AGX-THOR")


def effective_mode_spec(platform: str, mode: str) -> dict:
    """Return the mode spec with platform-specific overrides applied.

    On edge platforms (DGX-SPARK / IGX-THOR / AGX-THOR), `shared` mode
    maps to llm_mode=remote (Edge 4B runs as a standalone vLLM on
    localhost:30081 — blueprint treats it as a remote endpoint) +
    vlm_mode=local_shared (VLM NIM still deploys locally). Other modes
    and other platforms pass through unchanged.
    """
    spec = dict(MODES[mode])
    if mode == "shared" and platform in _EDGE_PLATFORMS_WITHOUT_LOCAL_LLM_NIM:
        spec["llm_mode"] = "remote"
        spec["vlm_mode"] = "local_shared"
        spec["description"] = (
            "Edge shared mode: Edge 4B LLM via standalone vLLM on port "
            "30081 (LLM_MODE=remote), VLM NIM shares the same GPU"
        )
        spec["_edge_override"] = True
    return spec

# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------

# Per-(eval-)profile verification lives in `skills/deploy/eval/<profile>.json`
# (loaded + templated at task-generation time). Keep this dict narrow:
#   - description      → task.toml metadata
#   - profile          → real `/deploy -p <profile>` argument when the eval key
#                        differs (e.g. eval profile `alerts_cv` deploys
#                        `-p alerts -m verification`). Empty or absent means
#                        the dict key is itself the deploy profile.
#   - deploy_mode      → value of `/deploy -m ...` for this eval variant
# Everything else (platforms, modes, checks) lives in the spec.
PROFILES: dict[str, dict] = {
    "base": {
        "description": "VSS base profile — agent, UI, VST, LLM/VLM NIMs",
        # "profile" omitted → the dict key ("base") is also the deploy profile
    },
    "alerts_cv": {
        "description": "VSS alerts profile, CV mode (`deploy -m verification`) — "
                       "RT-CV generates candidate alerts, VLM reviews each",
        "profile": "alerts",
        "deploy_mode": "verification",
    },
    "alerts_vlm": {
        "description": "VSS alerts profile, VLM mode (`deploy -m real-time`) — "
                       "VLM continuously processes live video",
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
    """Real `/deploy` profile name for an eval-profile entry.

    Eval keys like `alerts_cv` map to the underlying `-p alerts` argument
    of `/deploy`; plain keys like `base` map to themselves. Respects the
    optional `profile` field in PROFILES (empty/absent ⇒ key is the profile)."""
    override = PROFILES.get(eval_profile, {}).get("profile")
    return override or eval_profile

# ---------------------------------------------------------------------------
# Instruction generation
# ---------------------------------------------------------------------------

def _describe_model(role: str, mode: str, remote: dict | None,
                    edge_override: bool = False) -> str:
    """One-line description of the LLM/VLM configuration for the instruction."""
    if mode == "remote" and edge_override and role == "LLM":
        # Edge shared mode: LLM is Edge 4B running locally in a vLLM
        # container on port 30081, NOT the launchpad remote endpoint.
        return (
            "- LLM: Edge 4B via **local** vLLM container on "
            "`http://localhost:30081` "
            "(model `nvidia/NVIDIA-Nemotron-Edge-4B-v2.1-EA-020126_FP8`). "
            "The blueprint treats this as `LLM_MODE=remote` because the "
            "agent reaches it via `LLM_BASE_URL`, but the vLLM container "
            "runs on this same host. Do NOT use a launchpad URL."
        )
    if mode == "remote" and remote:
        url = remote.get("url", "")
        model = remote.get("model", "")
        return f"- {role}: remote, endpoint `{url}` (model `{model}`)"
    if mode == "local_shared":
        return f"- {role}: local NIM, mode `local_shared` (shares GPU)"
    if mode == "local":
        return f"- {role}: local NIM, mode `local` (dedicated GPU)"
    return f"- {role}: mode `{mode}`"


def _query_hint(mode_spec: dict, llm_remote: dict | None,
                vlm_remote: dict | None) -> str:
    """One-line English suffix describing the eval target for this mode.

    Intentionally minimal — no feasibility advice, no GPU counts, no mode
    names. The `/deploy` skill reads the host's real hardware + env vars and
    decides the actual compose configuration. See `skills/deploy/SKILL.md`
    and the VSS prerequisites page for the decision table."""
    llm = mode_spec["llm_mode"]
    vlm = mode_spec["vlm_mode"]
    llm_url = (llm_remote or {}).get("url") or "$LLM_REMOTE_URL"
    vlm_url = (vlm_remote or {}).get("url") or "$VLM_REMOTE_URL"

    if llm == "remote" and vlm == "remote":
        return f"with a remote LLM at {llm_url} and a remote VLM at {vlm_url}"
    if llm == "remote":
        return f"with a remote LLM at {llm_url}"
    if vlm == "remote":
        return f"with a remote VLM at {vlm_url}"
    if llm == "local_shared" and vlm == "local_shared":
        return "on a single GPU"
    if llm == "local" and vlm == "local":
        return "on dedicated GPUs"
    return ""


def generate_instruction(
    profile: str,
    platform: str,
    mode: str,
    llm_remote: dict | None,
    vlm_remote: dict | None,
) -> str:
    """Short, query-style instruction. The agent + `/deploy` skill pick
    the right compose configuration from the available hardware and env.

    Shape: "Deploy the <profile> profile <hint>."  No step-by-step recipe,
    no feasibility rules — the skill owns that decision (SKILL.md cites the
    VSS prerequisites doc). If the host can't support the target, the
    skill reports the blocker.
    """
    profile_def = PROFILES[profile]
    is_debug = bool(profile_def.get("debug"))
    underlying = deploy_profile(profile)
    deploy_flag_m = profile_def.get("deploy_mode")
    mode_spec = effective_mode_spec(platform, mode)
    hint = _query_hint(mode_spec, llm_remote, vlm_remote)

    verb_phrase = f"Deploy the **{underlying}** profile"
    if deploy_flag_m:
        verb_phrase += f" in **{deploy_flag_m}** mode"
    if hint:
        verb_phrase += f" {hint}"
    verb_phrase += "."

    body = [
        verb_phrase,
        "",
        "Use the `/deploy` skill.",
    ]
    if is_debug:
        body.append(
            "After the stack is up, also run the skill's debug workflow "
            "to verify the video path end-to-end."
        )
    return "\n".join(body) + "\n"


# ---------------------------------------------------------------------------
# Test script generation
# ---------------------------------------------------------------------------

def _render_eval_spec(spec: dict, profile: str, platform: str, mode: str,
                      mode_spec: dict, llm_remote: dict | None,
                      vlm_remote: dict | None) -> dict:
    """Substitute {{platform}}, {{mode}}, {{llm_mode}}, {{vlm_mode}}, and the
    remote-endpoint placeholders into every string field of the spec. Returns
    a fully-resolved spec ready to ship to the task's tests/ dir.

    `{{mode}}` is the short trial-mode token (e.g. "shared", "remote-all").
    `{{mode_description}}` is the prose form ("LLM and VLM share a single GPU").
    `{{repo_root}}` is `$HOME/video-search-and-summarization` — a shell-
    expansion that matches whichever default user the Brev provider assigns
    (Crusoe → `ubuntu`, Massed Compute → `shadeform`, etc.). The deploy skill
    clones into `$HOME`, so checks should reference `{{repo_root}}`, not a
    hardcoded `/home/ubuntu/...` path.
    """
    substitutions = {
        "profile": profile,
        "platform": platform,
        "mode": mode,
        "mode_description": mode_spec.get("description", "") or "",
        "llm_mode": mode_spec["llm_mode"],
        "vlm_mode": mode_spec["vlm_mode"],
        "llm_remote_url":   (llm_remote or {}).get("url", ""),
        "llm_remote_model": (llm_remote or {}).get("model", ""),
        "vlm_remote_url":   (vlm_remote or {}).get("url", ""),
        "vlm_remote_model": (vlm_remote or {}).get("model", ""),
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


def generate_test_script(spec_name: str) -> str:
    """Wrapper test.sh that invokes the generic LLM-as-judge verifier
    against the rendered eval spec shipped alongside it. Harbor reads
    /logs/verifier/reward.txt."""
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
        "exit 0\n"
    )


# ---------------------------------------------------------------------------
# Solution script generation
# ---------------------------------------------------------------------------

def generate_solve_script(
    profile: str,
    platform: str,
    mode: str,
    llm_remote: dict | None,
    vlm_remote: dict | None,
) -> str:
    """Gold solution: configure .env + deploy."""
    mode_spec = effective_mode_spec(platform, mode)
    env_profile = deploy_profile(profile)

    overrides: dict[str, str] = {
        "HARDWARE_PROFILE": platform,
        "MDX_SAMPLE_APPS_DIR": "$REPO/deployments",
        "MDX_DATA_DIR": "$REPO/data",
        "HOST_IP": "$(hostname -I | awk '{print $1}')",
        "LLM_MODE": mode_spec["llm_mode"],
        "VLM_MODE": mode_spec["vlm_mode"],
    }
    if mode == "dedicated":
        overrides["LLM_DEVICE_ID"] = "0"
        overrides["VLM_DEVICE_ID"] = "1"

    # Remote endpoints: URL is stored without trailing /v1 — config.yml
    # appends /v1 automatically via `base_url: ${LLM_BASE_URL}/v1`.
    if mode_spec["llm_mode"] == "remote" and llm_remote:
        overrides["LLM_BASE_URL"] = llm_remote["url"].rstrip("/").removesuffix("/v1")
        overrides["LLM_NAME"] = llm_remote["model"]
    if mode_spec["vlm_mode"] == "remote" and vlm_remote:
        overrides["VLM_BASE_URL"] = vlm_remote["url"].rstrip("/").removesuffix("/v1")
        overrides["VLM_NAME"] = vlm_remote["model"]

    sed_lines = "\n".join(
        'sed -i "s|^' + k + "=.*|" + k + "=" + v + '|" "$ENV_FILE"'
        for k, v in overrides.items()
    )

    lines = [
        "#!/bin/bash",
        "# Gold solution: deploy " + profile + " on " + platform + "/" + mode,
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
        "# --- Clone repo ---",
        'if [ ! -d "$REPO" ]; then',
        "    git clone --branch " + VSS_BRANCH + " " + VSS_REPO_URL + ' "$REPO"',
        "fi",
        'mkdir -p "$REPO/data"',
        "",
        "# --- Configure .env ---",
        "PROFILE=" + env_profile,
        "ENV_FILE=$REPO/deployments/developer-workflow/dev-profile-$PROFILE/.env",
        "",
        sed_lines,
        "",
        'if [ -n "${NGC_CLI_API_KEY:-}" ]; then',
        '    sed -i "s|^NGC_CLI_API_KEY=.*|NGC_CLI_API_KEY=$NGC_CLI_API_KEY|" "$ENV_FILE"',
        "fi",
        "",
        "# --- Deploy ---",
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
    mode: str,
    profile_def: dict,
    output_root: Path,
    skill_dir: Path | None,
    llm_remote: dict | None,
    vlm_remote: dict | None,
) -> None:
    """Write a single Harbor task directory for <profile>/<platform>-<mode>."""
    platform_spec = PLATFORMS[platform]
    mode_spec = effective_mode_spec(platform, mode)

    task_id = make_task_id(platform, mode)
    task_dir = output_root / profile / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # -- instruction.md --
    (task_dir / "instruction.md").write_text(
        generate_instruction(profile, platform, mode, llm_remote, vlm_remote),
    )

    # -- task.toml --
    meta_lines = [
        "[task]",
        f'name = "nvidia-vss/deploy-{profile}-{task_id}"',
        f'description = "{profile_def["description"]} on {platform}/{mode}"',
        f'keywords = ["deploy", "{profile}", "{platform}", "{mode}"]',
        "",
        "[environment]",
        '# Harbor copies this into $CLAUDE_CONFIG_DIR/skills so the agent',
        '# can invoke /deploy via the skill.',
        'skills_dir = "/skills"',
        "",
        "[metadata]",
        f'profile = "{profile}"',
        f'platform = "{platform}"',
        f'mode = "{mode}"',
        "# GPU requirements — BrevEnvironment checks these against the",
        "# instance's actual GPU capacity before the trial runs.",
        f'gpu_type = "{platform_spec["gpu_type"]}"',
        f'gpu_count = {mode_spec["gpus_needed"]}',
        f'min_vram_gb_per_gpu = {platform_spec["min_vram_per_gpu"]}',
        f'brev_search = "{platform_spec["brev_search"]}"',
        "# Disk + driver requirements — BrevEnvironment validates both via",
        "# `df -BG /` and `nvidia-smi --query-gpu=driver_version` after the",
        "# instance is reachable; a mismatch raises and the trial is aborted.",
        f'min_root_disk_gb = {_min_root_disk_gb(mode_spec)}',
    ]
    min_driver = _min_gpu_driver_version(mode_spec)
    if min_driver:
        meta_lines.append(f'min_gpu_driver_version = "{min_driver}"')
    if mode_spec["llm_mode"] == "remote" and llm_remote:
        meta_lines.append(f'llm_remote_url = "{llm_remote["url"]}"')
        meta_lines.append(f'llm_remote_model = "{llm_remote["model"]}"')
    if mode_spec["vlm_mode"] == "remote" and vlm_remote:
        meta_lines.append(f'vlm_remote_url = "{vlm_remote["url"]}"')
        meta_lines.append(f'vlm_remote_model = "{vlm_remote["model"]}"')
    # Forward Anthropic credentials + judge model to the verifier so the
    # LLM-as-judge in tests/generic_judge.py can call Claude.
    meta_lines += [
        "",
        "[verifier.env]",
        'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
        'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
        'JUDGE_MODEL = "${JUDGE_MODEL:-claude-haiku-4-5}"',
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
    # The spec file is keyed on the EVAL-profile name (e.g. alerts_cv.json),
    # not the underlying deploy-profile — different modes of the same deploy
    # profile can ship distinct spec files (alerts_cv.json vs alerts_vlm.json).
    spec_path = skill_dir / "eval" / f"{profile}.json" if skill_dir else None
    if spec_path and spec_path.exists():
        raw_spec = json.loads(spec_path.read_text())
        rendered = _render_eval_spec(
            raw_spec, profile, platform, mode, mode_spec, llm_remote, vlm_remote,
        )
        spec_name = spec_path.name
        (tests_dir / spec_name).write_text(json.dumps(rendered, indent=2))
        (tests_dir / "test.sh").write_text(generate_test_script(spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
    else:
        # No spec yet for this profile — emit a no-op verifier that
        # reports a clear failure instead of silently passing.
        (tests_dir / "test.sh").write_text(
            "#!/bin/bash\n"
            f"echo 'FAIL: no eval spec at skills/deploy/eval/{underlying}.json' >&2\n"
            "mkdir -p /logs/verifier\n"
            "echo 0 > /logs/verifier/reward.txt\n"
            "exit 0\n"
        )

    # -- solution/solve.sh --
    solution_dir = task_dir / "solution"
    solution_dir.mkdir(exist_ok=True)
    (solution_dir / "solve.sh").write_text(
        generate_solve_script(profile, platform, mode, llm_remote, vlm_remote),
    )

    # -- skills/deploy/ --
    if skill_dir and skill_dir.exists():
        skill_dest = task_dir / "skills" / "deploy"
        if skill_dest.exists():
            shutil.rmtree(skill_dest)
        shutil.copytree(skill_dir, skill_dest)


def make_task_id(platform: str, mode: str) -> str:
    """Task directory name.  Equal to the platform short name when the
    mode is this platform's default, otherwise '<short>-<mode>'."""
    pspec = PLATFORMS[platform]
    if mode == pspec.get("default_mode"):
        return pspec["short_name"]
    return f"{pspec['short_name']}-{mode}"


def _mode_needs_local_nim(mode_spec: dict) -> bool:
    """True if the mode deploys at least one local NIM (needs NGC to pull)."""
    return mode_spec["llm_mode"] != "remote" or mode_spec["vlm_mode"] != "remote"


def _spec_platforms_for(profile: str, skill_dir: Path | None) -> dict[str, list[str]] | None:
    """If the skill's `eval/<profile>.json` declares `resources.platforms`,
    return {platform: [modes...]}. Else return None (adapter falls back to
    PLATFORMS defaults below). Gives the spec author control over which
    platforms/modes to exercise — e.g. `alerts_cv` only on 2-GPU hosts."""
    if skill_dir is None:
        return None
    spec_path = skill_dir / "eval" / f"{profile}.json"
    if not spec_path.exists():
        return None
    try:
        spec = json.loads(spec_path.read_text())
    except Exception:
        return None
    resources = (spec.get("resources") or {}).get("platforms")
    if not isinstance(resources, dict) or not resources:
        return None
    return {p: list((v or {}).get("modes") or []) for p, v in resources.items()}


def expand_matrix(
    profile_filter: str | None,
    platform_filter: str | None,
    mode_filter: str | None,
    have_llm_remote: bool,
    have_vlm_remote: bool,
    have_ngc_key: bool,
    skill_dir: Path | None = None,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str, str]]]:
    """Return (included, skipped) where:
        included = list of (profile, platform, mode) that will be generated
        skipped  = list of (profile, platform, mode, reason)
    For each profile, the platform × mode matrix comes from:
      - `spec.resources.platforms` if declared in `skills/deploy/eval/<profile>.json`
      - otherwise falls back to the full PLATFORMS × supported_modes defaults
    Also filters: --profile/--platform/--mode CLI, remote URL availability for
    modes that need them, and NGC_CLI_API_KEY for modes that pull local NIMs."""
    included: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str, str, str]] = []
    for profile in PROFILES:
        if profile_filter and profile != profile_filter:
            continue

        # Prefer the spec's own declaration; fall back to the adapter defaults.
        spec_matrix = _spec_platforms_for(profile, skill_dir)
        if spec_matrix is not None:
            platform_modes = spec_matrix
        else:
            platform_modes = {
                p: list(pspec["supported_modes"])
                for p, pspec in PLATFORMS.items()
            }

        for platform, modes in platform_modes.items():
            if platform_filter and platform != platform_filter:
                continue
            if platform not in PLATFORMS:
                skipped.append((profile, platform, "-", f"unknown platform {platform!r}"))
                continue
            for mode in modes:
                if mode_filter and mode != mode_filter:
                    continue
                if mode not in MODES:
                    skipped.append((profile, platform, mode, f"unknown mode {mode!r}"))
                    continue
                mspec = MODES[mode]
                reason = None
                if mspec["llm_mode"] == "remote" and not have_llm_remote:
                    reason = "LLM_REMOTE_URL/MODEL not set"
                elif mspec["vlm_mode"] == "remote" and not have_vlm_remote:
                    reason = "VLM_REMOTE_URL/MODEL not set"
                elif _mode_needs_local_nim(mspec) and not have_ngc_key:
                    reason = "NGC_CLI_API_KEY not set (needed to pull local NIMs)"
                if reason:
                    skipped.append((profile, platform, mode, reason))
                else:
                    included.append((profile, platform, mode))
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
    parser.add_argument("--mode", default=None, choices=list(MODES.keys()))
    parser.add_argument(
        "--llm-remote-url", default=None,
        help="Remote LLM endpoint (no trailing /v1). Enables remote-* modes for LLM.",
    )
    parser.add_argument(
        "--llm-remote-model", default=None,
        help="Model ID served at --llm-remote-url (e.g. nvidia/nvidia-nemotron-nano-9b-v2)",
    )
    parser.add_argument(
        "--vlm-remote-url", default=None,
        help="Remote VLM endpoint (no trailing /v1). Enables remote-* modes for VLM.",
    )
    parser.add_argument(
        "--vlm-remote-model", default=None,
        help="Model ID served at --vlm-remote-url",
    )
    parser.add_argument(
        "--assume-ngc-key", action="store_true",
        help="Pretend NGC_CLI_API_KEY is available even if env doesn't have it",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir) if args.skill_dir else None

    # Resolve remote endpoints (URL + model must be paired). CLI args take
    # priority; fall back to the standard env vars so `source .env; python3
    # generate.py` Just Works without re-passing every flag.
    llm_url   = args.llm_remote_url   or os.environ.get("LLM_REMOTE_URL")
    llm_model = args.llm_remote_model or os.environ.get("LLM_REMOTE_MODEL")
    vlm_url   = args.vlm_remote_url   or os.environ.get("VLM_REMOTE_URL")
    vlm_model = args.vlm_remote_model or os.environ.get("VLM_REMOTE_MODEL")

    llm_remote: dict | None = None
    if llm_url:
        if not llm_model:
            print(
                "LLM_REMOTE_URL set but LLM_REMOTE_MODEL is empty — "
                "set both or neither (pass --llm-remote-url/--llm-remote-model "
                "or define LLM_REMOTE_URL/LLM_REMOTE_MODEL in .env).",
                file=sys.stderr,
            )
            sys.exit(1)
        llm_remote = {"url": llm_url, "model": llm_model}

    vlm_remote: dict | None = None
    if vlm_url:
        if not vlm_model:
            print(
                "VLM_REMOTE_URL set but VLM_REMOTE_MODEL is empty — "
                "set both or neither.",
                file=sys.stderr,
            )
            sys.exit(1)
        vlm_remote = {"url": vlm_url, "model": vlm_model}

    have_ngc_key = args.assume_ngc_key or bool(os.environ.get("NGC_CLI_API_KEY"))

    # --- Inputs summary ---
    print("=== Inputs ===")
    print(f"  output_dir       : {output_root}")
    print(f"  skill_dir        : {skill_dir or '(none)'}")
    print(f"  filter profile   : {args.profile or '(all)'}")
    print(f"  filter platform  : {args.platform or '(all)'}")
    print(f"  filter mode      : {args.mode or '(all)'}")
    if llm_remote:
        llm_src = "CLI" if args.llm_remote_url else "env"
        print(f"  LLM remote       : {llm_remote['url']}  ({llm_remote['model']}) [{llm_src}]")
    else:
        print(f"  LLM remote       : (not set — pass --llm-remote-url or export LLM_REMOTE_URL; remote-* modes needing LLM will be skipped)")
    if vlm_remote:
        vlm_src = "CLI" if args.vlm_remote_url else "env"
        print(f"  VLM remote       : {vlm_remote['url']}  ({vlm_remote['model']}) [{vlm_src}]")
    else:
        print(f"  VLM remote       : (not set — pass --vlm-remote-url or export VLM_REMOTE_URL; remote-* modes needing VLM will be skipped)")
    if have_ngc_key:
        source = "--assume-ngc-key" if args.assume_ngc_key else "NGC_CLI_API_KEY env"
        print(f"  NGC key          : available ({source})")
    else:
        print(f"  NGC key          : (not set — modes with local NIMs will be skipped)")
    print()

    included, skipped = expand_matrix(
        args.profile, args.platform, args.mode,
        have_llm_remote=llm_remote is not None,
        have_vlm_remote=vlm_remote is not None,
        have_ngc_key=have_ngc_key,
        skill_dir=skill_dir,
    )

    # --- Print skip decisions ---
    if skipped:
        print(f"=== Skipped ({len(skipped)}) ===")
        for profile, platform, mode, reason in skipped:
            task_id = make_task_id(platform, mode)
            print(f"  SKIP {profile}/{task_id}   reason: {reason}")
        print()

    if not included:
        print("No (profile, platform, mode) combinations match filters "
              "with the provided env.", file=sys.stderr)
        sys.exit(1)

    # --- Generate ---
    print(f"=== Generating ({len(included)}) ===")
    for profile, platform, mode in included:
        task_id = make_task_id(platform, mode)
        print(f"  GEN  {profile}/{task_id}")
        generate_task(
            profile, platform, mode,
            PROFILES[profile], output_root, skill_dir,
            llm_remote, vlm_remote,
        )

    print()
    print(f"Summary: {len(included)} generated, {len(skipped)} skipped.")
    print()
    print("Coverage:")
    by_profile: dict[str, list[str]] = {}
    for p, plat, m in included:
        by_profile.setdefault(p, []).append(make_task_id(plat, m))
    for p, tasks in by_profile.items():
        print(f"  {p}: {', '.join(tasks)}")
    print()
    print("Run a profile's tasks with:")
    first_profile = list(by_profile.keys())[0]
    print(f"  harbor run --env 'tools.eval.harbor.envs.brev_env:BrevEnvironment' \\")
    print(f"    -p {output_root}/{first_profile} -a claude-code -n 1")


if __name__ == "__main__":
    main()
