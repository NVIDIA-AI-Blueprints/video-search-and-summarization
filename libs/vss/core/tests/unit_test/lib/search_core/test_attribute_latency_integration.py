# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Request lifecycle and evidence contracts for frame refinement optimizations."""

from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vss_core.search_core._internal.frame_refinement import FrameRefinementContext
from vss_core.search_core._internal.frame_refinement import frame_refinement_context
from vss_core.search_core._internal.frame_refinement import record_diagnostic
from vss_core.search_core.models.attribute_search import AttributeSearchInput
from vss_core.search_core.models.search import SearchInput
from vss_core.search_core.models.search import SearchOutput
from vss_core.search_core.primitives import _attribute_helpers as ah
from vss_core.search_core.primitives import _search_helpers
from vss_core.search_core.primitives.attribute_search import AttributeSearch
from vss_core.search_core.primitives.search import Search
from vss_core.vios import VSTError


def _attribute():
    return AttributeSearch(
        es=object(),
        embed=object(),
        behavior_index="behavior",
        behavior_index_wildcard="behavior-*",
        frames_index="frames",
    )


def _search():
    return Search(
        embed=object(), attribute=object(), behavior_es=SimpleNamespace(endpoint="unused"), behavior_index="behavior"
    )


@pytest.mark.asyncio
async def test_concurrent_attribute_envelopes_own_diagnostics_but_share_budget(monkeypatch):
    contexts = []

    async def attributes(**kwargs):
        context = frame_refinement_context.get()
        contexts.append(context)
        record_diagnostic(context, {"query": kwargs["search_input"].query, "status": "skipped"})
        await asyncio.sleep(0)
        return []

    monkeypatch.setattr(ah, "search_attributes", attributes)
    parent = FrameRefinementContext()
    token = frame_refinement_context.set(parent)
    try:
        first, second = await asyncio.gather(
            _attribute().run(AttributeSearchInput(query="hat")), _attribute().run(AttributeSearchInput(query="shirt"))
        )
        assert contexts == [parent, parent]
        assert first.frame_refinement == [{"query": "hat", "status": "skipped"}]
        assert second.frame_refinement == [{"query": "shirt", "status": "skipped"}]
        assert len(parent.diagnostics) == 2
    finally:
        frame_refinement_context.reset(token)


@pytest.mark.asyncio
async def test_direct_attribute_failure_resets_context(monkeypatch):
    async def fail(**kwargs):
        assert frame_refinement_context.get() is not None
        raise RuntimeError("failed")

    monkeypatch.setattr(ah, "search_attributes", fail)
    with pytest.raises(RuntimeError):
        await _attribute().run(AttributeSearchInput(query="hat"))
    assert frame_refinement_context.get() is None


@pytest.mark.asyncio
async def test_concurrent_search_outputs_and_context_reset(monkeypatch):
    async def execute(**kwargs):
        context = frame_refinement_context.get()
        record_diagnostic(context, {"query": kwargs["search_input"].query, "status": "skipped"})
        await asyncio.sleep(0)
        return SearchOutput()

    monkeypatch.setattr(_search_helpers, "execute_core_search_wrapper", execute)
    first, second = await asyncio.gather(
        _search().run(SearchInput(query="hat")), _search().run(SearchInput(query="shirt"))
    )
    assert first.frame_refinement == [{"query": "hat", "status": "skipped"}]
    assert second.frame_refinement == [{"query": "shirt", "status": "skipped"}]
    assert frame_refinement_context.get() is None


@pytest.mark.asyncio
async def test_stream_close_drains_generator_and_restores_parent(monkeypatch):
    closed = False

    async def execute(**kwargs):
        nonlocal closed
        try:
            record_diagnostic(frame_refinement_context.get(), {"status": "skipped"})
            yield SearchOutput()
        finally:
            closed = True

    monkeypatch.setattr(_search_helpers, "execute_core_search", execute)
    parent = FrameRefinementContext()
    token = frame_refinement_context.set(parent)
    try:
        stream = _search().stream(SearchInput(query="hat"))
        event = await anext(stream)
        assert event.output.frame_refinement == [{"status": "skipped"}]
        assert frame_refinement_context.get() is parent
        await asyncio.create_task(stream.aclose())
        assert closed
        assert frame_refinement_context.get() is parent
    finally:
        frame_refinement_context.reset(token)


@pytest.mark.asyncio
async def test_frame_alignment_deduplicates_without_changing_subjects(monkeypatch):
    calls = []

    async def refine(**kwargs):
        calls.append(kwargs)
        return [("winning-frame", {"leftX": 9}, 0.95, "winning-timestamp"), None]

    monkeypatch.setattr(ah, "refine_frames", refine)

    def candidate(object_id):
        return {"_source": {"sensor": {"id": "cam"}, "object": {"id": object_id}}}

    results = await ah._perform_frame_lookups(
        [candidate("a"), candidate(None), candidate("b"), candidate("a")],
        [1.0],
        "frames",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 2, tzinfo=UTC),
        object(),
    )
    assert calls[0]["keys"] == [("cam", "a"), ("cam", "b")]
    assert results == [results[0], None, None, results[0]]
    assert results[0][1] == {"leftX": 9}


