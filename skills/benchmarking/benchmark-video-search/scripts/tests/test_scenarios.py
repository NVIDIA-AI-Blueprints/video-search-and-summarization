# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Scenario isolation, durable inference evidence, and annotation-free replay."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flows
import run_eval_flows as runner
from flows import pipeline
from flows.artifacts import IngestManifest, RunConfig, read_retrieval_artifact
from flows.query import CliQueryBackend, QueryUnanswerableError
from flows.routing import load_decompositions


class RecordingBackend(CliQueryBackend):
    def search(self, query):
        plan = self.plan_for_query(query)
        plan["effective_argv"] = self.build_argv(query, plan)
        self.executed_plans.append(plan)
        return [], 0.01


def config(tmp_path, **overrides):
    gallery = tmp_path / "gallery.json"
    gallery.write_text(
        json.dumps(
            {
                "meta": {"set": "test"},
                "gallery": [{"chunk_id": "a", "video": "a.mp4"}],
                "queries": [
                    {
                        "query": "a person wearing red",
                        "query_id": "p",
                        "query_domain": "pas",
                        "resolved_attributes": {"shirt": "red"},
                        "relevant_clip_ids": ["a"],
                    },
                    {
                        "query": "a person running",
                        "query_id": "e",
                        "query_domain": "event",
                        "relevant_clip_ids": ["a"],
                    },
                ],
            }
        )
    )
    return RunConfig(
        endpoint="unused",
        data_dir=tmp_path,
        dataset="test",
        subset="",
        concurrency=1,
        vst_url=None,
        stage="retrieve",
        gallery_gt=str(gallery),
        run_name="test",
        retrieval_out=str(tmp_path / "nested" / "result.json"),
        **overrides,
    )


def test_gallery_defaults_and_explicit_legacy_options():
    args = runner.parse_args(
        ["--endpoint", "unused", "--gallery-gt", "g", "--ingestion-manifest", "m"]
    )
    assert args.scenarios == ["embed-only", "attribute-only", "live-routed"]
    assert args.scoring == "clip-relevance"
    args = runner.parse_args(
        [
            "--endpoint",
            "unused",
            "--gallery-gt",
            "g",
            "--ingestion-manifest",
            "m",
            "--fixed-search-path",
        ]
    )
    assert args.scenarios is None
    with pytest.raises(SystemExit):
        runner.parse_args(
            [
                "--endpoint",
                "unused",
                "--gallery-gt",
                "g",
                "--scenarios",
                "embed-only",
                "--fixed-search-path",
            ]
        )
    args = runner.parse_args(
        ["--endpoint", "unused", "--gallery-gt", "g", "--scenarios", "embed-only"]
    )
    assert args.no_decompose


def test_three_scenarios_share_ingest_and_replay_route_without_llm(
    tmp_path, monkeypatch
):
    cfg = config(
        tmp_path,
        scenarios=["embed-only", "attribute-only", "live-routed"],
        query_domain="pas",
    )
    backend = RecordingBackend(
        ["vss"],
        decompositions={
            "a person wearing red": {
                "query": "red person",
                "attributes": ["red shirt"],
                "has_action": False,
                "video_sources": ["another"],
                "timestamp_start": "2025-01-01T00:00:01Z",
            }
        },
    )
    pipeline.run_pipeline(cfg, None, backend, None, None)
    base = tmp_path / "nested" / "result"
    embedded = read_retrieval_artifact(f"{base}.embed-only.retrieval.json")
    attribute = read_retrieval_artifact(f"{base}.attribute-only.retrieval.json")
    routed = read_retrieval_artifact(f"{base}.live-routed.retrieval.json")
    assert len(embedded.queries) == 1
    assert embedded.queries[0].plan["query"] == "a person wearing red"
    assert attribute.queries[0].plan["attributes"] == ["a person wearing red"]
    argv = attribute.queries[0].plan["effective_argv"]
    assert "--candidate-limit" in argv and "--attribute-min-similarity" in argv
    assert "--video-source" not in argv and "--timestamp-start" not in argv
    assert routed.queries[0].plan["path"] == "attribute"
    assert routed.queries[0].evidence["decomposition_mode"] == "replay"
    assert routed.queries[0].decompose_s is None
    assert routed.queries[0].evidence["released_pas_requirements"] == {"shirt": "red"}
    assert load_decompositions(f"{base}.live-routed.retrieval.json") == {
        "p": backend.decompositions["a person wearing red"]
    }
    summary = json.loads(Path(f"{base}.comparison.json").read_text())
    assert summary["scenarios"]["live-routed"]["routing_consistency"] == {
        "compared": 1,
        "consistent": 1,
    }


@pytest.mark.parametrize(
    "decomposition",
    [
        None,
        {},
        {"attributes": []},
        {"attributes": "red"},
        {"attributes": [1]},
        {"attributes": [""]},
    ],
)
def test_attribute_replay_invalid_never_substitutes_original(decomposition):
    saved = {} if decomposition is None else {"query": decomposition}
    backend = CliQueryBackend(
        ["vss"], attribute_input="decomposed", decompositions=saved
    )
    with pytest.raises(QueryUnanswerableError, match="missing|invalid|empty"):
        backend.plan_for_query("query")


