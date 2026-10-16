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

"""Stage isolation: ingest alone, retrieve alone, score alone, and the chain.

The centrepiece is the on-disk round trip -- retrieve writes a retrieval
artifact, then a SEPARATE score stage reads it back and reproduces the golden
fixture without any backend or deployment in the loop.

    python3 -m pytest scripts/tests/test_pipeline_stages.py
"""

# isort: skip_file

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _parity_harness as h
import flows
import run_eval_flows as rf
from flows import pipeline
from flows.artifacts import (
    IngestManifest,
    RunConfig,
    read_ingest_manifest,
    read_retrieval_artifact,
    write_ingest_manifest,
)

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "parity_baseline.json"


class _FakeIngestBackend:
    """Uploads succeed and prove indexing, so no VST/ES calls happen."""

    name = "fake-ingest"
    proves_indexing = True

    def describe(self) -> dict[str, Any]:
        return {"flow": "fake-ingest", "ingest_proof": "chunk-count"}

    def upload(self, video_file: Path) -> dict[str, Any]:
        return {
            "success": True,
            "video_name": video_file.stem,
            "sensor_id": f"sensor-{video_file.stem}",
            "upload_latency_s": 0.1,
            "file_size_mb": 1.0,
            "chunks_processed": 10,
            "upload_speed_mbps": 10.0,
        }


def _cfg(tmp_path: Path, stage: str, **overrides: Any) -> RunConfig:
    base: dict[str, Any] = {
        "endpoint": "http://fake:8000",
        "data_dir": tmp_path,
        "dataset": h.DATASET,
        "subset": h.SUBSET,
        "concurrency": 1,
        "vst_url": None,
        "tolerate_unanswered": 1,
        "use_dataset_decompositions": True,
        "run_name": "parity",
    }
    if stage == "score":
        # score resolves the dataset identity from the artifact (A2): leave unset.
        base.update({"dataset": None, "subset": None, "data_dir": None})
    base.update(overrides)
    return RunConfig(stage=stage, **base)


def test_ingest_alone_writes_a_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(flows.DATASETS, h.DATASET, {"": f"{h.DATASET}/dataset.json"})
    videos = tmp_path / h.DATASET / "videos"
    videos.mkdir(parents=True)
    (videos / "clip_a.mp4").write_bytes(b"x")
    (videos / "clip_b.mp4").write_bytes(b"x")

    out = tmp_path / "ingest.json"
    cfg = _cfg(
        tmp_path,
        "ingest",
        skip_existing=False,
        skip_readiness_wait=True,
        skip_index_probe=True,
        ingest_out=str(out),
    )
    result = pipeline.run_pipeline(
        cfg, _FakeIngestBackend(), h.make_backend(), None, None
    )

    assert result is None  # ingest stage does not score
    manifest = read_ingest_manifest(out)
    assert manifest.upload_stats is not None and manifest.ingest_description is not None
    assert manifest.upload_stats["successful"] == 2
    assert manifest.ingest_description["flow"] == "fake-ingest"


def test_retrieve_alone_writes_an_artifact_with_raw_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(flows.DATASETS, h.DATASET, {"": f"{h.DATASET}/dataset.json"})
    h.build_dataset(tmp_path)

    out = tmp_path / "retrieval.json"
    cfg = _cfg(tmp_path, "retrieve", retrieval_out=str(out))
    result = pipeline.run_pipeline(
        cfg, None, h.make_backend(), h.make_decomposer(), None
    )

    assert result is None  # retrieve stage does not score
    artifact = read_retrieval_artifact(out)
    assert artifact.dataset == h.DATASET
    assert artifact.data_dir == str(tmp_path)
    answered = [r for r in artifact.queries if r.status == "answered"]
    assert answered and all("average_precision" not in vars(r) for r in answered)
    assert len(artifact.unanswered) == 1


