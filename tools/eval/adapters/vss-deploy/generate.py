#!/usr/bin/env python3
"""Generate Harbor tasks for VSS deploy skill evaluation.

Each task provisions a real Brev GPU instance, deploys a VSS profile,
verifies containers and endpoints are healthy, then tears down.

Usage:
    python generate.py --output-dir ../../datasets/vss-deploy
    python generate.py --output-dir ../../datasets/vss-deploy-skill --skill deploy --skill-dir ../../../skills/deploy

Run with Harbor:
    harbor run --env "tools.eval.harbor.brev_env:BrevEnvironment" \
        -p tools/eval/datasets/vss-deploy -a claude-code -n 1 -l 5
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

SCENARIOS = [
    {
        "id": "base-spark-shared",
        "profile": "base",
        "hardware": "DGX-SPARK",
        "llm_mode": "local_shared",
        "vlm_mode": "local_shared",
        "gpu": "GB10",
        "brev_instance_type": "nvidia-dgx-spark",
        "description": "Base profile on DGX Spark with shared GPU (default config)",
        "instruction": (
            "Deploy the VSS base profile on this machine.\n"
            "The GPU is a DGX Spark (GB10). Use the default shared GPU mode.\n"
            "NGC_CLI_API_KEY is already set in the environment.\n"
            "The VSS repo is cloned at /home/ubuntu/video-search-and-summarization.\n"
        ),
        "expected_containers": [
            "mdx-vss-agent",
            "mdx-vss-ui",
            "mdx-elasticsearch",
            "mdx-kafka",
            "mdx-redis",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
            {"port": 3000, "path": "/", "name": "Agent UI"},
        ],
    },
    {
        "id": "base-h100-remote-llm",
        "profile": "base",
        "hardware": "H100",
        "llm_mode": "remote",
        "vlm_mode": "local_shared",
        "gpu": "H100",
        "brev_instance_type": "p5.48xlarge",
        "description": "Base profile on H100 with remote LLM via NVIDIA API",
        "instruction": (
            "Deploy the VSS base profile on this machine.\n"
            "The GPU is an H100. Use remote LLM via NVIDIA API (https://integrate.api.nvidia.com/v1).\n"
            "Keep VLM local (shared mode).\n"
            "NGC_CLI_API_KEY and NVIDIA_API_KEY are set in the environment.\n"
            "The VSS repo is cloned at /home/ubuntu/video-search-and-summarization.\n"
        ),
        "expected_containers": [
            "mdx-vss-agent",
            "mdx-vss-ui",
            "mdx-elasticsearch",
            "mdx-kafka",
            "mdx-redis",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
            {"port": 3000, "path": "/", "name": "Agent UI"},
        ],
    },
    {
        "id": "base-h100-dedicated-gpu",
        "profile": "base",
        "hardware": "H100",
        "llm_mode": "local",
        "vlm_mode": "local",
        "gpu": "H100",
        "brev_instance_type": "p5.48xlarge",
        "description": "Base profile on H100 with dedicated GPUs (LLM on 0, VLM on 1)",
        "instruction": (
            "Deploy the VSS base profile on this machine.\n"
            "The GPU is an H100. Use dedicated GPUs: LLM on device 0, VLM on device 1.\n"
            "NGC_CLI_API_KEY is set in the environment.\n"
            "The VSS repo is cloned at /home/ubuntu/video-search-and-summarization.\n"
        ),
        "expected_containers": [
            "mdx-vss-agent",
            "mdx-vss-ui",
            "mdx-elasticsearch",
            "mdx-kafka",
            "mdx-redis",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
            {"port": 3000, "path": "/", "name": "Agent UI"},
        ],
    },
    {
        "id": "base-remote-all",
        "profile": "base",
        "hardware": "DGX-SPARK",
        "llm_mode": "remote",
        "vlm_mode": "remote",
        "gpu": "none",
        "brev_instance_type": "c5.2xlarge",
        "description": "Base profile with both LLM and VLM remote (no local GPU for inference)",
        "instruction": (
            "Deploy the VSS base profile on this machine.\n"
            "Use remote LLM and remote VLM via NVIDIA API (https://integrate.api.nvidia.com/v1).\n"
            "NVIDIA_API_KEY is set in the environment.\n"
            "The VSS repo is cloned at /home/ubuntu/video-search-and-summarization.\n"
        ),
        "expected_containers": [
            "mdx-vss-agent",
            "mdx-vss-ui",
            "mdx-elasticsearch",
            "mdx-kafka",
            "mdx-redis",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
            {"port": 3000, "path": "/", "name": "Agent UI"},
        ],
    },
]


def generate_task(scenario: dict, output_dir: Path, skill_name: str | None, skill_dir: Path | None) -> None:
    """Generate a single Harbor task directory from a scenario."""
    task_dir = output_dir / scenario["id"]
    task_dir.mkdir(parents=True, exist_ok=True)

    # -- instruction.md --
    instruction = scenario["instruction"]
    if skill_name:
        instruction = f"Use your /{skill_name} skill to complete this task.\n\n{instruction}"
    instruction += (
        "\nAfter deployment, verify that all containers are running and the Agent API "
        "and UI endpoints respond. Then tear down the deployment with docker compose down.\n"
    )
    (task_dir / "instruction.md").write_text(instruction)

    # -- task.toml --
    task_toml = (
        f'[task]\n'
        f'id = "{scenario["id"]}"\n'
        f'difficulty = "medium"\n'
        f'tags = ["deploy", "{scenario["profile"]}", "{scenario["llm_mode"]}"]\n'
        f'\n'
        f'[metadata]\n'
        f'gpu = "{scenario["gpu"]}"\n'
        f'brev_instance_type = "{scenario["brev_instance_type"]}"\n'
    )
    (task_dir / "task.toml").write_text(task_toml)

    # -- environment/Dockerfile --
    env_dir = task_dir / "environment"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "Dockerfile").write_text(DOCKERFILE_TEMPLATE)

    # -- environment/setup.sh (runs on Brev instance after boot) --
    (env_dir / "setup.sh").write_text(SETUP_SCRIPT)

    # -- tests/test.sh --
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test.sh").write_text(
        generate_test_script(scenario["expected_containers"], scenario["expected_endpoints"])
    )

    # -- solution/solve.sh --
    solution_dir = task_dir / "solution"
    solution_dir.mkdir(exist_ok=True)
    (solution_dir / "solve.sh").write_text(
        generate_solve_script(scenario)
    )

    # -- Copy skill into task if requested --
    if skill_dir and skill_dir.exists():
        skill_dest = task_dir / "environment" / "skills" / (skill_name or "deploy")
        if skill_dest.exists():
            shutil.rmtree(skill_dest)
        shutil.copytree(skill_dir, skill_dest)


def generate_test_script(expected_containers: list[str], expected_endpoints: list[dict]) -> str:
    """Generate the verifier script that checks deployment health."""
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

# Teardown
echo ""
echo "=== Tearing down ==="
REPO=/home/ubuntu/video-search-and-summarization
if [ -f "$REPO/deployments/resolved.yml" ]; then
    cd "$REPO/deployments"
    docker compose -f resolved.yml down --timeout 30 2>/dev/null || true
fi

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
"""


