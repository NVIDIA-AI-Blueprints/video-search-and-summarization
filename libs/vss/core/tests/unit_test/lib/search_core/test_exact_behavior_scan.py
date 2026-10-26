# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_exact_scan_budget_releases_scroll():
    from vss_core.search_core.clients.elastic import ElasticClient
    from vss_core.search_core.errors import InvalidInputError

    raw = AsyncMock()
    raw.search.return_value = {
        "_scroll_id": "cursor",
        "hits": {"total": {"relation": "eq", "value": 2}, "hits": []},
        "timed_out": False,
        "_shards": {"failed": 0},
    }
    es = ElasticClient(endpoint="http://fixture", client=raw)
    with pytest.raises(InvalidInputError):
        await es.exhaustive_vector_search(index="index", filters=[], vector=[1.0, 0.0], field="v", candidate_limit=1)
    raw.clear_scroll.assert_awaited_once_with(scroll_id="cursor")


@pytest.mark.asyncio
async def test_projection_is_opt_in_and_keeps_vector_exclusions():
    from vss_core.search_core.clients.elastic import ElasticClient

    raw = AsyncMock()
    raw.search.return_value = {"_scroll_id": "id", "hits": {"total": {"relation": "eq", "value": 0}, "hits": []}}
    es = ElasticClient(endpoint="http://fixture", client=raw)
    args = {
        "index": "behaviors",
        "filters": [],
        "vector": [1.0, 0.0],
        "field": "embeddings.vector",
        "candidate_limit": 100,
    }
    await es.exhaustive_vector_search(**args)
    assert "includes" not in raw.search.call_args.kwargs["body"]["_source"]
    await es.exhaustive_vector_search(**args, source_includes=["object", "sensor", "timestamp", "end"])
    source = raw.search.call_args.kwargs["body"]["_source"]
    assert source["includes"] == ["object", "sensor", "timestamp", "end"]
    assert "embeddings.vector" in source["excludes"]
    assert raw.clear_scroll.await_count == 2


def _page(ids, *, total=2, relation="eq", cursor="cursor", timed_out=False):
    return {
        "_scroll_id": cursor,
        "timed_out": timed_out,
        "hits": {
            "total": {"value": total, "relation": relation},
            "hits": [{"_index": "index", "_id": value, "_score": 0.8} for value in ids],
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pages",
    [
        [_page(["a"], relation="gte")],
        [_page(["a"], timed_out=True)],
        [_page(["a"]), _page([])],
        [_page(["a"]), _page(["a"])],
        [_page(["a"]), _page(["b"], total=3)],
        [_page(["a"], cursor=None)],
    ],
    ids=["inexact-count", "partial", "truncated", "duplicate", "changing-total", "missing-cursor"],
)
async def test_incomplete_scans_fail_instead_of_advertising_partial_rankings(pages):
    from vss_core.search_core.clients.elastic import ElasticClient
    from vss_core.search_core.errors import BackendUnreachableError

    raw = AsyncMock()
    raw.search.return_value = pages[0]
    raw.scroll.side_effect = pages[1:]
    es = ElasticClient(endpoint="http://fixture", client=raw)
    with pytest.raises(BackendUnreachableError):
        await es.exhaustive_vector_search(index="index", filters=[], vector=[1.0], field="v", candidate_limit=10)
    if pages[0].get("_scroll_id"):
        raw.clear_scroll.assert_awaited_once_with(scroll_id="cursor")


@pytest.mark.asyncio
async def test_complete_scan_keeps_exact_coverage_before_score_filtering():
    from vss_core.search_core.clients.elastic import ElasticClient

    raw = AsyncMock()
    raw.search.return_value = _page(["b"])
    raw.scroll.side_effect = [_page(["a"]), _page([])]
    es = ElasticClient(endpoint="http://fixture", client=raw)
    result = await es.exhaustive_vector_search(
        index="index", filters=[], vector=[1.0], field="v", candidate_limit=2, min_score=0.9
    )
    assert result["hits"]["hits"] == []
    assert result["candidate_coverage"] == {"indexed": 2, "exhaustive": True, "budget": 2}
    raw.clear_scroll.assert_awaited_once_with(scroll_id="cursor")
