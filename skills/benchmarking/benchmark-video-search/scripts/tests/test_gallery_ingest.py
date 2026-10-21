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

"""A6: run_ingest emits the clip->sensor ingestion manifest for a gallery,
so a clip/drive run is self-contained. Plus an end-to-end gallery flow
(ingest -> dense retrieve -> clip-relevance score).

    python3 -m pytest scripts/tests/test_gallery_ingest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flows import pipeline
from flows.artifacts import RunConfig, read_retrieval_artifact

# A 2-clip gallery release on disk: gt/queries_gt.json + additional_gt/clips_gt.json
GALLERY_GT = {
    "meta": {"set": "synthetic-gallery"},
    "gallery": [
        {"chunk_id": "c0", "video": "clips/c0.mp4"},
        {"chunk_id": "c1", "video": "clips/c1.mp4"},
    ],
    "queries": [
        {
            "query_id": "q0",
            "query": "red box",
            "relevant_clip_ids": ["c0"],
            "slice": "event",
            "query_domain": "event",
        }
    ],
}


class _FakeIngest:
    """Uploads each clip and records the sensor_id the manifest will carry."""

    name = "fake-ingest"
    proves_indexing = False

    def describe(self) -> dict[str, Any]:
        return {"flow": "vst-direct", "ingest_proof": "none"}

    def upload(self, vf: Path) -> dict[str, Any]:
        return {
            "success": True,
            "video_name": vf.stem,
            "sensor_id": f"sensor-{vf.stem}",
            "upload_latency_s": 0.1,
            "file_size_mb": 0.0,
        }


class _FakeQuery:
    """Returns a dense ranking: each gallery clip once, ranked by a fixed similarity."""

    name = "cli"

    def __init__(self) -> None:
        # Instance attribute, not a mutable class-attribute default (RUF012).
        self.decompositions: dict[str, dict] = {}

    def describe(self) -> dict[str, Any]:
        return {"backend": "cli", "full_gallery": True}

    def plan_for_query(self, q: str) -> dict[str, Any]:
        return {"path": "embed", "query": q, "source_type": "video_file"}

    def search(self, q: str) -> tuple[list[dict[str, Any]], float]:
        if q == "red box":  # relevant is c0 -> rank it first
            return (
                [
                    {
                        "video_name": "c0.mp4",
                        "sensor_id": "sensor-c0",
                        "similarity": 0.9,
                        "start_time": "2025-01-01T00:00:00Z",
                        "end_time": "2025-01-01T00:00:05Z",
                        "verification": {"result": "confirmed", "criteria_met": {}},
                        "_verification_source": "verification",
                    },
                    {
                        "video_name": "c1.mp4",
                        "sensor_id": "sensor-c1",
                        "similarity": 0.4,
                        "start_time": "2025-01-01T00:00:00Z",
                        "end_time": "2025-01-01T00:00:05Z",
                        "verification": {"result": "confirmed", "criteria_met": {}},
                        "_verification_source": "verification",
                    },
                ],
                1.0,
            )
        return [], 0.0


def _gallery_dir(tmp_path: Path) -> Path:
    """Write the 2-clip gallery release to disk."""
    root = tmp_path / "release"
    root.mkdir(parents=True)
    (root / "gt").mkdir()
    (root / "additional_gt").mkdir()
    (root / "clips").mkdir()
    (root / "additional_gt" / "clips_gt.json").write_text(
        json.dumps({"gallery": GALLERY_GT["gallery"]})
    )
    (root / "gt" / "queries_gt.json").write_text(json.dumps(GALLERY_GT))
    for c in ("c0", "c1"):
        (root / "clips" / f"{c}.mp4").write_bytes(b"fake")
    return root


def test_gallery_ingest_writes_clip_manifest(tmp_path: Path) -> None:
    """A6: --stage ingest --gallery-gt uploads the clips and writes the clip->sensor manifest."""
    root = _gallery_dir(tmp_path)
    cfg = RunConfig(
        endpoint="http://fake",
        data_dir=tmp_path,
        dataset="warehouse",
        subset="",
        concurrency=1,
        vst_url="http://fake:30888",
        stage="ingest",
        gallery_gt=str(root),
        ingest_out=str(tmp_path / "man.json"),
        skip_existing=False,
        skip_readiness_wait=True,
        skip_index_probe=True,
    )
    pipeline.run_pipeline(cfg, _FakeIngest(), _FakeQuery(), None, None)
    manifest = json.loads((tmp_path / "man.json").read_text())
    assert sorted(c["dataset_id"] for c in manifest["clips"]) == ["c0", "c1"]
    assert {c["sensor_id"] for c in manifest["clips"]} == {"sensor-c0", "sensor-c1"}
    assert {c["filename"] for c in manifest["clips"]} == {"c0.mp4", "c1.mp4"}


def test_gallery_flow_ingest_retrieve_score(tmp_path: Path) -> None:
    """End-to-end: ingest the gallery, retrieve densely, score with clip-relevance."""
    root = _gallery_dir(tmp_path)
    # 1. ingest -> manifest
    ingest_cfg = RunConfig(
        endpoint="http://fake",
        data_dir=tmp_path,
        dataset="warehouse",
        subset="",
        concurrency=1,
        vst_url="http://fake:30888",
        stage="ingest",
        gallery_gt=str(root),
        ingest_out=str(tmp_path / "man.json"),
        skip_existing=False,
        skip_readiness_wait=True,
        skip_index_probe=True,
    )
    pipeline.run_pipeline(ingest_cfg, _FakeIngest(), _FakeQuery(), None, None)
    # 2. retrieve -> artifact (dense, full gallery)
    retrieve_cfg = RunConfig(
        endpoint="http://fake",
        data_dir=tmp_path,
        dataset="warehouse",
        subset="",
        concurrency=1,
        vst_url="http://fake:30888",
        stage="retrieve",
        gallery_gt=str(root),
        full_gallery=True,
        skip_ingest=True,
        skip_readiness_wait=True,
        skip_index_probe=True,
        retrieval_out=str(tmp_path / "art.json"),
        tolerate_unanswered=0,
    )
    pipeline.run_pipeline(retrieve_cfg, None, _FakeQuery(), None, None)
    artifact = read_retrieval_artifact(tmp_path / "art.json")
    assert artifact.query_source == "gallery"
    assert sorted(r.query for r in artifact.queries) == ["red box"]
    # 3. score -> clip-relevance result (mAP should be 1.0: the relevant clip ranked first)
    score_cfg = RunConfig(
        endpoint="",
        data_dir=tmp_path,
        dataset="warehouse",
        subset="",
        concurrency=1,
        vst_url=None,
        stage="score",
        scoring="clip-relevance",
        gallery_gt=str(root),
        ingestion_manifest=str(tmp_path / "man.json"),
        retrieval_in=str(tmp_path / "art.json"),
        output_file=str(tmp_path / "result.json"),
    )
    result = pipeline.run_pipeline(score_cfg, None, None, None, None)
    assert result.scoring["model"] == "clip-relevance"
    assert result.summary["raw"]["mAP"] == pytest.approx(1.0)
    assert result.summary["raw"]["mAP_scope"] == "full-gallery"


def test_gallery_skip_ingest_never_uploads(tmp_path):
    root = _gallery_dir(tmp_path)
    cfg = RunConfig(
        endpoint="http://fake",
        data_dir=tmp_path,
        dataset="warehouse",
        subset="",
        concurrency=1,
        vst_url="http://fake:30888",
        gallery_gt=str(root),
        skip_ingest=True,
    )

    class NeverUpload(_FakeIngest):
        def upload(self, vf):
            raise AssertionError("skip-ingest must not upload")

    result = pipeline.run_ingest(cfg, NeverUpload(), _FakeQuery())
    assert result.upload_stats is None


def test_gallery_skipped_sources_keep_listed_identities(tmp_path, monkeypatch):
    root = _gallery_dir(tmp_path)
    monkeypatch.setattr(pipeline.flows, "list_sensor_names", lambda _: {"c0", "c1"})
    monkeypatch.setattr(
        pipeline.flows,
        "list_sensor_streams",
        lambda _: {"assigned-id-9": "c0", "assigned-id-4": "c1"},
    )
    cfg = RunConfig(
        endpoint="http://fake",
        data_dir=tmp_path,
        dataset="warehouse",
        subset="",
        concurrency=1,
        vst_url="http://fake:30888",
        gallery_gt=str(root),
        ingest_out=str(tmp_path / "manifest.json"),
        skip_existing=True,
        skip_readiness_wait=True,
        skip_index_probe=True,
    )
    pipeline.run_ingest(cfg, _FakeIngest(), _FakeQuery())
    clips = json.loads((tmp_path / "manifest.json").read_text())["clips"]
    assert {c["dataset_id"]: c["sensor_id"] for c in clips} == {
        "c0": "assigned-id-9",
        "c1": "assigned-id-4",
    }
