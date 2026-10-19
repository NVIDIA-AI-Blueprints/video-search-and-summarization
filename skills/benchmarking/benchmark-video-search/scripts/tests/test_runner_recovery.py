# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Campaign safety: gallery membership, durable publication and compatible resume."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flows
import run_eval_flows as runner
from flows import pipeline
from flows.artifacts import (
    RunConfig,
    atomic_write_json,
    read_retrieval_artifact,
)
from flows.query import CliExitError, CliQueryBackend


def config(tmp_path, **overrides):
    gallery = tmp_path / "gallery.json"
    if not gallery.exists():
        atomic_write_json(
            gallery,
            {
                "gallery": [
                    {"chunk_id": "c0", "video": "one/c0.mp4"},
                    {"chunk_id": "c1", "video": "two/c1.mp4"},
                ],
                "queries": [
                    {"query_id": "q0", "query": "first", "relevant_clip_ids": ["c0"]},
                    {"query_id": "q1", "query": "second", "relevant_clip_ids": ["c1"]},
                ],
            },
        )
    for path in (tmp_path / "one/c0.mp4", tmp_path / "two/c1.mp4"):
        path.parent.mkdir(exist_ok=True)
        path.touch()
    identity = tmp_path / "ingestion.json"
    if not identity.exists():
        atomic_write_json(
            identity,
            {
                "clips": [
                    {"dataset_id": "c0", "sensor_id": "s0", "filename": "c0.mp4"},
                    {"dataset_id": "c1", "sensor_id": "s1", "filename": "c1.mp4"},
                ]
            },
        )
    overrides.setdefault("ingestion_manifest", str(identity))
    return RunConfig(
        endpoint="unused",
        data_dir=tmp_path,
        dataset="warehouse",
        subset="",
        concurrency=1,
        vst_url=None,
        stage="retrieve",
        gallery_gt=str(gallery),
        retrieval_out=str(tmp_path / "run.retrieval.json"),
        **overrides,
    )


class RecordingBackend(CliQueryBackend):
    def __init__(self, fail=None, **kwargs):
        super().__init__(["vss"], **kwargs)
        self.calls = []
        self.fail = fail

    def search(self, query, query_id=None):
        self.calls.append(query_id or query)
        if query == self.fail:
            raise CliExitError("temporary backend failure", returncode=3)
        plan = self.plan_for_query(query, query_id=query_id)
        self.executed_plans.append(plan)
        self.timings_by_query[query_id or query] = {"identity": query_id}
        return [{"sensor_id": "s0", "video_name": "c0", "similarity": 0.5}], 0.1


class FakeIngest:
    name = "fake"
    proves_indexing = True

    def __init__(self, fail=None):
        self.paths = []
        self.fail = fail

    def describe(self):
        return {"backend": "fake"}

    def upload(self, path):
        self.paths.append(path)
        return {
            "success": path.stem != self.fail,
            "sensor_id": "s-" + path.stem,
            "video_name": path.stem,
            "upload_latency_s": 0.01,
            "error": "timeout" if path.stem == self.fail else None,
        }


def ingest_cfg(cfg, **kwargs):
    return replace(
        cfg,
        stage="ingest",
        skip_existing=False,
        skip_readiness_wait=True,
        skip_index_probe=True,
        ingest_out=str(cfg.data_dir / "mapping.json"),
        **kwargs,
    )


def test_gallery_uploads_exact_declared_media_across_directories(tmp_path):
    cfg = config(tmp_path)
    (tmp_path / "one/unrelated.mp4").touch()
    backend = FakeIngest()
    pipeline.run_ingest(ingest_cfg(cfg), backend, RecordingBackend())
    assert {path.relative_to(tmp_path).as_posix() for path in backend.paths} == {
        "one/c0.mp4",
        "two/c1.mp4",
    }
    mapping = json.loads((tmp_path / "mapping.json").read_text())
    assert {row["dataset_id"] for row in mapping["clips"]} == {"c0", "c1"}


def test_cleanup_retains_gallery_instead_of_default_dataset(tmp_path, monkeypatch):
    cfg = ingest_cfg(config(tmp_path), only_dataset=True)
    warehouse = tmp_path / "warehouse/videos"
    warehouse.mkdir(parents=True)
    (warehouse / "warehouse.mp4").touch()
    monkeypatch.setattr(
        flows,
        "list_sensor_streams",
        lambda *args: {"s0": "c0.mp4", "s1": "c1.mp4", "foreign": "warehouse.mp4"},
    )
    deleted = []
    monkeypatch.setattr(
        pipeline.requests,
        "delete",
        lambda url, **kwargs: deleted.append(url) or Mock(status_code=200),
    )
    pipeline.run_ingest(cfg, FakeIngest(), RecordingBackend())
    assert deleted == ["unused/api/v1/videos/foreign"]


