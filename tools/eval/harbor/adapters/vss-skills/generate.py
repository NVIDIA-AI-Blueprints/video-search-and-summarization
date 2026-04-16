#!/usr/bin/env python3
"""Generate Harbor tasks for evaluating ALL VSS skills.

Generates one task per skill.  Each task provisions a Brev GPU instance,
deploys the required VSS profile, then asks the agent to exercise the
skill.  A test script verifies the expected API responses.

Skills and their required profiles:
    deploy              — (none, self-contained)
    alerts              — alerts
    sensor-ops          — base
    incident-report     — alerts
    video-analytics     — alerts
    video-search        — search
    video-summarization — lvs

Usage:
    # Generate tasks for all skills
    python generate.py --output-dir ../../datasets/vss-skills

    # Generate for a single skill
    python generate.py --output-dir ../../datasets/vss-skills --skill alerts

    # Point skills directory (copied into each task)
    python generate.py --output-dir ../../datasets/vss-skills \
        --skills-dir ../../../../skills

Run with Harbor (Brev):
    harbor run --env "tools.eval.harbor.envs.brev_env:BrevEnvironment" \
        -p tools/eval/harbor/datasets/vss-skills -a claude-code -n 1

Run with Harbor (Docker, compose-only validation — deploy skill only):
    harbor run -e docker \
        -p tools/eval/harbor/datasets/vss-skills -i "deploy-*" -a claude-code
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VSS_REPO_URL = "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git"
VSS_BRANCH = "feat/skills"

# Default Brev instance: L40S with 2 GPUs (enough for dedicated LLM+VLM)
DEFAULT_BREV_INSTANCE_TYPE = "g6e.2xlarge"
DEFAULT_GPU = "L40S"

# ---------------------------------------------------------------------------
# Skill definitions
# ---------------------------------------------------------------------------

SKILLS: dict[str, dict] = {
    "deploy": {
        "profile": "base",
        "description": "Deploy VSS base profile on L40S with dedicated GPUs",
        "instruction": (
            "Deploy the VSS base profile using the /deploy skill.\n"
            "Use dedicated GPU mode: LLM on device 0, VLM on device 1.\n"
            "Hardware profile: L40S.\n"
        ),
        "needs_deploy": False,  # deploy IS the skill under test
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
    "alerts": {
        "profile": "alerts",
        "description": "Manage and verify alerts via the alerts skill",
        "instruction": (
            "The alerts profile is already deployed.\n\n"
            "Use your /alerts skill to:\n"
            "1. Check the current alert status — list recent alerts.\n"
            "2. Submit a test behavior alert for sensor 'camera-01' with "
            "category 'collision' at place 'Loading Dock'.\n"
            "3. Query the verdict status of the submitted alert.\n"
        ),
        "needs_deploy": True,
        "expected_containers": [
            "mdx-vss-agent",
            "mdx-elasticsearch",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
        ],
    },
    "sensor-ops": {
        "profile": "base",
        "description": "Manage sensors via VIOS using the sensor-ops skill",
        "instruction": (
            "The base profile is already deployed.\n\n"
            "Use your /sensor-ops skill to:\n"
            "1. List all currently configured sensors.\n"
            "2. Check the status of all sensors.\n"
            "3. Add a test RTSP sensor with URL "
            "'rtsp://wowzaec2demo.streamlock.net/vod/mp4:BigBuckBunny_115k.mp4' "
            "and name 'test-sensor'.\n"
            "4. Verify the newly added sensor appears in the sensor list.\n"
        ),
        "needs_deploy": True,
        "expected_containers": [
            "mdx-vss-agent",
        ],
        "expected_endpoints": [
            {"port": 30888, "path": "/vst/api/v1/sensor/list", "name": "VIOS API"},
        ],
    },
    "incident-report": {
        "profile": "alerts",
        "description": "Generate incident reports using the incident-report skill",
        "instruction": (
            "The alerts profile is already deployed.\n\n"
            "Use your /incident-report skill to:\n"
            "1. Query recent incidents from Elasticsearch.\n"
            "2. Generate a narrative incident report summarizing the findings.\n"
            "If no incidents exist yet, report that and explain what data "
            "would be needed.\n"
        ),
        "needs_deploy": True,
        "expected_containers": [
            "mdx-vss-agent",
            "mdx-elasticsearch",
        ],
        "expected_endpoints": [
            {"port": 9901, "path": "/mcp", "name": "VA-MCP"},
        ],
    },
    "video-analytics": {
        "profile": "alerts",
        "description": "Query video analytics data via the video-analytics skill",
        "instruction": (
            "The alerts profile is already deployed.\n\n"
            "Use your /video-analytics skill to:\n"
            "1. Initialize a VA-MCP session on port 9901.\n"
            "2. List available sensor IDs.\n"
            "3. Query the 10 most recent incidents.\n"
            "4. Summarize the results.\n"
        ),
        "needs_deploy": True,
        "expected_containers": [
            "mdx-vss-agent",
            "mdx-elasticsearch",
        ],
        "expected_endpoints": [
            {"port": 9901, "path": "/mcp", "name": "VA-MCP"},
        ],
    },
    "video-search": {
        "profile": "search",
        "description": "Search video archives using the video-search skill",
        "instruction": (
            "The search profile is already deployed.\n\n"
            "Use your /video-search skill to:\n"
            "1. Search for 'people walking' across all video archives.\n"
            "2. Search for 'vehicles in parking lot'.\n"
            "3. Report the results including timestamps and similarity scores.\n"
            "If no video content is indexed yet, report that and explain "
            "what data would be needed.\n"
        ),
        "needs_deploy": True,
        "expected_containers": [
            "mdx-vss-agent",
            "mdx-elasticsearch",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
        ],
    },
    "video-summarization": {
        "profile": "lvs",
        "description": "Summarize long videos using the video-summarization skill",
        "instruction": (
            "The LVS profile is already deployed.\n\n"
            "Use your /video-summarization skill to:\n"
            "1. Check that the LVS service is available at port 8000.\n"
            "2. If video content is available, summarize it.\n"
            "3. If no content is available, explain how to upload a video "
            "via VIOS and trigger summarization.\n"
        ),
        "needs_deploy": True,
        "expected_containers": [
            "mdx-vss-agent",
        ],
        "expected_endpoints": [
            {"port": 8000, "path": "/docs", "name": "Agent API"},
        ],
    },
}


# ---------------------------------------------------------------------------
# Brev helpers (reused from vss-deploy generator)
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
# Test script generation
# ---------------------------------------------------------------------------

def generate_test_script(skill_name: str, skill_def: dict) -> str:
    """Generate the test.sh verifier for a skill task."""
    containers = skill_def["expected_containers"]
    endpoints = skill_def["expected_endpoints"]

    container_checks = "\n".join(
        'check_container "' + c + '"' for c in containers
    )
    endpoint_checks = "\n".join(
        'check_endpoint ' + str(e["port"]) + ' "' + e["path"] + '" "' + e["name"] + '"'
        for e in endpoints
    )

    # Skill-specific functional checks
    functional_checks = _get_functional_checks(skill_name)

    lines = [
        "#!/bin/bash",
        "# Verifier for skill: " + skill_name,
        "set -euo pipefail",
        "",
        "PASS=0",
        "FAIL=0",
        "",
        "check_container() {",
        "    local name=$1",
        "    if docker ps --format '{{.Names}}' | grep -q \"$name\"; then",
        '        echo "PASS: container $name is running"',
        "        ((PASS++))",
        "    else",
        '        echo "FAIL: container $name not found"',
        "        ((FAIL++))",
        "    fi",
        "}",
        "",
        "check_endpoint() {",
        "    local port=$1 path=$2 name=$3",
        '    if curl -sf -o /dev/null --max-time 10 "http://localhost:${port}${path}"; then',
        '        echo "PASS: $name (port $port) responds"',
        "        ((PASS++))",
        "    else",
        '        echo "FAIL: $name (port $port) not responding"',
        "        ((FAIL++))",
        "    fi",
        "}",
        "",
        'echo "=== Checking containers ==="',
        container_checks,
        "",
        'echo ""',
        'echo "=== Checking endpoints ==="',
        endpoint_checks,
        "",
        functional_checks,
        "",
        'echo ""',
        'echo "=== Results: $PASS passed, $FAIL failed ==="',
        "",
        'if [ "$FAIL" -gt 0 ]; then',
        "    exit 1",
        "fi",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"


def _get_functional_checks(skill_name: str) -> str:
    """Return skill-specific functional verification commands."""

    if skill_name == "deploy":
        return ""

    if skill_name == "alerts":
        return "\n".join([
            'echo ""',
            'echo "=== Checking alerts API ==="',
            "# Verify alerts endpoint accepts submissions",
            "RESP=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "
            "-X POST http://localhost:8000/api/v1/alerts "
            '-H "Content-Type: application/json" '
            """-d '{"sensorId":"test","timestamp":"2025-01-01T00:00:00Z","""
            """"end":"2025-01-01T00:01:00Z","category":"test","""
            """"place":{"name":"test"}}' 2>/dev/null || echo "000")""",
            'if [ "$RESP" = "202" ] || [ "$RESP" = "200" ]; then',
            '    echo "PASS: alerts endpoint accepts submissions (HTTP $RESP)"',
            "    ((PASS++))",
            "else",
            '    echo "FAIL: alerts endpoint returned HTTP $RESP (expected 200 or 202)"',
            "    ((FAIL++))",
            "fi",
        ])

    if skill_name == "sensor-ops":
        return "\n".join([
            'echo ""',
            'echo "=== Checking VIOS sensor API ==="',
            "RESP=$(curl -s --max-time 10 http://localhost:30888/vst/api/v1/sensor/list 2>/dev/null)",
            'if echo "$RESP" | jq . >/dev/null 2>&1; then',
            '    echo "PASS: VIOS sensor list returns valid JSON"',
            "    ((PASS++))",
            "else",
            '    echo "FAIL: VIOS sensor list did not return valid JSON"',
            "    ((FAIL++))",
            "fi",
        ])

    if skill_name == "incident-report":
        return "\n".join([
            'echo ""',
            'echo "=== Checking VA-MCP for incident queries ==="',
            "# Initialize MCP session",
            "SESSION_ID=$(curl -si --max-time 10 -X POST http://localhost:9901/mcp \\",
            '  -H "Content-Type: application/json" \\',
            '  -H "Accept: application/json, text/event-stream" \\',
            """  -d '{"jsonrpc":"2.0","method":"initialize","params":{"""
            """"protocolVersion":"2024-11-05","capabilities":{},"""
            """"clientInfo":{"name":"test","version":"1.0"}},"id":0}' \\""",
            """  2>/dev/null | grep -i "mcp-session-id" | awk '{print $2}' | tr -d '\\r')""",
            'if [ -n "$SESSION_ID" ]; then',
            '    echo "PASS: VA-MCP session initialized (ID: $SESSION_ID)"',
            "    ((PASS++))",
            "else",
            '    echo "FAIL: VA-MCP session initialization failed"',
            "    ((FAIL++))",
            "fi",
        ])

    if skill_name == "video-analytics":
        return "\n".join([
            'echo ""',
            'echo "=== Checking VA-MCP analytics ==="',
            "# Initialize MCP session",
            "SESSION_ID=$(curl -si --max-time 10 -X POST http://localhost:9901/mcp \\",
            '  -H "Content-Type: application/json" \\',
            '  -H "Accept: application/json, text/event-stream" \\',
            """  -d '{"jsonrpc":"2.0","method":"initialize","params":{"""
            """"protocolVersion":"2024-11-05","capabilities":{},"""
            """"clientInfo":{"name":"test","version":"1.0"}},"id":0}' \\""",
            """  2>/dev/null | grep -i "mcp-session-id" | awk '{print $2}' | tr -d '\\r')""",
            'if [ -n "$SESSION_ID" ]; then',
            '    echo "PASS: VA-MCP session initialized"',
            "    ((PASS++))",
            "",
            "    # Query sensor IDs",
            "    SENSORS=$(curl -s --max-time 10 -X POST http://localhost:9901/mcp \\",
            '      -H "Content-Type: application/json" \\',
            '      -H "Accept: application/json, text/event-stream" \\',
            '      -H "mcp-session-id: $SESSION_ID" \\',
            """      -d '{"jsonrpc":"2.0","method":"tools/call","params":{"""
            """"name":"video_analytics__get_sensor_ids","arguments":{}},"id":1}' \\""",
            """      2>/dev/null | grep '^data:' | head -1)""",
            '    if [ -n "$SENSORS" ]; then',
            '        echo "PASS: VA-MCP get_sensor_ids responded"',
            "        ((PASS++))",
            "    else",
            '        echo "FAIL: VA-MCP get_sensor_ids returned empty"',
            "        ((FAIL++))",
            "    fi",
            "else",
            '    echo "FAIL: VA-MCP session initialization failed"',
            "    ((FAIL++))",
            "fi",
        ])

    if skill_name == "video-search":
        return "\n".join([
            'echo ""',
            'echo "=== Checking search endpoint ==="',
            "RESP=$(curl -s --max-time 15 -X POST http://localhost:8000/generate \\",
            '  -H "Content-Type: application/json" \\',
            """  -d '{"input_message": "test search query"}' 2>/dev/null)""",
            'if [ -n "$RESP" ]; then',
            '    echo "PASS: search generate endpoint responded"',
            "    ((PASS++))",
            "else",
            '    echo "FAIL: search generate endpoint did not respond"',
            "    ((FAIL++))",
            "fi",
        ])

    if skill_name == "video-summarization":
        return "\n".join([
            'echo ""',
            'echo "=== Checking summarization endpoint ==="',
            "RESP=$(curl -s --max-time 15 -X POST http://localhost:8000/generate \\",
            '  -H "Content-Type: application/json" \\',
            """  -d '{"input_message": "list available videos"}' 2>/dev/null)""",
            'if [ -n "$RESP" ]; then',
            '    echo "PASS: LVS generate endpoint responded"',
            "    ((PASS++))",
            "else",
            '    echo "FAIL: LVS generate endpoint did not respond"',
            "    ((FAIL++))",
            "fi",
        ])

    return ""


