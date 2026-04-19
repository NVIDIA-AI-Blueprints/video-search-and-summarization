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
import os
import shutil
import sys
from pathlib import Path

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

# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------

PROFILES: dict[str, dict] = {
    "base": {
        "description": "VSS base profile — agent, UI, VST, LLM/VLM NIMs",
        "expected_containers": [
            "vss-agent",
            "metropolis-vss-ui",
            "mdx-redis",
            "centralizedb-dev",
            "phoenix",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
            {"port": 3000, "path": "/", "name": "Agent UI"},
        ],
    },
    "alerts": {
        "description": "VSS alerts profile — CV perception, alert verification",
        "expected_containers": [
            "vss-agent",
            "mdx-redis",
            "centralizedb-dev",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
        ],
    },
    "lvs": {
        "description": "VSS LVS profile — long video summarization",
        "expected_containers": [
            "vss-agent",
            "metropolis-vss-ui",
            "mdx-redis",
            "centralizedb-dev",
            "phoenix",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
            {"port": 3000, "path": "/", "name": "Agent UI"},
        ],
    },
    "search": {
        "description": "VSS search profile — Cosmos Embed1 semantic search",
        "expected_containers": [
            "vss-agent",
            "metropolis-vss-ui",
            "mdx-redis",
            "centralizedb-dev",
            "phoenix",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
            {"port": 3000, "path": "/", "name": "Agent UI"},
        ],
    },
    "base-debug": {
        "description": "VSS base profile + debug workflow — deploy, then run the "
                       "/deploy skill's debug script to verify end-to-end video flow",
        "derives_from": "base",
        "debug": True,
        "expected_containers": [
            "vss-agent",
            "metropolis-vss-ui",
            "mdx-redis",
            "centralizedb-dev",
            "phoenix",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
            {"port": 3000, "path": "/", "name": "Agent UI"},
        ],
    },
}

# ---------------------------------------------------------------------------
# Instruction generation
# ---------------------------------------------------------------------------

def _describe_model(role: str, mode: str, remote: dict | None) -> str:
    """One-line description of the LLM/VLM configuration for the instruction."""
    if mode == "remote" and remote:
        url = remote.get("url", "")
        model = remote.get("model", "")
        return f"- {role}: remote, endpoint `{url}` (model `{model}`)"
    if mode == "local_shared":
        return f"- {role}: local NIM, mode `local_shared` (shares GPU)"
    if mode == "local":
        return f"- {role}: local NIM, mode `local` (dedicated GPU)"
    return f"- {role}: mode `{mode}`"