@pytest.mark.parametrize("failed", ["c0", "c1"])
def test_failed_upload_preserves_successful_manifest(tmp_path, failed):
    cfg = ingest_cfg(config(tmp_path))
    original = b'{"clips":[{"dataset_id":"old","sensor_id":"old-sensor"}]}\n'
    Path(cfg.ingest_out).write_bytes(original)
    with pytest.raises(SystemExit, match="upload.*failed"):
        pipeline.run_ingest(cfg, FakeIngest(fail=failed), RecordingBackend())
    assert Path(cfg.ingest_out).read_bytes() == original


def test_missing_gallery_media_aborts_before_cleanup(tmp_path, monkeypatch):
    cfg = ingest_cfg(config(tmp_path), only_dataset=True)
    (tmp_path / "two/c1.mp4").unlink()
    prune = Mock()
    monkeypatch.setattr(pipeline, "prune_foreign_videos", prune)
    with pytest.raises(SystemExit, match="every declared gallery clip"):
        pipeline.run_ingest(cfg, FakeIngest(), RecordingBackend())
    prune.assert_not_called()


def test_atomic_publication_preserves_old_document_on_serialization_failure(tmp_path):
    path = tmp_path / "result.json"
    atomic_write_json(path, {"old": True})
    with pytest.raises(TypeError):
        atomic_write_json(path, {"unserializable": object()})
    assert json.loads(path.read_text()) == {"old": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["result.json"]


def test_late_failure_saves_full_aborted_cohort_and_resume_reuses_answered(tmp_path):
    cfg = config(tmp_path)
    with pytest.raises(SystemExit, match="Partial retrieval saved"):
        pipeline.run_pipeline(cfg, None, RecordingBackend(fail="second"), None, None)
    artifact = read_retrieval_artifact(cfg.retrieval_out)
    assert artifact.described["retrieval_status"] == "aborted"
    assert [(row.query_id, row.status) for row in artifact.queries] == [
        ("q0", "answered"),
        ("q1", "unanswerable"),
    ]
    assert artifact.queries[0].hits
    assert "temporary backend failure" in artifact.queries[1].error
    assert "temporary backend failure" in artifact.described["retrieval_error"]
    backend = RecordingBackend()
    resumed = replace(cfg, resume_from=str(cfg.retrieval_out) + ".checkpoint")
    pipeline.run_pipeline(resumed, None, backend, None, None)
    assert backend.calls == ["q1"]
    recovered = read_retrieval_artifact(cfg.retrieval_out)
    assert recovered.described["retrieval_status"] == "completed"
    assert recovered.described["resume_reused_queries"] == 1
    assert len(recovered.executed_plans) == 2
    assert all(row.status == "answered" for row in recovered.queries)


@pytest.mark.parametrize(
    "changed",
    [
        "budget",
        "cohort",
        "ingestion",
        "models",
        "preprocessing",
        "source",
        "endpoint",
        "concurrency",
        "timeout",
        "cwd",
    ],
)
def test_resume_rejects_changed_campaign_identity(tmp_path, changed, monkeypatch):
    cfg = config(tmp_path)
    mapping = tmp_path / "identity.json"
    atomic_write_json(mapping, {"ingestion": 1})
    cfg.ingestion_manifest = str(mapping)
    pipeline.run_pipeline(cfg, None, RecordingBackend(), None, None)
    resumed = replace(cfg, resume_from=str(cfg.retrieval_out) + ".checkpoint")
    backend = RecordingBackend()
    if changed == "budget":
        backend.top_k = 13
    elif changed == "cohort":
        gt = json.loads(Path(cfg.gallery_gt).read_text())
        gt["queries"][0]["query_id"] = "changed"
        atomic_write_json(cfg.gallery_gt, gt)
    elif changed == "ingestion":
        atomic_write_json(mapping, {"ingestion": 2})
    elif changed == "models":
        backend.deployment_models = {"embed": ["different"]}
    elif changed == "endpoint":
        resumed.endpoint = "another deployment"
    elif changed == "concurrency":
        resumed.concurrency = 2
    elif changed == "timeout":
        backend.timeout = 9
    elif changed == "cwd":
        backend.cwd = "/another/checkout"
    elif changed == "source":
        from flows import provenance

        original_capture = provenance.capture_provenance

        def changed_capture(*args, **kwargs):
            result = original_capture(*args, **kwargs)
            result["source_identity"]["implementation_sha256"] = "changed"
            return result

        monkeypatch.setattr(provenance, "capture_provenance", changed_capture)
    else:
        preprocessing = tmp_path / "preprocessing.json"
        atomic_write_json(preprocessing, {"size": 224})
        resumed.preprocessing_provenance = str(preprocessing)
    with pytest.raises(SystemExit, match="identity differ|another run"):
        pipeline.run_pipeline(resumed, None, backend, None, None)
    assert backend.calls == []


def test_resume_does_not_relabel_an_unrelated_destination_checkpoint(tmp_path):
    cfg = config(tmp_path)
    pipeline.run_pipeline(cfg, None, RecordingBackend(), None, None)
    source = str(cfg.retrieval_out) + ".checkpoint"
    destination = tmp_path / "other.json"
    other = replace(cfg, retrieval_out=str(destination))
    pipeline.run_pipeline(other, None, RecordingBackend(top_k=13), None, None)
    header = Path(str(destination) + ".checkpoint/checkpoint.json")
    original = header.read_bytes()
    with pytest.raises(SystemExit, match="another run"):
        pipeline.run_pipeline(
            replace(other, resume_from=source), None, RecordingBackend(), None, None
        )
    assert header.read_bytes() == original


def test_corrupt_query_checkpoint_reruns_only_that_query(tmp_path):
    cfg = config(tmp_path)
    pipeline.run_pipeline(cfg, None, RecordingBackend(), None, None)
    checkpoint_dir = Path(str(cfg.retrieval_out) + ".checkpoint")
    (checkpoint_dir / "query-00000001.json").write_text('{"truncated"')
    backend = RecordingBackend()
    pipeline.run_pipeline(
        replace(cfg, resume_from=str(checkpoint_dir)), None, backend, None, None
    )
    assert backend.calls == ["q1"]


def test_resume_artifact_and_interrupt_are_supported(tmp_path):
    cfg = config(tmp_path)

    class Interrupt(RecordingBackend):
        def search(self, query, query_id=None):
            if query_id == "q1":
                raise KeyboardInterrupt()
            return super().search(query, query_id=query_id)

    with pytest.raises(SystemExit, match="Partial retrieval saved"):
        pipeline.run_pipeline(cfg, None, Interrupt(), None, None)
    backend = RecordingBackend()
    pipeline.run_pipeline(
        replace(cfg, resume_from=cfg.retrieval_out), None, backend, None, None
    )
    assert backend.calls == ["q1"]


def test_duplicate_text_queries_keep_separate_evidence_and_timings(
    tmp_path, monkeypatch
):
    cfg = config(tmp_path)
    cfg.concurrency = 2
    cfg.decomposition_out = str(tmp_path / "evidence")
    gt = json.loads(Path(cfg.gallery_gt).read_text())
    for row in gt["queries"]:
        row["query"] = "blue shirt"
    atomic_write_json(cfg.gallery_gt, gt)

    class Response:
        status_code = 200
        text = '{"choices":[]}'

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"query":"blue shirt","attributes":["blue shirt"],"has_action":false}'
                        }
                    }
                ]
            }

    post = Mock(return_value=Response())
    monkeypatch.setattr("flows.decompose.requests.post", post)
    decomposer = flows.LiveDecomposer(
        "unused", repo_root=flows.REPO_ROOT, model="fixed"
    )
    pipeline.run_pipeline(cfg, None, RecordingBackend(), decomposer, None)
    artifact = read_retrieval_artifact(cfg.retrieval_out)
    assert {row.query_id for row in artifact.queries} == {"q0", "q1"}
    assert all(row.timings == {"identity": row.query_id} for row in artifact.queries)
    evidence = [
        json.loads(path.read_text())
        for path in Path(cfg.decomposition_out).glob("*.json")
    ]
    assert {row["query_id"] for row in evidence} == {"q0", "q1"}
    assert len({row.evidence["decomposition_file"] for row in artifact.queries}) == 2
    replay = flows.load_decompositions(cfg.decomposition_out)
    backend = RecordingBackend(decompositions=replay)
    replay_cfg = replace(cfg, retrieval_out=str(tmp_path / "replay.json"))
    pipeline.run_pipeline(replay_cfg, None, backend, None, None)
    assert post.call_count == 2
    assert {
        row.query_id
        for row in read_retrieval_artifact(replay_cfg.retrieval_out).queries
    } == {"q0", "q1"}