# ---------------------------------------------------------------------------
# Solution script generation
# ---------------------------------------------------------------------------

def generate_solve_script(skill_name: str, skill_def: dict) -> str:
    """Generate the gold solution script for a skill task."""
    profile = skill_def["profile"]

    # Common preamble: clone repo + deploy the required profile
    preamble_lines = [
        "#!/bin/bash",
        "# Gold solution for skill: " + skill_name,
        "set -euo pipefail",
        "",
        "REPO=/home/ubuntu/video-search-and-summarization",
        "",
        "# === 1. Prerequisites ===",
        "",
        "if ! command -v docker &>/dev/null; then",
        "    curl -fsSL https://get.docker.com | sh",
        "    sudo usermod -aG docker $USER",
        "    sg docker -c 'docker ps' >/dev/null 2>&1 || true",
        "fi",
        "",
        "if ! docker info 2>/dev/null | grep -q nvidia; then",
        "    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \\",
        "        | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg",
        "    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \\",
        "        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \\",
        "        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list",
        "    sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit",
        "    sudo nvidia-ctk runtime configure --runtime=docker",
        "    sudo systemctl restart docker",
        "fi",
        "",
        "nvidia-smi &>/dev/null || { sudo modprobe nvidia; sudo modprobe nvidia_uvm; }",
        "",
        "sudo sysctl -w vm.max_map_count=262144",
        "sudo sysctl -w net.core.rmem_max=5242880",
        "sudo sysctl -w net.core.wmem_max=5242880",
        "",
        "# === 2. Clone repo ===",
        "",
        'if [ ! -d "$REPO" ]; then',
        "    git clone --branch " + VSS_BRANCH + " " + VSS_REPO_URL + ' "$REPO"',
        "fi",
        'mkdir -p "$REPO/data"',
    ]

    # Deploy section (for skills that need a running deployment)
    deploy_lines = []
    if skill_def["needs_deploy"]:
        deploy_lines = [
            "",
            "# === 3. Deploy " + profile + " profile ===",
            "",
            "PROFILE=" + profile,
            "ENV_FILE=$REPO/deployments/developer-workflow/dev-profile-$PROFILE/.env",
            "",
            '# Configure for L40S dedicated mode',
            'sed -i "s|^HARDWARE_PROFILE=.*|HARDWARE_PROFILE=L40S|" "$ENV_FILE"',
            'sed -i "s|^LLM_MODE=.*|LLM_MODE=local|" "$ENV_FILE"',
            'sed -i "s|^VLM_MODE=.*|VLM_MODE=local|" "$ENV_FILE"',
            'sed -i "s|^LLM_DEVICE_ID=.*|LLM_DEVICE_ID=0|" "$ENV_FILE"',
            'sed -i "s|^VLM_DEVICE_ID=.*|VLM_DEVICE_ID=1|" "$ENV_FILE"',
            'sed -i "s|^MDX_SAMPLE_APPS_DIR=.*|MDX_SAMPLE_APPS_DIR=$REPO/deployments|" "$ENV_FILE"',
            'sed -i "s|^MDX_DATA_DIR=.*|MDX_DATA_DIR=$REPO/data|" "$ENV_FILE"',
            """sed -i "s|^HOST_IP=.*|HOST_IP=$(hostname -I | awk '{print $1}')|" "$ENV_FILE\"""",
            "",
            'if [ -n "${NGC_CLI_API_KEY:-}" ]; then',
            '    sed -i "s|^NGC_CLI_API_KEY=.*|NGC_CLI_API_KEY=$NGC_CLI_API_KEY|" "$ENV_FILE"',
            "fi",
            'if [ -n "${NVIDIA_API_KEY:-}" ]; then',
            '    sed -i "s|^NVIDIA_API_KEY=.*|NVIDIA_API_KEY=$NVIDIA_API_KEY|" "$ENV_FILE"',
            "fi",
            "",
            "cd $REPO/deployments",
            "docker compose --env-file $ENV_FILE config > resolved.yml",
            "docker compose -f resolved.yml up -d --force-recreate",
            "",
            '# Wait for services',
            'echo "Waiting for containers..."',
            "for i in $(seq 1 90); do",
            "    if curl -sf -o /dev/null --max-time 5 http://localhost:8000/docs 2>/dev/null; then",
            '        echo "Agent API is up after $((i*10))s"',
            "        break",
            "    fi",
            "    sleep 10",
            "done",
        ]

    # Skill-specific exercise
    skill_lines = _get_skill_exercise(skill_name, skill_def)

    all_lines = preamble_lines + deploy_lines + [""] + skill_lines
    return "\n".join(all_lines) + "\n"


