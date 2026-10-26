# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Measurement isolation, boundedness, and cleanup rather than timing thresholds."""

import asyncio
import contextvars
import json
import threading
from contextlib import nullcontext

import pytest

from vss.ingestion import IngestionQueue
from vss.observability import collect_vss_metrics, measure, metric_scope, record
from vss.observability.context import current_scope, defer_observations
from vss.video_processing.execution import _native, _Worker


def test_disabled_nested_and_sanitized_collection() -> None:
    assert current_scope() is None
    with measure("summarization.total", library="summarization"):
        record("summarization.events", 2, library="summarization")
    secret = "https://user:password@host/video?token=very-secret"
    with collect_vss_metrics() as outer:
        with metric_scope(source_id=secret, model=secret, pipeline=secret):
            with measure("summarization.total", library="summarization"):
                record("summarization.events", 2, library="summarization")
            with collect_vss_metrics() as inner:
                record("summarization.events", 3, library="summarization")
    assert outer.complete and inner.complete
    assert [e.value for e in inner.observations] == [3]
    count, total = outer.observations
    assert count.parent_id == total.span_id
    assert count.source_id == total.source_id
    serialized = json.dumps([e.model_dump() for e in outer.observations])
    assert secret not in serialized and "password" not in serialized
    assert count.value == 2 and total.value is not None and total.value >= 0


def test_unavailable_zero_invalid_and_overflow() -> None:
    with collect_vss_metrics(max_events=2) as metrics:
        record("summarization.input_tokens", 0, library="summarization")
        record(
            "summarization.output_tokens",
            None,
            library="summarization",
            availability="not_reported",
        )
        record("summarization.events", 1, library="summarization")
    assert not metrics.complete and metrics.dropped_count == 1
    assert metrics.observations[0].value == 0
    assert metrics.observations[1].availability == "not_reported"
    for value in (float("nan"), float("inf"), -1, None, 1.5, True, 2**4096, -(2**4096)):
        with collect_vss_metrics() as invalid:
            record("summarization.events", value, library="summarization")
        assert not invalid.complete and not invalid.observations


@pytest.mark.parametrize("sink_error", [RuntimeError, asyncio.CancelledError])
def test_sink_failure_never_masks_primary_error(sink_error) -> None:
    def sink(_event: object) -> None:
        raise sink_error("secret-bearing exporter failure")

    with (
        pytest.raises(ValueError, match="primary"),
        collect_vss_metrics(sink=sink) as metrics,
    ):
        with measure("summarization.total", library="summarization"):
            raise ValueError("primary")
    assert not metrics.complete
    assert metrics.observations[0].status == "error"
    assert "secret-bearing" not in metrics.observations[0].model_dump_json()


@pytest.mark.parametrize("deferred", [False, True])
async def test_sink_cancellation_does_not_cancel_successful_work(deferred):
    calls = []

    def sink(event):
        calls.append(event)
        raise asyncio.CancelledError("private sink failure")

    with collect_vss_metrics(sink=sink) as metrics:
        with defer_observations() if deferred else nullcontext():
            with measure("summarization.total", library="summarization"):
                record("summarization.events", 1, library="summarization")
        await asyncio.sleep(0)
    assert len(calls) == 1 and not metrics.complete
    assert len(metrics.observations) == 2
    assert all(event.status == "ok" for event in metrics.observations)


def test_source_identity_is_stable_within_collection_and_unlinkable_between_runs():
    secret = "https://user:password@example.invalid/video?token=private"
    values = []
    for _ in range(2):
        with collect_vss_metrics() as metrics:
            with metric_scope(source_id=secret):
                record("summarization.events", 1, library="summarization")
                record("summarization.events", 2, library="summarization")
        assert metrics.complete
        assert len({event.source_id for event in metrics.observations}) == 1
        values.append(metrics.observations[0].source_id)
        assert secret not in json.dumps(
            [event.model_dump() for event in metrics.observations]
        )
    assert values[0] != values[1]