def generate_solve_script(scenario: dict) -> str:
    """Generate the gold solution (oracle) for a scenario."""
    overrides = {
        "HARDWARE_PROFILE": scenario["hardware"],
        "MDX_SAMPLE_APPS_DIR": "/home/ubuntu/video-search-and-summarization/deployments",
        "MDX_DATA_DIR": "/home/ubuntu/video-search-and-summarization/data",
        "HOST_IP": "$(hostname -I | awk '{print $1}')",
    }

    if scenario["llm_mode"] == "remote":
        overrides["LLM_MODE"] = "remote"
        overrides["LLM_BASE_URL"] = "https://integrate.api.nvidia.com/v1"
    if scenario["vlm_mode"] == "remote":
        overrides["VLM_MODE"] = "remote"
        overrides["VLM_BASE_URL"] = "https://integrate.api.nvidia.com/v1"
    if scenario["llm_mode"] == "local":
        overrides["LLM_MODE"] = "local"
        overrides["LLM_DEVICE_ID"] = "0"
    if scenario["vlm_mode"] == "local":
        overrides["VLM_MODE"] = "local"
        overrides["VLM_DEVICE_ID"] = "1"

    sed_commands = "\n".join(
        f'sed -i "s|^{k}=.*|{k}={v}|" "$ENV_FILE"'
        for k, v in overrides.items()
    )

    return f"""#!/bin/bash
# Gold solution: deploy {scenario["profile"]} profile with {scenario["llm_mode"]} LLM.
set -euo pipefail

REPO=/home/ubuntu/video-search-and-summarization
PROFILE={scenario["profile"]}
ENV_FILE=$REPO/deployments/developer-workflow/dev-profile-$PROFILE/.env

# Setup
cd $REPO
bash tools/eval/adapters/vss-deploy/environment/setup.sh 2>/dev/null || true

# Apply env overrides
{sed_commands}

# Resolve compose
cd $REPO/deployments
docker compose --env-file $ENV_FILE config > resolved.yml

# Deploy
docker compose -f resolved.yml up -d --force-recreate

# Wait for containers to be healthy (up to 10 min)
echo "Waiting for containers..."
for i in $(seq 1 60); do
    if curl -sf -o /dev/null --max-time 5 http://localhost:8000/docs 2>/dev/null; then
        echo "Agent API is up after $((i*10))s"
        break
    fi
    sleep 10
done
"""


