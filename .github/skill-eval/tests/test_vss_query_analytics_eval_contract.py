# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the query-analytics evaluation contract."""

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = REPO_ROOT / "skills/vss-query-analytics"
SKILL = SKILL_DIR / "SKILL.md"
EVAL_SPEC = SKILL_DIR / "evals/query_analytics.json"
ADAPTER = (
    REPO_ROOT
    / ".github/skill-eval/adapters/vss-query-analytics/generate.py"
)


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "vss_query_analytics_adapter", ADAPTER
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_adapter_renders_platform_for_agent_and_verifier(
    tmp_path: Path,
) -> None:
    adapter = _load_adapter()
    raw_spec = json.loads(EVAL_SPEC.read_text(encoding="utf-8"))
    raw_spec["_source_path"] = str(EVAL_SPEC)

    adapter.generate_task(
        "RTXPRO6000BW",
        "alerts",
        raw_spec,
        tmp_path,
        SKILL_DIR,
        REPO_ROOT / "skills/vss-deploy-profile",
    )

    step_dirs = sorted(
        (tmp_path / "alerts/rtxpro6000bw").glob("step-*"),
        key=lambda path: int(path.name.removeprefix("step-")),
    )
    assert len(step_dirs) == len(raw_spec["expects"])
    for step_dir in step_dirs:
        instruction = (step_dir / "instruction.md").read_text(
            encoding="utf-8"
        )
        verifier_spec = (
            step_dir / "tests/query_analytics.json"
        ).read_text(encoding="utf-8")
        assert "{{platform}}" not in instruction
        assert "{{platform}}" not in verifier_spec

    assert "RTXPRO6000BW" in (
        step_dirs[0] / "instruction.md"
    ).read_text(encoding="utf-8")


def test_liveness_contract_uses_resolved_health_endpoint() -> None:
    adapter = _load_adapter()
    skill = SKILL.read_text(encoding="utf-8")
    solution = adapter.generate_solve_script("RTXPRO6000BW")

    assert '${VA_MCP_URL%/}/health' in solution
    assert "2*|3*)" in solution
    assert "405|406" not in solution
    assert skill.count(
        'curl -sf --max-time 5 "${VA_MCP_URL%/}/health"'
    ) == 2
    assert "Prefer `/health` over `GET /mcp`" in skill
