#!/usr/bin/env python3
"""Generate Harbor tasks for VSS deploy skill evaluation.

Each task provisions a bare Brev GPU instance. The agent must handle
everything: install prerequisites, clone the repo, configure .env,
deploy VSS, and verify it works. The verifier then checks containers
and endpoints independently.

Usage:
    python generate.py --output-dir ../../datasets/vss-deploy
    python generate.py --output-dir ../../datasets/vss-deploy-skill \
        --skill deploy --skill-dir ../../../../skills/deploy
    python generate.py --output-dir ../../datasets/vss-deploy \
        --hardware H100 --mode remote-llm

Run with Harbor + Brev:
    harbor run --env "tools.eval.harbor.envs.brev_env:BrevEnvironment" \
        -p tools/eval/harbor/datasets/vss-deploy -a claude-code -n 1 -l 5
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform matrix — from https://docs.nvidia.com/vss/3.1.0/quickstart.html
# ---------------------------------------------------------------------------

PLATFORMS = {
    "H100": {
        "hardware": "H100",
        "gpu_label": "H100",
        "modes": [
            {"id": "shared",      "llm": "local_shared", "vlm": "local_shared", "gpus": 1, "brev": "p5.48xlarge"},
            {"id": "dedicated",   "llm": "local",        "vlm": "local",        "gpus": 2, "brev": "p5.48xlarge"},
            {"id": "remote-llm",  "llm": "remote",       "vlm": "local_shared", "gpus": 1, "brev": "p5.48xlarge"},
            {"id": "remote-vlm",  "llm": "local_shared", "vlm": "remote",       "gpus": 1, "brev": "p5.48xlarge"},
            {"id": "remote-all",  "llm": "remote",       "vlm": "remote",       "gpus": 0, "brev": "p5.48xlarge"},
        ],
    },
    "RTXPRO6000BW": {
        "hardware": "RTXPRO6000BW",
        "gpu_label": "RTX PRO 6000",
        "modes": [
            {"id": "shared",      "llm": "local_shared", "vlm": "local_shared", "gpus": 1, "brev": "g6e.2xlarge"},
            {"id": "dedicated",   "llm": "local",        "vlm": "local",        "gpus": 2, "brev": "g6e.4xlarge"},
            {"id": "remote-llm",  "llm": "remote",       "vlm": "local_shared", "gpus": 1, "brev": "g6e.2xlarge"},
            {"id": "remote-vlm",  "llm": "local_shared", "vlm": "remote",       "gpus": 1, "brev": "g6e.2xlarge"},
            {"id": "remote-all",  "llm": "remote",       "vlm": "remote",       "gpus": 0, "brev": "c5.2xlarge"},
        ],
    },
    "L40S": {
        "hardware": "L40S",
        "gpu_label": "L40S",
        "modes": [
            # No shared — L40S requires dedicated or remote
            {"id": "dedicated",   "llm": "local",        "vlm": "local",  "gpus": 2, "brev": "g6e.4xlarge"},
            {"id": "remote-llm",  "llm": "remote",       "vlm": "local",  "gpus": 1, "brev": "g6e.xlarge"},
            {"id": "remote-vlm",  "llm": "local",        "vlm": "remote", "gpus": 1, "brev": "g6e.xlarge"},
            {"id": "remote-all",  "llm": "remote",       "vlm": "remote", "gpus": 0, "brev": "c5.2xlarge"},
        ],
    },
    "DGX-SPARK": {
        "hardware": "DGX-SPARK",
        "gpu_label": "GB10",
        "modes": [
            {"id": "remote-llm",  "llm": "remote", "vlm": "local_shared", "gpus": 1, "brev": "nvidia-dgx-spark"},
        ],
    },
    "IGX-THOR": {
        "hardware": "IGX-THOR",
        "gpu_label": "IGX",
        "modes": [
            {"id": "remote-llm",  "llm": "remote", "vlm": "local_shared", "gpus": 1, "brev": "nvidia-igx"},
        ],
    },
    "AGX-THOR": {
        "hardware": "AGX-THOR",
        "gpu_label": "AGX",
        "modes": [
            {"id": "remote-llm",  "llm": "remote", "vlm": "local_shared", "gpus": 1, "brev": "nvidia-agx"},
        ],
    },
}

# Base profile expected containers and endpoints
BASE_EXPECTED_CONTAINERS = [
    "mdx-vss-agent",
    "mdx-vss-ui",
    "mdx-elasticsearch",
    "mdx-kafka",
    "mdx-redis",
]

BASE_EXPECTED_ENDPOINTS = [
    {"port": 8000, "path": "/docs", "name": "Agent API"},
    {"port": 3000, "path": "/", "name": "Agent UI"},
]

# Mode descriptions for instructions
MODE_DESCRIPTIONS = {
    "shared":     "Use shared GPU mode (LLM and VLM on the same GPU).",
    "dedicated":  "Use dedicated GPUs: LLM on device 0, VLM on device 1.",
    "remote-llm": "Use remote LLM via NVIDIA API (https://integrate.api.nvidia.com/v1). Keep VLM local.",
    "remote-vlm": "Keep LLM local. Use remote VLM via NVIDIA API (https://integrate.api.nvidia.com/v1).",
    "remote-all": "Use remote LLM and remote VLM via NVIDIA API (https://integrate.api.nvidia.com/v1).",
}

VSS_REPO_URL = "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git"
VSS_BRANCH = "feat/skills"


def build_scenarios(
    hardware_filter: str | None = None,
    mode_filter: str | None = None,
) -> list[dict]:
    """Expand the platform matrix into individual scenarios."""
    scenarios = []

    for hw_key, platform in PLATFORMS.items():
        if hardware_filter and hw_key != hardware_filter:
            continue

        for mode in platform["modes"]:
            if mode_filter and mode["id"] != mode_filter:
                continue

            task_id = f"base-{hw_key.lower()}-{mode['id']}"

            instruction = (
                f"Deploy VSS base profile. {MODE_DESCRIPTIONS[mode['id']]}\n"
            )

            scenarios.append({
                "id": task_id,
                "profile": "base",
                "hardware": platform["hardware"],
                "llm_mode": mode["llm"],
                "vlm_mode": mode["vlm"],
                "gpus": mode["gpus"],
                "gpu": platform["gpu_label"],
                "brev_instance_type": mode["brev"],
                "description": f"Base profile on {platform['hardware']} — {mode['id']}",
                "instruction": instruction,
                "expected_containers": BASE_EXPECTED_CONTAINERS,
                "expected_endpoints": BASE_EXPECTED_ENDPOINTS,
            })

    return scenarios


def generate_task(
    scenario: dict,
    output_dir: Path,
    skill_name: str | None,
    skill_dir: Path | None,
) -> None:
    """Generate a single Harbor task directory from a scenario."""
    task_dir = output_dir / scenario["id"]
    task_dir.mkdir(parents=True, exist_ok=True)

    # -- instruction.md --
    instruction = scenario["instruction"]
    if skill_name:
        instruction = f"Use your /{skill_name} skill to complete this task.\n\n{instruction}"
    (task_dir / "instruction.md").write_text(instruction)

    # -- task.toml --
    task_toml = (
        f'[task]\n'
        f'id = "{scenario["id"]}"\n'
        f'difficulty = "medium"\n'
        f'tags = ["deploy", "{scenario["profile"]}", '
        f'"{scenario["hardware"]}", "{scenario["llm_mode"]}"]\n'
        f'\n'
        f'[metadata]\n'
        f'gpu = "{scenario["gpu"]}"\n'
        f'brev_instance_type = "{scenario["brev_instance_type"]}"\n'
    )
    (task_dir / "task.toml").write_text(task_toml)

    # -- tests/test.sh --
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test.sh").write_text(
        generate_test_script(scenario["expected_containers"], scenario["expected_endpoints"])
    )

    # -- solution/solve.sh --
    solution_dir = task_dir / "solution"
    solution_dir.mkdir(exist_ok=True)
    (solution_dir / "solve.sh").write_text(generate_solve_script(scenario))

    # -- Copy skill into task if requested --
    if skill_dir and skill_dir.exists():
        skill_dest = task_dir / "skills" / (skill_name or "deploy")
        if skill_dest.exists():
            shutil.rmtree(skill_dest)
        shutil.copytree(skill_dir, skill_dest)


def generate_test_script(
    expected_containers: list[str],
    expected_endpoints: list[dict],
) -> str:
    """Generate the verifier that checks deployment health."""
    container_checks = "\n".join(
        f'check_container "{c}"' for c in expected_containers
    )
    endpoint_checks = "\n".join(
        f'check_endpoint {e["port"]} "{e["path"]}" "{e["name"]}"' for e in expected_endpoints
    )

    return f"""#!/bin/bash
