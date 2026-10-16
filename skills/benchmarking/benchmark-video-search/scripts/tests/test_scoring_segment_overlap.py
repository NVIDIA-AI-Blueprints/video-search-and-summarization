# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The segment-overlap scoring backend: registry, byte-identity, and the A3 slice.

python3 -m pytest scripts/tests/test_scoring_segment_overlap.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows
from flows.artifacts import (
    IngestManifest,
    QueryRecord,
    RetrievalArtifact,
    RunConfig,
    ScoredResult,
)
from flows.scoring import DEFAULT_SCORING, SCORING_BACKENDS, SegmentOverlapScoring

GT = {
    "queries": {
        "q": [
            {
                "video_name": "v",
                "start_time": "2025-01-01T00:00:10Z",
                "end_time": "2025-01-01T00:00:15Z",
            },
        ]
    }
}


def _hit(video: str, start: str, end: str) -> dict[str, Any]:
    return {
        "video_name": video,
        "description": "",
        "start_time": start,
        "end_time": end,
        "sensor_id": "s",
        "screenshot_url": "",
        "similarity": 0.5,
        "object_ids": [],
        "_verification_source": "absent",
    }


def _artifact(hits: list[dict[str, Any]]) -> RetrievalArtifact:
    return RetrievalArtifact(
        dataset="slice_ds",
        subset="",
        data_dir="",
        concurrency=1,
        described={"backend": "cli"},
        queries=[
            QueryRecord(
                index=0, query="q", status="answered", search_latency_s=1.0, hits=hits
            )
        ],
        unanswered=[],
        executed_plans=[],
        wall_clock_s=1.0,
        sources_seen=["absent"],
        verdicts={},
        inventory={},
        ingest=IngestManifest(),
    )


def _cfg(tmp_path: Path, top_k: int) -> RunConfig:
    return RunConfig(
        endpoint="",
        data_dir=tmp_path,
        dataset="slice_ds",
        subset="",
        concurrency=1,
        vst_url=None,
        top_k=top_k,
        scoring="segment-overlap",
    )


def _score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, top_k: int) -> ScoredResult:
    monkeypatch.setitem(flows.DATASETS, "slice_ds", {"": "slice_ds/dataset.json"})
    (tmp_path / "slice_ds").mkdir(parents=True, exist_ok=True)
    (tmp_path / "slice_ds" / "dataset.json").write_text(json.dumps(GT))
    backend = SegmentOverlapScoring()
    cfg = _cfg(tmp_path, top_k)
    artifact = _artifact(
        [
            _hit("w", "2025-01-01T00:00:00Z", "2025-01-01T00:00:05Z"),
            _hit("x", "2025-01-01T00:00:00Z", "2025-01-01T00:00:05Z"),
            _hit("y", "2025-01-01T00:00:00Z", "2025-01-01T00:00:05Z"),
            _hit(
                "v", "2025-01-01T00:00:10Z", "2025-01-01T00:00:15Z"
            ),  # the match, rank 4
            _hit("z", "2025-01-01T00:00:00Z", "2025-01-01T00:00:05Z"),
        ]
    )
    gt = backend.load_ground_truth(cfg, artifact)
    return backend.score(artifact, gt, cfg)


def test_registry_exposes_segment_overlap_as_the_default() -> None:
    assert SCORING_BACKENDS["segment-overlap"] is SegmentOverlapScoring
    assert DEFAULT_SCORING == "segment-overlap"


def test_all_five_hits_score_when_top_k_allows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _score(tmp_path, monkeypatch, top_k=5)
    assert result.query_results[0]["recall"] == 1.0  # rank-4 match is in scope
    assert result.query_results[0]["total_retrieved"] == 5


def test_a3_dense_hits_are_sliced_to_top_k(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dense artifact must score against the baseline's candidate budget, not all of it."""
    result = _score(tmp_path, monkeypatch, top_k=2)
    assert result.query_results[0]["total_retrieved"] == 2  # sliced from 5 to 2
    assert result.query_results[0]["recall"] == 0.0  # the rank-4 match got sliced off


def test_segment_overlap_result_carries_no_scoring_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Byte-identity with the legacy runner: absence of scoring.model marks segment-overlap."""
    result = _score(tmp_path, monkeypatch, top_k=5)
    assert "scoring" not in result.to_output()
    assert result.scoring is None


def test_dense_slice_is_surfaced_in_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The A3 slice must be visible, not silent: the result records the budget."""
    result = _score(tmp_path, monkeypatch, top_k=2)
    assert result.summary["candidate_budget"] == {
        "scored_top_k": 2,
        "max_retrieved": 5,
        "queries_sliced": 1,
    }


def test_no_slice_means_no_candidate_budget_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Byte-parity: a normal top_k artifact is not sliced, so no budget key appears."""
    result = _score(tmp_path, monkeypatch, top_k=5)
    assert "candidate_budget" not in result.summary


def test_segment_overlap_refuses_a_gallery_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: segment-overlap has no segment GT for a clip-gallery artifact -- clean error."""
    monkeypatch.setitem(flows.DATASETS, "slice_ds", {"": "slice_ds/dataset.json"})
    (tmp_path / "slice_ds").mkdir(parents=True, exist_ok=True)
    (tmp_path / "slice_ds" / "dataset.json").write_text(json.dumps(GT))
    artifact = _artifact([])
    artifact.query_source = "gallery"
    artifact.gallery_gt = "gt.json"
    cfg = _cfg(tmp_path, top_k=5)
    with pytest.raises(SystemExit, match="clip gallery"):
        SegmentOverlapScoring().load_ground_truth(cfg, artifact)
