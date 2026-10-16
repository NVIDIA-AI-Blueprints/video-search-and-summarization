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

"""The clip-level scoring backends ported from the eval-replication bundle.

ClipRelevanceScoring wraps the bundle's pure ClipScorer; DriveScoring loads the
original Drive ``evaluate_domain_test.py`` for byte-level metric parity. These
tests build a synthetic gallery + ingestion manifest + dense retrieval artifact
and score it both ways.

    python3 -m pytest scripts/tests/test_scoring_clip_drive.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flows.artifacts import IngestManifest, QueryRecord, RetrievalArtifact, RunConfig
from flows.scoring import SCORING_BACKENDS, ClipRelevanceScoring, DriveScoring
from flows.scoring.clip_scoring import ClipScorer

# A minimal but correct stand-in for the Drive evaluate_domain_test.py, so the
# drive backend's dynamic loading + scoring is exercised without an external path.
DRIVE_SCORER_STUB = """
import numpy as np
K_VALUES = (1, 5, 10)
def load_gt(path):
    import json
    from pathlib import Path
    p = Path(path)
    return json.loads(p.read_text()) if p.is_file() else {"gallery": [], "queries": []}
def l2n(x):
    x = np.asarray(x, dtype=np.float32)
    return x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
def average_precision(sorted_labels):
    n_pos = np.sum(sorted_labels)
    if n_pos == 0: return 0.0
    hits = np.cumsum(sorted_labels)
    ranks = np.arange(1, len(sorted_labels) + 1)
    return float(np.sum((hits / ranks) * sorted_labels) / n_pos)
def ndcg_at_k(sorted_labels, k):
    n_pos = int(np.sum(sorted_labels))
    if n_pos == 0: return 0.0
    k = min(k, len(sorted_labels))
    d = 1.0 / np.log2(np.arange(2, k + 2))
    dcg = float(np.sum(sorted_labels[:k] * d))
    idcg = float(np.sum(d[: min(n_pos, k)]))
    return dcg / idcg if idcg else 0.0
def auc_score(scores, labels):
    n_pos = labels.sum(); n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0: return 1.0 if n_neg == 0 else 0.0
    order = np.argsort(scores)
    ranks = np.arange(1, len(labels) + 1, dtype=np.float64)
    return float((np.sum(ranks[labels[order] == 1]) - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))
def aggregate(rows, gallery_size):
    if not rows: return {}
    out = {"mAP": float(np.mean([r["ap"] for r in rows])), "num_queries": len(rows)}
    for k in K_VALUES:
        out[f"recall@{k}"] = float(np.mean([r[f"recall@{k}"] for r in rows]))
        out[f"hit@{k}"] = float(np.mean([r[f"hit@{k}"] for r in rows]))
    return out
