# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from unittest.mock import AsyncMock

import pytest

from vss_core.search_core import readiness
from vss_core.search_core.errors import BackendUnreachableError
from vss_core.search_core.errors import IndexNotFoundError
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.runtime import RAW_INDEX_ANCHOR
from vss_core.search_core.runtime import SearchRuntime


def response(counts, total=None):
    return {
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "failed": 0},
        "hits": {"total": {"value": sum(counts) if total is None else total, "relation": "eq"}},
        "aggregations": {"sources": {"buckets": {str(i): {"doc_count": c} for i, c in enumerate(counts)}}},
    }


@pytest.fixture
def clients(monkeypatch):
    es, behavior = AsyncMock(), AsyncMock()
    monkeypatch.setattr(readiness.ElasticClient, "from_runtime", lambda _rt: es)
    monkeypatch.setattr(readiness.ElasticClient, "from_runtime_behavior", lambda _rt: behavior)
    monkeypatch.setattr(
        readiness, "get_name_to_stream_id_map", AsyncMock(return_value={"clip": "uuid", "clip-long": "uuid2"})
    )
    return es, behavior


@pytest.mark.asyncio
async def test_exact_id_counts_and_legitimate_detection_gaps(clients):
    es, behavior = clients
    es.search.return_value = response([1, 2])
    behavior.search.side_effect = [response([0, 1]), response([0, 150])]
    result = await readiness.gallery_readiness(
        SearchRuntime(vst_internal_url="http://h", frames_index=RAW_INDEX_ANCHOR), ["clip", "clip-long"]
    )
    assert result["ready"]
    assert result["attribute_gaps"] == ["clip"]
    body = es.search.call_args.kwargs["body"]
    assert body["aggs"]["sources"]["filters"]["filters"]["0"] == {"terms": {"sensor.id.keyword": ["clip", "uuid"]}}
    assert body["size"] == 0
    es.aclose.assert_awaited_once()
    behavior.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_other_gallery_data_cannot_establish_attribute_readiness(clients):
    es, behavior = clients
    es.search.return_value = response([1])
    behavior.search.return_value = response([0], total=500)
    result = await readiness.gallery_readiness(
        SearchRuntime(vst_internal_url="http://h", frames_index=RAW_INDEX_ANCHOR), ["clip"]
    )
    assert not result["ready"]
    assert result["behavior"]["total"] == 500
    assert result["behavior"]["gallery_total"] == 0


@pytest.mark.asyncio
async def test_missing_anchors_and_embed_only(clients):
    es, behavior = clients
    es.search.return_value = response([1])
    behavior.search.side_effect = IndexNotFoundError("missing")
    runtime = SearchRuntime(vst_internal_url="http://h")
    assert not (await readiness.gallery_readiness(runtime, ["clip"]))["ready"]
    assert (await readiness.gallery_readiness(runtime, ["clip"], require_attributes=False))["ready"]


@pytest.mark.asyncio
async def test_unregistered_source_never_matches_unrelated_documents(clients):
    es, behavior = clients
    es.search.return_value = response([0])
    behavior.search.return_value = response([0])
    result = await readiness.gallery_readiness(SearchRuntime(vst_internal_url="http://h"), ["absent"])
    assert not result["ready"]
    assert result["unregistered"] == ["absent"]
    assert es.search.call_args.kwargs["body"]["aggs"]["sources"]["filters"]["filters"]["0"] == {"match_none": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["timeout", "shards", "inexact", "missing", "badcount"])
async def test_reject_partial_or_malformed_readiness(clients, defect):
    es, behavior = clients
    data = response([1])
    if defect == "timeout":
        data["timed_out"] = True
    if defect == "shards":
        data["_shards"]["failed"] = 1
    if defect == "inexact":
        data["hits"]["total"]["relation"] = "gte"
    if defect == "missing":
        del data["aggregations"]
    if defect == "badcount":
        data["aggregations"]["sources"]["buckets"]["0"]["doc_count"] = -1
    es.search.return_value = data
    with pytest.raises(BackendUnreachableError):
        await readiness.gallery_readiness(SearchRuntime(vst_internal_url="http://h"), ["clip"])
    es.aclose.assert_awaited_once()
    behavior.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_discovery_requires_reconfiguration(clients):
    es, behavior = clients
    es.search.return_value = response([1])
    behavior.search.return_value = response([1])
    result = await readiness.gallery_readiness(SearchRuntime(vst_internal_url="http://h"), ["clip"])
    assert not result["ready"]
    assert "re-run vss configure" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("sources", [[], [" "], ["clip", "clip"]])
async def test_invalid_source_scope(clients, sources):
    with pytest.raises(InvalidInputError):
        await readiness.gallery_readiness(SearchRuntime(), sources)


@pytest.mark.asyncio
async def test_embed_only_does_not_require_attribute_permissions(clients):
    es, behavior = clients
    es.search.return_value = response([1])
    behavior.search.side_effect = BackendUnreachableError("elasticsearch", "forbidden")
    result = await readiness.gallery_readiness(
        SearchRuntime(vst_internal_url="http://h"), ["clip"], require_attributes=False
    )
    assert result["ready"]
    assert result["behavior"]["checked"] is False
    behavior.search.assert_not_awaited()
