# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the vss-search-archive Harbor adapter."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_PATH = REPO_ROOT / ".github/skill-eval/adapters/vss-search-archive/generate.py"
SPEC_PATH = REPO_ROOT / "skills/vss-search-archive/evals/search.json"


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "vss_search_archive_adapter", ADAPTER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _search_spec() -> dict:
    return json.loads(SPEC_PATH.read_text())


def test_non_object_expect_is_rejected_as_validation_error() -> None:
    adapter = _load_adapter()
    spec = _search_spec()
    spec["expects"][2] = "not-an-object"

    with pytest.raises(TypeError, match=r"spec\.expects\[3\] must be an object"):
        adapter._validate_spec(spec)


def test_verification_scenario_requires_ask_video_skill() -> None:
    adapter = _load_adapter()
    spec = _search_spec()
    spec["skills"].remove("vss-ask-video")

    with pytest.raises(ValueError, match="requires vss-ask-video"):
        adapter._validate_spec(spec)


def test_missing_source_step_stops_instead_of_substituting() -> None:
    spec = _search_spec()
    missing = spec["expects"][2]

    assert missing["scenario"] == "search-missing-source"
    assert "airport" in missing["query"]
    assert "did not invoke `vss search run`" in missing["checks"][1]
    assert "asked the user to clarify" in missing["checks"][2]


def test_missing_source_preamble_makes_stopping_the_autonomous_outcome() -> None:
    adapter = _load_adapter()
    preamble = adapter.MISSING_SOURCE_PREAMBLE
    autonomy_line = adapter.MISSING_SOURCE_AUTONOMY_LINE

    assert "stopping IS the correct autonomous outcome" in preamble
    assert "Never substitute another source" in preamble
    assert "ask the user to clarify" in preamble
    assert "/vst/api/v1/sensor/list" in preamble
    assert "without prompting for confirmation" not in autonomy_line
    assert "reportable final result" in autonomy_line


def test_missing_source_instruction_uses_qualified_autonomy_line(
    tmp_path: Path,
) -> None:
    adapter = _load_adapter()
    spec = _search_spec()
    spec["_source_path"] = str(SPEC_PATH)
    adapter.generate_task(
        platform="RTXPRO6000BW",
        profile="search",
        spec=spec,
        output_root=tmp_path,
        skill_dir=SPEC_PATH.parents[1],
        deploy_skill_dir=None,
        video_io_skill_dir=None,
        ask_video_skill_dir=SPEC_PATH.parents[1].parent / "vss-ask-video",
    )

    step3 = (
        tmp_path / "search" / "rtxpro6000bw" / "step-3" / "instruction.md"
    ).read_text()
    assert adapter.MISSING_SOURCE_AUTONOMY_LINE in step3
    assert "Run autonomously without prompting for confirmation." not in step3
    assert "/vst/api/v1/sensor/list" in step3

    step4 = (
        tmp_path / "search" / "rtxpro6000bw" / "step-4" / "instruction.md"
    ).read_text()
    assert "Run autonomously without prompting for confirmation." in step4