def generate_instruction(
    profile: str,
    platform: str,
    mode: str,
    llm_remote: dict | None,
    vlm_remote: dict | None,
) -> str:
    """High-level goal + context. Agent uses /deploy skill for the workflow."""
    mode_spec = MODES[mode]
    profile_def = PROFILES[profile]
    is_debug = bool(profile_def.get("debug"))
    underlying = profile_def.get("derives_from", profile)

    if is_debug:
        lines = [
            f"Use the `/deploy` skill to **deploy and debug** the VSS "
            f"**{underlying}** profile on this machine.",
            "",
            "## Target configuration",
            "",
            f"- Hardware profile: `{platform}`",
            f"- GPU mode: **{mode}** — {mode_spec['description']}",
            _describe_model("LLM", mode_spec["llm_mode"], llm_remote),
            _describe_model("VLM", mode_spec["vlm_mode"], vlm_remote),
            "",
            "## Repository",
            "",
            "If the VSS repository is not already present, clone it from:",
            f"  `{VSS_REPO_URL}` (branch `{VSS_BRANCH}`)",
            "",
            "## Credentials",
            "",
            "- `NGC_CLI_API_KEY` is available in the environment for pulling "
            "NIM containers from `nvcr.io`.",
            "",
            "## Workflow",
            "",
            f"1. Deploy the `{underlying}` profile end-to-end (`/deploy` skill, "
            "autonomous — do not stop to confirm).",
            "2. Once containers are up and the Agent API responds at "
            "`http://localhost:8000/docs`, run the skill's debug script to "
            "verify the full video pipeline works:",
            "",
            "   ```bash",
            "   pip install websocket-client",
            "   python skills/deploy/scripts/test_base.py \\",
            "       http://localhost:8000 --profile base",
            "   ```",
            "",
            "   (The script is bundled with the `/deploy` skill. See "
            "`skills/deploy/SKILL.md` → *Debugging a Deployment* and "
            "`skills/deploy/references/base.md` → *Debugging* for details.)",
            "",
            "## Success criteria",
            "",
            "- Expected containers are Up",
            "- Agent API responds at `http://localhost:8000/docs`",
            "- `test_base.py` exits 0 (video uploaded + both "
            "WebSocket queries return non-empty content)",
            "",
        ]
        return "\n".join(lines) + "\n"

    lines = [
        f"Use the `/deploy` skill to deploy the VSS **{profile}** profile on this machine.",
        "",
        "## Target configuration",
        "",
        f"- Hardware profile: `{platform}`",
        f"- GPU mode: **{mode}** — {mode_spec['description']}",
        _describe_model("LLM", mode_spec["llm_mode"], llm_remote),
        _describe_model("VLM", mode_spec["vlm_mode"], vlm_remote),
        "",
        "## Repository",
        "",
        f"If the VSS repository is not already present, clone it from:",
        f"  `{VSS_REPO_URL}` (branch `{VSS_BRANCH}`)",
        "",
        "## Credentials",
        "",
        "- `NGC_CLI_API_KEY` is available in the environment for pulling NIM "
        "containers from `nvcr.io`.",
        "",
        "## Success criteria",
        "",
        "Deployment is successful when the Agent API responds at "
        "`http://localhost:8000/docs` and the expected core containers "
        "are running.",
        "",
        "Run the deployment end-to-end without prompting for confirmation — "
        "proceed autonomously from cloning through `docker compose up -d`.",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Test script generation
# ---------------------------------------------------------------------------

def generate_test_script(profile: str, profile_def: dict, platform: str, mode: str) -> str:
    """Verifier: check .env config, running containers, healthy endpoints."""
    containers = profile_def["expected_containers"]
    endpoints = profile_def["expected_endpoints"]
    mode_spec = MODES[mode]
    # base + base-debug both run the E2E sanity check.
    underlying = profile_def.get("derives_from", profile)
    run_e2e = underlying == "base"
    env_profile = underlying  # .env lives at dev-profile-<underlying>/.env

    container_checks = "\n".join(
        'check_container "' + c + '"' for c in containers
    )
    endpoint_checks = "\n".join(
        'check_endpoint ' + str(e["port"]) + ' "' + e["path"] + '" "' + e["name"] + '"'
        for e in endpoints
    )

    env_checks = [
        ("HARDWARE_PROFILE", platform),
        ("LLM_MODE", mode_spec["llm_mode"]),
        ("VLM_MODE", mode_spec["vlm_mode"]),
    ]
    validate_lines = "\n".join(
        'validate_env "' + k + '" "' + v + '"' for k, v in env_checks
    )

    lines = [
        "#!/bin/bash",
        "# Verifier for deploy: " + profile + " on " + platform + "/" + mode,
        "# Writes reward to /logs/verifier/reward.txt",
        "set -uo pipefail",
        "",
        "PASS=0",
        "FAIL=0",
        "TOTAL=0",
        "",
        "mkdir -p /logs/verifier",
        "",
        'check_pass() { echo "PASS: $1"; ((PASS++)) || true; ((TOTAL++)) || true; }',
        'check_fail() { echo "FAIL: $1"; ((FAIL++)) || true; ((TOTAL++)) || true; }',
        "",
        "check_container() {",
        "    local name=$1",
        "    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q \"$name\"; then",
        '        check_pass "container $name is running"',
        "    else",
        '        check_fail "container $name not found"',
        "    fi",
        "}",
        "",
        "check_endpoint() {",
        "    local port=$1 path=$2 name=$3",
        '    if curl -sf -o /dev/null --max-time 15 "http://localhost:${port}${path}" 2>/dev/null; then',
        '        check_pass "$name (port $port) responds"',
        "    else",
        '        check_fail "$name (port $port) not responding"',
        "    fi",
        "}",
        "",
        "validate_env() {",
        "    local key=$1 expected=$2",
        "    local actual",
        "    actual=$(grep \"^${key}=\" \"$ENV_FILE\" 2>/dev/null | head -1 | cut -d= -f2- | tr -d \"'\\\"\")",
        '    if [ "$actual" = "$expected" ]; then',
        '        check_pass "$key=$expected"',
        "    else",
        '        check_fail "$key expected \'$expected\' got \'$actual\'"',
        "    fi",
        "}",
        "",
        "# --- Find the VSS repository ---",
        'REPO=""',
        "for d in /home/*/video-search-and-summarization \\",
        "         /workspace/video-search-and-summarization; do",
        '    [ -d "$d/deployments" ] && REPO="$d" && break',
        "done",
        "",
        'if [ -z "$REPO" ]; then',
        '    check_fail "VSS repository not found"',
        '    echo 0 > /logs/verifier/reward.txt',
        "    exit 0",
        "fi",
        'check_pass "VSS repository found"',
        "",
        'ENV_FILE="$REPO/deployments/developer-workflow/dev-profile-' + env_profile + '/.env"',
        "",
        'echo "=== Checking .env ==="',
        validate_lines,
        "",
        'echo ""',
        'echo "=== Checking containers ==="',
        container_checks,
        "",
        'echo ""',
        'echo "=== Checking endpoints ==="',
        endpoint_checks,
        "",
        'echo ""',
    ]
    if run_e2e:
        lines += [
            'echo "=== Warehouse video E2E sanity check ==="',
            'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"',
            'python3 -m pip install --quiet websocket-client >/dev/null 2>&1 || true',
            'if python3 "$TEST_DIR/test_base.py" http://localhost:8000 --profile base; then',
            '    check_pass "warehouse video E2E"',
            'else',
            '    check_fail "warehouse video E2E"',
            'fi',
            'echo ""',
        ]
    lines += [
        'echo "=== Results: $PASS passed, $FAIL failed (of $TOTAL) ==="',
        "",
        'if [ "$TOTAL" -gt 0 ]; then',
        '    python3 -c "print($PASS / $TOTAL)" > /logs/verifier/reward.txt 2>/dev/null \\',
        "        || echo 0 > /logs/verifier/reward.txt",
        "else",
        "    echo 0 > /logs/verifier/reward.txt",
        "fi",
        "",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"


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
    mode_spec = MODES[mode]
    env_profile = PROFILES[profile].get("derives_from", profile)

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
        "REPO=/home/ubuntu/video-search-and-summarization",
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
    mode_spec = MODES[mode]

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
    meta_lines.append("")
    (task_dir / "task.toml").write_text("\n".join(meta_lines))

    # -- environment/ placeholder (not used with BrevEnvironment) --
    env_dir = task_dir / "environment"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "Dockerfile").write_text("FROM scratch\n")

    # -- tests/test.sh (+ E2E helper for `base` and `base-debug`) --
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test.sh").write_text(
        generate_test_script(profile, profile_def, platform, mode),
    )
    underlying = profile_def.get("derives_from", profile)
    if underlying == "base" and skill_dir:
        # Canonical source lives in the /deploy skill: skills/deploy/scripts/.
        script_src = skill_dir / "scripts" / "test_base.py"
        if script_src.exists():
            shutil.copy(script_src, tests_dir / "test_base.py")

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


