# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Campaign cleanup must fail closed before expensive ingestion or retrieval."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows import pipeline
from flows.artifacts import RunConfig


@pytest.mark.parametrize(
    "payload", [{"status": "partial"}, {"status": "failure"}, {}, [], None]
)
def test_clear_rejects_unsuccessful_body(monkeypatch, payload):
    monkeypatch.setattr(
        pipeline.flows, "list_sensor_streams", Mock(side_effect=[{"s": "clip"}, {}])
    )
    monkeypatch.setattr(
        pipeline.requests, "delete", Mock(return_value=Mock(json=lambda: payload))
    )
    result = pipeline.clear_all_videos("http://agent", "http://vst")
    assert result["deleted"] == 0
    assert result["failed"] == ["clip"]


def test_clear_requires_empty_inventory_even_after_success(monkeypatch):
    monkeypatch.setattr(
        pipeline.flows,
        "list_sensor_streams",
        Mock(side_effect=[{"s": "clip"}, {"new": "late"}]),
    )
    monkeypatch.setattr(
        pipeline.requests,
        "delete",
        Mock(return_value=Mock(json=lambda: {"status": "success"})),
    )
    assert pipeline.clear_all_videos("http://agent", "http://vst")["failed"] == ["late"]


def test_clear_fails_when_postcheck_unavailable(monkeypatch):
    monkeypatch.setattr(
        pipeline.flows,
        "list_sensor_streams",
        Mock(side_effect=[{"s": "clip"}, RuntimeError("offline")]),
    )
    monkeypatch.setattr(
        pipeline.requests,
        "delete",
        Mock(return_value=Mock(json=lambda: {"status": "success"})),
    )
    with pytest.raises(SystemExit, match="verify cleanup"):
        pipeline.clear_all_videos("http://agent", "http://vst")


def test_prune_preserves_ours_and_checks_deleted_identity(tmp_path, monkeypatch):
    (tmp_path / "ours.mp4").touch()
    monkeypatch.setattr(
        pipeline.flows,
        "list_sensor_streams",
        Mock(side_effect=[{"s": "other", "o": "ours"}, {"s": "other", "o": "ours"}]),
    )
    monkeypatch.setattr(
        pipeline.requests,
        "delete",
        Mock(return_value=Mock(json=lambda: {"status": "success"})),
    )
    result = pipeline.prune_foreign_videos("http://agent", "http://vst", tmp_path)
    assert result["kept"] == 1
    assert result["deleted"] == 0
    assert result["failed"] == ["other"]


@pytest.fixture(autouse=True)
def registered_source(monkeypatch):
    monkeypatch.setattr(
        pipeline.flows, "list_sensor_streams", lambda *a: {"id": "clip"}
    )
    # Resolve the current mock at call time, preserving each test's evolving
    # inventory and failures for both the initial listing and deletion check.
    monkeypatch.setattr(
        pipeline.flows,
        "list_sensor_sources",
        lambda *args: {
            sid: {"name": name, "url": f"/storage/{name}.mp4"}
            for sid, name in pipeline.flows.list_sensor_streams(*args).items()
        },
    )


def _cfg(tmp_path, **kw):
    return RunConfig(
        endpoint="http://agent",
        vst_url="http://vst",
        data_dir=tmp_path,
        dataset="warehouse",
        subset="",
        concurrency=1,
        skip_readiness_wait=True,
        index_probe_attempts=1,
        **kw,
    )


def test_direct_upload_requires_webhook_confirmation_before_cleanup(
    tmp_path, monkeypatch
):
    cleanup = Mock(side_effect=AssertionError("must not delete"))
    monkeypatch.setattr(pipeline, "clear_all_videos", cleanup)
    with pytest.raises(SystemExit, match="webhooks-confirmed"):
        pipeline.run_ingest(
            _cfg(tmp_path, clear=True),
            SimpleNamespace(name="vst-direct", describe=dict),
            None,
        )
    cleanup.assert_not_called()


def test_three_step_still_runs_both_readiness_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "ingest_videos",
        lambda *a, **k: {"per_file": [{"success": True, "video_name": "clip"}]},
    )
    monkeypatch.setattr(pipeline.flows, "print_upload_summary", lambda *a: None)
    coverage = Mock(return_value={"checked": True, "covered": True})
    monkeypatch.setattr(pipeline, "probe_index_coverage", coverage)
    readiness = Mock(return_value={"ready": True, "attribute_gaps": ["clip"]})
    backend = SimpleNamespace(name="agent-3step", proves_indexing=True, describe=dict)
    query = SimpleNamespace(search_path="embed", readiness=readiness)
    manifest = pipeline.run_ingest(_cfg(tmp_path), backend, query)
    coverage.assert_called_once()
    readiness.assert_called_once_with(["clip"], require_attributes=False)
    assert manifest.index_probe["pipeline_readiness"]["attribute_gaps"] == ["clip"]


def test_duplicate_cannot_bypass_index_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "ingest_videos",
        lambda *a, **k: {
            "per_file": [
                {
                    "success": True,
                    "video_name": "clip",
                    "complete_outcome": pipeline.flows.COMPLETE_ALREADY_REGISTERED,
                }
            ]
        },
    )
    monkeypatch.setattr(pipeline.flows, "print_upload_summary", lambda *a: None)
    with pytest.raises(SystemExit, match="duplicate-camera"):
        pipeline.run_ingest(
            _cfg(tmp_path, skip_index_probe=True),
            SimpleNamespace(name="agent-3step", describe=dict),
            None,
        )