# Verifier: check that VSS containers are running and endpoints respond.
# Exit 0 = pass, exit 1 = fail.
set -euo pipefail

PASS=0
FAIL=0

check_container() {{
    local name=$1
    if docker ps --format '{{{{.Names}}}}' | grep -q "$name"; then
        echo "PASS: container $name is running"
        ((PASS++))
    else
        echo "FAIL: container $name not found"
        ((FAIL++))
    fi
}}

check_endpoint() {{
    local port=$1 path=$2 name=$3
    if curl -sf -o /dev/null --max-time 10 "http://localhost:${{port}}${{path}}"; then
        echo "PASS: $name (port $port) responds"
        ((PASS++))
    else
        echo "FAIL: $name (port $port) not responding"
        ((FAIL++))
    fi
}}

echo "=== Checking containers ==="
{container_checks}

echo ""
echo "=== Checking endpoints ==="
{endpoint_checks}

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
"""


def generate_solve_script(scenario: dict) -> str:
    """Generate the gold solution (full setup + deploy from bare instance)."""
    overrides = {
        "HARDWARE_PROFILE": scenario["hardware"],
        "MDX_SAMPLE_APPS_DIR": "$REPO/deployments",
        "MDX_DATA_DIR": "$REPO/data",
        "HOST_IP": "$(hostname -I | awk '{{print $1}}')",
    }

    if scenario["llm_mode"] == "remote":
        overrides["LLM_MODE"] = "remote"
        overrides["LLM_BASE_URL"] = "https://integrate.api.nvidia.com/v1"
    elif scenario["llm_mode"] == "local":
        overrides["LLM_MODE"] = "local"
        overrides["LLM_DEVICE_ID"] = "0"

    if scenario["vlm_mode"] == "remote":
        overrides["VLM_MODE"] = "remote"
        overrides["VLM_BASE_URL"] = "https://integrate.api.nvidia.com/v1"
    elif scenario["vlm_mode"] == "local":
        overrides["VLM_MODE"] = "local"
        overrides["VLM_DEVICE_ID"] = "1"

    sed_commands = "\n".join(
        f'sed -i "s|^{k}=.*|{k}={v}|" "$ENV_FILE"'
        for k, v in overrides.items()
    )

    return f"""#!/bin/bash