def _get_skill_exercise(skill_name: str, skill_def: dict) -> list[str]:
    """Return the skill-specific commands for the gold solution."""

    if skill_name == "deploy":
        return [
            "# === 3. Deploy base profile ===",
            "",
            "PROFILE=base",
            "ENV_FILE=$REPO/deployments/developer-workflow/dev-profile-$PROFILE/.env",
            "",
            'sed -i "s|^HARDWARE_PROFILE=.*|HARDWARE_PROFILE=L40S|" "$ENV_FILE"',
            'sed -i "s|^LLM_MODE=.*|LLM_MODE=local|" "$ENV_FILE"',
            'sed -i "s|^VLM_MODE=.*|VLM_MODE=local|" "$ENV_FILE"',
            'sed -i "s|^LLM_DEVICE_ID=.*|LLM_DEVICE_ID=0|" "$ENV_FILE"',
            'sed -i "s|^VLM_DEVICE_ID=.*|VLM_DEVICE_ID=1|" "$ENV_FILE"',
            'sed -i "s|^MDX_SAMPLE_APPS_DIR=.*|MDX_SAMPLE_APPS_DIR=$REPO/deployments|" "$ENV_FILE"',
            'sed -i "s|^MDX_DATA_DIR=.*|MDX_DATA_DIR=$REPO/data|" "$ENV_FILE"',
            """sed -i "s|^HOST_IP=.*|HOST_IP=$(hostname -I | awk '{print $1}')|" "$ENV_FILE\"""",
            "",
            'if [ -n "${NGC_CLI_API_KEY:-}" ]; then',
            '    sed -i "s|^NGC_CLI_API_KEY=.*|NGC_CLI_API_KEY=$NGC_CLI_API_KEY|" "$ENV_FILE"',
            "fi",
            'if [ -n "${NVIDIA_API_KEY:-}" ]; then',
            '    sed -i "s|^NVIDIA_API_KEY=.*|NVIDIA_API_KEY=$NVIDIA_API_KEY|" "$ENV_FILE"',
            "fi",
            "",
            "cd $REPO/deployments",
            "docker compose --env-file $ENV_FILE config > resolved.yml",
            "docker compose -f resolved.yml up -d --force-recreate",
            "",
            'echo "Waiting for containers..."',
            "for i in $(seq 1 90); do",
            "    if curl -sf -o /dev/null --max-time 5 http://localhost:8000/docs 2>/dev/null; then",
            '        echo "Agent API is up after $((i*10))s"',
            "        break",
            "    fi",
            "    sleep 10",
            "done",
        ]

    if skill_name == "alerts":
        return [
            "# === 4. Exercise alerts skill ===",
            "",
            "# List recent alerts",
            "curl -s -X POST http://localhost:8000/generate \\",
            '  -H "Content-Type: application/json" \\',
            """  -d '{"input_message": "Show me recent alerts"}' | jq .""",
            "",
            "# Submit a test behavior alert",
            "curl -s -X POST http://localhost:8000/api/v1/alerts \\",
            '  -H "Content-Type: application/json" \\',
            """  -d '{""",
            '    "sensorId": "camera-01",',
            '    "timestamp": "2025-09-11T00:08:27.822Z",',
            '    "end": "2025-09-11T00:09:22.122Z",',
            '    "category": "collision",',
            '    "place": { "name": "Loading Dock" },',
            '    "objectIds": ["obj-001", "obj-002"],',
            '    "isAnomaly": true',
            """  }' | jq .""",
            "",
            "# Query verdict status",
            "sleep 5",
            "curl -s -X POST http://localhost:8000/generate \\",
            '  -H "Content-Type: application/json" \\',
            """  -d '{"input_message": "What is the verdict for the most recent alert?"}' | jq .""",
        ]

    if skill_name == "sensor-ops":
        return [
            "# === 4. Exercise sensor-ops skill ===",
            "",
            "# List sensors",
            "curl -s http://localhost:30888/vst/api/v1/sensor/list | jq .",
            "",
            "# Check sensor status",
            "curl -s http://localhost:30888/vst/api/v1/sensor/status | jq .",
            "",
            "# Add a test RTSP sensor",
            "curl -s -X POST http://localhost:30888/vst/api/v1/sensor/add \\",
            '  -H "Content-Type: application/json" \\',
            """  -d '{""",
            '    "sensorUrl": "rtsp://wowzaec2demo.streamlock.net/vod/mp4:BigBuckBunny_115k.mp4",',
            '    "username": "",',
            '    "password": "",',
            '    "name": "test-sensor"',
            """  }' | jq .""",
            "",
            "# Verify sensor was added",
            "sleep 2",
            "curl -s http://localhost:30888/vst/api/v1/sensor/list | jq .",
        ]

    if skill_name == "incident-report":
        return [
            "# === 4. Exercise incident-report skill ===",
            "",
            "# Initialize MCP session",
            "SESSION_ID=$(curl -si -X POST http://localhost:9901/mcp \\",
            '  -H "Content-Type: application/json" \\',
            '  -H "Accept: application/json, text/event-stream" \\',
            """  -d '{"jsonrpc":"2.0","method":"initialize","params":{"""
            """"protocolVersion":"2024-11-05","capabilities":{},"""
            """"clientInfo":{"name":"cli","version":"1.0"}},"id":0}' \\""",
            """  | grep -i "mcp-session-id" | awk '{print $2}' | tr -d '\\r')""",
            "",
            "# Query recent incidents",
            "curl -s -X POST http://localhost:9901/mcp \\",
            '  -H "Content-Type: application/json" \\',
            '  -H "Accept: application/json, text/event-stream" \\',
            '  -H "mcp-session-id: $SESSION_ID" \\',
            """  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"""
            """"name":"video_analytics__get_incidents","arguments":{"max_count":10}},"id":1}' \\""",
            """  | grep '^data:' | sed 's/^data: //' | jq -r '.result.content[0].text'""",
        ]

    if skill_name == "video-analytics":
        return [
            "# === 4. Exercise video-analytics skill ===",
            "",
            "# Initialize MCP session",
            "SESSION_ID=$(curl -si -X POST http://localhost:9901/mcp \\",
            '  -H "Content-Type: application/json" \\',
            '  -H "Accept: application/json, text/event-stream" \\',
            """  -d '{"jsonrpc":"2.0","method":"initialize","params":{"""
            """"protocolVersion":"2024-11-05","capabilities":{},"""
            """"clientInfo":{"name":"cli","version":"1.0"}},"id":0}' \\""",
            """  | grep -i "mcp-session-id" | awk '{print $2}' | tr -d '\\r')""",
            "",
            "# List sensor IDs",
            "curl -s -X POST http://localhost:9901/mcp \\",
            '  -H "Content-Type: application/json" \\',
            '  -H "Accept: application/json, text/event-stream" \\',
            '  -H "mcp-session-id: $SESSION_ID" \\',
            """  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"""
            """"name":"video_analytics__get_sensor_ids","arguments":{}},"id":1}' \\""",
            """  | grep '^data:' | sed 's/^data: //' | jq -r '.result.content[0].text'""",
            "",
            "# Query recent incidents",
            "curl -s -X POST http://localhost:9901/mcp \\",
            '  -H "Content-Type: application/json" \\',
            '  -H "Accept: application/json, text/event-stream" \\',
            '  -H "mcp-session-id: $SESSION_ID" \\',
            """  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"""
            """"name":"video_analytics__get_incidents","arguments":{"max_count":10}},"id":2}' \\""",
            """  | grep '^data:' | sed 's/^data: //' | jq -r '.result.content[0].text'""",
        ]

    if skill_name == "video-search":
        return [
            "# === 4. Exercise video-search skill ===",
            "",
            "# Search for 'people walking'",
            "curl -s -X POST http://localhost:8000/generate \\",
            '  -H "Content-Type: application/json" \\',
            """  -d '{"input_message": "find all instances of people walking"}' | jq .""",
            "",
            "# Search for 'vehicles in parking lot'",
            "curl -s -X POST http://localhost:8000/generate \\",
            '  -H "Content-Type: application/json" \\',
            """  -d '{"input_message": "find vehicles in parking lot"}' | jq .""",
        ]

    if skill_name == "video-summarization":
        return [
            "# === 4. Exercise video-summarization skill ===",
            "",
            "# Check LVS endpoint",
            "curl -s -X POST http://localhost:8000/generate \\",
            '  -H "Content-Type: application/json" \\',
            """  -d '{"input_message": "list available videos for summarization"}' | jq .""",
        ]

    return []


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_task(
    skill_name: str,
    skill_def: dict,
    output_dir: Path,
    skills_dir: Path | None,
    brev_instance_type: str | None,
) -> None:
    """Generate a single Harbor task directory for one skill."""
    task_id = skill_name
    task_dir = output_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # -- instruction.md --
    instruction = "Use your /" + skill_name + " skill to complete this task.\n\n"
    instruction += skill_def["instruction"]
    (task_dir / "instruction.md").write_text(instruction)

    # -- task.toml --
    brev_type = brev_instance_type or DEFAULT_BREV_INSTANCE_TYPE
    task_toml = (
        "[task]\n"
        'name = "nvidia-vss/skill-' + skill_name + '"\n'
        'description = "' + skill_def["description"] + '"\n'
        'keywords = ["skill", "' + skill_name + '", "'
        + skill_def["profile"] + '"]\n'
        "\n"
        "[metadata]\n"
        'gpu = "' + DEFAULT_GPU + '"\n'
        'brev_instance_type = "' + brev_type + '"\n'
        'skill = "' + skill_name + '"\n'
        'profile = "' + skill_def["profile"] + '"\n'
    )
    (task_dir / "task.toml").write_text(task_toml)

    # -- environment/ --
    env_dir = task_dir / "environment"
    env_dir.mkdir(exist_ok=True)
    # BrevEnvironment provisions bare instances — Dockerfile is a no-op
    (env_dir / "Dockerfile").write_text("FROM scratch\n")

    # -- tests/test.sh --
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test.sh").write_text(generate_test_script(skill_name, skill_def))

    # -- solution/solve.sh --
    solution_dir = task_dir / "solution"
    solution_dir.mkdir(exist_ok=True)
    (solution_dir / "solve.sh").write_text(generate_solve_script(skill_name, skill_def))

    # -- Copy skill into task if skills_dir provided --
    if skills_dir:
        skill_src = skills_dir / skill_name
        if skill_src.exists():
            skill_dest = task_dir / "skills" / skill_name
            if skill_dest.exists():
                shutil.rmtree(skill_dest)
            shutil.copytree(skill_src, skill_dest)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Harbor tasks for evaluating all VSS skills",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Output directory for generated task datasets",
    )
    parser.add_argument(
        "--skill", default=None, choices=list(SKILLS.keys()),
        help="Generate only for this skill (default: all)",
    )
    parser.add_argument(
        "--skills-dir", default=None,
        help="Path to skills/ directory to copy into each task",
    )
    parser.add_argument(
        "--brev-instance-type", default=None,
        help="Brev instance type to use (skips 'brev search' query). "
             "Falls back to BREV_INSTANCE_TYPE env var, then 'brev search', "
             "then default " + DEFAULT_BREV_INSTANCE_TYPE,
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    skills_dir = Path(args.skills_dir) if args.skills_dir else None

    # Resolve Brev instance type: CLI arg > env var > brev search
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

    # Select skills to generate
    if args.skill:
        selected = {args.skill: SKILLS[args.skill]}
    else:
        selected = SKILLS

    # The deploy skill provisions its own Brev instance — it must have a
    # valid instance type resolved from `brev search` (or passed explicitly).
    if "deploy" in selected and not brev_type:
        print(
            "ERROR: Could not resolve a Brev instance type.  The deploy "
            "skill requires a real instance type from 'brev search'.\n"
            "  - Make sure 'brev' CLI is installed and authenticated "
            "(brev login), or\n"
            "  - Pass --brev-instance-type <type>, or\n"
            "  - Set BREV_INSTANCE_TYPE env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    for skill_name, skill_def in selected.items():
        print(f"  Generating task: {skill_name} (profile={skill_def['profile']})")
        generate_task(skill_name, skill_def, output_dir, skills_dir, brev_type)

    # Summary
    print(f"\nGenerated {len(selected)} skill task(s) in {output_dir}")
    print()
    print("Skills:")
    for name, sdef in selected.items():
        print(f"  {name:24s} profile={sdef['profile']:8s} "
              f"deploy_first={sdef['needs_deploy']}")

    print()
    print("Run all skills on Brev:")
    print(f"  harbor run --env 'tools.eval.harbor.envs.brev_env:BrevEnvironment' \\")
    print(f"    -p {output_dir} -a claude-code -n 1")

    if args.skill:
        print()
        print(f"Run single skill '{args.skill}':")
        print(f"  harbor run --env 'tools.eval.harbor.envs.brev_env:BrevEnvironment' \\")
        print(f"    -p {output_dir} -i '{args.skill}' -a claude-code -n 1")


if __name__ == "__main__":
    main()
