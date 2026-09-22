# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the vss-introspect-video skill-eval adapter."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_PATH = (
    REPO_ROOT / ".github/skill-eval/adapters/vss-introspect-video/generate.py"
)
SKILL_DIR = REPO_ROOT / "skills/operations/vss-introspect-video"
PLANNER_DIR = REPO_ROOT / "skills/operations/vss-generate-evidence-plan"
SPEC_PATH = SKILL_DIR / "evals/mocked_loop.json"


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "vss_introspect_video_adapter", ADAPTER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_adapter_generates_complete_deterministic_mocked_loop(
    tmp_path: Path,
) -> None:
    adapter = _load_adapter()
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    spec["_source_path"] = str(SPEC_PATH)
    first = tmp_path / "first"
    second = tmp_path / "second"

    for output in (first, second):
        adapter.generate_task(
            "L40S",
            "mocked-loop",
            spec,
            output,
            SKILL_DIR,
            PLANNER_DIR,
        )

    assert _snapshot(first) == _snapshot(second)
    task = first / "mocked-loop/l40s"
    for relative in (
        "instruction.md",
        "task.toml",
        "tests/test.sh",
        "tests/mocked_loop.json",
        "solution/solve.sh",
        "environment/Dockerfile",
        "skills/vss-introspect-video/SKILL.md",
        "skills/vss-generate-evidence-plan/SKILL.md",
    ):
        assert task.joinpath(relative).is_file()

    instruction = task.joinpath("instruction.md").read_text(encoding="utf-8")
    metadata = task.joinpath("task.toml").read_text(encoding="utf-8")
    solution = task.joinpath("solution/solve.sh").read_text(encoding="utf-8")
    rendered_spec = task.joinpath("tests/mocked_loop.json").read_text(encoding="utf-8")
    assert instruction.startswith(adapter.PREAMBLE)
    assert "Do not deploy VSS" in instruction
    assert "fixture outputs" in instruction
    assert "gpu_count = 0" in metadata
    assert "timeout_sec = 600.0" in metadata
    assert "ledger-budgets.json" in solution
    assert "_source_path" not in rendered_spec
    assert "curl " not in solution
    assert "vss memory introspect" not in solution


def test_lightweight_evals_cover_all_requested_scenarios() -> None:
    cases = json.loads(SKILL_DIR.joinpath("evals/evals.json").read_text())
    assert {case["id"] for case in cases} == {
        "attribute-option-blind",
        "count-complete-coverage",
        "cross-event-order",
        "identity-across-windows",
        "whole-video-bounded-windows",
        "incomplete-evidence-unresolved",
        "partial-parallel-failure",
        "memory-only-completion",
        "ask-video-delegation",
    }


def test_skill_documents_full_agent_owned_loop_and_canonical_limits() -> None:
    skill = SKILL_DIR.joinpath("SKILL.md").read_text(encoding="utf-8")
    normalized = " ".join(skill.split())
    assert not any(SKILL_DIR.joinpath("references").glob("*"))
    assert "vss-generate-evidence-plan" in skill
    assert "before planning" not in normalized
    assert "option-blind" in skill
    assert "config/ledger-budgets.json" in skill
    assert "same frozen `base_revision`" in skill
    assert "batch-merge" in skill
    assert "one claim ID" in normalized
    assert "whole_video" in skill
    assert "uncovered intervals as explicit gaps" in normalized
    assert "${VSS_WORKSPACE:-$HOME/.vss}/runs/vss-introspection/<question-id>/" in skill
    assert "The evidence planner never answers the question" in skill
    assert "ordinary `vss vios`" in skill
    assert "Never exceed any maximum loaded" in skill
    assert "Never fabricate sensor or time fields" in normalized
    assert '{"type":"media_url","media_url":"https://..."}' in skill
    assert "`evidence_details`" in skill

    # The legacy command may appear only in an explicit prohibition.
    occurrences = [
        line for line in skill.splitlines() if "vss memory introspect" in line
    ]
    assert occurrences
    assert all("Do not use" in line or "Never invoke" in line for line in occurrences)


def test_mocked_spec_is_dispatchable_and_backend_free() -> None:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    assert spec["skills"] == [
        "vss-introspect-video",
        "vss-generate-evidence-plan",
    ]
    assert spec["resources"]["platforms"]["L40S"]["gpu_count"] == 0
    assert len(spec["expects"]) == 1
    serialized = json.dumps(spec)
    assert "config/ledger-budgets.json" in serialized
    assert "scripts/evidence_ledger.py" in serialized
    assert "do not deploy VSS or contact a backend" in serialized
