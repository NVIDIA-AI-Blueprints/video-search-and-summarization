# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the vss-ask-video skill and Harbor adapter."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys

import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_PATH = REPO_ROOT / ".github/skill-eval/adapters/vss-ask-video/generate.py"
SKILL_DIR = REPO_ROOT / "skills/operations/vss-ask-video"
SPEC_PATH = SKILL_DIR / "evals/base_profile_video_understanding.json"


def _load_adapter():
    spec = importlib.util.spec_from_file_location("vss_ask_video_adapter", ADAPTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adapter_has_no_direct_backend_fallback_contract() -> None:
    source = ADAPTER_PATH.read_text()
    assert "calls the VLM /v1/chat/completions endpoint directly" not in source
    assert "curl -sf" not in source
    assert (
        'keywords = ["vss-ask-video", "memory", "introspection", "openclaw"' in source
    )

    solution = _load_adapter().generate_solve_script("L40S")
    assert "uv run --project" not in solution
    assert 'uv tool install "${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}/libs/vss/cli"' in solution
    assert "vss --version" in solution
    assert "--extra cli" not in solution
    for forbidden in ("curl ", "/models", "/generate", ":9200", ":8018", ":30082"):
        assert forbidden not in solution


def test_generated_tasks_use_routing_metadata_and_project_local_cli(
    tmp_path: Path,
) -> None:
    adapter = _load_adapter()
    spec = json.loads(SPEC_PATH.read_text())
    spec["_source_path"] = str(SPEC_PATH)

    adapter.generate_task(
        "L40S",
        "base",
        spec,
        tmp_path,
        SKILL_DIR,
        None,
        None,
    )

    task_files = sorted(tmp_path.rglob("task.toml"))
    assert len(task_files) == len(spec["expects"])
    first_instruction = (
        tmp_path / "base" / "l40s" / "step-1" / "instruction.md"
    ).read_text()
    assert "Use `/vss-build-vision-ai` to deploy the VSS base profile on `L40S`" in first_instruction
    assert "{{platform}}" not in first_instruction
    for task_file in task_files:
        task = task_file.read_text()
        instruction = task_file.with_name("instruction.md").read_text()
        solution = task_file.parent.joinpath("solution/solve.sh").read_text()
        assert '"memory", "introspection", "openclaw"' in task
        assert "chat-completions" not in task
        assert instruction.startswith(adapter.PREAMBLE)
        assert "uv tool install" in solution and "uv run --project" not in solution
        assert "curl " not in solution


def test_specs_cover_markdown_and_introspection_state_routing() -> None:
    lightweight = json.loads(SKILL_DIR.joinpath("evals/evals.json").read_text())
    ids = {case["id"] for case in lightweight}
    assert {
        "hot-context-sufficient",
        "markdown-sufficient",
        "markdown-pointer-introspection-enabled",
        "no-markdown-introspection-enabled",
        "introspection-disabled",
        "introspection-unconfigured",
        "exact-stored-job",
        "explicit-fresh-window",
        "introspection-partial",
        "no-memory-grounded-window",
        "no-memory-without-scope",
        "invalid-child-identity",
        "direct-file-vlm",
        "separate-shell-cli",
    } <= ids

    harbor = json.loads(SPEC_PATH.read_text())
    contract = json.dumps(harbor)
    # The Harbor spec runs against a live deployment: step 1 deploys and seeds
    # the fixture the later steps read, so each routing state is reached by
    # doing the work rather than by describing a mocked tool result.
    assert "mocked" not in contract
    assert (
        "onboard the local file /tmp/vss-sample-data/dev-profile-sample-data/warehouse_safety_0001.mp4 "
        "into VIOS as a sensor named warehouse_sample" in contract
    )
    assert "VSS unified memory (Elasticsearch) enabled" in contract
    assert "Use `/vss-build-vision-ai` to deploy the VSS base profile" in contract
    assert "--prompt 'Describe the scene.' --max-frames 2 --no-persist" in contract

    # Recall of what an earlier step persisted, rather than a supplied note.
    assert "answers from what VSS unified memory already holds" in contract
    assert "It does not run vss vlm run a second time" in contract

    # The two introspection states are now stated as deployment facts.
    assert "Introspection is turned off on this deployment" in contract
    assert "Introspection is not configured on this deployment" in contract

    assert "--record-id without both --job-id and --record-type" in contract
    assert "calls the vss CLI on PATH" in contract
    assert "vss vlm run --file" in contract
    assert "vss configure check" in contract
    assert "--fps chosen from the skim/locate/inspect policy" in contract
    assert "--fps rather than a fixed --max-frames" in contract


def test_skill_examples_are_fresh_shell_safe_and_child_identity_is_complete() -> None:
    skill = SKILL_DIR.joinpath("SKILL.md").read_text()
    shell_blocks = skill.split("```bash")[1:]
    shell_blocks = [block.split("```", 1)[0] for block in shell_blocks]
    for block in shell_blocks:
        # The skill calls the vss on PATH as-is: no wrapper, array or checkout.
        assert "VSS=(" not in block and '"${VSS[@]}"' not in block
        assert "uv run" not in block and "VSS_REPO_ROOT" not in block
        if "--record-id" in block:
            assert "--job-id" in block
            assert "--record-type" in block
    assert "vss() {" not in skill
    assert "## Choose visual sampling density" in skill
    assert "at most 60 frames" in skill
    assert "Skim (`0.5`)" in skill
    assert "Locate (`1`)" in skill
    assert "Inspect (`2`)" in skill
    visual_blocks = [
        block
        for block in shell_blocks
        if "vss memory introspect" in block or "vss vlm run" in block
    ]
    assert visual_blocks, "skill must show introspection and VLM invocations"
    for block in visual_blocks:
        assert "--fps" in block
        assert "VLM_FPS=" in block
        assert "--num-frames" not in block
        assert "--max-frames" not in block


SPECS = []
for path in sorted((REPO_ROOT / "skills/operations").glob("*/evals/*.json")):
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
        sys.executable, str(REPO_ROOT / f".github/skill-eval/adapters/{skill}/generate.py"),
        "--spec", str(spec_path), "--platform", platform,
        "--skill-dir", str(spec_path.parents[1]),
        "--deploy-skill-dir", str(REPO_ROOT / "skills/vss-build-vision-ai"),
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
        assert (build_skill / "SKILL.md").read_bytes() == (REPO_ROOT / "skills/vss-build-vision-ai/SKILL.md").read_bytes()
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
