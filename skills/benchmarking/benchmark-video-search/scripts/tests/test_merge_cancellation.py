# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Concurrent cancellation must stop further inference and retain query identities."""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flows
from flows import pipeline
from flows.artifacts import IngestManifest, read_retrieval_artifact
from test_runner_recovery import RecordingBackend, config


def _three_queries(tmp_path):
    cfg = replace(config(tmp_path), concurrency=2)
    gallery = Path(cfg.gallery_gt)
    data = json.loads(gallery.read_text())
    data["queries"].append(
        {"query_id": "q2", "query": "queued", "relevant_clip_ids": ["c0"]}
    )
    gallery.write_text(json.dumps(data))
    return cfg


def _release_on_shutdown(monkeypatch, release):
    class ReleasingExecutor(ThreadPoolExecutor):
        def shutdown(self, *args, **kwargs):
            # Release blocked inference only after the collector has handled
            # the interrupt/failure and entered its cleanup path.
            release.set()
            return super().shutdown(*args, **kwargs)

    monkeypatch.setattr(pipeline, "ThreadPoolExecutor", ReleasingExecutor)


def _assert_durable_abort(cfg, backend):
    artifact = read_retrieval_artifact(cfg.retrieval_out)
    assert artifact.described["retrieval_status"] == "aborted"
    assert {row.query_id for row in artifact.queries} == {"q0", "q1", "q2"}
    assert all(row.status == "unanswerable" for row in artifact.queries)
    assert backend.calls == []
    checkpoint = Path(str(cfg.retrieval_out) + ".checkpoint")
    assert len(list(checkpoint.glob("query-*.json"))) == 3
    return artifact


def test_collector_interrupt_stops_search_after_inflight_decomposition(
    tmp_path, monkeypatch
):
    cfg = _three_queries(tmp_path)
    release = threading.Event()
    started = {name: threading.Event() for name in ("first", "second")}
    calls = []

    class BlockingDecomposer:
        def describe(self):
            return {"model": "fixed"}

        def decompose(self, query, video_sources=None):
            calls.append(query)
            assert query in started, "queued query must never begin inference"
            started[query].set()
            assert release.wait(5), "collector failed to release in-flight inference"
            return {"query": query}, 0.5

    def interrupted_collector(futures):
        assert all(event.wait(5) for event in started.values())
        raise KeyboardInterrupt()
        yield  # Make the interruption occur during iteration, as real signals do.

    _release_on_shutdown(monkeypatch, release)
    monkeypatch.setattr(pipeline, "as_completed", interrupted_collector)
    backend = RecordingBackend()
    with pytest.raises(SystemExit, match="ABORTED"):
        pipeline.run_retrieve(cfg, backend, BlockingDecomposer(), None, IngestManifest())
    _assert_durable_abort(cfg, backend)
    assert sorted(calls) == ["first", "second"]


def test_worker_failure_stops_queued_and_inflight_queries_and_keeps_timing(
    tmp_path, monkeypatch
):
    cfg = _three_queries(tmp_path)
    release = threading.Event()
    second_started = threading.Event()
    calls = []
    clock = threading.local()

    def perf_counter():
        previous = getattr(clock, "value", -7.0)
        clock.value = previous + 7.0
        return clock.value

    class FailingDecomposer:
        def describe(self):
            return {"model": "fixed"}

        def decompose(self, query, video_sources=None):
            calls.append(query)
            assert query != "queued", "failure must stop queued inference"
            if query == "first":
                assert second_started.wait(5), "second worker never began"
                raise flows.DecompositionError("controlled provider failure")
            second_started.set()
            assert release.wait(5), "collector failed to release in-flight inference"
            return {"query": query}, 0.5

    real_as_completed = pipeline.as_completed

    def release_after_first_completion(futures):
        iterator = real_as_completed(futures)
        yield next(iterator)
        # Worker failure sets cancellation before its future completes. Let
        # the other already-running model call finish after collection begins.
        release.set()
        yield from iterator

    _release_on_shutdown(monkeypatch, release)
    monkeypatch.setattr(pipeline, "as_completed", release_after_first_completion)
    monkeypatch.setattr(pipeline.time, "perf_counter", perf_counter)
    backend = RecordingBackend()
    with pytest.raises(SystemExit, match="controlled provider failure"):
        pipeline.run_retrieve(cfg, backend, FailingDecomposer(), None, IngestManifest())
    artifact = _assert_durable_abort(cfg, backend)
    failure = next(row for row in artifact.queries if row.query_id == "q0")
    assert failure.decompose_s == pytest.approx(7.0)
    assert "controlled provider failure" in failure.error
    assert sorted(calls) == ["first", "second"]
    assert not any("AssertionError" in (row.error or "") for row in artifact.queries)


def test_resume_rejects_changed_effective_thinking_settings(tmp_path):
    cfg = config(tmp_path)

    class FixedDecomposer(flows.LiveDecomposer):
        def decompose(self, query, video_sources=None, **kwargs):
            return {"query": query}, 0.5

    original = FixedDecomposer(
        "unused", repo_root=flows.REPO_ROOT, model="fixed",
        chat_template_kwargs={"enable_thinking": False},
    )
    pipeline.run_pipeline(cfg, None, RecordingBackend(), original, None)
    changed = FixedDecomposer(
        "unused", repo_root=flows.REPO_ROOT, model="fixed",
        chat_template_kwargs={"enable_thinking": True},
    )
    backend = RecordingBackend()
    with pytest.raises(SystemExit, match="resume settings"):
        pipeline.run_pipeline(
            replace(cfg, resume_from=str(cfg.retrieval_out) + ".checkpoint", retrieval_out=str(tmp_path / "resumed.retrieval.json")),
            None, backend, changed, None,
        )
    assert backend.calls == []
