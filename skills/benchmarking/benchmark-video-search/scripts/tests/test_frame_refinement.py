# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows.query import (
    CliExitError,
    CliQueryBackend,
    QueryUnanswerableError,
    parse_cli_output,
)


def result(body, returncode=0):
    return SimpleNamespace(stdout=json.dumps(body), stderr="", returncode=returncode)


def test_probe_profile_in_description_and_argv(monkeypatch):
    from flows import query

    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return result(
            {"enabled": True, "fingerprint": "version-1", "indices": {"raw": "uuid"}}
        )

    monkeypatch.setattr(query.subprocess, "run", run)
    backend = CliQueryBackend(
        ["vss"], frame_scoring="source", frame_lookup_transport="single"
    )
    backend.capture_frame_capabilities()
    assert (
        backend.describe()["frame_profiles"]["video_file"]["fingerprint"] == "version-1"
    )
    assert "frame-capabilities" in seen[0]
    argv = backend.build_argv("query")
    assert argv[argv.index("--frame-scoring") + 1] == "source"
    assert "--timestamp-start" not in argv


@pytest.mark.parametrize(
    "rows",
    [
        [{"kind": "lookup", "status": "failed"}],
        [
            {"kind": "capabilities", "fingerprint": "changed"},
            {"kind": "lookup", "status": "succeeded"},
        ],
    ],
)
def test_failed_refinement_keeps_candidate_pool(monkeypatch, rows):
    from flows import query

    hits = [{"video_name": "clip", "similarity": 0.8}]
    monkeypatch.setattr(
        query.subprocess,
        "run",
        lambda *a, **kw: result({"data": hits, "frame_refinement": rows}),
    )
    backend = CliQueryBackend(["vss"])
    backend.frame_profiles = {"video_file": {"fingerprint": "expected"}}
    with pytest.raises(QueryUnanswerableError):
        backend.search("query", query_id="q1")
    assert backend.metadata_by_query["q1"]["candidate_pool"] == hits


@pytest.mark.parametrize("status", ["no_match", "not_attempted", "skipped"])
def test_no_match_and_skipped_are_not_failures(monkeypatch, status):
    from flows import query

    monkeypatch.setattr(
        query.subprocess,
        "run",
        lambda *a, **kw: result({"data": [], "frame_refinement": [{"status": status}]}),
    )
    assert CliQueryBackend(["vss"]).search("query")[0] == []


def test_malformed_diagnostics_are_not_empty_results():
    with pytest.raises(QueryUnanswerableError):
        parse_cli_output(json.dumps({"data": [], "frame_refinement": {"failed": 1}}))


def test_probe_failure_is_not_capability_fallback(monkeypatch):
    from flows import query

    monkeypatch.setattr(query.subprocess, "run", lambda *a, **kw: result({}, 3))
    with pytest.raises(CliExitError):
        CliQueryBackend(["vss"]).capture_frame_capabilities()


def test_failed_refinement_evidence_survives_pipeline(tmp_path):
    from flows import pipeline
    from flows.artifacts import IngestManifest
    from test_runner_recovery import RecordingBackend, config

    class Failed(RecordingBackend):
        def search(self, query, query_id=None):
            self.metadata_by_query[query_id or query] = {
                "candidate_pool": [{"video_name": "c0", "similarity": 0.8}],
                "frame_refinement": [{"kind": "lookup", "status": "failed"}],
            }
            raise QueryUnanswerableError("failed refinement")

    cfg = config(tmp_path, tolerate_unanswered=2)
    artifact = pipeline.run_retrieve(cfg, Failed(), None, None, IngestManifest())
    assert all(row.status == "unanswerable" for row in artifact.queries)
    assert all(
        row.evidence["candidate_pool"][0]["video_name"] == "c0"
        for row in artifact.queries
    )


def test_changed_effective_profile_rejects_resume(tmp_path):
    from dataclasses import replace

    from flows import pipeline
    from flows.artifacts import IngestManifest
    from test_runner_recovery import RecordingBackend, config

    class Profiled(RecordingBackend):
        fingerprint = "version-1"

        def capture_frame_capabilities(self, source_types=None):
            self.frame_profiles = {
                "video_file": {"enabled": True, "fingerprint": self.fingerprint}
            }

    cfg = config(tmp_path)
    pipeline.run_retrieve(
        cfg,
        Profiled(search_path="fusion", attributes=["shirt"]),
        None,
        None,
        IngestManifest(),
    )
    changed = Profiled(search_path="fusion", attributes=["shirt"])
    changed.fingerprint = "version-2"
    with pytest.raises(SystemExit, match="identity|settings|match|another run"):
        pipeline.run_retrieve(
            replace(cfg, resume_from=cfg.retrieval_out + ".checkpoint"),
            changed,
            None,
            None,
            IngestManifest(),
        )


def test_parser_config_and_backend_preserve_modes(monkeypatch):
    import flows
    import run_eval_flows as runner
    from flows.artifacts import RunConfig

    args = runner.parse_args(
        [
            "--endpoint",
            "unused",
            "--dry-run",
            "--frame-scoring",
            "source",
            "--frame-lookup-transport",
            "single",
        ]
    )
    cfg = RunConfig.from_args(args, None)
    assert (cfg.frame_scoring, cfg.frame_lookup_transport) == ("source", "single")
    monkeypatch.setattr(flows, "resolve_vss_cmd", lambda *a: (["vss"], "test"))
    backend = runner.build_query_backend(args)
    assert backend.describe()["frame_scoring"] == "source"
    assert backend.describe()["frame_lookup_transport"] == "single"


@pytest.mark.parametrize(
    "path,bounded,should_probe",
    [
        ("embed", False, False),
        ("attribute", False, False),
        ("attribute", True, True),
        ("fusion", False, True),
    ],
)
def test_only_frame_capable_plans_probe_live_backend(
    tmp_path, path, bounded, should_probe
):
    from flows import pipeline
    from flows.artifacts import IngestManifest
    from test_runner_recovery import RecordingBackend, config

    class Profiled(RecordingBackend):
        def capture_frame_capabilities(self, source_types=None):
            self.probe_calls.append(source_types)
            super().capture_frame_capabilities(source_types)

        def plan_for_query(self, query, query_id=None):
            plan = super().plan_for_query(query, query_id)
            if bounded:
                plan.update(
                    timestamp_start="2025-01-01T00:00:00Z",
                    timestamp_end="2025-01-01T00:00:05Z",
                )
            return plan

    backend = Profiled(search_path=path, attributes=["shirt"])
    backend.probe_calls = []
    backend.frame_profiles = {"rtsp": {"fingerprint": "stale"}}
    pipeline.run_retrieve(config(tmp_path), backend, None, None, IngestManifest())
    assert backend.probe_calls == ([{"video_file"}] if should_probe else [])
    assert "rtsp" not in backend.frame_profiles
    if not should_probe:
        assert backend.describe()["frame_profiles"] == {}