def test_scoring_preflight_occurs_before_any_search(tmp_path):
    cfg = config(
        tmp_path,
        scoring="clip-relevance",
        ingestion_manifest=str(tmp_path / "missing.json"),
    )
    backend = RecordingBackend()
    with pytest.raises(SystemExit, match="ingestion manifest not found"):
        pipeline.run_pipeline(cfg, None, backend, None, None)
    assert backend.calls == []


def test_resume_cli_requires_unchanged_ingestion_and_one_scenario():
    with pytest.raises(SystemExit, match="2"):
        runner.parse_args(["--endpoint", "unused", "--resume-from", "old"])
    args = runner.parse_args(
        ["--endpoint", "unused", "--stage", "retrieve", "--resume-from", "old"]
    )
    assert args.resume_from == "old"


def test_resume_requires_explicit_ingestion_identity(tmp_path):
    cfg = config(tmp_path)
    cfg.ingestion_manifest = None
    pipeline.run_pipeline(cfg, None, RecordingBackend(), None, None)
    backend = RecordingBackend()
    with pytest.raises(SystemExit, match="unchanged indexed data cannot be verified"):
        pipeline.run_pipeline(
            replace(cfg, resume_from=str(cfg.retrieval_out) + ".checkpoint"),
            None,
            backend,
            None,
            None,
        )
    assert backend.calls == []


