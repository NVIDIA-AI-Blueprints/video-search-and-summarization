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
    spec = importlib.util.spec_from_file_location("vss_search_archive_adapter", ADAPTER_PATH)
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


def test_ingestion_contract_gates_rtvi_cv_before_mutation() -> None:
    spec = _search_spec()
    deploy = spec["expects"][0]
    ingest = spec["expects"][1]

    assert "/api/v1/ready" in deploy["query"]
    assert "ds-ready` exactly `YES" in deploy["query"]
    assert "/api/v1/ready" in ingest["query"]
    assert "Before cleanup, download, or upload" in ingest["checks"][1]
    assert "original source-setup budget" in ingest["checks"][1]


def test_agent_backed_ingestion_means_agent_http_routes() -> None:
    spec = _search_spec()
    ingest = spec["expects"][1]
    route_check = ingest["checks"][4]

    assert "Bash/curl" in route_check
    assert "POST /api/v1/videos" in route_check
    assert "upload to its returned URL" in route_check
    assert "POST /api/v1/videos/<sensor-id>/complete" in route_check
    assert "no dedicated Workflow or Agent tool call was required" in route_check
