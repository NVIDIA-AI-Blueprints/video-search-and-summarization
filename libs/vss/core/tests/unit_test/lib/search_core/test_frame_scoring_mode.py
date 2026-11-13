# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import pytest

from vss_core.search_core.models.attribute_search import AttributeSearchInput
from vss_core.search_core.primitives import _attribute_helpers as h
from vss_core.search_core.primitives.attribute_search import AttributeSearch


def test_frame_lookup_always_uses_indexed_cosine():
    args = {
        "sensor_id": "sensor",
        "object_id": "0",
        "start_time": "start",
        "end_time": "end",
        "query_embedding": [1.0, 0.0],
    }
    query = h._frame_lookup_body(**args)["query"]["bool"]
    assert "function_score" not in str(query)
    assert query["must"][0]["nested"]["path"] == "objects"


def test_indexed_query_restricts_sensor_time_object_and_keeps_missing_vector_score():
    object_id = "18446744073709551614"
    body = h._indexed_frame_lookup_body("camera", object_id, "start", "end", [1.0, 0.0])
    query = body["query"]["bool"]
    assert query["filter"] == [
        {"term": {"sensorId.keyword": "camera"}},
        {"range": {"timestamp": {"gte": "start", "lte": "end"}}},
    ]
    nested = query["must"][0]["nested"]
    score = nested["query"]["script_score"]
    assert nested["path"] == "objects" and nested["score_mode"] == "max"
    assert score["query"]["bool"]["filter"] == [
        {"term": {"objects.id.keyword": object_id}},
    ]
    assert "l2norm(params.zero_vector" in score["script"]["source"]
    assert "dotProduct(params.query_vector" in score["script"]["source"]
    assert ".magnitude" not in score["script"]["source"]
    assert score["script"]["params"]["query_norm"] == 1.0
    assert score["script"]["params"]["zero_vector"] == [0.0, 0.0]


@pytest.mark.asyncio
async def test_attribute_search_keeps_request_scopes_after_failure(monkeypatch):
    async def fail(**kwargs):
        raise RuntimeError("backend failed")

    monkeypatch.setattr(h, "search_attributes", fail)
    primitive = AttributeSearch(
        es=object(),
        embed=object(),
        behavior_index="b",
        behavior_index_wildcard="b-*",
        frames_index="f",
    )
    with pytest.raises(RuntimeError, match="backend failed"):
        await primitive.run(AttributeSearchInput(query="person"))


def test_preparation_reuses_only_within_request_and_distinct_embedding():
    with h.frame_query_scope():
        first = h._prepare_frame_query([3.0, 4.0])
        assert first["query_norm"] == 5.0
        with h.frame_query_scope():
            assert h._prepare_frame_query([3.0, 4.0]) is first
        assert h._prepare_frame_query([4.0, 3.0]) is not first
    with h.frame_query_scope():
        assert h._prepare_frame_query([3.0, 4.0]) is not first


@pytest.mark.asyncio
async def test_preparation_scopes_are_isolated_between_requests():
    import asyncio

    async def request():
        with h.frame_query_scope():
            prepared = h._prepare_frame_query([1.0, 0.0])
            await asyncio.sleep(0)
            assert h._prepare_frame_query([1.0, 0.0]) is prepared
            return prepared

    left, right = await asyncio.gather(request(), request())
    assert left is not right