def test_empty_domain_aborts_before_any_upload_or_cleanup(tmp_path, monkeypatch):
    cfg = ingest_cfg(config(tmp_path, query_domain="absent"), only_dataset=True)
    backend = FakeIngest()
    prune = Mock()
    monkeypatch.setattr(pipeline, "prune_foreign_videos", prune)
    with pytest.raises(SystemExit, match="no queries matched"):
        pipeline.run_ingest(cfg, backend, RecordingBackend())
    assert backend.paths == []
    prune.assert_not_called()


def test_resume_rejects_negative_checkpoint_indices(tmp_path):
    cfg = config(tmp_path)
    pipeline.run_pipeline(cfg, None, RecordingBackend(), None, None)
    checkpoint_dir = Path(str(cfg.retrieval_out) + ".checkpoint")
    path = checkpoint_dir / "query-00000000.json"
    record = json.loads(path.read_text())
    record["index"] = -1
    atomic_write_json(path, record)
    backend = RecordingBackend()
    with pytest.raises(SystemExit, match="query identity does not match"):
        pipeline.run_pipeline(
            replace(cfg, resume_from=str(checkpoint_dir)), None, backend, None, None
        )
    assert backend.calls == []


def test_optional_decomposition_diagnostics_cannot_change_valid_outcome(
    tmp_path, monkeypatch
):
    cfg = config(tmp_path)
    cfg.decomposition_out = str(tmp_path / "unwritable-evidence")

    class Decomposer(flows.LiveDecomposer):
        def decompose(
            self, query, video_sources=None, evidence_sink=None, query_id=None
        ):
            parsed = {"query": query, "attributes": [], "has_action": False}
            evidence_sink(
                {
                    "query_id": query_id,
                    "original_query": query,
                    "raw_response": "raw provider answer",
                    "parsed": parsed,
                }
            )
            return parsed, 0.01

    original_write = pipeline.atomic_write_json

    def write(path, payload):
        if Path(path).parent == Path(cfg.decomposition_out):
            raise OSError("diagnostic volume unavailable")
        return original_write(path, payload)

    monkeypatch.setattr(pipeline, "atomic_write_json", write)
    decomposer = Decomposer("unused", repo_root=flows.REPO_ROOT, model="fixed")
    pipeline.run_pipeline(cfg, None, RecordingBackend(), decomposer, None)
    records = read_retrieval_artifact(cfg.retrieval_out).queries
    assert all(row.status == "answered" for row in records)
    assert all(
        "diagnostic volume unavailable"
        in row.evidence["decomposition_diagnostic_error"]
        for row in records
    )
    assert all(
        row.evidence["decomposition_evidence"]["raw_response"] == "raw provider answer"
        for row in records
    )


def test_concurrent_fresh_checkpoint_creation_preserves_existing_header(
    tmp_path, monkeypatch
):
    cfg = config(tmp_path)
    directory = Path(str(cfg.retrieval_out) + ".checkpoint")
    directory.mkdir()
    header = directory / "checkpoint.json"
    atomic_write_json(header, {"existing": "another campaign"})
    original = header.read_bytes()
    original_exists = Path.exists
    # Reproduce another process creating the destination after the initial check.
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: False if path == directory else original_exists(path),
    )
    backend = RecordingBackend()
    with pytest.raises(SystemExit, match="checkpoint already exists"):
        pipeline.run_pipeline(cfg, None, backend, None, None)
    assert header.read_bytes() == original
    assert backend.calls == []
