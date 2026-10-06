# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render every operational runtime spec through its real adapter."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
VIDEO_FIXTURES = {
    "base_profile_local_nim_reuse": ("warehouse_safety_0001.mp4",),
    "base_profile_video_understanding": ("warehouse_safety_0001.mp4",),
    "base_profile_report": ("warehouse_safety_0001.mp4",),
    "lvs_profile_summarize": ("warehouse_sample.mp4",),
    "vios_ops": ("warehouse_sample.mp4",),
    "nvstreamer_ops": ("warehouse_safety_0001.mp4", "warehouse_sample.mp4"),
    "alerts_vlm_real_time": ("warehouse_sample.mp4",),
    "search": ("warehouse_sample.mp4", "sample-warehouse-ladder.mp4"),
}
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
    checks = "\n".join(spec["expects"][0]["checks"])
    assert "reading or invoking the bundled `/vss-build-vision-ai`" in checks
    assert "sandbox-installed `vss configure check` succeeds" in checks
    for filename in VIDEO_FIXTURES.get(spec_path.stem, ()):
        assert f'upload "$SAMPLE_DIR/{filename}" /tmp/vss-sample-data/dev-profile-sample-data/' in query
        assert filename in checks
        assert "sandbox `sha256sum` matches its host source" in checks
    if spec_path.stem in VIDEO_FIXTURES:
        assert "keep NGC credentials on the host" in query
        assert "nvidia/vss-developer/dev-profile-sample-data:3.2.0" in query
    if spec_path.stem == "base_profile_video_understanding":
        assert "/app/warehouse_safety_0001.mp4" not in query
        assert "/tmp/vss-sample-data/dev-profile-sample-data/warehouse_safety_0001.mp4" in spec["expects"][-1]["query"]
    if spec_path.stem == "search":
        assert "fresh `mktemp -d` directory" in query
        assert "do not fetch NGC from the sandbox" in spec["expects"][1]["query"]
        assert "copied into NemoClaw during setup with matching hashes" in spec["expects"][1]["checks"][1]
        assert "no sample bundle was downloaded" not in checks
        assert "When NemoClaw is selected, the fresh sample bundle may be downloaded" in checks
    if spec_path.stem == "lvs_profile_summarize":
        assert "Run on ONE `{{platform}}` host" in query
    if "This deployment uses the in-stack agent:" in query:
        assert "separate evaluation client" in query
        assert "do not select it in Build Vision AI's Q3" in query
        assert "VSS_AGENT_ADAPTER_ENABLED=false" in query
        assert "use Build Vision AI to attach NemoClaw" not in query
    if skill == "vss-manage-video-io-storage":
        assert "base-profile composition" in query
        assert "SDR controller" in query
        for later in spec["expects"][1:]:
            assert "Do NOT invoke `/vss-build-vision-ai`" not in later["query"]
            assert "**Deploy VIOS" not in later["query"]
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
