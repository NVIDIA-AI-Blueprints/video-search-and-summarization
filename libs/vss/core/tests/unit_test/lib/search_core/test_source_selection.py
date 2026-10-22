# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Complete-pool diversity preserves baseline ranking and explicit bounds."""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError
import pytest

from vss_core.search_core._internal.source_selection import SourceSelectionContext
from vss_core.search_core._internal.source_selection import select_sources
from vss_core.search_core._internal.source_selection import source_selection_context
from vss_core.search_core._internal.source_selection import validate_source_selection
from vss_core.search_core.errors import IndexNotFoundError
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.events import ErrorEvent
from vss_core.search_core.events import FinalResultEvent
from vss_core.search_core.models.search import SearchInput
from vss_core.search_core.models.search import SearchOutput
from vss_core.search_core.models.search import SearchResult
from vss_core.search_core.models.search import SourceSelection
from vss_core.search_core.primitives.attribute_search import AttributeSearch
from vss_core.search_core.primitives.search import Search
from vss_core.search_core.runtime import BEHAVIOR_INDEX_ANCHOR


def scan(count, *, digest="stable", scope="same", budget=100):
    return {
        "exhaustive": True,
        "snapshot_verified": True,
        "eligible_documents": count,
        "scored_documents": count,
        "retained_documents": count,
        "budget": budget,
        "snapshot_digest": hashlib.sha256(digest.encode()).hexdigest(),
        "snapshot_scope_digest": hashlib.sha256(scope.encode()).hexdigest(),
        "snapshot_indices": [{"name": "behavior", "uuid": "uuid"}],
    }


def result(source, index, *, video_name=None):
    return SearchResult(
        video_name=source if video_name is None else video_name,
        sensor_id=source,
        description="",
        start_time="",
        end_time="",
        screenshot_url="",
        similarity=1 - index / 1000,
    )


def hit(source, index):
    return {
        "_id": str(index),
        "_score": 1 - index / 1000,
        "_source": {"sensor": {"id": source}, "object": {"id": str(index), "type": "Person"}},
    }


class Es:
    endpoint = "unused"

    def __init__(self, hits=None, *, changing=False, absent=False, no_proof=False):
        self.hits = hits if hits is not None else [hit("a", 0), hit("a", 1), hit("b", 2), hit("c", 3)]
        self.calls = 0
        self.changing = changing
        self.absent = absent
        self.no_proof = no_proof

    async def exhaustive_vector_search(self, **kwargs):
        self.calls += 1
        if self.absent:
            raise IndexNotFoundError(kwargs["index"])
        context = source_selection_context.get()
        if context is not None and not self.no_proof:
            context.scans.append(
                scan(
                    len(self.hits),
                    digest=str(self.calls) if self.changing else "stable",
                    budget=kwargs["candidate_limit"],
                )
            )
        return {"hits": {"hits": self.hits}}


class Embed:
    async def get_text_embedding(self, query):
        return [1.0]


def search(es, *, cap=1):
    attr = AttributeSearch(
        es=es,
        embed=Embed(),
        behavior_index=BEHAVIOR_INDEX_ANCHOR,
        behavior_index_wildcard="behavior-*",
        frames_index=None,
    )
    return Search(
        embed=object(),
        attribute=attr,
        behavior_es=es,
        behavior_index=BEHAVIOR_INDEX_ANCHOR,
        max_hits_per_source=cap,
        merge_adjacent=False,
    )


def inp(*, attributes=None, top_k=2, budget=100):
    return SearchInput(search_mode="attribute", attributes=attributes or ["hat"], top_k=top_k, candidate_limit=budget)


@pytest.mark.asyncio
async def test_complete_ranking_and_selected_positions_preserve_requested_limit():
    es = Es()
    output = await search(es).run(inp())
    assert [row.sensor_id for row in output.data] == ["a", "b"]
    selection = output.source_selection
    assert [row.sensor_id for row in selection.original_pool] == ["a", "a", "b", "c"]
    assert selection.selected_indices == [0, 2]
    assert selection.requested_top_k == 2
    assert selection.candidate_limit == 100
    assert selection.completeness["complete"] is True
    assert all(stage["removed"] == 0 for stage in selection.stages if stage["kind"] == "truncation")
    assert source_selection_context.get() is None
    assert es.calls == 1
    assert SearchOutput.model_validate_json(output.model_dump_json()).source_selection.selected_indices == [0, 2]


@pytest.mark.asyncio
async def test_stream_matches_run_without_context_leak_or_extra_retrieval():
    es = Es()
    events = []
    stream = search(es).stream(inp())
    async for event in stream:
        assert source_selection_context.get() is None
        events.append(event)
    assert isinstance(events[-1], FinalResultEvent)
    assert events[-1].output.source_selection.selected_indices == [0, 2]
    assert len(events[-1].output.source_selection.original_pool) == 4
    assert es.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options,match", [({"changing": True}, "snapshots changed"), ({"no_proof": True}, "scan evidence")]
)
async def test_unverified_or_changed_multiattribute_snapshot_fails(options, match):
    with pytest.raises(InvalidInputError, match=match):
        await search(Es(**options)).run(inp(attributes=["hat", "shirt"]))
    assert source_selection_context.get() is None


