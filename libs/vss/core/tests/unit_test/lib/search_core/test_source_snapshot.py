# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in source selection requires stable complete versioned scan evidence."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import resource
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vss_core.search_core._internal.source_selection import source_selection_context
from vss_core.search_core.clients.elastic import ElasticClient
from vss_core.search_core.errors import BackendUnreachableError
from vss_core.search_core.errors import InvalidInputError


def page(hits, total):
    return {
        "_scroll_id": "snapshot",
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "failed": 0},
        "hits": {"total": {"value": total, "relation": "eq"}, "hits": hits},
    }


def hit(identity="a", seq=1, score=0.8):
    return {
        "_index": "gallery-1",
        "_id": identity,
        "_seq_no": seq,
        "_primary_term": 1,
        "_score": score,
        "_source": {"sensor": {"id": "camera"}},
    }


def indices(uuid="uuid-one", extra=False):
    raw = {"gallery-1": {"settings": {"index": {"uuid": uuid}}}}
    if extra:
        raw["gallery-2"] = {"settings": {"index": {"uuid": "uuid-two"}}}
    return raw


async def scan(documents, *, versions=None, enabled=True, minimum=0):
    raw = AsyncMock()
    raw.indices.get.side_effect = versions or [indices(), indices()]
    raw.search.return_value = page(documents, len(documents))
    raw.scroll.return_value = page([], len(documents))
    context = SimpleNamespace(scans=[], stages=[])
    token = source_selection_context.set(context if enabled else None)
    try:
        result = await ElasticClient(endpoint="http://unused", client=raw).exhaustive_vector_search(
            index="gallery-*",
            filters=[{"term": {"sensor.id": "camera"}}],
            vector=[1.0, 0.0],
            field="embeddings.vector",
            nested_path="embeddings",
            candidate_limit=100,
            min_score=minimum,
        )
        return result, raw, context
    finally:
        source_selection_context.reset(token)


@pytest.mark.asyncio
async def test_snapshot_covers_below_threshold_documents_and_is_order_independent():
    first, raw, context = await scan([hit("a", score=0.1), hit("b", score=0.9)], minimum=0.5)
    second, _, _ = await scan([hit("b", score=0.9), hit("a", score=0.1)], minimum=0.5)
    evidence = first["candidate_coverage"]
    assert evidence["snapshot_digest"] == second["candidate_coverage"]["snapshot_digest"]
    assert evidence["eligible_documents"] == evidence["scored_documents"] == 2
    assert evidence["retained_documents"] == evidence["score_filtered_documents"] == 1
    assert evidence["scoped_documents"] is evidence["missing_vector_documents"] is None
    assert context.scans == [evidence]
    assert raw.search.call_args.kwargs["index"] == ["gallery-1"]
    assert raw.search.call_args.kwargs["body"]["seq_no_primary_term"] is True
    raw.clear_scroll.assert_awaited_once_with(scroll_id="snapshot")


@pytest.mark.asyncio
async def test_same_count_update_changes_snapshot_digest():
    first, _, _ = await scan([hit(seq=1, score=0.1)], minimum=0.5)
    second, _, _ = await scan([hit(seq=2, score=0.1)], minimum=0.5)
    assert first["candidate_coverage"]["snapshot_digest"] != second["candidate_coverage"]["snapshot_digest"]


@pytest.mark.asyncio
@pytest.mark.parametrize("after", [indices("recreated"), indices(extra=True)])
async def test_index_recreation_or_wildcard_expansion_rejected(after):
    with pytest.raises(BackendUnreachableError, match="identities changed"):
        await scan([hit()], versions=[indices(), after])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value",
    [("_seq_no", None), ("_primary_term", 0), ("_index", "foreign"), ("_id", ""), ("_score", float("nan"))],
)
async def test_unverifiable_document_rejected(field, value):
    document = hit()
    document[field] = value
    with pytest.raises(BackendUnreachableError):
        await scan([document])


@pytest.mark.asyncio
async def test_normal_scan_does_not_add_metadata_reads_or_version_fields():
    result, raw, context = await scan([{"_index": "gallery-1", "_id": "a", "_score": 0.5}], enabled=False)
    assert result["candidate_coverage"] == {"indexed": 1, "exhaustive": True, "budget": 100}
    raw.indices.get.assert_not_awaited()
    assert "seq_no_primary_term" not in raw.search.call_args.kwargs["body"]
    assert context.scans == []


@pytest.mark.asyncio
async def test_cancelled_snapshot_releases_scroll_without_publishing_evidence():
    raw = AsyncMock()
    raw.indices.get.return_value = indices()
    raw.search.return_value = page([hit()], 2)
    entered = asyncio.Event()

    async def wait(**_kwargs):
        entered.set()
        await asyncio.sleep(100)

    raw.scroll.side_effect = wait
    context = SimpleNamespace(scans=[])
    token = source_selection_context.set(context)
    try:
        task = asyncio.create_task(
            ElasticClient(endpoint="http://unused", client=raw).exhaustive_vector_search(
                index="gallery-*", filters=[], vector=[1.0, 0.0], field="embeddings.vector", candidate_limit=100
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        source_selection_context.reset(token)
    assert context.scans == []
    raw.clear_scroll.assert_awaited_once_with(scroll_id="snapshot")


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("VSS_SOURCE_SELECTION_STRESS") != "1", reason="opt-in 100k RSS/JSON stress receipt")
async def test_snapshot_100k_bounded_memory_and_json_receipt():
    total = 100000

    class Raw:
        indices = SimpleNamespace(get=AsyncMock(return_value=indices()))
        position = 0
        released = False

        async def search(self, **_kwargs):
            return await self.scroll()

        async def scroll(self, **_kwargs):
            end = min(total, self.position + 512)
            rows = [hit(str(i), seq=i) for i in range(self.position, end)]
            self.position = end
            return page(rows, total)

        async def clear_scroll(self, **_kwargs):
            self.released = True

    raw = Raw()
    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.perf_counter()
    context = SimpleNamespace(scans=[])
    token = source_selection_context.set(context)
    try:
        result = await ElasticClient(endpoint="http://unused", client=raw).exhaustive_vector_search(
            index="gallery-*", filters=[], vector=[1.0, 0.0], field="embeddings.vector", candidate_limit=total
        )
    finally:
        source_selection_context.reset(token)
    payload_bytes = len(json.dumps(result, separators=(",", ":")).encode())
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    receipt = {
        "eligible": total,
        "returned": len(result["hits"]["hits"]),
        "json_bytes": payload_bytes,
        "elapsed_seconds": time.perf_counter() - started,
        "rss_baseline_kib": baseline,
        "rss_peak_kib": peak,
        "rss_delta_kib": max(0, peak - baseline),
        "snapshot": context.scans[0],
    }
    Path("/tmp/vss-source-snapshot-stress.json").write_text(json.dumps(receipt, indent=2))
    assert receipt["returned"] == total
    assert payload_bytes < 40 * 1024 * 1024
    assert receipt["rss_delta_kib"] < 256 * 1024
    assert raw.released
    with pytest.raises(InvalidInputError):
        await ElasticClient(endpoint="http://unused", client=raw).exhaustive_vector_search(
            index="gallery-*", filters=[], vector=[1.0], field="embeddings.vector", candidate_limit=total + 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [{}, {"gallery-1": {}}, {"gallery-1": {"settings": {"index": {}}}}])
async def test_missing_index_identity_metadata_rejected_before_search(metadata):
    with pytest.raises(BackendUnreachableError):
        await scan([hit()], versions=[metadata])
