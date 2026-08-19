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
    step_query = query["query"]
    availability = query["checks"][0]
    contract = "\n".join([step_query, *query["checks"]])

    assert "GET /storage/<streamId>/timelines" in step_query
    assert "do not use wall-clock time" in step_query
    assert "2025-01-01T00:00:00.000Z" in step_query
    assert "binary `/storage/file/<streamId>`" in step_query
    assert "/storage/file/<streamId>/url" in step_query

    assert "GET /storage/<streamId>/timelines" in availability
    assert "not wall-clock time" in availability
    assert "InvalidParameterError" in availability
    assert "do not prove the video is missing" in availability
    assert "HTTP 200 with non-empty image or MP4 bytes" in availability
    assert "binary `GET /storage/file/<streamId>`" in availability
    assert "Do not treat `GET /storage/file/<streamId>/url` as a media-bytes probe" in availability
    assert "JSON envelope" in availability
    assert "(binary or `/url`)" not in availability
    assert "snapshot or clip endpoint returns HTTP 200" not in contract
