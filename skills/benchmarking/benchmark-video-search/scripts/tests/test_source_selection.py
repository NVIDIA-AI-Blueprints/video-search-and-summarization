# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Source diversity keeps full ranking provenance and optional selected verification."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flows
import run_eval_flows as runner
from flows.artifacts import IngestManifest, QueryRecord, RunConfig
from flows.query import CliQueryBackend, QueryUnanswerableError
from flows.source_selection import validate_selection


def pool():
    return [{"sensor_id": sensor, "video_name": sensor, "similarity": .9 - i * .1,
             "object_ids": [str(i)], "critic_result": None} for i, sensor in enumerate(["a", "a", "b"])]


def envelope(original=None):
    original = pool() if original is None else original
    indices = [0, 2] if original else []
    return {"original_pool": original, "selected_indices": indices,
            "policy_version": "source-cap-v1", "max_hits_per_source": 1,
            "requested_top_k": 2, "candidate_limit": 100,
            "counts": {"original_pool": len(original), "selected": len(indices),
                       "known_sources": len({hit["sensor_id"] for hit in original}), "unknown_rows": 0,
                       "excluded_by_source_cap": max(0, len(original) - len(indices)), "excluded_by_top_k": 0},
            "stages": [], "completeness": {"complete": True, "snapshot_verified": True, "expected_scans": 1,
                "scans": [{"exhaustive": True, "snapshot_verified": True, "budget": 100,
                           "eligible_documents": len(original), "scored_documents": len(original),
                           "snapshot_digest": "1" * 64, "snapshot_scope_digest": "2" * 64,
                           "snapshot_indices": [{"name": "index", "uuid": "uuid"}]}]}}


def backend():
    return CliQueryBackend(["vss"], search_path="attribute", attribute_input="original",
                           candidate_limit=100, retrieval_pool_size=2, max_hits_per_source=1)