def test_attribute_replay_ignores_decomposed_scope_and_route():
    backend = CliQueryBackend(
        ["vss"],
        attribute_input="decomposed",
        decompositions={
            "original": {
                "attributes": ["red shirt"],
                "has_action": True,
                "object_ids": ["42"],
                "video_sources": ["foreign"],
                "timestamp_end": "end",
                "top_k": 1,
            }
        },
    )
    plan = backend.plan_for_query("original")
    assert plan["path"] == "attribute" and plan["attributes"] == ["red shirt"]
    assert (
        plan["video_sources"] == []
        and plan["timestamp_end"] is None
        and plan["top_k"] is None
    )


@pytest.mark.parametrize(
    "content", ['{"query":"person", "attributes":[]}', "not JSON", "[1]"]
)
def test_raw_decomposition_saved_before_parsing(tmp_path, monkeypatch, content):
    class Response:
        text = "raw HTTP envelope"

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr("flows.decompose.requests.post", lambda *a, **kw: Response())
    decomposer = flows.LiveDecomposer(
        "unused", repo_root=flows.REPO_ROOT, model="fixed-model"
    )
    snapshots = []

    def save(evidence):
        snapshots.append(json.loads(json.dumps(evidence)))
        (tmp_path / "query.json").write_text(json.dumps(evidence))

    if content.startswith("{"):
        parsed, _ = decomposer.decompose("person", evidence_sink=save)
        assert load_decompositions(tmp_path) == {"person": parsed}
        assert load_decompositions(tmp_path / "query.json") == {"person": parsed}
    else:
        with pytest.raises(flows.DecompositionError):
            decomposer.decompose("person", evidence_sink=save)
        assert snapshots[-1]["error"]
    raw = next(row for row in snapshots if row["raw_response"] is not None)
    assert raw["raw_response"] == content and raw["parsed"] is None
    assert raw["submitted_request"]["messages"][-1]["content"]


def test_equal_options_preserve_explicit_legacy_choices():
    args = runner.parse_args(
        [
            "--endpoint=unused",
            "--gallery-gt=g",
            "--ingestion-manifest=m",
            "--search-path=attribute",
            "--attribute-input=original",
            "--scoring=clip-relevance",
        ]
    )
    assert args.scenarios is None and args.attribute_input_explicit
    with pytest.raises(SystemExit):
        runner.parse_args(
            [
                "--endpoint=unused",
                "--gallery-gt=g",
                "--scenarios",
                "embed-only",
                "embed-only",
            ]
        )


def test_indexed_coverage_is_retained_without_changing_parser_contract():
    from flows.query import parse_cli_output

    coverage = [{"eligible_indexed_vectors": 1616, "limit": 100000, "complete": True}]
    metadata = {}
    hits, messages, timings = parse_cli_output(
        json.dumps({"data": [], "candidate_coverage": coverage})
        + "\n"
        + json.dumps({"event": "completed"}),
        metadata=metadata,
    )
    assert hits == [] and messages == [] and timings == {}
    assert metadata["candidate_coverage"] == coverage


def test_critic_settings_are_effective_cli_arguments():
    backend = CliQueryBackend(
        ["vss"],
        critic_enabled=False,
        critic_eval_count=7,
        critic_frame_budget=9,
        critic_input_width=224,
        critic_input_height=224,
        critic_diagnostic_dir="/tmp/evidence",
    )
    argv = backend.build_argv("original")
    for flag in (
        "--no-critic",
        "--critic-eval-count",
        "--critic-frame-budget",
        "--critic-input-width",
        "--critic-input-height",
        "--critic-diagnostic-dir",
    ):
        assert flag in argv
    assert backend.describe()["critic_enabled"] is False


def test_live_routed_missing_replay_does_not_fallback(tmp_path):
    cfg = config(tmp_path, scenarios=["live-routed"])
    backend = RecordingBackend(
        ["vss"],
        decompositions={
            "a person wearing red": {"attributes": ["red"], "has_action": False}
        },
    )
    pipeline.run_pipeline(cfg, None, backend, None, None)
    artifact = read_retrieval_artifact(
        tmp_path / "nested" / "result.live-routed.retrieval.json"
    )
    missing = next(row for row in artifact.queries if row.query == "a person running")
    assert missing.status == "unanswerable"
    assert "missing or invalid" in missing.error
    assert missing.hits == []


def test_scenarios_ingest_once(tmp_path, monkeypatch):
    cfg = config(tmp_path, scenarios=["embed-only", "attribute-only"])
    cfg.stage = "run"
    ingestions = []

    def ingest(*args):
        ingestions.append(args)
        return IngestManifest()

    monkeypatch.setattr(pipeline, "run_ingest", ingest)

    class Score:
        def __init__(self):
            self.summary = {}

    monkeypatch.setattr(pipeline, "run_score", lambda *args: Score())
    pipeline.run_pipeline(cfg, object(), RecordingBackend(["vss"]), None, None)
    assert len(ingestions) == 1
