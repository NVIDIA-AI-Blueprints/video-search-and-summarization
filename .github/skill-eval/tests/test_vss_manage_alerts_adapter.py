# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the vss-manage-alerts Harbor adapter."""

from __future__ import annotations

import json
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
    for idx in range(2, len(expects) + 1):
        instruction = (root / f"step-{idx}/instruction.md").read_text()
        if idx in routing:
            assert "/vss-manage-alerts" not in instruction
            assert "real-time (VLM)" not in instruction
            assert "Answer with the VSS skills installed" in instruction
        else:
            assert "Use the `/vss-manage-alerts` skill" in instruction
