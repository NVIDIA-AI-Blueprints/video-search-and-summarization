# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded document inspection must be complete and release snapshot contexts."""

from unittest.mock import AsyncMock

import pytest

from vss_core.search_core.clients.elastic import ElasticClient
from vss_core.search_core.errors import BackendUnreachableError
from vss_core.search_core.errors import InvalidInputError


def page(hits, total=1701, scroll="cursor"):
    return {"_scroll_id": scroll, "timed_out": False, "_shards": {"total": 1, "successful": 1, "failed": 0},
            "hits": {"total": {"value": total, "relation": "eq"}, "hits": hits}}


def hit(i):
    return {"_index": "raw", "_id": str(i), "_source": {"id": str(i), "sensorId": "clip"}}


@pytest.mark.asyncio
async def test_visits_more_than_1615_documents_and_filters_exact_scope():
    raw = AsyncMock()
    hits = [hit(i) for i in range(1701)]
    raw.search.return_value = page(hits[:512])
    raw.scroll.side_effect = [page(hits[512:1024]), page(hits[1024:1536]), page(hits[1536:], scroll="last")]
    visited = []
    es = ElasticClient(endpoint="http://es", client=raw)
    filters = [{"terms": {"sensorId.keyword": ["clip", "uuid"]}}]
    result = await es.visit_documents(index="raw", filters=filters, source_fields=["id", "sensorId"], document_limit=2000, visitor=lambda row: visited.append(row["_id"]))
    assert len(visited) == 1701
    assert result == {"index": "raw", "documents_checked": 1701, "document_limit": 2000, "exhaustive": True}
    assert raw.search.call_args.kwargs["body"]["query"] == {"bool": {"filter": filters}}
    assert raw.search.call_args.kwargs["body"]["_source"] == ["id", "sensorId"]
    raw.clear_scroll.assert_awaited_once_with(scroll_id="last")


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["budget", "truncated", "duplicate", "partial", "timeout", "malformed", "visitor"])
async def test_failures_cannot_claim_completeness_and_release_scroll(defect):
    raw = AsyncMock()
    raw.search.return_value = page([hit(0)], total=2)
    raw.scroll.return_value = page([hit(1)], total=2, scroll="last")
    budget = 10
    def visitor(_row):
        if defect == "visitor":
            raise ValueError("inspection failed")
    if defect == "budget":
        budget = 1
    if defect == "truncated":
        raw.scroll.return_value = page([], total=2, scroll="last")
    if defect == "duplicate":
        raw.scroll.return_value = page([hit(0)], total=2, scroll="last")
    if defect == "partial":
        raw.search.return_value["_shards"]["failed"] = 1
    if defect == "timeout":
        raw.search.return_value["timed_out"] = True
    if defect == "malformed":
        raw.search.return_value["hits"]["total"]["relation"] = "gte"
    with pytest.raises(InvalidInputError if defect == "budget" else ValueError if defect == "visitor" else BackendUnreachableError):
        await ElasticClient(endpoint="http://es", client=raw).visit_documents(index="raw", filters=[], source_fields=["id"], document_limit=budget, visitor=visitor)
    raw.clear_scroll.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_scroll_identity_cannot_claim_success():
    raw = AsyncMock()
    raw.search.return_value = page([hit(0)], total=1, scroll=None)
    with pytest.raises(BackendUnreachableError, match="scroll identity"):
        await ElasticClient(endpoint="http://es", client=raw).visit_documents(index="raw", filters=[], source_fields=["id"], document_limit=10, visitor=lambda _hit: None)


@pytest.mark.asyncio
async def test_invalid_later_scroll_identity_releases_previous_context():
    raw = AsyncMock()
    raw.search.return_value = page([hit(0)], total=2, scroll="original")
    raw.scroll.return_value = page([hit(1)], total=2, scroll=[])
    with pytest.raises(BackendUnreachableError, match="scroll identity"):
        await ElasticClient(endpoint="http://es", client=raw).visit_documents(index="raw", filters=[], source_fields=["id"], document_limit=10, visitor=lambda _hit: None)
    raw.clear_scroll.assert_awaited_once_with(scroll_id="original")


@pytest.mark.asyncio
async def test_frame_violation_after_first_thousand_is_inspected():
    from vss_core.search_core.content_validation import ContentInspector
    from vss_core.search_core.content_validation import validate_content_contract

    contract = validate_content_contract({"schema_version": 1, "embedding": {"model": "m", "dimensions": 2, "unit_normalized": True}, "frame_counts": {"clip": 1701}, "document_limit": 2000}, ["clip"], True)
    inspector = ContentInspector(contract, {"clip": ["clip"]}, "raw")
    raw = AsyncMock()
    hits = [hit(i) for i in range(1701)]
    hits[-1]["_source"]["id"] = "1701"
    raw.search.return_value = page(hits[:512])
    raw.scroll.side_effect = [page(hits[512:1024]), page(hits[1024:1536]), page(hits[1536:])]
    await ElasticClient(endpoint="http://es", client=raw).visit_documents(index="raw", filters=[], source_fields=["id", "sensorId"], document_limit=2000, visitor=inspector.visit)
    assert inspector.result["documents_checked"] == 1701
    assert inspector.result["violation_count"] == 1
    assert inspector.result["examples"][0]["id"] == "1700"