@pytest.mark.asyncio
async def test_unknown_identity_rows_are_independently_eligible_and_bounded():
    rows = [hit("unknown", index) for index in range(3)]
    # Missing sensor id is deliberately never deduplicated across attributes.
    output = await search(Es(rows)).run(inp(top_k=3))
    assert output.source_selection.selected_indices == [0, 1, 2]
    assert output.source_selection.counts["unknown_rows"] == 3
    with pytest.raises(InvalidInputError, match="exceeds candidate_limit"):
        await search(Es(rows)).run(inp(attributes=["hat", "shirt"], budget=3))


@pytest.mark.asyncio
async def test_invalid_indexed_hit_cannot_be_silently_removed():
    with pytest.raises(InvalidInputError, match="malformed indexed"):
        await search(Es([{"_source": {"sensor": {"id": "a"}}}])).run(inp())


@pytest.mark.asyncio
async def test_only_proven_absent_uploads_anchor_may_skip_snapshot_identity():
    output = await search(Es(absent=True)).run(inp(attributes=["hat", "shirt"]))
    assert output.data == []
    assert output.source_selection.selected_indices == []
    assert output.source_selection.completeness["absent_uploads_anchor"] is True
    assert output.source_selection.completeness["snapshot_verified"] is False
    assert output.source_selection.completeness["expected_scans"] == 2


def test_unknown_fallback_exact_source_identity_and_stable_selection():
    pool = [
        result("unknown", 0, video_name="clip"),
        result("unknown", 1, video_name="clip"),
        result("", 2, video_name="unknown"),
        result("UNKNOWN", 3, video_name="clip"),
        result("", 4, video_name="unknown"),
    ]
    context = SourceSelectionContext(scans=[scan(5)], expected_scans=1)
    token = source_selection_context.set(context)
    try:
        selected = select_sources(pool, cap=1, requested_top_k=4, candidate_limit=100)
        assert selected.selected_indices == [0, 2, 3, 4]
    finally:
        source_selection_context.reset(token)


@pytest.mark.parametrize("cap", [0, -1, True, 1.5, 100001])
def test_invalid_caps_rejected(cap):
    with pytest.raises(InvalidInputError):
        validate_source_selection("attribute", "video_file", 100, cap)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        SearchInput(query="hat"),
        SearchInput(search_mode="attribute", attributes=["hat"], source_type="rtsp", candidate_limit=100),
        SearchInput(search_mode="attribute", attributes=["hat"]),
    ],
)
async def test_invalid_modes_fail_before_backend_calls_for_run_and_stream(payload):
    es = Es()
    with pytest.raises(InvalidInputError):
        await search(es).run(payload)
    events = [event async for event in search(es).stream(payload)]
    assert isinstance(events[-1], ErrorEvent)
    assert es.calls == 0


def test_default_payload_omits_selection_and_legacy_loading():
    output = SearchOutput(data=[result("a", 0)])
    assert "source_selection" not in output.model_dump()
    assert "source_selection" not in json.loads(output.model_dump_json())
    assert SearchOutput.model_validate(output.model_dump()).source_selection is None


@pytest.mark.asyncio
async def test_source_selection_rejects_forged_positions_counts_and_data():
    output = await search(Es()).run(inp())
    envelope = output.source_selection.model_dump()
    for key, value in [
        ("selected_indices", [True, 2]),
        ("selected_indices", [2, 0]),
        ("selected_indices", [0, 1]),
        ("counts", {}),
    ]:
        altered = {**envelope, key: value}
        with pytest.raises(ValidationError):
            SourceSelection.model_validate(altered)
    altered = output.model_dump()
    altered["data"] = altered["data"][:1]
    with pytest.raises(ValidationError):
        SearchOutput.model_validate(altered)