def test_readiness_failure_prevents_campaign(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "ingest_videos",
        lambda *a, **k: {"per_file": [{"success": True, "video_name": "clip"}]},
    )
    monkeypatch.setattr(pipeline.flows, "print_upload_summary", lambda *a: None)
    monkeypatch.setattr(
        pipeline,
        "probe_index_coverage",
        lambda *a, **k: {"checked": True, "covered": True},
    )
    query = SimpleNamespace(
        search_path="attribute",
        readiness=lambda *a, **k: {"ready": False, "error": "raw index missing"},
    )
    with pytest.raises(SystemExit, match="raw index missing"):
        pipeline.run_ingest(
            _cfg(tmp_path), SimpleNamespace(name="agent-3step", describe=dict), query
        )


def test_attribute_smoke_uses_floor_zero_and_disables_critic(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "ingest_videos",
        lambda *a, **k: {"per_file": [{"success": True, "video_name": "clip"}]},
    )
    monkeypatch.setattr(pipeline.flows, "print_upload_summary", lambda *a: None)
    monkeypatch.setattr(
        pipeline,
        "probe_index_coverage",
        lambda *a, **k: {"checked": True, "covered": True},
    )
    factory = Mock(
        return_value=SimpleNamespace(search=lambda q: ([{"video_name": "clip"}], 0.1))
    )
    monkeypatch.setattr(pipeline.flows, "CliQueryBackend", factory)
    readiness = Mock(return_value={"ready": True})
    query = SimpleNamespace(
        search_path="attribute", readiness=readiness, vss_cmd=["vss"], cwd=None
    )
    manifest = pipeline.run_ingest(
        _cfg(tmp_path), SimpleNamespace(name="agent-3step", describe=dict), query
    )
    readiness.assert_called_once_with(["clip"], require_attributes=True)
    assert factory.call_args.kwargs["attribute_min_similarity"] == 0
    assert factory.call_args.kwargs["critic_enabled"] is False
    decomposition = factory.call_args.kwargs["decompositions"][pipeline.PROBE_QUERY]
    assert decomposition["video_sources"] == ["clip"]
    assert decomposition["has_action"] is False
    assert manifest.index_probe["attribute_search"] == {"checked": True, "hits": 1}


def test_attribute_smoke_rejects_empty_results(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "ingest_videos",
        lambda *a, **k: {"per_file": [{"success": True, "video_name": "clip"}]},
    )
    monkeypatch.setattr(pipeline.flows, "print_upload_summary", lambda *a: None)
    monkeypatch.setattr(
        pipeline,
        "probe_index_coverage",
        lambda *a, **k: {"checked": True, "covered": True},
    )
    monkeypatch.setattr(
        pipeline.flows,
        "CliQueryBackend",
        Mock(return_value=SimpleNamespace(search=lambda q: ([], 0.1))),
    )
    query = SimpleNamespace(
        search_path="attribute",
        readiness=lambda *a, **k: {"ready": True},
        vss_cmd=["vss"],
        cwd=None,
    )
    with pytest.raises(SystemExit, match="returned no hits"):
        pipeline.run_ingest(
            _cfg(tmp_path), SimpleNamespace(name="agent-3step", describe=dict), query
        )


def test_readiness_resolves_source_spelling_without_prefix_matches(monkeypatch):
    monkeypatch.setattr(
        pipeline.flows,
        "list_sensor_streams",
        lambda *a: {"id": "Clip.mp4", "other": "Clip_other.mp4"},
    )
    assert pipeline.resolve_probe_sources("http://vst", ["clip"]) == {
        "clip": "Clip.mp4"
    }


def test_readiness_rejects_duplicate_registrations(monkeypatch):
    monkeypatch.setattr(
        pipeline.flows,
        "list_sensor_streams",
        lambda *a: {"id": "clip", "duplicate": "clip.mp4"},
    )
    with pytest.raises(ValueError, match="one registered"):
        pipeline.resolve_probe_sources("http://vst", ["clip"])


def test_legacy_explicit_embed_gallery_does_not_require_attribute_pipeline(
    tmp_path, monkeypatch
):
    from test_gallery_ingest import _gallery_dir

    root = _gallery_dir(tmp_path)
    monkeypatch.setattr(
        pipeline,
        "ingest_videos",
        lambda *a, **k: {
            "per_file": [
                {"success": True, "video_name": name, "sensor_id": name + "-sensor"}
                for name in ("c0", "c1")
            ]
        },
    )
    monkeypatch.setattr(
        pipeline.flows, "list_sensor_streams", lambda *a: {"s0": "c0", "s1": "c1"}
    )
    monkeypatch.setattr(pipeline.flows, "print_upload_summary", lambda *a: None)
    monkeypatch.setattr(
        pipeline,
        "probe_index_coverage",
        lambda *a, **k: {"checked": True, "covered": True},
    )
    readiness = Mock(return_value={"ready": True})
    query = SimpleNamespace(search_path="embed", readiness=readiness)
    pipeline.run_ingest(
        _cfg(
            tmp_path,
            gallery_gt=str(root),
            scenarios=None,
            ingest_out=str(tmp_path / "manifest.json"),
        ),
        SimpleNamespace(name="agent-3step", describe=dict),
        query,
    )
    readiness.assert_called_once_with(["c0", "c1"], require_attributes=False)
