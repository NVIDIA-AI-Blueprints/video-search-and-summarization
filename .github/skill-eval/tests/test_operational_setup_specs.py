# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render every operational runtime spec through its real adapter."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
SPECS = []
for path in sorted((REPO / "skills/operations").glob("*/evals/*.json")):
    document = json.loads(path.read_text())
    if isinstance(document, dict) and document.get("resources", {}).get("platforms"):
        for platform in document["resources"]["platforms"]:
            SPECS.append((path, platform))


@pytest.mark.parametrize("spec_path,platform", SPECS, ids=[
    f"{path.parents[1].name}/{path.stem}/{platform}" for path, platform in SPECS
])
def test_operational_setup_is_spec_owned(spec_path, platform, tmp_path):
    spec = json.loads(spec_path.read_text())
    skill = spec_path.parents[1].name
    query = spec["expects"][0]["query"]
    assert "vss-build-vision-ai" in spec["skills"]
    assert "Use `/vss-build-vision-ai`" in query
    assert "$SKILLS_EVAL_OPERATIONAL_HARNESS" in query
    assert f"install `/{skill}`" in query
    assert 'openshell sandbox get "$NEMOCLAW_SANDBOX_NAME"' in query
    assert "sandbox gateway must be ready" in query
    # Preserve the alerts backend independently of the evaluated operator.
    if skill == "vss-manage-alerts" and "in-stack agent" in query:
        assert "do not replace it with the evaluation harness" in query
        assert "harness selection does not apply" not in query

    result = subprocess.run([
        sys.executable, str(REPO / f".github/skill-eval/adapters/{skill}/generate.py"),
        "--spec", str(spec_path), "--platform", platform,
        "--skill-dir", str(spec_path.parents[1]),
        "--deploy-skill-dir", str(REPO / "skills/vss-build-vision-ai"),
        "--output-dir", str(tmp_path),
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    setups = list(tmp_path.rglob("step-1/instruction.md"))
    assert setups, "adapter did not emit a setup task"
    for instruction_path in setups:
        instruction = instruction_path.read_text()
        assert query.split("\n\n")[-1] in instruction
        assert "Use `/vss-build-vision-ai`" in instruction
        assert "{{platform}}" not in instruction
        build_skill = instruction_path.parent / "skills/vss-build-vision-ai"
        assert (build_skill / "SKILL.md").read_bytes() == (REPO / "skills/vss-build-vision-ai/SKILL.md").read_bytes()
        assert "## Selected agent harness: NemoClaw" not in instruction
        # Setup directives must not be injected into operational queries.
        for later in instruction_path.parent.parent.glob("step-*/instruction.md"):
            if later != instruction_path:
                assert query.split("\n\n")[-1] not in later.read_text()
