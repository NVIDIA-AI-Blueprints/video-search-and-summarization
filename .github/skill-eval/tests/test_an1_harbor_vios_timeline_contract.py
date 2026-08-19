# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Keep the AN-1 Harbor runtime spec aligned with the VIOS file-sensor timeline contract."""

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_SPEC = (
    REPO_ROOT
    / "skills/vss-build-vision-agent/eval/profile_an_1_stored_video_summarization_runtime_harbor.json"
)


def test_an1_harbor_runtime_requires_timeline_bounded_media_probes() -> None:
    """Clip/snapshot 400s from wall-clock times must not fail the availability check."""
    spec = json.loads(EVAL_SPEC.read_text())
    query = spec["expects"][2]
    contract = "\n".join([query["query"], *query["checks"]])

    assert "GET /storage/<streamId>/timelines" in query["query"]
    assert "do not use wall-clock time" in query["query"]
    assert "2025-01-01T00:00:00.000Z" in query["query"]

    availability = query["checks"][0]
    assert "GET /storage/<streamId>/timelines" in availability
    assert "InvalidParameterError" in availability
    assert "/storage/file/<streamId>" in availability
    assert "not wall-clock time" in availability
    assert "snapshot or clip endpoint returns HTTP 200" not in contract