def test_preserves_original_pool_and_selected_annotations(monkeypatch):
    original = pool()
    selected = [copy.deepcopy(original[i]) for i in [0, 2]]
    selected[0]["critic_result"] = {"result": "rejected"}
    body = {"data": selected, "source_selection": envelope(original)}
    monkeypatch.setattr("flows.query.subprocess.run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(body), stderr=""))
    b = backend()
    hits, _ = b.search("red", query_id="q")
    assert hits == original
    assert b.metadata_by_query["q"]["_selected_results"] == selected
    assert "original_pool" not in b.metadata_by_query["q"]["selection"]
    argv = b.build_argv("red")
    assert argv[argv.index("--top-k") + 1] == "2"
    assert argv[argv.index("--candidate-limit") + 1] == "100"


@pytest.mark.parametrize("change", ["missing", "bool_index", "duplicate", "wrong_order", "wrong_hit", "incomplete", "wrong_cap"])
def test_malformed_or_missing_selection_fails_query(monkeypatch, change):
    original = pool()
    selected = [original[0], original[2]]
    e = envelope(original)
    if change == "bool_index": e["selected_indices"] = [False, 2]
    if change == "duplicate": e["selected_indices"] = [0, 0]
    if change == "wrong_order": e["selected_indices"] = [2, 0]
    if change == "wrong_hit": selected = [original[0], original[1]]
    if change == "incomplete": e["completeness"]["complete"] = False
    if change == "wrong_cap": e["max_hits_per_source"] = 2
    body = {"data": selected}
    if change != "missing": body["source_selection"] = e
    monkeypatch.setattr("flows.query.subprocess.run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(body), stderr=""))
    with pytest.raises(QueryUnanswerableError): backend().search("red")


def test_artifact_distinguishes_disabled_from_enabled_empty():
    disabled = QueryRecord(index=0, query="q", status="answered")
    assert "selected_hits" not in disabled.to_dict()
    assert QueryRecord.from_dict(disabled.to_dict()).selected_hits is None
    e = envelope([])
    metadata = validate_selection([], [], e)
    enabled = QueryRecord(index=0, query="q", status="answered", selected_hits=[], selection=metadata)
    assert enabled.to_dict()["selected_hits"] == []
    assert QueryRecord.from_dict(enabled.to_dict()).selected_hits == []


@pytest.mark.parametrize("extra", [["--scenarios", "embed-only"], ["--source-type", "rtsp"],
                                    ["--stage", "ingest"], ["--max-hits-per-source", "0"]])
def test_rejects_scope_before_ingestion(extra):
    with pytest.raises(SystemExit):
        runner.parse_args(["--endpoint", "unused", "--gallery-gt", "g", "--ingestion-manifest", "m",
                           "--max-hits-per-source", "1", *extra])


def test_selection_parser_and_config_defaults():
    args = runner.parse_args(["--endpoint", "unused", "--gallery-gt", "g", "--ingestion-manifest", "m",
                              "--max-hits-per-source", "1"])
    assert RunConfig.from_args(args, None).max_hits_per_source == 1
    assert "attribute-only" in args.scenarios


def test_old_cli_rejected_before_retrieval(monkeypatch):
    args = runner.parse_args(["--endpoint", "unused", "--gallery-gt", "g", "--ingestion-manifest", "m",
                              "--max-hits-per-source", "1", "--skip-vss-preflight", "--skip-vss-configure", "--no-original-query"])
    monkeypatch.setattr(flows, "resolve_vss_cmd", lambda *a: (["vss"], "test"))
    monkeypatch.setattr(flows, "cli_supports_flag", lambda *a, **k: False)
    with pytest.raises(SystemExit, match="before ingestion"):
        runner.build_query_backend(args)


def test_clip_offline_views_preserve_raw_primary(tmp_path):
    from flows.scoring import ClipRelevanceScoring
    from test_scoring_clip_drive import HITS, _artifact, _cfg, _write_gt_and_manifest
    gt, manifest = _write_gt_and_manifest(tmp_path)
    artifact = _artifact(tmp_path, copy.deepcopy(HITS))
    for record in artifact.queries:
        for hit in record.hits:
            hit.pop("verification", None)
            hit["_verification_source"] = "absent"
        selected = [copy.deepcopy(hit) for hit in record.hits[:2]]
        selected[0]["verification"] = {"result": "rejected"}
        selected[0]["_verification_source"] = "verification"
        e = envelope(record.hits)
        e["selected_indices"] = [0, 1]
        e["counts"] = {"original_pool": 4, "selected": 2, "known_sources": 4, "unknown_rows": 0,
                       "excluded_by_source_cap": 0, "excluded_by_top_k": 2}
        record.selection = validate_selection(record.hits, selected, e)
        record.selected_hits = selected
    cfg = _cfg(tmp_path, "clip-relevance", gt, manifest)
    scorer = ClipRelevanceScoring()
    result = scorer.score(artifact, scorer.load_ground_truth(cfg, artifact), cfg)
    assert result.summary["raw"]["mAP"] == 1
    assert "critic_filtered" not in result.summary
    assert "raw_returned" in result.summary
    assert result.summary["source_selected"]["verification_coverage"]["attempted"] == 2
    assert result.summary["source_selected"]["critic_filtered"]["mAP"] < 1
    assert "critic_filtered" not in result.query_results[0]
    assert result.summary["verification_pool"] == "source_selected"
    assert result.summary["verification_coverage"]["attempted"] == 2
    assert result.summary["verification_coverage"]["candidate_hits"] == 4
    assert result.query_results[0]["verification_coverage"]["attempted"] == 1
    for view in (result.summary["raw_returned_groups"], result.summary["source_selected"]["groups"],
                 result.summary["source_selected"]["critic_filtered_groups"]):
        assert view["domain"]["event"]["queries_scored"] == 1
        assert view["domain"]["pas"]["queries_scored"] == 1
        assert view["event_slice"]["event"]["queries_scored"] == 1
        assert view["pas_difficulty"]["unknown"]["queries_scored"] == 1


def test_pipeline_normalizes_original_once_and_resumes_selected_verdicts(tmp_path, monkeypatch):
    from dataclasses import replace

    from flows import pipeline
    from test_runner_recovery import config

    original = pool()
    selected = [copy.deepcopy(original[i]) for i in [0, 2]]
    selected[0]["critic_result"] = {"result": "rejected", "criteria_met": {}}
    selected[1]["critic_result"] = {"result": "confirmed", "criteria_met": {}}
    body = {"data": selected, "source_selection": envelope(original)}
    monkeypatch.setattr("flows.query.subprocess.run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(body), stderr=""))
    cfg = config(tmp_path)
    original_normalize = flows.normalize_results
    calls = []
    def normalize(raw):
        calls.append(len(raw))
        return original_normalize(raw)
    monkeypatch.setattr(flows, "normalize_results", normalize)
    artifact = pipeline.run_retrieve(cfg, backend(), None, None, IngestManifest())
    assert calls == [3, 3]
    assert artifact.verdicts["rejected"] == 2
    assert artifact.verdicts["confirmed"] == 2
    assert all(len(record.hits) == 3 and len(record.selected_hits) == 2 for record in artifact.queries)
    assert all("original_pool" not in (record.evidence or {}) for record in artifact.queries)
    resumed = pipeline.run_retrieve(replace(cfg, resume_from=cfg.retrieval_out + ".checkpoint"),
                                    backend(), None, None, IngestManifest())
    assert calls == [3, 3]
    assert resumed.verdicts == artifact.verdicts
    with pytest.raises(SystemExit, match="another run|settings"):
        changed = backend()
        changed.max_hits_per_source = 2
        pipeline.run_retrieve(replace(cfg, resume_from=cfg.retrieval_out + ".checkpoint"),
                              changed, None, None, IngestManifest())


def test_drive_keeps_complete_original_metrics_and_reports_selection_only(tmp_path):
    from flows.scoring import DriveScoring
    from test_scoring_clip_drive import (
        DRIVE_SCORER_STUB,
        HITS,
        _artifact,
        _cfg,
        _write_gt_and_manifest,
    )
    gt, manifest = _write_gt_and_manifest(tmp_path)
    directory = tmp_path / "drive"
    directory.mkdir()
    (directory / "evaluate_domain_test.py").write_text(DRIVE_SCORER_STUB)
    artifact = _artifact(tmp_path, copy.deepcopy(HITS))
    for record in artifact.queries:
        record.selected_hits = copy.deepcopy(record.hits[:1])
        record.selected_hits[0]["verification"] = {"result": "rejected"}
    cfg = _cfg(tmp_path, "drive", gt, manifest, str(directory))
    scorer = DriveScoring()
    result = scorer.score(artifact, scorer.load_ground_truth(cfg, artifact), cfg)
    assert result.summary["mAP"] == pytest.approx(1)
    assert result.summary["source_selected"]["candidate_hits"] == 2
    assert result.summary["source_selected"]["metrics_supported"] is False
    assert "clip-relevance" in result.summary["source_selected"]["reason"]


@pytest.mark.parametrize("change", ["bad_scan", "bool_count", "bad_stage", "snapshot_scope"])
def test_saved_artifact_rejects_invalid_proof(change):
    e = envelope()
    selected = [e["original_pool"][index] for index in e["selected_indices"]]
    if change == "bad_scan": e["completeness"]["scans"] = [None]
    if change == "bool_count": e["counts"]["excluded_by_source_cap"] = True
    if change == "bad_stage": e["stages"] = [{"stage": "conversion", "before": 3, "after": 2, "removed": 1, "kind": "conversion"}]
    if change == "snapshot_scope":
        e["completeness"]["scans"].append({**e["completeness"]["scans"][0], "snapshot_scope_digest": "3" * 64})
        e["completeness"]["expected_scans"] = 2
    with pytest.raises(ValueError):
        QueryRecord.from_dict({"index": 0, "query": "q", "status": "answered", "hits": e["original_pool"],
                               "selected_hits": selected, "selection": {k: v for k, v in e.items() if k != "original_pool"}})


def test_stable_source_identity_precedes_enrichment_aliases():
    original = pool()
    original[0].update(source_id="source-a", sensor_id="original filename")
    original[1].update(source_id="source-a", sensor_id="resolved-uuid")
    original[2].update(source_id="source-b", sensor_id="resolved-other-uuid")
    e = envelope(original)
    e["counts"]["known_sources"] = 2
    assert validate_selection(original, [original[0], original[2]], e)["selected_indices"] == [0, 2]


def test_distinct_stable_sources_with_same_display_name_remain_distinct():
    original = pool()
    for i, hit in enumerate(original):
        hit.update(source_id=f"source-{i}", sensor_id="shared", video_name="same.mp4")
    e = envelope(original)
    e["selected_indices"] = [0, 1]
    e["counts"].update(known_sources=3, excluded_by_source_cap=0, excluded_by_top_k=1)
    assert validate_selection(original, original[:2], e)["selected_indices"] == [0, 1]


def test_drive_selection_coverage_excludes_ineligible_answered_queries(tmp_path):
    from flows.scoring import DriveScoring
    from test_scoring_clip_drive import (
        DRIVE_SCORER_STUB,
        HITS,
        _artifact,
        _cfg,
        _write_gt_and_manifest,
    )

    gt, manifest = _write_gt_and_manifest(tmp_path)
    document = json.loads(Path(gt).read_text())
    document["queries"][1]["near_universal"] = True
    Path(gt).write_text(json.dumps(document))
    directory = tmp_path / "drive"
    directory.mkdir()
    (directory / "evaluate_domain_test.py").write_text(DRIVE_SCORER_STUB)
    artifact = _artifact(tmp_path, copy.deepcopy(HITS))
    artifact.queries[0].selected_hits = copy.deepcopy(artifact.queries[0].hits[:1])
    artifact.queries[1].selected_hits = copy.deepcopy(artifact.queries[1].hits[:3])
    cfg = _cfg(tmp_path, "drive", gt, manifest, str(directory))
    scorer = DriveScoring()
    result = scorer.score(artifact, scorer.load_ground_truth(cfg, artifact), cfg)
    assert result.summary["queries_scored"] == 1
    assert result.summary["source_selected"]["queries"] == 1
    assert result.summary["source_selected"]["candidate_hits"] == 1
    assert result.summary["source_selected"]["attempted"] == 1


@pytest.mark.parametrize("field,value", [("snapshot_digest", True), ("snapshot_scope_digest", "UPPERCASE"),
                                        ("snapshot_indices", [None]),
                                        ("snapshot_indices", [{"name": "index", "uuid": "a"}, {"name": "index", "uuid": "b"}])])
def test_snapshot_proof_shape_is_strict(field, value):
    e = envelope()
    e["completeness"]["scans"][0][field] = value
    with pytest.raises(ValueError):
        validate_selection(e["original_pool"], [e["original_pool"][i] for i in e["selected_indices"]], e)


def test_malformed_selection_keeps_received_pool_and_proof_in_checkpoint(tmp_path, monkeypatch):
    from flows import pipeline
    from test_runner_recovery import config

    e = envelope()
    e["counts"]["original_pool"] = 999
    selected = [e["original_pool"][index] for index in e["selected_indices"]]
    body = {"data": selected, "source_selection": e}
    monkeypatch.setattr("flows.query.subprocess.run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(body), stderr=""))
    cfg = config(tmp_path, tolerate_unanswered=2)
    artifact = pipeline.run_retrieve(cfg, backend(), None, None, IngestManifest())
    for record in artifact.queries:
        assert record.status == "unanswerable"
        assert record.evidence["received_source_selection"] == e
        assert record.evidence["received_selected_hits"] == selected
        assert "counts" in record.evidence["source_selection_error"]
        saved = json.loads((Path(cfg.retrieval_out + ".checkpoint") / f"query-{record.index:08d}.json").read_text())
        assert saved["evidence"]["received_source_selection"] == e


@pytest.mark.parametrize("stage", [{"stage": "conversion", "kind": "anything", "before": 100, "after": 3, "removed": 97},
                                   {"kind": "aggregation", "before": 3, "after": 3, "removed": 0}])
def test_stage_proof_cannot_hide_candidate_loss(stage):
    e = envelope()
    e["stages"] = [stage]
    with pytest.raises(ValueError):
        validate_selection(e["original_pool"], [e["original_pool"][i] for i in e["selected_indices"]], e)
