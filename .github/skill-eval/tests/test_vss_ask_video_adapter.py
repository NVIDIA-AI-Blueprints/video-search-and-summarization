# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the vss-ask-video skill and Harbor adapter."""

from __future__ import annotations

import importlib.util
import json
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
    assert "uv run --project" in solution
    assert "--no-dev --extra cli vss" in solution
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
    for task_file in task_files:
        task = task_file.read_text()
        instruction = task_file.with_name("instruction.md").read_text()
        solution = task_file.parent.joinpath("solution/solve.sh").read_text()
        assert '"memory", "introspection", "openclaw"' in task
        assert "chat-completions" not in task
        assert instruction.startswith(adapter.PREAMBLE)
        assert "uv run --project" in solution
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
    assert "mocked" in contract
    assert "introspection.enabled=false" in contract
    assert "introspection=null" in contract
    assert "--record-id without both --job-id and --record-type" in contract
    assert "complete project-local uv run invocation" in contract
    assert "vss vlm run --file" in contract
    assert "vss configure check" in contract
    assert "--fps chosen from the skim/locate/inspect policy" in contract
    assert "--fps rather than a fixed --num-frames" in contract


def test_skill_examples_are_fresh_shell_safe_and_child_identity_is_complete() -> None:
    skill = SKILL_DIR.joinpath("SKILL.md").read_text()
    shell_blocks = skill.split("```bash")[1:]
    shell_blocks = [block.split("```", 1)[0] for block in shell_blocks]
    for block in shell_blocks:
        if '"${VSS[@]}"' in block:
            assert (
                'VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"'
                in block
            )
            assert "VSS=(uv run" in block
            assert "--no-dev" in block
            assert "--extra cli" in block
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
        if '"${VSS[@]}" memory introspect' in block or '"${VSS[@]}" vlm run' in block
    ]
    assert visual_blocks, "skill must show introspection and VLM invocations"
    for block in visual_blocks:
        assert "--fps" in block
        assert "VLM_FPS=" in block
        assert "--num-frames" not in block