# -- Templates --

DOCKERFILE_TEMPLATE = """\
# This Dockerfile is used when Harbor runs in local Docker mode.
# When using BrevEnvironment, the Brev instance IS the environment
# and setup.sh runs directly on it instead.
FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \\
    curl git jq python3 python3-pip \\
    && rm -rf /var/lib/apt/lists/*

COPY setup.sh /setup.sh
RUN chmod +x /setup.sh
"""

SETUP_SCRIPT = """\
#!/bin/bash
# Setup script — runs on the Brev instance to prepare for VSS deployment.
# Idempotent: safe to run multiple times.
set -euo pipefail

REPO_DIR=/home/ubuntu/video-search-and-summarization
REPO_URL=https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
BRANCH=feat/skills

# Clone VSS repo if not present
if [ ! -d "$REPO_DIR" ]; then
    echo "Cloning VSS repo..."
    git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

# Ensure Docker is installed
if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
fi

# Ensure NVIDIA Container Toolkit
if ! docker info 2>/dev/null | grep -q nvidia; then
    echo "Installing NVIDIA Container Toolkit..."
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \\
        | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \\
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \\
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
fi

# Load GPU modules if needed
if ! nvidia-smi &>/dev/null; then
    sudo modprobe nvidia 2>/dev/null || true
    sudo modprobe nvidia_uvm 2>/dev/null || true
fi

# Kernel settings for Elasticsearch/Kafka
sudo sysctl -w vm.max_map_count=262144 2>/dev/null || true
sudo sysctl -w net.core.rmem_max=5242880 2>/dev/null || true
sudo sysctl -w net.core.wmem_max=5242880 2>/dev/null || true

# Create data directory
mkdir -p "$REPO_DIR/data"

echo "Setup complete."
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null || echo "No GPU detected"
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Harbor tasks for VSS deploy eval")
    parser.add_argument("--output-dir", required=True, help="Output directory for generated tasks")
    parser.add_argument("--skill", default=None, help="Skill name to inject (e.g. 'deploy')")
    parser.add_argument("--skill-dir", default=None, help="Path to skill directory to copy into tasks")
    parser.add_argument("--limit", type=int, default=None, help="Max number of tasks to generate")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    skill_dir = Path(args.skill_dir) if args.skill_dir else None

    scenarios = SCENARIOS[: args.limit] if args.limit else SCENARIOS

    for scenario in scenarios:
        print(f"Generating task: {scenario['id']}")
        generate_task(scenario, output_dir, args.skill, skill_dir)

    print(f"\nGenerated {len(scenarios)} tasks in {output_dir}")
    print(f"\nRun with:")
    print(f"  harbor run --env 'tools.eval.harbor.brev_env:BrevEnvironment' \\")
    print(f"    -p {output_dir} -a claude-code -n 1 -l {len(scenarios)}")


if __name__ == "__main__":
    main()
