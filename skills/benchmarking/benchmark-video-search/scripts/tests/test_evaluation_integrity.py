# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Campaign regressions using stable identities and released annotation schemas."""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows.artifacts import IngestManifest, QueryRecord, RetrievalArtifact, RunConfig
from flows.decompose import DecompositionError, LiveDecomposer
from flows.query import CliQueryBackend, QueryUnanswerableError, parse_cli_output
from flows.routing import load_decompositions, validate_decomposition
from flows.scoring.clip_relevance import ClipRelevanceScoring
from flows.scoring.clip_scoring import ClipScorer
from flows.scoring.drive import DriveScoring
from flows.scoring.gallery import build_context, load_gallery_gt
from flows.scoring.pas_ground_truth import frame_supported_queries


@pytest.mark.parametrize("data", [None, False, "", {}, 0, [None], ["hit"], [7]])
def test_invalid_payload_never_becomes_empty_ranking(data):
    with pytest.raises(QueryUnanswerableError, match="list"):
        parse_cli_output(json.dumps({"data": data}))
    assert parse_cli_output('{"data": []}')[0] == []


@pytest.mark.parametrize(
    "fields",
    [
        {"attributes": "red hat"},
        {"attributes": None},
        {"attributes": [None]},
        {"has_action": "false"},
        {"has_action": None},
        {"has_action": 0},
        {"video_sources": "camera"},
        {"object_ids": [True]},
        {"query": []},
        {"source_type": "stream"},
        {"top_k": True},
        {"top_k": 0},
        {"timestamp_start": "yesterday"},
        {"timestamp_end": 5},
        {
            "timestamp_start": "2025-01-02T00:00:00Z",
            "timestamp_end": "2025-01-01T00:00:00Z",
        },
    ],
)
def test_bad_decomposition_is_rejected_fresh_and_replay(fields, monkeypatch):
    with pytest.raises(ValueError):
        validate_decomposition(fields)
    backend = CliQueryBackend(["vss"], decompositions={"qid": fields})
    with pytest.raises(QueryUnanswerableError, match="invalid decomposition"):
        backend.build_argv("original", query_id="qid")
    from flows import REPO_ROOT

    response = SimpleNamespace(
        text="HTTP response",
        raise_for_status=lambda: None,
        json=lambda: {"choices": [{"message": {"content": json.dumps(fields)}}]},
    )
    monkeypatch.setattr("flows.decompose.requests.post", lambda *a, **kw: response)
    evidence = []
    decomposer = LiveDecomposer("unused", repo_root=REPO_ROOT, model="test")
    with pytest.raises(DecompositionError, match="invalid decomposition"):
        decomposer.decompose(
            "original",
            evidence_sink=lambda row: evidence.append(dict(row)),
            query_id="qid",
        )
    assert evidence[-1]["query_id"] == "qid"
    assert evidence[-1]["raw_response"] == json.dumps(fields)
    assert evidence[-1]["error"] and evidence[-1]["parsed"] is None


def test_replay_isolates_corrupt_and_invalid_entries_and_preserves_ids(tmp_path):
    (tmp_path / "one.json").write_text(
        json.dumps(
            {
                "query_id": "p1",
                "original_query": "red shirt",
                "parsed": {"attributes": ["red shirt"], "has_action": False},
            }
        )
    )
    (tmp_path / "two.json").write_text(
        json.dumps(
            {
                "query_id": "p2",
                "original_query": "red shirt",
                "parsed": {"attributes": "red shirt"},
            }
        )
    )
    (tmp_path / "broken.json").write_text('{"parsed":')
    with pytest.warns(UserWarning):
        saved = load_decompositions(tmp_path)
    backend = CliQueryBackend(["vss"], decompositions=saved)
    backend.require_decomposition = True
    backend.ambiguous_query_texts = {"red shirt"}
    assert backend.plan_for_query("red shirt", "p1")["path"] == "attribute"
    with pytest.raises(QueryUnanswerableError, match="invalid"):
        backend.plan_for_query("red shirt", "p2")
    with pytest.raises(QueryUnanswerableError, match="missing"):
        backend.plan_for_query("red shirt", "p3")
    # Shared-text legacy evidence cannot masquerade as evidence for either ID.
    backend.decompositions = {"red shirt": {"attributes": ["red shirt"]}}
    with pytest.raises(QueryUnanswerableError, match="missing"):
        backend.plan_for_query("red shirt", "p1")
    backend.ambiguous_query_texts = set()
    assert backend.plan_for_query("red shirt", "p1")["attributes"] == ["red shirt"]