@pytest.mark.asyncio
async def test_complete_pool_equals_disabled_exact_baseline_including_frame_order():
    from datetime import UTC
    from datetime import datetime

    class FrameEs(Es):
        async def search(self, *, index, body):
            params = body["query"]["function_score"]["script_score"]["script"]["params"]
            object_id = params["target_id"]
            return {
                "hits": {
                    "hits": [
                        {
                            "_score": (int(object_id) + 1) / 10,
                            "_source": {
                                "id": object_id,
                                "timestamp": "2025-01-01T00:00:01Z",
                                "objects": [{"id": object_id}],
                            },
                        }
                    ]
                }
            }

    def build(cap):
        es = FrameEs()
        attribute = AttributeSearch(
            es=es,
            embed=Embed(),
            behavior_index=BEHAVIOR_INDEX_ANCHOR,
            behavior_index_wildcard="behavior-*",
            frames_index="frames",
            frame_scoring="source",
            frame_lookup_transport="single",
        )
        return Search(
            embed=object(),
            attribute=attribute,
            behavior_es=es,
            behavior_index=BEHAVIOR_INDEX_ANCHOR,
            max_hits_per_source=cap,
            merge_adjacent=False,
            frame_scoring="source",
            frame_lookup_transport="single",
        )

    bounded = inp().model_copy(
        update={"timestamp_start": datetime(2025, 1, 1, tzinfo=UTC), "timestamp_end": datetime(2025, 1, 2, tzinfo=UTC)}
    )
    enabled = await build(1).run(bounded)
    baseline = await build(None).run(bounded.model_copy(update={"top_k": 100}))
    assert [row.model_dump(exclude={"source_id"}) for row in enabled.source_selection.original_pool] == [
        row.model_dump(exclude={"source_id"}) for row in baseline.data
    ]
    assert [row.sensor_id for row in baseline.data] == ["c", "b", "a", "a"]
    assert enabled.source_selection.selected_indices == [0, 1]


@pytest.mark.asyncio
async def test_cancelled_selection_restores_caller_context():
    import asyncio

    entered = asyncio.Event()

    class BlockingEs(Es):
        async def exhaustive_vector_search(self, **kwargs):
            assert source_selection_context.get() is not None
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(search(BlockingEs()).run(inp()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert source_selection_context.get() is None


@pytest.mark.asyncio
async def test_stable_indexed_identity_survives_partial_metadata_resolution(monkeypatch):
    from vss_core.search_core.primitives import _attribute_helpers

    async def enrich(rows, *args, **kwargs):
        for index, row in enumerate(rows):
            row.metadata.video_name = "same-file"
            if index:
                row.metadata.sensor_id = "resolved-uuid"
        return rows

    monkeypatch.setattr(_attribute_helpers, "_enrich_append_results", enrich)
    output = await search(Es([hit("same-file", 0), hit("same-file", 1)])).run(inp())
    assert [row.sensor_id for row in output.source_selection.original_pool] == ["same-file", "resolved-uuid"]
    assert [row.source_id for row in output.source_selection.original_pool] == ["same-file", "same-file"]
    assert output.source_selection.selected_indices == [0]
    # Different indexed identities sharing a display name stay distinct.
    output = await search(Es([hit("source-a", 0), hit("source-b", 1)])).run(inp())
    assert output.source_selection.selected_indices == [0, 1]


@pytest.mark.asyncio
async def test_disabled_request_masks_outer_selection_context():
    outer = SourceSelectionContext(max_candidates=1)
    token = source_selection_context.set(outer)
    try:
        output = await search(Es(no_proof=True), cap=None).run(inp())
        assert output.source_selection is None
        assert len(output.data) == 2
        assert source_selection_context.get() is outer
        assert outer.scans == []
    finally:
        source_selection_context.reset(token)


def test_default_result_serialization_omits_stable_source_id():
    row = result("a", 0)
    assert "source_id" not in row.model_dump()
    assert "source_id" not in json.loads(row.model_dump_json())


@pytest.mark.asyncio
async def test_malformed_scan_and_stage_proofs_raise_validation_errors():
    output = await search(Es()).run(inp())
    for bad in [
        {"completeness": {"complete": True, "expected_scans": 1, "scans": [None]}},
        {"stages": [{"stage": "fake", "before": 4, "after": 4, "removed": 0, "kind": "hidden"}]},
        {"counts": {**output.source_selection.counts, "selected": True}},
    ]:
        with pytest.raises(ValidationError):
            SourceSelection.model_validate({**output.source_selection.model_dump(), **bad})
    proof = output.source_selection.model_dump()
    proof["completeness"]["scans"][0]["snapshot_indices"] = [{}]
    with pytest.raises(ValidationError):
        SourceSelection.model_validate(proof)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"query": "hat", "search_mode": "embed"},
        {"search_mode": "attribute", "attributes": ["hat"], "source_type": "rtsp", "candidate_limit": 100},
        {"search_mode": "attribute", "attributes": ["hat"]},
    ],
)
async def test_unconfigured_facade_rejects_selection_before_building_clients(monkeypatch, payload):
    from unittest.mock import Mock

    from vss_core.search_core.host import VSSSearch
    from vss_core.search_core.runtime import SearchRuntime

    facade = VSSSearch(SearchRuntime(max_hits_per_source=1))
    build = Mock(side_effect=AssertionError("invalid request constructed backend clients"))
    monkeypatch.setattr(facade, "_build_search", build)
    with pytest.raises(InvalidInputError):
        await facade.search(**payload)
    with pytest.raises(InvalidInputError):
        facade.search_stream(**payload)
    build.assert_not_called()