"""

CLIPS = ["c0", "c1", "c2", "c3"]
GALLERY_GT = {
    "meta": {},
    "gallery": [{"chunk_id": c, "video": f"{c}.mp4"} for c in CLIPS],
    "queries": [
        {
            "query_id": "q0",
            "query": "find the red box",
            "relevant_clip_ids": ["c0", "c2"],
            "slice": "event",
            "query_domain": "event",
        },
        {
            "query_id": "q1",
            "query": "find the blue cart",
            "relevant_clip_ids": ["c1"],
            "slice": "pas",
            "query_domain": "pas",
        },
    ],
}
MANIFEST = {
    "clips": [
        {"dataset_id": c, "sensor_id": f"s{i}", "filename": f"{c}.mp4"}
        for i, c in enumerate(CLIPS)
    ]
}


def _write_gt_and_manifest(tmp_path: Path) -> tuple[str, str]:
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps(GALLERY_GT))
    man_path = tmp_path / "ingestion.json"
    man_path.write_text(json.dumps(MANIFEST))
    return str(gt_path), str(man_path)


def _hits_ranked(order_sims: list[tuple[str, float]]) -> list[dict[str, Any]]:
    """One dense hit per clip, ranked: (clip_id, similarity)."""
    return [
        {
            "sensor_id": f"s{CLIPS.index(cid)}",
            "video_name": f"{cid}.mp4",
            "similarity": sim,
            "start_time": "2025-01-01T00:00:00Z",
            "end_time": "2025-01-01T00:00:05Z",
            "verification": {"result": "confirmed", "criteria_met": {}},
            "_verification_source": "verification",
        }
        for cid, sim in order_sims
    ]


def _artifact(
    tmp_path: Path, hits_by_query: dict[str, list[dict[str, Any]]]
) -> RetrievalArtifact:
    records = [
        QueryRecord(
            index=i,
            query=q,
            status="answered",
            search_latency_s=1.0,
            hits=hits_by_query[q],
        )
        for i, q in enumerate(hits_by_query)
    ]
    return RetrievalArtifact(
        dataset="drive-gallery",
        subset="",
        data_dir=str(tmp_path),
        concurrency=1,
        described={"backend": "cli", "full_gallery": True},
        queries=records,
        unanswered=[],
        executed_plans=[],
        wall_clock_s=1.0,
        sources_seen=["verification"],
        verdicts={"confirmed": 8},
        inventory={"stable": True},
        ingest=IngestManifest(ingest_description={"flow": "test"}),
        query_source="gallery",
        gallery_gt="gt.json",
    )


def _cfg(
    tmp_path: Path,
    scoring: str,
    gallery_gt: str,
    manifest: str,
    scorer_dir: str | None = None,
) -> RunConfig:
    return RunConfig(
        endpoint="",
        data_dir=tmp_path,
        dataset="drive-gallery",
        subset="",
        concurrency=1,
        vst_url=None,
        scoring=scoring,
        gallery_gt=gallery_gt,
        ingestion_manifest=manifest,
        drive_scorer_dir=scorer_dir,
        full_gallery=True,
    )


# q0 relevant {c0,c2}: return them ranked first. q1 relevant {c1}: rank it first.
HITS = {
    "find the red box": _hits_ranked(
        [("c0", 0.9), ("c2", 0.8), ("c1", 0.3), ("c3", 0.1)]
    ),
    "find the blue cart": _hits_ranked(
        [("c1", 0.95), ("c0", 0.4), ("c2", 0.3), ("c3", 0.2)]
    ),
}


def test_clip_relevance_perfect_dense_ranking(tmp_path: Path) -> None:
    gt, manifest = _write_gt_and_manifest(tmp_path)
    backend = ClipRelevanceScoring()
    cfg = _cfg(tmp_path, "clip-relevance", gt, manifest)
    artifact = _artifact(tmp_path, HITS)
    gt_ctx = backend.load_ground_truth(cfg, artifact)
    result = backend.score(artifact, gt_ctx, cfg)

    out = result.to_output()
    assert result.scoring["model"] == "clip-relevance"
    raw = result.summary["raw"]
    # Both queries rank their relevant clips first.
    assert raw["mAP"] == pytest.approx(1.0)
    assert raw["recall"] == pytest.approx(1.0)
    assert raw["mAP_scope"] == "full-gallery"
    assert result.summary["gallery_size"] == 4
    # scoring block is present on the clip-level result (unlike segment-overlap).
    assert "scoring" in out


def test_clip_relevance_partial_coverage_is_labeled(tmp_path: Path) -> None:
    gt, manifest = _write_gt_and_manifest(tmp_path)
    backend = ClipRelevanceScoring()
    cfg = _cfg(tmp_path, "clip-relevance", gt, manifest)
    # Only 2 of 4 clips returned -> coverage < full, and ClipScorer must say so.
    partial = {
        "find the red box": _hits_ranked([("c0", 0.9), ("c2", 0.8)]),
        "find the blue cart": _hits_ranked([("c1", 0.95), ("c3", 0.2)]),
    }
    result = backend.score(
        _artifact(tmp_path, partial),
        backend.load_ground_truth(cfg, _artifact(tmp_path, partial)),
        cfg,
    )
    assert result.summary["raw"]["mAP_scope"].startswith("observed-ranking")
    assert result.summary["raw"]["complete_rankings"] == 0


def test_drive_perfect_dense_ranking(tmp_path: Path) -> None:
    gt, manifest = _write_gt_and_manifest(tmp_path)
    scorer_dir = tmp_path / "drive_scorer"
    scorer_dir.mkdir()
    (scorer_dir / "evaluate_domain_test.py").write_text(DRIVE_SCORER_STUB)
    backend = DriveScoring()
    cfg = _cfg(tmp_path, "drive", gt, manifest, scorer_dir=str(scorer_dir))
    artifact = _artifact(tmp_path, HITS)
    ctx = backend.load_ground_truth(cfg, artifact)
    result = backend.score(artifact, ctx, cfg)

    assert result.scoring["model"] == "drive"
    assert result.summary["mAP"] == pytest.approx(1.0)
    assert result.summary["hit@1"] == pytest.approx(1.0)
    assert result.summary["gallery_size"] == 4
    assert result.scoring["scorer_sha256"]


def test_drive_requires_full_gallery_coverage(tmp_path: Path) -> None:
    gt, manifest = _write_gt_and_manifest(tmp_path)
    scorer_dir = tmp_path / "drive_scorer"
    scorer_dir.mkdir()
    (scorer_dir / "evaluate_domain_test.py").write_text(DRIVE_SCORER_STUB)
    backend = DriveScoring()
    cfg = _cfg(tmp_path, "drive", gt, manifest, scorer_dir=str(scorer_dir))
    # A bounded (non-dense) artifact: only 2 of 4 clips per query -> must raise.
    partial = {q: hits[:2] for q, hits in HITS.items()}
    artifact = _artifact(tmp_path, partial)
    ctx = backend.load_ground_truth(cfg, artifact)
    with pytest.raises(ValueError, match="exactly once"):
        backend.score(artifact, ctx, cfg)


def test_clip_backends_require_gallery_inputs(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, HITS)
    cfg = RunConfig(
        endpoint="",
        data_dir=tmp_path,
        dataset="d",
        subset="",
        concurrency=1,
        vst_url=None,
        scoring="clip-relevance",
    )
    with pytest.raises(SystemExit, match="gallery-gt"):
        ClipRelevanceScoring().load_ground_truth(cfg, artifact)
    with pytest.raises(SystemExit, match="gallery-gt"):
        DriveScoring().load_ground_truth(cfg, artifact)


def test_clip_scorer_parity_with_original_drive_math() -> None:
    """ClipScorer's AP/nDCG must match the original Drive math on the same labels."""
    import math

    mapping = {
        c: {"sensor_id": f"s{i}", "filename": f"{c}.mp4"} for i, c in enumerate(CLIPS)
    }
    q = {"query_id": "q0", "query": "t", "relevant_clip_ids": ["c0", "c2"]}
    scorer = ClipScorer(mapping, [q])
    # relevant at ranks 1 and 3
    hits = [
        {"sensor_id": "s0"},
        {"sensor_id": "s1"},
        {"sensor_id": "s2"},
        {"sensor_id": "s3"},
    ]
    result = scorer.score(q, hits)
    # AP = (1/1 + 2/3) / 2 relevant
    assert result["average_precision"] == pytest.approx((1 + 2 / 3) / 2)
    # nDCG@10: dcg = 1/log2(2) + 1/log2(4); idcg = 1/log2(2) + 1/log2(3)
    dcg = 1 / math.log2(2) + 1 / math.log2(4)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert result["ndcg"] == pytest.approx(dcg / idcg)