def test_retrieve_then_score_from_disk_reproduces_the_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(flows.DATASETS, h.DATASET, {"": f"{h.DATASET}/dataset.json"})
    h.build_dataset(tmp_path)

    # The golden was captured with this ingest context in the flow block; feed it
    # back via --ingest-in so the on-disk round trip reproduces it exactly.
    manifest_path = tmp_path / "ingest.json"
    write_ingest_manifest(
        manifest_path,
        IngestManifest(
            ingest_description={"flow": "fake-ingest", "ingest_proof": "none"}
        ),
    )
    artifact_path = tmp_path / "retrieval.json"
    pipeline.run_pipeline(
        _cfg(
            tmp_path,
            "retrieve",
            retrieval_out=str(artifact_path),
            ingest_in=str(manifest_path),
        ),
        None,
        h.make_backend(),
        h.make_decomposer(),
        None,
    )

    result_path = tmp_path / "result.json"
    scored = pipeline.run_pipeline(
        _cfg(
            tmp_path,
            "score",
            retrieval_in=str(artifact_path),
            output_file=str(result_path),
        ),
        None,
        None,
        None,
        None,
    )

    assert scored is not None and result_path.exists()
    expected = json.loads(GOLDEN.read_text())
    assert h.strip_nondeterministic(scored.to_output()) == expected


def test_default_run_also_dumps_the_artifact_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(flows.DATASETS, h.DATASET, {"": f"{h.DATASET}/dataset.json"})
    h.build_dataset(tmp_path)

    artifact_path = tmp_path / "retrieval.json"
    result_path = tmp_path / "result.json"
    cfg = _cfg(
        tmp_path,
        "run",
        retrieval_out=str(artifact_path),
        output_file=str(result_path),
        skip_ingest=True,
    )
    result = pipeline.run_pipeline(
        cfg, _FakeIngestBackend(), h.make_backend(), h.make_decomposer(), None
    )

    assert result is not None  # the full chain still scores
    assert artifact_path.exists()  # ... and the artifact was saved for re-scoring
    artifact = read_retrieval_artifact(artifact_path)
    assert any(r.status == "answered" for r in artifact.queries)


# ---------------------------------------------------------------------------
# parse_args stage validation
# ---------------------------------------------------------------------------


def test_score_stage_needs_no_endpoint_but_requires_retrieval_in() -> None:
    # No endpoint at all -- the whole point of A1.
    args = rf.parse_args(["--stage", "score", "--retrieval-in", "a.json"])
    assert args.stage == "score" and args.endpoint is None
    with pytest.raises(SystemExit):
        rf.parse_args(["--stage", "score"])


def test_endpoint_still_required_for_deployment_stages() -> None:
    for stage in ("run", "retrieve", "ingest"):
        with pytest.raises(SystemExit):
            rf.parse_args(["--stage", stage])


def test_retrieval_in_only_valid_for_score() -> None:
    with pytest.raises(SystemExit):
        rf.parse_args(
            [
                "--endpoint",
                "http://h:8000",
                "--stage",
                "run",
                "--retrieval-in",
                "a.json",
            ]
        )


def test_ingest_stage_rejects_skip_ingest() -> None:
    with pytest.raises(SystemExit):
        rf.parse_args(
            ["--endpoint", "http://h:8000", "--stage", "ingest", "--skip-ingest"]
        )


def test_destructive_flags_only_apply_to_run_or_ingest() -> None:
    with pytest.raises(SystemExit):
        rf.parse_args(
            [
                "--endpoint",
                "http://h:8000",
                "--stage",
                "retrieve",
                "--clear",
                "--confirm-delete",
            ]
        )


def test_score_stage_rejects_dry_run() -> None:
    """R2: --dry-run is meaningless for the offline score stage; reject it."""
    with pytest.raises(SystemExit):
        rf.parse_args(["--stage", "score", "--retrieval-in", "a.json", "--dry-run"])


def test_retrieve_strips_raw_from_artifact_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5: the artifact keeps the normalized hit (and _verification_source) but not _raw."""
    monkeypatch.setitem(flows.DATASETS, h.DATASET, {"": f"{h.DATASET}/dataset.json"})
    h.build_dataset(tmp_path)
    out = tmp_path / "retrieval.json"
    cfg = _cfg(tmp_path, "retrieve", retrieval_out=str(out))
    pipeline.run_pipeline(cfg, None, h.make_backend(), h.make_decomposer(), None)
    artifact = read_retrieval_artifact(out)
    answered = [r for r in artifact.queries if r.status == "answered"]
    assert answered
    for record in answered:
        for hit in record.hits:
            assert "_raw" not in hit
            assert "_verification_source" in hit
