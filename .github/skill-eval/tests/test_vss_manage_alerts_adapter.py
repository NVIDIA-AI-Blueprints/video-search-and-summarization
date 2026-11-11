# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the vss-manage-alerts Harbor adapter."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_PATH = REPO_ROOT / ".github/skill-eval/adapters/vss-manage-alerts/generate.py"
SPEC_PATH = (
    REPO_ROOT / "skills/operations/vss-manage-alerts/evals/alerts_vlm_real_time.json"
)
SKILL_DIR = REPO_ROOT / "skills/operations/vss-manage-alerts"
DEPLOY_SKILL_DIR = REPO_ROOT / "skills/vss-build-vision-ai"


def test_ondemand_canary_uses_local_alert_auth_overlay(tmp_path: Path) -> None:
    spec_path = SKILL_DIR / "evals/ondemand_verification.json"
    command = [
        sys.executable, str(ADAPTER_PATH),
        "--output-dir", str(tmp_path),
        "--skill-dir", str(SKILL_DIR),
        "--deploy-skill-dir", str(DEPLOY_SKILL_DIR),
        "--spec", str(spec_path),
        "--platform", "L40S",
    ]
    env = os.environ.copy()
    env.pop("SKILLS_EVAL_CLEAN_WORKER_AFTER_LEG", None)
    subprocess.run(command, check=True, capture_output=True, text=True, env=env)
    (root,) = (tmp_path / "ondemand_verification").iterdir()
    assert "vss1009-alert-auth" not in (root / "step-1/instruction.md").read_text()

    env["SKILLS_EVAL_CLEAN_WORKER_AFTER_LEG"] = "true"
    subprocess.run(command, check=True, capture_output=True, text=True, env=env)
    instruction = (root / "step-1/instruction.md").read_text()
    assert "vss1009-alert-auth:develop-latest" in instruction
    assert "patches/alert-bridge.yml" in instruction
    assert "pull --ignore-buildable" in instruction


def test_skill_routing_step_names_no_skill_or_mode(tmp_path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ADAPTER_PATH),
            "--output-dir", str(tmp_path),
            "--skill-dir", str(SKILL_DIR),
            "--deploy-skill-dir", str(DEPLOY_SKILL_DIR),
            "--spec", str(SPEC_PATH),
            "--platform", "L40S",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    expects = json.loads(SPEC_PATH.read_text())["expects"]
    routing = [i for i, e in enumerate(expects, 1) if e.get("scenario") == "skill-routing"]
    assert routing, "alerts_vlm_real_time has no skill-routing step"

    (root,) = (tmp_path / "alerts_vlm_real_time").iterdir()
    setup_instruction = (root / "step-1/instruction.md").read_text()
    assert "If these optional CI variables are absent" in setup_instruction
    assert "https://inference-api.nvidia.com/v1" in setup_instruction
    assert "COMPATIBLE_API_KEY" in setup_instruction
    assert "nvidia/nvidia/llama-3.1-nemotron-ultra-253b-v1" in setup_instruction
    assert "nvidia/nvidia/cosmos3-nano-reasoner" in setup_instruction
    for idx in range(2, len(expects) + 1):
        instruction = (root / f"step-{idx}/instruction.md").read_text()
        if idx in routing:
            assert "/vss-manage-alerts" not in instruction
            assert "real-time (VLM)" not in instruction
            assert "Answer with the VSS skills installed" in instruction
        else:
            assert "Use the `/vss-manage-alerts` skill" in instruction