# Gold solution: setup bare instance + deploy {scenario["profile"]} on {scenario["hardware"]} ({scenario["llm_mode"]} LLM, {scenario["vlm_mode"]} VLM).
set -euo pipefail

REPO=/home/ubuntu/video-search-and-summarization

# === 1. Prerequisites ===

# Docker
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    newgrp docker
fi

# NVIDIA Container Toolkit
if ! docker info 2>/dev/null | grep -q nvidia; then
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \\
        | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \\
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \\
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
fi

# GPU modules
nvidia-smi &>/dev/null || {{ sudo modprobe nvidia; sudo modprobe nvidia_uvm; }}

# Kernel settings
sudo sysctl -w vm.max_map_count=262144
sudo sysctl -w net.core.rmem_max=5242880
sudo sysctl -w net.core.wmem_max=5242880

# === 2. Clone repo ===

if [ ! -d "$REPO" ]; then
    git clone --branch {VSS_BRANCH} {VSS_REPO_URL} "$REPO"
fi
mkdir -p "$REPO/data"

# === 3. Configure .env ===

PROFILE={scenario["profile"]}
ENV_FILE=$REPO/deployments/developer-workflow/dev-profile-$PROFILE/.env

{sed_commands}

# === 4. Resolve compose (dry-run) ===

cd $REPO/deployments
docker compose --env-file $ENV_FILE config > resolved.yml

# === 5. Deploy ===

docker compose -f resolved.yml up -d --force-recreate

# === 6. Wait for healthy ===

echo "Waiting for containers..."
for i in $(seq 1 90); do
    if curl -sf -o /dev/null --max-time 5 http://localhost:8000/docs 2>/dev/null; then
        echo "Agent API is up after $((i*10))s"
        break
    fi
    sleep 10
done
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Harbor tasks for VSS deploy eval")
    parser.add_argument("--output-dir", required=True, help="Output directory for generated tasks")
    parser.add_argument("--skill", default=None, help="Skill name to inject (e.g. 'deploy')")
    parser.add_argument("--skill-dir", default=None, help="Path to skill directory to copy into tasks")
    parser.add_argument("--hardware", default=None, choices=list(PLATFORMS.keys()),
                        help="Generate only for this hardware platform")
    parser.add_argument("--mode", default=None,
                        help="Generate only for this mode (shared, dedicated, remote-llm, remote-vlm, remote-all)")
    parser.add_argument("--limit", type=int, default=None, help="Max number of tasks to generate")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    skill_dir = Path(args.skill_dir) if args.skill_dir else None

    scenarios = build_scenarios(args.hardware, args.mode)
    if args.limit:
        scenarios = scenarios[: args.limit]

    if not scenarios:
        print(f"No scenarios match hardware={args.hardware} mode={args.mode}")
        return

    for scenario in scenarios:
        print(f"  {scenario['id']}")
        generate_task(scenario, output_dir, args.skill, skill_dir)

    print(f"\nGenerated {len(scenarios)} tasks in {output_dir}")
    print(f"\nPlatform coverage:")
    by_hw = {}
    for s in scenarios:
        by_hw.setdefault(s["hardware"], []).append(s["id"])
    for hw, ids in by_hw.items():
        print(f"  {hw}: {len(ids)} scenarios")

    print(f"\nRun with:")
    print(f"  harbor run --env 'tools.eval.harbor.envs.brev_env:BrevEnvironment' \\")
    print(f"    -p {output_dir} -a claude-code -n 1 -l {len(scenarios)}")


if __name__ == "__main__":
    main()
