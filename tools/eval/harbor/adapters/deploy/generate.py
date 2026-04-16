#!/usr/bin/env python3
"""Generate Harbor tasks for VSS deploy skill evaluation.

Generates one task per profile (base, alerts, lvs, search).  Each task
provisions a Brev GPU instance, then asks the agent to deploy that
profile using the /deploy skill.

For remote LLM/VLM modes the tasks point at model endpoints running on
the *generating host* (this machine), so the Brev instance calls back
over the network instead of pulling its own NIMs.  The generator auto-
detects local model ports by probing localhost for /v1/models.

Usage:
    # Generate all profiles (auto-detect local models)
    python generate.py --output-dir ../../datasets/deploy

    # Single profile
    python generate.py --output-dir ../../datasets/deploy --profile base

    # Explicit host IP + model ports
    python generate.py --output-dir ../../datasets/deploy \
        --host-ip 10.160.0.184 \
        --llm-port 31081 --vlm-port 31082

Run with Harbor:
    harbor run --env "tools.eval.harbor.envs.brev_env:BrevEnvironment" \
        -p tools/eval/harbor/datasets/deploy -a claude-code -n 1
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VSS_REPO_URL = "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git"
VSS_BRANCH = "feat/skills"

# Candidate ports to scan for local model endpoints
MODEL_CANDIDATE_PORTS = [
    8000, 8001, 8080, 8888,
    30081, 30082,
    31080, 31081, 31082, 31083,
    5000, 5001,
]

# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------

PROFILES: dict[str, dict] = {
    "base": {
        "bp_profile": "bp_developer_base",
        "mode": "2d",
        "description": "Deploy VSS base profile — agent, UI, VST, LLM/VLM NIMs",
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
        "bp_profile": "bp_developer_alerts",
        "mode": "2d_cv",
        "description": "Deploy VSS alerts profile — CV perception, alert verification, analytics",
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
        "bp_profile": "bp_developer_lvs",
        "mode": "2d",
        "description": "Deploy VSS LVS profile — long video summarization",
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
        "bp_profile": "bp_developer_search",
        "mode": "2d",
        "description": "Deploy VSS search profile — Cosmos Embed1 semantic video search",
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
# Local model discovery
# ---------------------------------------------------------------------------

def detect_host_ip() -> str | None:
    """Detect this host's IP reachable from external machines."""
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split()[0]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def probe_model_endpoint(port: int, host: str = "localhost") -> dict | None:
    """Probe a port for an OpenAI-compatible /v1/models endpoint.

    Returns {"port": int, "model_id": str} or None.
    """
    try:
        url = f"http://{host}:{port}/v1/models"
        with urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read())
            if isinstance(data, dict) and "data" in data and len(data["data"]) > 0:
                model_id = data["data"][0].get("id", "unknown")
                return {"port": port, "model_id": model_id}
    except (URLError, OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def discover_local_models(
    candidate_ports: list[int] | None = None,
) -> dict[str, dict]:
    """Scan localhost for LLM and VLM model endpoints.

    Returns {"llm": {"port": N, "model_id": "..."}, "vlm": {...}} with
    whatever was found.  Classification is by model name heuristic:
    cosmos/vision/vlm → VLM, everything else → LLM.
    """
    if candidate_ports is None:
        candidate_ports = MODEL_CANDIDATE_PORTS

    found: dict[str, dict] = {}

    print("Scanning localhost for model endpoints...")
    for port in candidate_ports:
        info = probe_model_endpoint(port)
        if info is None:
            continue

        model_id = info["model_id"].lower()
        is_vlm = any(kw in model_id for kw in ["cosmos", "vision", "vlm", "qwen3-vl", "reason"])
        role = "vlm" if is_vlm else "llm"

        # First match wins per role
        if role not in found:
            found[role] = info
            print(f"  Found {role.upper()}: port {port} → {info['model_id']}")

    return found


# ---------------------------------------------------------------------------
# Brev helpers
# ---------------------------------------------------------------------------

def query_brev_instances() -> list[dict]:
    """Query available Brev instances via `brev search --json`."""
    try:
        result = subprocess.run(
            ["brev", "search", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: could not query brev search: {e}")
    return []


def find_brev_instance_type(
    brev_instances: list[dict],
    gpu_name: str,
    gpu_count: int,
    min_vram: int,
) -> str | None:
    """Find the cheapest Brev instance type matching GPU requirements."""
    if not brev_instances:
        return None

    candidates = [
        inst for inst in brev_instances
        if gpu_name.lower() in inst["gpu_name"].lower()
        and inst["gpu_count"] >= gpu_count
        and inst["total_vram_gb"] >= min_vram * gpu_count
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda x: x["price_per_hour"])
    return candidates[0]["type"]


# ---------------------------------------------------------------------------
# Instruction generation
# ---------------------------------------------------------------------------

def generate_instruction(profile: str, profile_def: dict, host_ip: str | None, models: dict) -> str:
    """Generate the instruction.md for a deploy task.

    State the goal and relevant context only — the agent uses the
    `/deploy` skill (registered via task.toml skills_dir) to figure out
    the workflow.
    """
    lines = [
        f"Use the `/deploy` skill to deploy the VSS **{profile}** profile on this machine.",
        "",
        "## Target configuration",
        "",
        "- Hardware profile: `L40S`",
    ]

    if host_ip and "llm" in models:
        lines.append(
            f"- LLM: remote, running at `http://{host_ip}:{models['llm']['port']}/v1` "
            f"(model: `{models['llm']['model_id']}`)"
        )
    else:
        lines.append("- LLM: remote, via NVIDIA NIM API")

    if host_ip and "vlm" in models:
        lines.append(
            f"- VLM: remote, running at `http://{host_ip}:{models['vlm']['port']}/v1` "
            f"(model: `{models['vlm']['model_id']}`)"
        )
    else:
        lines.append("- VLM: remote, via NVIDIA NIM API")

    lines.extend([
        "",
        "## Credentials",
        "",
        "- `NGC_CLI_API_KEY` is available in the environment for NGC "
        "container registry authentication.",
        "",
        "## Success criteria",
        "",
        "Deployment is successful when the Agent API responds at "
        "`http://localhost:8000/docs` and core containers "
        "(`vss-agent`, `metropolis-vss-ui`, `mdx-redis`) are running.",
        "",
    ])

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Test script generation
# ---------------------------------------------------------------------------

def generate_test_script(profile: str, profile_def: dict, host_ip: str | None, models: dict) -> str:
    """Generate test.sh verifier for a deploy task.

    Full deployment validation: checks .env settings, running containers,
    and endpoint health.  Writes harbor reward to /logs/verifier/reward.txt.
    """
    containers = profile_def["expected_containers"]
    endpoints = profile_def["expected_endpoints"]

    container_checks = "\n".join(
        'check_container "' + c + '"' for c in containers
    )
    endpoint_checks = "\n".join(
        'check_endpoint ' + str(e["port"]) + ' "' + e["path"] + '" "' + e["name"] + '"'
        for e in endpoints
    )

    env_checks = [
        ("HARDWARE_PROFILE", "L40S"),
        ("LLM_MODE", "remote"),
        ("VLM_MODE", "remote"),
    ]
    validate_lines = "\n".join(
        'validate_env "' + k + '" "' + v + '"' for k, v in env_checks
    )

    lines = [
        "#!/bin/bash",
        "# Verifier for deploy profile: " + profile,
        "# Writes reward to /logs/verifier/reward.txt for harbor.",
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
        'ENV_FILE="$REPO/deployments/developer-workflow/dev-profile-' + profile + '/.env"',
        "",
        "# --- Validate .env settings ---",
        'echo "=== Checking .env configuration ==="',
        validate_lines,
        "",
        "# --- Check containers ---",
        'echo ""',
        'echo "=== Checking containers ==="',
        container_checks,
        "",
        "# --- Check endpoints ---",
        'echo ""',
        'echo "=== Checking endpoints ==="',
        endpoint_checks,
        "",
        "# --- Write reward ---",
        'echo ""',
        'echo "=== Results: $PASS passed, $FAIL failed (of $TOTAL) ==="',
        "",
        "# Reward is fraction of checks passed",
        'if [ "$TOTAL" -gt 0 ]; then',
        "    python3 -c \"print($PASS / $TOTAL)\" > /logs/verifier/reward.txt 2>/dev/null \\",
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

def generate_solve_script(profile: str, profile_def: dict, host_ip: str | None, models: dict) -> str:
    """Generate gold solution solve.sh for a deploy task (compose-only)."""
    overrides: dict[str, str] = {
        "HARDWARE_PROFILE": "L40S",
        "MDX_SAMPLE_APPS_DIR": "$REPO/deployments",
        "MDX_DATA_DIR": "$REPO/data",
        "HOST_IP": "$(hostname -I | awk '{print $1}')",
        "LLM_MODE": "remote",
        "VLM_MODE": "remote",
    }

    if host_ip and "llm" in models:
        overrides["LLM_BASE_URL"] = f"http://{host_ip}:{models['llm']['port']}/v1"
    else:
        overrides["LLM_BASE_URL"] = "https://integrate.api.nvidia.com/v1"

    if host_ip and "vlm" in models:
        overrides["VLM_BASE_URL"] = f"http://{host_ip}:{models['vlm']['port']}/v1"
    else:
        overrides["VLM_BASE_URL"] = "https://integrate.api.nvidia.com/v1"

    sed_lines = "\n".join(
        'sed -i "s|^' + k + "=.*|" + k + "=" + v + '|" "$ENV_FILE"'
        for k, v in overrides.items()
    )

    lines = [
        "#!/bin/bash",
        "# Gold solution: configure " + profile + " profile (compose-only)",
        "set -euo pipefail",
        "",
        "REPO=/home/ubuntu/video-search-and-summarization",
        "",
        "# === 1. Prerequisites ===",
        "",
        "if ! command -v docker &>/dev/null; then",
        "    curl -fsSL https://get.docker.com | sh",
        "fi",
        "",
        "sudo sysctl -w vm.max_map_count=262144 2>/dev/null || true",
        "sudo sysctl -w net.core.rmem_max=5242880 2>/dev/null || true",
        "sudo sysctl -w net.core.wmem_max=5242880 2>/dev/null || true",
        "",
        "# NGC login",
        'if [ -n "${NGC_CLI_API_KEY:-}" ]; then',
        "    docker login nvcr.io -u '\\$oauthtoken' -p \"$NGC_CLI_API_KEY\" 2>/dev/null || true",
        "fi",
        "",
        "# === 2. Clone repo ===",
        "",
        'if [ ! -d "$REPO" ]; then',
        "    git clone --branch " + VSS_BRANCH + " " + VSS_REPO_URL + ' "$REPO"',
        "fi",
        'mkdir -p "$REPO/data"',
        "",
        "# === 3. Configure .env ===",
        "",
        "PROFILE=" + profile,
        "ENV_FILE=$REPO/deployments/developer-workflow/dev-profile-$PROFILE/.env",
        "",
        sed_lines,
        "",
        '# Set NGC key in .env',
        'if [ -n "${NGC_CLI_API_KEY:-}" ]; then',
        '    sed -i "s|^NGC_CLI_API_KEY=.*|NGC_CLI_API_KEY=$NGC_CLI_API_KEY|" "$ENV_FILE"',
        "fi",
        "",
        "# === 4. Generate resolved compose + deploy ===",
        "",
        "cd $REPO/deployments",
        "docker compose --env-file $ENV_FILE config 2>/dev/null > resolved.yml",
        "docker compose -f resolved.yml up -d",
        "",
        "# === 5. Wait for healthy ===",
        "",
        'echo "Waiting for Agent API..."',
        "for i in $(seq 1 90); do",
        "    if curl -sf -o /dev/null --max-time 5 http://localhost:8000/docs 2>/dev/null; then",
        '        echo "Agent API up after $((i*10))s"',
        "        break",
        "    fi",
        "    sleep 10",
        "done",
    ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_task(
    profile: str,
    profile_def: dict,
    output_dir: Path,
    skill_dir: Path | None,
    brev_instance_type: str,
    host_ip: str | None,
    models: dict,
) -> None:
    """Generate a single Harbor task directory for one profile."""
    task_id = profile
    task_dir = output_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # -- instruction.md --
    instruction = generate_instruction(profile, profile_def, host_ip, models)
    (task_dir / "instruction.md").write_text(instruction)

    # -- task.toml --
    meta_lines = [
        "[task]",
        f'name = "nvidia-vss/deploy-{profile}"',
        f'description = "{profile_def["description"]}"',
        f'keywords = ["deploy", "{profile}"]',
        "",
        "[environment]",
        '# Harbor copies this directory into $CLAUDE_CONFIG_DIR/skills/ before the',
        '# agent runs so it can invoke the /deploy skill.',
        'skills_dir = "/skills"',
        "",
        "[metadata]",
        'gpu = "L40S"',
        f'brev_instance_type = "{brev_instance_type}"',
        f'profile = "{profile}"',
    ]
    if host_ip and "llm" in models:
        meta_lines.append(f'llm_host = "{host_ip}:{models["llm"]["port"]}"')
    if host_ip and "vlm" in models:
        meta_lines.append(f'vlm_host = "{host_ip}:{models["vlm"]["port"]}"')
    (task_dir / "task.toml").write_text("\n".join(meta_lines) + "\n")

    # -- environment/ --
    env_dir = task_dir / "environment"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "Dockerfile").write_text("FROM scratch\n")

    # -- tests/test.sh --
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test.sh").write_text(
        generate_test_script(profile, profile_def, host_ip, models),
    )

    # -- solution/solve.sh --
    solution_dir = task_dir / "solution"
    solution_dir.mkdir(exist_ok=True)
    (solution_dir / "solve.sh").write_text(
        generate_solve_script(profile, profile_def, host_ip, models),
    )

    # -- Copy deploy skill into task --
    if skill_dir and skill_dir.exists():
        skill_dest = task_dir / "skills" / "deploy"
        if skill_dest.exists():
            shutil.rmtree(skill_dest)
        shutil.copytree(skill_dir, skill_dest)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Harbor tasks for VSS deploy skill evaluation",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Output directory for generated task datasets",
    )
    parser.add_argument(
        "--profile", default=None, choices=list(PROFILES.keys()),
        help="Generate only for this profile (default: all)",
    )
    parser.add_argument(
        "--skill-dir", default=None,
        help="Path to skills/deploy directory to copy into tasks",
    )
    parser.add_argument(
        "--brev-instance-type", default=None,
        help="Brev instance type override (skips 'brev search')",
    )
    parser.add_argument(
        "--host-ip", default=None,
        help="IP of this host reachable from Brev instances "
             "(auto-detected if omitted)",
    )
    parser.add_argument(
        "--llm-port", type=int, default=None,
        help="Port of the local LLM endpoint (skips auto-scan)",
    )
    parser.add_argument(
        "--vlm-port", type=int, default=None,
        help="Port of the local VLM endpoint (skips auto-scan)",
    )
    parser.add_argument(
        "--no-remote-models", action="store_true",
        help="Skip model discovery — generate tasks for local NIM deployment",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    skill_dir = Path(args.skill_dir) if args.skill_dir else None

    # --- Resolve Brev instance type ---
    brev_type = args.brev_instance_type or os.environ.get("BREV_INSTANCE_TYPE")

    if brev_type:
        print(f"  Using Brev instance type: {brev_type}")
    else:
        print("Querying Brev for available instance types...")
        brev_instances = query_brev_instances()
        if brev_instances:
            brev_type = find_brev_instance_type(
                brev_instances,
                gpu_name="L40S",
                gpu_count=2,
                min_vram=48,
            )
        if brev_type:
            print(f"  Resolved Brev instance type: {brev_type}")
        else:
            print(
                "ERROR: Could not resolve a Brev instance type.\n"
                "  - Make sure 'brev' CLI is installed and authenticated, or\n"
                "  - Pass --brev-instance-type <type>, or\n"
                "  - Set BREV_INSTANCE_TYPE env var.",
                file=sys.stderr,
            )
            sys.exit(1)

    # --- Discover local model endpoints ---
    host_ip: str | None = None
    models: dict[str, dict] = {}

    if not args.no_remote_models:
        host_ip = args.host_ip or detect_host_ip()
        if host_ip:
            print(f"  Host IP: {host_ip}")
        else:
            print("  WARNING: Could not detect host IP — remote model mode disabled")

        if host_ip:
            if args.llm_port or args.vlm_port:
                # Explicit ports — probe them directly
                if args.llm_port:
                    info = probe_model_endpoint(args.llm_port)
                    if info:
                        models["llm"] = info
                        print(f"  LLM: port {args.llm_port} → {info['model_id']}")
                    else:
                        print(f"  WARNING: --llm-port {args.llm_port} "
                              "did not respond to /v1/models")
                if args.vlm_port:
                    info = probe_model_endpoint(args.vlm_port)
                    if info:
                        models["vlm"] = info
                        print(f"  VLM: port {args.vlm_port} → {info['model_id']}")
                    else:
                        print(f"  WARNING: --vlm-port {args.vlm_port} "
                              "did not respond to /v1/models")
            else:
                # Auto-scan
                models = discover_local_models()

        if not models:
            print("  No local model endpoints found — tasks will use local NIM deployment")
            host_ip = None
    else:
        print("  Skipping model discovery (--no-remote-models)")

    # --- Select profiles ---
    if args.profile:
        selected = {args.profile: PROFILES[args.profile]}
    else:
        selected = PROFILES

    # --- Generate tasks ---
    for profile_name, profile_def in selected.items():
        print(f"  Generating task: {profile_name}")
        generate_task(
            profile_name, profile_def, output_dir, skill_dir,
            brev_type, host_ip, models,
        )

    # --- Summary ---
    print(f"\nGenerated {len(selected)} deploy task(s) in {output_dir}")
    print()
    print("Profiles:")
    for name, pdef in selected.items():
        print(f"  {name:10s}  {pdef['description']}")

    if host_ip and models:
        print()
        print("Remote model endpoints (baked into tasks):")
        for role, info in models.items():
            print(f"  {role.upper():4s}  http://{host_ip}:{info['port']}/v1  "
                  f"({info['model_id']})")

    print()
    print("Run with Harbor:")
    print(f"  harbor run --env 'tools.eval.harbor.envs.brev_env:BrevEnvironment' \\")
    print(f"    -p {output_dir} -a claude-code -n 1")

    if args.profile:
        print()
        print(f"Run single profile '{args.profile}':")
        print(f"  harbor run --env 'tools.eval.harbor.envs.brev_env:BrevEnvironment' \\")
        print(f"    -p {output_dir} -i '{args.profile}' -a claude-code -n 1")


if __name__ == "__main__":
    main()