def test_duplicate_text_backend_keeps_concurrent_replay_plans_and_timings(monkeypatch):
    backend = CliQueryBackend(
        ["vss"],
        decompositions={
            "p1": {"query": "first rewrite", "attributes": []},
            "p2": {"query": "second rewrite", "attributes": []},
        },
    )
    backend.ambiguous_query_texts = {"same text"}
    backend.require_decomposition = True

    def search(argv, **kwargs):
        rewrite = argv[argv.index("--query") + 1]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": [], "timings": {"rewrite": rewrite}}),
            stderr="",
        )

    monkeypatch.setattr("flows.query.subprocess.run", search)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda qid: backend.search("same text", query_id=qid), ["p1", "p2"]
            )
        )
    assert all(hits == [] for hits, _ in results)
    assert backend.timings_by_query == {
        "p1": {"rewrite": "first rewrite"},
        "p2": {"rewrite": "second rewrite"},
    }
    assert {plan["query_id"]: plan["query"] for plan in backend.executed_plans} == {
        "p1": "first rewrite",
        "p2": "second rewrite",
    }


def fixture_context(tmp_path):
    gt = {
        "gallery": [
            {"chunk_id": "a", "video": "clips/a.mp4"},
            {"chunk_id": "b", "video": "clips/b.mp4"},
        ],
        "queries": [
            {
                "query_id": "p1",
                "query": "blue t-shirt",
                "query_domain": "pas",
                "pas_tier": "easy",
                "relevant_clip_ids": ["a"],
            },
            {
                "query_id": "p2",
                "query": "blue t-shirt",
                "query_domain": "pas",
                "pas_tier": "hard",
                "relevant_clip_ids": ["b"],
            },
            {
                "query_id": "e1",
                "query": "generic event",
                "query_domain": "event",
                "near_universal": True,
                "relevant_clip_ids": ["a", "b"],
            },
        ],
    }
    gtpath = tmp_path / "gt.json"
    gtpath.write_text(json.dumps(gt))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "clips": [
                    {"dataset_id": cid, "sensor_id": cid, "filename": f"{cid}.mp4"}
                    for cid in ("a", "b")
                ]
            }
        )
    )
    return gtpath, manifest, build_context(gtpath, manifest, pas_frame_supported=False)


def test_repeated_text_scores_distinct_ids_and_excludes_failed_ineligible(tmp_path):
    gtpath, _manifest, context = fixture_context(tmp_path)
    records = [
        QueryRecord(
            0, "blue t-shirt", "answered", hits=[{"sensor_id": "a"}], query_id="p1"
        ),
        QueryRecord(
            1, "blue t-shirt", "answered", hits=[{"sensor_id": "a"}], query_id="p2"
        ),
        QueryRecord(
            2, "generic event", "unanswerable", error="bad JSON", query_id="e1"
        ),
    ]
    artifact = RetrievalArtifact(
        "gallery",
        "",
        "",
        1,
        {},
        records,
        [{"index": 2, "query": "generic event", "reason": "bad JSON"}],
        [],
        0,
        [],
        {},
        {},
        IngestManifest(),
    )
    cfg = RunConfig(None, None, None, None, 1, None)
    result = ClipRelevanceScoring().score(artifact, context, cfg)
    assert result.summary["queries_scored"] == 2
    assert result.summary["queries_unanswered"] == 0
    assert result.summary["raw"]["mAP"] == 0.5
    assert result.summary["groups"]["pas_difficulty"]["easy"]["mAP"] == 1
    assert result.summary["groups"]["pas_difficulty"]["hard"]["mAP"] == 0
    assert len(result.summary["excluded_failures"]) == 1
    records[0].query_id = None
    with pytest.raises(SystemExit, match="ambiguous"):
        ClipRelevanceScoring().score(artifact, context, cfg)
    # Relative media paths in merged fixtures resolve from the fixture, not cwd.
    assert load_gallery_gt(gtpath)["gallery"][0]["video_path"] == str(
        tmp_path / "clips/a.mp4"
    )


def test_offline_explicit_tolerance_overrides_saved_setting(tmp_path):
    _, _, context = fixture_context(tmp_path)
    records = [
        QueryRecord(0, "blue t-shirt", "answered", hits=[], query_id="p1"),
        QueryRecord(1, "blue t-shirt", "unanswerable", error="timeout", query_id="p2"),
    ]
    artifact = RetrievalArtifact(
        "gallery", "", "", 1, {}, records, [], [], 0, [], {}, {}, IngestManifest()
    )
    cfg = RunConfig(None, None, None, None, 1, None)
    with pytest.raises(SystemExit, match="unanswered"):
        ClipRelevanceScoring().score(artifact, context, cfg)
    cfg.tolerate_unanswered_override = 1
    result = ClipRelevanceScoring().score(artifact, context, cfg)
    assert result.summary["tolerate_unanswered"] == 1
    assert artifact.tolerate_unanswered == 0