def test_excess_source_members_or_source_sets_invalidates_collection():
    with collect_vss_metrics() as members:
        with metric_scope(source_ids=(str(i) for i in range(33))):
            record("summarization.events", 1, library="summarization")
    assert not members.complete and members.observations[0].source_id == "unknown"
    assert not members.source_sets
    with collect_vss_metrics(max_events=256) as sets:
        for index in range(129):
            with metric_scope(source_ids=("common", str(index))):
                record("summarization.events", 1, library="summarization")
    assert not sets.complete and len(sets.source_sets) == 128
    assert sets.observations[-1].source_id == "unknown"


def test_source_aliases_are_bounded_idempotent_opaque_and_source_specific():
    from vss.observability.context import correlate_source

    correlate_source("disabled", "disabled-upload")  # no collector, no allocation
    with collect_vss_metrics() as metrics:
        for index in range(128):
            correlate_source("private-original", f"private-upload-{index}")
        correlate_source("private-original", "private-upload-0")  # valid at capacity
        assert metrics.sources(
            ["private-original", "private-upload-0"]
        ) == metrics.source_identity("private-original")
        with metric_scope(source_id="private-upload-0", model="private-upload-0"):
            record("summarization.events", 1, library="summarization")
    assert metrics.complete and len(metrics._source_aliases) == 128
    event = metrics.observations[0]
    assert event.source_id != event.model
    assert "private" not in json.dumps(metrics._source_aliases)
    metrics.correlate_source("after-close", "after-close-upload")
    assert len(metrics._source_aliases) == 128


def test_alias_conflicts_late_registration_and_overflow_do_not_rewrite_evidence():
    from vss.observability.context import correlate_source

    for scenario in ("conflict", "canonical_target", "late", "overflow"):
        with collect_vss_metrics() as metrics:
            correlate_source("first", "uploaded")
            first = metrics.source_identity("uploaded")
            if scenario == "conflict":
                correlate_source("second", "uploaded")
            elif scenario == "canonical_target":
                correlate_source("second", "first")
            elif scenario == "late":
                with metric_scope(source_id="already-observed"):
                    record("summarization.events", 1, library="summarization")
                correlate_source("second", "already-observed")
            else:
                for index in range(128):
                    correlate_source("first", str(index))
            assert metrics.source_identity("uploaded") == first
        assert not metrics.complete
        assert len(metrics._source_aliases) <= 128


def test_alias_chains_flatten_and_resolving_spans_preserves_canonical_source():
    from vss.observability.context import correlate_source

    with collect_vss_metrics() as metrics:
        correlate_source("original", "upload-one")
        correlate_source("upload-one", "upload-two")
        with measure("summarization.total", library="summarization") as span:
            span.resolve(source_id="upload-two")
    assert metrics.complete
    assert metrics.source_identity("upload-two") == metrics.source_identity("original")
    assert metrics.observations[0].source_id == metrics.source_identity("original")
    assert set(metrics._source_aliases.values()) == {
        metrics.source_identity("original")
    }


def test_alias_registered_during_an_outstanding_span_invalidates_stale_lineage():
    from vss.observability.context import correlate_source

    with collect_vss_metrics() as metrics:
        with metric_scope(source_id="uploaded"):
            with measure("summarization.total", library="summarization"):
                correlate_source("original", "uploaded")
        with metric_scope(source_id="uploaded"):
            record("summarization.events", 1, library="summarization")
    assert not metrics.complete
    assert len(metrics.observations) == 2
    assert metrics.observations[0].source_id != metrics.observations[1].source_id


