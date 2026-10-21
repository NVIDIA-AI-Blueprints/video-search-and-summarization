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


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_vector", [False, True])
async def test_optional_content_validation_is_a_separate_ready_gate(clients, bad_vector):
    es, behavior = clients
    es.search.return_value = response([2])
    behavior.search.return_value = response([1])
    contract = {"schema_version": 1, "embedding": {"model": "expected", "dimensions": 2, "unit_normalized": True},
                "frame_counts": {"clip": 10}, "document_limit": 2000}
    async def embeddings(**kwargs):
        for identity in ("chunk-a", "chunk-b"):
            kwargs["visitor"]({"_index": "embed", "_id": identity, "_source": {"sensor": {"id": "uuid"},
                "llm": {"info": {"requestedModel": "expected"}, "visionEmbeddings": [{"vector": [2.0 if bad_vector else 1.0, 0.0]}]}}})
        return {"documents_checked": 2, "exhaustive": True, "document_limit": 2000}
    async def frames(**kwargs):
        kwargs["visitor"]({"_index": "raw", "_id": "frame", "_source": {"sensorId": "clip", "id": "9"}})
        return {"documents_checked": 1, "exhaustive": True, "document_limit": 2000}
    es.visit_documents.side_effect = embeddings
    behavior.visit_documents.side_effect = frames
    result = await readiness.gallery_readiness(SearchRuntime(vst_internal_url="http://h", frames_index=RAW_INDEX_ANCHOR), ["clip"], content_contract=contract)
    assert result["ready"] is not bad_vector
    content = result["content_validation"]
    assert content["embedding"]["documents_checked"] == 2
    assert content["embedding"]["vectors_checked"] == 2
    assert content["raw"]["exhaustive"]
    assert es.visit_documents.call_args.kwargs["filters"] == [{"terms": {"sensor.id.keyword": ["clip", "uuid"]}}]


@pytest.mark.asyncio
async def test_invalid_content_contract_fails_before_backend_access(clients):
    es, behavior = clients
    with pytest.raises(InvalidInputError):
        await readiness.gallery_readiness(SearchRuntime(), ["clip"], content_contract={})
    es.search.assert_not_awaited()
    behavior.search.assert_not_awaited()


def test_registered_filename_aliases_are_exact_and_supported_only():
    names = {"clip.mp4": "uuid", "clip-long.MKV": "uuid2", "other.avi": "uuid3"}
    aliases = readiness._source_aliases(names, list(names))
    assert aliases == {"clip.mp4": ["clip.mp4", "uuid", "clip"],
                       "clip-long.MKV": ["clip-long.MKV", "uuid2", "clip-long"],
                       "other.avi": ["other.avi", "uuid3"]}
    assert readiness._source_aliases(names, ["clip"])["clip"] == []


@pytest.mark.parametrize("foreign", ["clip", "clip.mkv", "uuid"])
@pytest.mark.asyncio
async def test_alias_collision_with_outside_cohort_registration_fails_before_search(clients, monkeypatch, foreign):
    es, behavior = clients
    monkeypatch.setattr(readiness, "get_name_to_stream_id_map", AsyncMock(return_value={"clip.mp4": "uuid", foreign: "other-uuid"}))
    with pytest.raises(InvalidInputError, match="Ambiguous registered source alias"):
        await readiness.gallery_readiness(SearchRuntime(vst_internal_url="http://h"), ["clip.mp4"])
    es.search.assert_not_awaited()
    behavior.search.assert_not_awaited()


def test_unrelated_catalog_collision_does_not_expand_or_block_scope():
    names = {"clip.mp4": "uuid", "clip": "uuid2", "other": "uuid3"}
    assert readiness._source_aliases(names, ["other"]) == {"other": ["other", "uuid3"]}


@pytest.mark.asyncio
async def test_filename_registration_and_raw_stem_share_count_and_content_aliases(clients, monkeypatch):
    es, behavior = clients
    monkeypatch.setattr(readiness, "get_name_to_stream_id_map", AsyncMock(return_value={"clip.mp4": "uuid", "clip-long.mp4": "uuid2"}))
    es.search.return_value = response([1])
    behavior.search.return_value = response([1])
    value = {"schema_version": 1, "embedding": {"model": "expected", "dimensions": 2, "unit_normalized": True},
             "frame_counts": {"clip.mp4": 10}, "document_limit": 2000}
    async def embeddings(**kwargs):
        kwargs["visitor"]({"_index": "embed", "_id": "chunk", "_source": {"sensor": {"id": "uuid"}, "llm": {"info": {"requestedModel": "expected"}, "visionEmbeddings": [{"vector": [1.0, 0.0]}]}}})
        return {"documents_checked": 1, "exhaustive": True}
    async def frames(**kwargs):
        kwargs["visitor"]({"_index": "raw", "_id": "frame", "_source": {"sensorId": "clip", "id": "9"}})
        return {"documents_checked": 1, "exhaustive": True}
    es.visit_documents.side_effect = embeddings
    behavior.visit_documents.side_effect = frames
    result = await readiness.gallery_readiness(SearchRuntime(vst_internal_url="http://h", frames_index=RAW_INDEX_ANCHOR), ["clip.mp4"], content_contract=value)
    assert result["ready"]
    assert result["raw"]["counts"] == {"clip.mp4": 1}
    assert result["content_validation"]["raw"]["per_source"]["clip.mp4"]["documents"] == 1
    raw_filter = {"terms": {"sensorId.keyword": ["clip.mp4", "uuid", "clip"]}}
    assert behavior.search.call_args_list[-1].kwargs["body"]["aggs"]["sources"]["filters"]["filters"]["0"] == raw_filter
    assert behavior.visit_documents.call_args.kwargs["filters"] == [raw_filter]
    assert result["content_validation"]["contract"]["frame_counts"] == {"clip.mp4": 10}
