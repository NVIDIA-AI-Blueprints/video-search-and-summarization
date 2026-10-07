# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render every operational runtime spec through its real adapter."""

import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
SPECS = []
for path in sorted((REPO / "skills/operations").glob("*/evals/*.json")):
    document = json.loads(path.read_text())
    if isinstance(document, dict) and document.get("resources", {}).get("platforms"):
        # Setup ownership is platform-independent; adapter/platform rendering
        # is covered separately. Exercise every operational spec once.
        SPECS.append((path, next(iter(document["resources"]["platforms"]))))


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
    for key in ("NEMOCLAW_GATEWAY_PORT", "NEMOCLAW_DASHBOARD_PORT", "NEMOCLAW_DASHBOARD_RELAY_PORT"):
        assert "$" + key in query
    checks = "\n".join(spec["expects"][0]["checks"])
    declared = spec.get("sandbox_fixtures", [])
    requested = list(dict.fromkeys(re.findall(r'\$SAMPLE_DIR/([A-Za-z0-9_.-]+\.mp4)', query)))
    assert declared == requested

    for filename in declared:
        assert f'upload "$SAMPLE_DIR/{filename}" /tmp/vss-sample-data/dev-profile-sample-data/' in query
        assert filename in checks
    if "This deployment uses the in-stack agent:" in query:
        assert "separate evaluation client" in query
        assert "do not select it in Build Vision AI's Q3" in query
        assert "VSS_AGENT_ADAPTER_ENABLED=false" in query
        assert "use Build Vision AI to attach NemoClaw" not in query
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
        if spec_path.stem == "search":
            # Both deployment and ingestion must use the spec's conditional
            # fixture policy, without conflicting adapter-only instructions.
            for step in (1, 2):
                content = (instruction_path.parent.parent / f"step-{step}/instruction.md").read_text()
                assert "do not download or ingest sample media" not in content
                assert "download the exact pinned NGC bundle into a fresh directory" not in content
        # Setup directives must not be injected into operational queries.
        for later in instruction_path.parent.parent.glob("step-*/instruction.md"):
            if later != instruction_path:
                assert query.split("\n\n")[-1] not in later.read_text()
