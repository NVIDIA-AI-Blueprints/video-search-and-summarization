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


def test_ingestion_contract_delegates_readiness_to_bundled_operation() -> None:
    spec = _search_spec()
    deploy = spec["expects"][0]
    ingest = spec["expects"][1]

    assert "required models are ready" in deploy["query"]
    assert "RT-CV `/api/v1/ready`" in deploy["checks"][4]
    assert (
        "invoked the bundled `ingest_search_fixtures.sh` operation exactly once"
        in ingest["checks"][0]
    )
    assert (
        "verified Agent, VST, RT-Embed, and RT-CV readiness before mutation"
        in ingest["checks"][1]
    )


def test_agent_backed_ingestion_routes_live_in_bundled_operation() -> None:
    script = (
        REPO_ROOT / "skills/vss-search-archive/scripts/ingest_search_fixtures.sh"
    ).read_text()

    assert '"${AGENT_URL}/api/v1/videos"' in script
    assert '"${AGENT_URL}/api/v1/videos/${sensor}/complete"' in script
    assert '"${AGENT_URL}/api/v1/videos/${SENSOR_TO_DELETE}"' in script
    assert "/api/v1/videos-for-search/" not in script


def test_ingestion_preamble_requires_one_self_contained_operation() -> None:
    adapter = _load_adapter()
    preamble = adapter.INGESTION_PREAMBLE

    assert "bundled self-contained fixture-ingestion operation exactly once" in preamble
    assert "Do not invoke `/vss-deploy-profile`" in preamble
    assert "reconstruct the ingestion workflow in ad hoc shell" in preamble
    assert "report the structured failure and stop" in preamble