@pytest.mark.asyncio
async def test_metadata_cache_successful_snapshots_and_failures(monkeypatch):
    catalogue = AsyncMock(side_effect=[VSTError("offline"), {"cam": "stream"}, {}])
    timeline = AsyncMock(return_value=("clip-start", "clip-end"))
    screenshots = AsyncMock(side_effect=[VSTError("offline"), {"stream": ("envelope-start", "envelope-end")}])
    monkeypatch.setattr(ah, "get_name_to_stream_id_map", catalogue)
    monkeypatch.setattr(ah, "get_timeline", timeline)
    monkeypatch.setattr(ah, "get_timelines_map", screenshots)
    context = ah._AttributeMetadataContext()
    with pytest.raises(VSTError):
        await context.resolve_stream("cam", "origin-a")
    assert await context.resolve_stream("cam", "origin-a") == "stream"
    assert await context.resolve_stream("stream", "origin-a") == "stream"
    assert await context.clip_timeline("stream", "origin-a") == ("clip-start", "clip-end")
    assert await context.clip_timeline("stream", "origin-a") == ("clip-start", "clip-end")
    assert await context.screenshot_timelines("origin-a") == {}
    assert await context.screenshot_timelines("origin-a") == {"stream": ("envelope-start", "envelope-end")}
    assert await context.screenshot_timelines("origin-a") == {"stream": ("envelope-start", "envelope-end")}
    for _ in range(2):
        with pytest.raises(VSTError):
            await context.resolve_stream("cam", "origin-b")
    assert catalogue.await_count == 3
    assert timeline.await_count == 1
    assert screenshots.await_count == 2


@pytest.mark.asyncio
async def test_direct_attribute_cancellation_restores_caller_state(monkeypatch):
    started = asyncio.Event()

    async def wait(**kwargs):
        assert frame_refinement_context.get() is not None
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(ah, "search_attributes", wait)
    task = asyncio.create_task(_attribute().run(AttributeSearchInput(query="hat")))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert frame_refinement_context.get() is None


@pytest.mark.asyncio
async def test_cached_enrichment_preserves_clip_and_screenshot_semantics(monkeypatch):
    from vss_core.search_core.models.attribute_search import AttributeSearchMetadata
    from vss_core.search_core.models.attribute_search import AttributeSearchResult

    catalogue = AsyncMock(return_value={"cam": "stream"})
    timeline = AsyncMock(return_value=("2025-01-01T00:00:00Z", "2025-01-01T00:00:02Z"))
    envelopes = {"stream": ("2025-01-01T00:00:00Z", "2025-01-01T00:00:10Z")}
    monkeypatch.setattr(ah, "get_name_to_stream_id_map", catalogue)
    monkeypatch.setattr(ah, "get_stream_id", AsyncMock(return_value="stream"))
    monkeypatch.setattr(ah, "get_timeline", timeline)
    screenshots = AsyncMock(return_value=envelopes)
    monkeypatch.setattr(ah, "get_timelines_map", screenshots)
    item = AttributeSearchResult(
        metadata=AttributeSearchMetadata(
            sensor_id="cam",
            object_id="person",
            object_type="Person",
            start_time="2025-01-01T00:00:01.700Z",
            end_time="2025-01-01T00:00:01.900Z",
            frame_timestamp="2025-01-01T00:00:01Z",
        )
    )
    uncached = await ah._enrich_append_results([item.model_copy(deep=True)], "internal", "external", "hat")
    timeline.reset_mock()
    screenshots.reset_mock()
    context = ah._AttributeMetadataContext()
    for query in ["hat", "shirt"]:
        cached = await ah._enrich_append_results(
            [item.model_copy(deep=True)], "internal", "external", query, _metadata=context
        )
        assert cached[0].model_dump() == uncached[0].model_dump()
    assert catalogue.await_count == timeline.await_count == screenshots.await_count == 1
    assert cached[0].metadata.end_time == "2025-01-01T00:00:02Z"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_fusion_drains_sibling_before_request_context_cleanup(monkeypatch, cancelled):
    from vss_core.search_core.errors import BackendUnreachableError
    from vss_core.search_core.models.search import SearchResult

    sibling_started = asyncio.Event()
    sibling_drained = asyncio.Event()
    contexts = []
    sibling_tasks = []
    failure = asyncio.CancelledError() if cancelled else BackendUnreachableError("elasticsearch", "unavailable")

    class Attributes:
        async def ainvoke(self, payload):
            if payload["video_sources"] == ["failed"]:
                await sibling_started.wait()
                raise failure
            context = frame_refinement_context.get()
            contexts.append(context)
            sibling_tasks.append(asyncio.current_task())
            async with context.permits(1):
                sibling_started.set()
                try:
                    await asyncio.Future()
                finally:
                    await asyncio.sleep(0)
                    sibling_drained.set()

    async def execute(**kwargs):
        candidates = [
            SearchResult(
                video_name=name,
                sensor_id_raw=name,
                sensor_id=name,
                description="",
                screenshot_url="",
                similarity=0.5,
                start_time="2025-01-01T00:00:00Z",
                end_time="2025-01-01T00:00:05Z",
            )
            for name in ["failed", "waiting"]
        ]
        await _search_helpers.fusion_search_rerank(candidates, ["hat"], Attributes())
        raise AssertionError("failed fusion unexpectedly succeeded")

    monkeypatch.setattr(_search_helpers, "execute_core_search_wrapper", execute)
    with pytest.raises(type(failure)):
        await _search().run(SearchInput(query="hat"))
    assert sibling_drained.is_set()
    assert all(task.done() for task in sibling_tasks)
    assert contexts[0]._available == 16
    assert frame_refinement_context.get() is None