async def test_delayed_jobs_keep_submitter_context_and_cleanup_notification() -> None:
    queue = IngestionQueue(max_concurrency=1, max_pending=3)
    running = asyncio.Event()
    release = asyncio.Event()
    context_value = contextvars.ContextVar("test_value", default="missing")
    values: list[str] = []

    async def work() -> None:
        values.append(context_value.get())
        running.set()
        await release.wait()

    with collect_vss_metrics() as first:
        context_value.set("first")
        with metric_scope(source_id="first"):
            a = queue.submit(work)
        await running.wait()
        with collect_vss_metrics() as second:
            context_value.set("second")
            with metric_scope(source_id="second"):
                b = queue.submit(work)
            release.set()
            await asyncio.gather(a, b)
            await asyncio.gather(a.wait_closed(), b.wait_closed())
    assert first.complete and second.complete
    assert values == ["first", "second"]
    assert {e.request_id for e in first.observations} == {a.id.hex}
    assert {e.request_id for e in second.observations} == {b.id.hex}
    assert {e.source_id for e in first.observations}.isdisjoint(
        e.source_id for e in second.observations
    )


async def test_cancellation_waits_for_delayed_cleanup_and_shields_notification() -> (
    None
):
    queue = IngestionQueue(max_concurrency=1)
    started, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()

    with collect_vss_metrics() as metrics:
        job = queue.submit(work)
        await started.wait()
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        await cleanup.wait()
        waiter = asyncio.create_task(job.wait_closed())
        await asyncio.sleep(0)
        assert not waiter.done() and queue.running_count == 1
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await job.wait_closed()
    assert metrics.complete and queue.running_count == 0
    assert metrics.observations[-1].status == "cancelled"


async def test_early_exit_is_incomplete_and_late_records_are_inert() -> None:
    queue = IngestionQueue(max_concurrency=1)
    started, release = asyncio.Event(), asyncio.Event()

    async def work() -> None:
        started.set()
        await release.wait()
        record("summarization.events", 1, library="summarization")

    with collect_vss_metrics() as metrics:
        job = queue.submit(work)
        await started.wait()
    snapshot = metrics.observations
    assert not metrics.complete
    release.set()
    await job
    await job.wait_closed()
    assert metrics.observations == snapshot


async def test_cancel_before_task_start_and_pending_cancellation_release_capacity() -> (
    None
):
    queue = IngestionQueue(max_concurrency=1)

    async def work() -> None:
        await asyncio.sleep(0)

    with collect_vss_metrics() as metrics:
        a, b = queue.submit(work), queue.submit(work)
        b.cancel()
        await b.wait_closed()
        a.cancel()
        await a.wait_closed()
    assert queue.running_count == queue.pending_count == 0
    assert metrics.complete
    assert len(metrics.observations) == 2
    assert all(e.status == "cancelled" for e in metrics.observations)


async def test_worker_context_and_failure_are_scalar_only() -> None:
    worker = _Worker()
    with collect_vss_metrics() as metrics, metric_scope(source_id="worker-source"):
        assert await _native(worker, lambda: 42) == 42
        with pytest.raises(Exception, match="Native decoder operation failed"):
            await _native(worker, lambda: (_ for _ in ()).throw(ValueError("secret")))
        await worker.close_executor()
    assert metrics.complete
    assert [e.status for e in metrics.observations] == ["ok", "error"]
    assert all(e.source_id.startswith("opaque:") for e in metrics.observations)
    assert "secret" not in json.dumps([e.model_dump() for e in metrics.observations])


async def test_queued_native_work_marks_early_collection_exit_incomplete() -> None:
    worker = _Worker()
    entered, release = threading.Event(), threading.Event()

    def blocked() -> None:
        entered.set()
        release.wait(timeout=5)

    first = worker.submit(blocked)
    await asyncio.to_thread(entered.wait, 5)
    try:
        with collect_vss_metrics() as metrics:
            queued = worker.submit(lambda: 42)
        assert not queued.done() and not metrics.complete
        assert metrics.observations == ()
    finally:
        release.set()
        await first
        assert await queued == 42
        await worker.close_executor()
    assert not metrics.complete and metrics.observations == ()


async def test_native_submission_failure_releases_measurement_lease() -> None:
    worker = _Worker()
    await worker.close_executor()
    with collect_vss_metrics() as metrics:
        with pytest.raises(RuntimeError, match="shutdown"):
            worker.submit(lambda: 42)
    assert metrics.complete