def expand_matrix(
    profile_filter: str | None,
    platform_filter: str | None,
    mode_filter: str | None,
    have_llm_remote: bool,
    have_vlm_remote: bool,
    have_ngc_key: bool,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str, str]]]:
    """Return (included, skipped) where:
        included = list of (profile, platform, mode) that will be generated
        skipped  = list of (profile, platform, mode, reason)
    Filters applied: --profile/--platform/--mode + per-platform supported_modes
    + remote URL availability for modes that need them
    + NGC_CLI_API_KEY availability for modes that pull local NIMs."""
    included: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str, str, str]] = []
    for profile in PROFILES:
        if profile_filter and profile != profile_filter:
            continue
        for platform, pspec in PLATFORMS.items():
            if platform_filter and platform != platform_filter:
                continue
            for mode in pspec["supported_modes"]:
                if mode_filter and mode != mode_filter:
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

    # Resolve remote endpoints (URL + model must be paired)
    llm_remote: dict | None = None
    if args.llm_remote_url:
        if not args.llm_remote_model:
            print("--llm-remote-url requires --llm-remote-model", file=sys.stderr)
            sys.exit(1)
        llm_remote = {"url": args.llm_remote_url, "model": args.llm_remote_model}
    vlm_remote: dict | None = None
    if args.vlm_remote_url:
        if not args.vlm_remote_model:
            print("--vlm-remote-url requires --vlm-remote-model", file=sys.stderr)
            sys.exit(1)
        vlm_remote = {"url": args.vlm_remote_url, "model": args.vlm_remote_model}

    have_ngc_key = args.assume_ngc_key or bool(os.environ.get("NGC_CLI_API_KEY"))

    # --- Inputs summary ---
    print("=== Inputs ===")
    print(f"  output_dir       : {output_root}")
    print(f"  skill_dir        : {skill_dir or '(none)'}")
    print(f"  filter profile   : {args.profile or '(all)'}")
    print(f"  filter platform  : {args.platform or '(all)'}")
    print(f"  filter mode      : {args.mode or '(all)'}")
    if llm_remote:
        print(f"  LLM remote       : {llm_remote['url']}  ({llm_remote['model']})")
    else:
        print(f"  LLM remote       : (not set — remote-* modes needing LLM will be skipped)")
    if vlm_remote:
        print(f"  VLM remote       : {vlm_remote['url']}  ({vlm_remote['model']})")
    else:
        print(f"  VLM remote       : (not set — remote-* modes needing VLM will be skipped)")
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