def test_released_cohorts_and_reference_metric_parity_when_available(tmp_path):
    """Use authentic local release/reference files, without deployment access."""
    root = Path(__file__).resolve().parents[5].parent / "embed_debug"
    release = root / "data"
    drive = root / "drive/cosmos_embed1/scripts/evaluate_domain_test.py"
    pas = root / "physicalai-event-videos-test-pas-evaluation/scripts/pas_common.py"
    if not all(path.exists() for path in (release / "gt/queries_gt.json", drive, pas)):
        pytest.skip("Local released Drive/PAS fixtures are unavailable")
    np = pytest.importorskip("numpy")

    def module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    drive_ref, pas_ref = module("drive_reference", drive), module("pas_reference", pas)
    for source, expected_queries, expected_eligible, expected_excluded in (
        (release, 6040, 2316, 684),
        (release / "subset", 600, 189, 61),
    ):
        gt = load_gallery_gt(source)
        assert len(gt["queries"]) == expected_queries
        rows = [
            {
                "dataset_id": g["chunk_id"],
                "sensor_id": str(i),
                "filename": Path(g["video"]).name,
            }
            for i, g in enumerate(gt["gallery"])
        ]
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "clips": rows,
                    "subset_fingerprint": gt.get("meta", {}).get("subset_fingerprint"),
                }
            )
        )
        context = build_context(source, manifest)
        cfg = RunConfig(
            None,
            None,
            None,
            None,
            1,
            None,
            gallery_gt=str(source),
            ingestion_manifest=str(manifest),
            drive_scorer_dir=str(drive.parent),
        )
        artifact_stub = SimpleNamespace(query_source="gallery")
        assert (
            len(DriveScoring().load_ground_truth(cfg, artifact_stub)["gt"]["queries"])
            == expected_queries
        )
        assert len(context["all_by_id"]) == expected_queries
        tracks = json.loads(
            (source / "additional_gt/tracks_pas_attributes.json").read_text()
        )["tracks"]
        ours, excluded = frame_supported_queries(
            gt["queries"], tracks, set(context["gallery"])
        )
        original, unsupported = pas_ref.frame_ground_truth(
            tracks,
            [t for t in tracks if t["eligible_for_gt"]],
            [q for q in gt["queries"] if q["query_domain"] == "pas"],
        )
        assert (
            len(original) == expected_eligible
            and len(excluded) == len(unsupported) == expected_excluded
        )
        assert {
            q["query_id"]: set(q["relevant_track_uids"])
            for q in ours
            if q["query_domain"] == "pas"
        } == {q["query_id"]: set(q["frame_supported_track_uids"]) for q in original}
        scorer = ClipScorer(context["mapping"], context["queries"])
        randomizer = random.Random(42)
        for q in context["queries"]:
            ranking = list(context["gallery"])
            randomizer.shuffle(ranking)
            result = scorer.score(q, [{"clip_id": cid} for cid in ranking])
            labels = np.array([int(cid in q["relevant_clip_ids"]) for cid in ranking])
            assert result["average_precision"] == pytest.approx(
                drive_ref.average_precision(labels), abs=1e-12
            )
            assert result["ndcg_at_10"] == pytest.approx(
                drive_ref.ndcg_at_k(labels, 10), abs=1e-12
            )
            ranks = [
                i + 1 for i, cid in enumerate(ranking) if cid in q["relevant_clip_ids"]
            ]
            reference_ap10 = sum(
                (i + 1) / rank for i, rank in enumerate(ranks) if rank <= 10
            ) / min(len(ranks), 10)
            assert result["ap_at_10"] == pytest.approx(reference_ap10, abs=1e-12)
            if q["query_domain"] == "pas":
                assert result["difficulty"] == q["pas_tier"]


def test_recorded_cli_target_is_part_of_retrieval_identity(monkeypatch):
    from flows import query as query_mod

    recorded = {
        "base_url": "https://deployment-one.example",
        "written_at": "yesterday",
        "services": {
            "rt_embed": {
                "url": "https://deployment-one.example/embed",
                "models": ["same-model"],
            }
        },
    }
    monkeypatch.setattr(
        query_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(recorded)
        ),
    )
    backend = CliQueryBackend(["vss"])
    backend.capture_deployment_provenance()
    first = backend.describe()["deployment_identity"]
    recorded["written_at"] = "today"
    backend.capture_deployment_provenance()
    assert backend.describe()["deployment_identity"] == first
    recorded["base_url"] = "https://deployment-two.example"
    backend.capture_deployment_provenance()
    assert backend.describe()["deployment_identity"] != first
    recorded["base_url"] = "https://deployment-one.example"
    recorded["services"]["rt_embed"]["url"] = "https://other-embed.example"
    backend.capture_deployment_provenance()
    assert backend.describe()["deployment_identity"] != first
    assert backend.deployment_models["rt_embed"] == ["same-model"]