@pytest.mark.asyncio
async def test_attribute_subclass_envelope_diagnostics_survive_adapter():
    from vss_core.search_core.models.attribute_search import AttributeSearchOutput
    from vss_core.search_core.primitives.search import _wrap_attribute

    class IndependentAttribute(AttributeSearch):
        async def run(self, inp):
            return AttributeSearchOutput(frame_refinement=[{"status": "skipped", "reason": "custom"}])

    primitive = IndependentAttribute(
        es=object(), embed=object(), behavior_index="behavior", behavior_index_wildcard="behavior-*", frames_index=None
    )
    context = FrameRefinementContext()
    token = frame_refinement_context.set(context)
    try:
        assert await _wrap_attribute(primitive).ainvoke({"query": "hat"}) == []
        assert context.diagnostics == [{"status": "skipped", "reason": "custom"}]
    finally:
        frame_refinement_context.reset(token)


@pytest.mark.asyncio
async def test_native_zero_frame_score_survives_attribute_and_fusion_consumers():
    from vss_core.search_core.models.search import SearchResult
    from vss_core.search_core.primitives.search import _wrap_attribute

    class EndpointEs:
        endpoint = "unused"

        async def search(self, *, index, body):
            if "knn" in body:
                return {
                    "hits": {
                        "hits": [
                            {
                                "_score": 0.8,
                                "_source": {
                                    "object": {"id": "person", "type": "Person"},
                                    "sensor": {"id": "cam"},
                                    "timestamp": "2025-01-01T00:00:00Z",
                                    "end": "2025-01-01T00:00:05Z",
                                },
                            }
                        ]
                    }
                }
            return {
                "hits": {
                    "hits": [
                        {
                            "_score": 0.0,
                            "_source": {
                                "id": "opposite-vector-frame",
                                "timestamp": "2025-01-01T00:00:01Z",
                            },
                            "fields": {"winning_object": [{"id": "person", "bbox": {"leftX": 1}}]},
                        }
                    ]
                }
            }

    class Embed:
        async def get_text_embedding(self, query):
            return [1.0]

    es = EndpointEs()
    primitive = AttributeSearch(
        es=es,
        embed=Embed(),
        behavior_index="behavior",
        behavior_index_wildcard="behavior-*",
        frames_index="frames",
        frame_scoring="source",
        frame_lookup_transport="single",
    )
    search = Search(
        embed=object(),
        attribute=primitive,
        behavior_es=es,
        behavior_index="behavior",
        frame_scoring="source",
        frame_lookup_transport="single",
        merge_adjacent=False,
    )
    output = await search.run(
        SearchInput(
            search_mode="attribute",
            attributes=["hat"],
            timestamp_start=datetime(2025, 1, 1, tzinfo=UTC),
            timestamp_end=datetime(2025, 1, 2, tzinfo=UTC),
        )
    )
    assert output.data[0].similarity == 0.0
    assert output.data[0].attribute_evidence[0]["frame_score"] == 0.0
    assert any(row.get("status") == "succeeded" for row in output.frame_refinement)
    candidate = SearchResult(
        video_name="cam",
        sensor_id="cam",
        description="",
        screenshot_url="",
        similarity=0.5,
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:05Z",
    )
    reranked = await _search_helpers.fusion_search_rerank(
        [candidate],
        ["hat"],
        _wrap_attribute(primitive),
        fusion_method="weighted_linear",
        w_embed=0.35,
        w_attribute=0.55,
    )
    assert reranked[0].similarity == pytest.approx(0.35 * 0.5)
    assert reranked[0].attribute_evidence[0]["frame_score"] == 0.0