def test_scoring_registry_includes_clip_and_drive() -> None:
    assert SCORING_BACKENDS["clip-relevance"] is ClipRelevanceScoring
    assert SCORING_BACKENDS["drive"] is DriveScoring
    assert sorted(SCORING_BACKENDS) == ["clip-relevance", "drive", "segment-overlap"]


def _eval_dataset_artifact(tmp_path: Path) -> RetrievalArtifact:
    """An artifact from a normal eval-dataset (segment-overlap) retrieval run."""
    return RetrievalArtifact(
        dataset="warehouse",
        subset="",
        data_dir=str(tmp_path),
        concurrency=1,
        described={"backend": "cli"},
        queries=[
            QueryRecord(
                index=0, query="q", status="answered", search_latency_s=1.0, hits=[]
            )
        ],
        unanswered=[],
        executed_plans=[],
        wall_clock_s=1.0,
        sources_seen=[],
        verdicts={},
        inventory={},
        ingest=IngestManifest(),
        query_source="eval-dataset",
    )


def test_clip_and_drive_refuse_an_eval_dataset_artifact(tmp_path: Path) -> None:
    """F1: clip-level scorers must refuse a non-gallery artifact with a clean error."""
    art = _eval_dataset_artifact(tmp_path)
    cfg = RunConfig(
        endpoint="",
        data_dir=tmp_path,
        dataset="warehouse",
        subset="",
        concurrency=1,
        vst_url=None,
        scoring="clip-relevance",
        gallery_gt="g.json",
        ingestion_manifest="m.json",
    )
    with pytest.raises(SystemExit, match="clip gallery"):
        ClipRelevanceScoring().load_ground_truth(cfg, art)
    with pytest.raises(SystemExit, match="clip gallery"):
        DriveScoring().load_ground_truth(cfg, art)


def test_clip_relevance_gates_unanswered(tmp_path: Path) -> None:
    """F3: the shared unanswered gate applies to clip-level scoring too."""
    gt, manifest = _write_gt_and_manifest(tmp_path)
    backend = ClipRelevanceScoring()
    cfg = _cfg(tmp_path, "clip-relevance", gt, manifest)
    # 1 answered + 2 unanswered, tolerate 0 -> must abort.
    art = _artifact(tmp_path, HITS)
    art.unanswered = [
        {"index": 2, "query": "x", "reason": "timeout"},
        {"index": 3, "query": "y", "reason": "timeout"},
    ]
    art.queries.append(
        QueryRecord(index=2, query="x", status="unanswerable", error="timeout")
    )
    art.queries.append(
        QueryRecord(index=3, query="y", status="unanswerable", error="timeout")
    )
    art.tolerate_unanswered = 0
    with pytest.raises(SystemExit, match="unanswered"):
        backend.score(art, backend.load_ground_truth(cfg, art), cfg)
