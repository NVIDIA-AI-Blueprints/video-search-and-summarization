# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Model-independent representative scoring, scope, and error contracts."""

import asyncio
from datetime import UTC
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from vss_core.search_core.errors import BackendUnreachableError
from vss_core.search_core.errors import ConfigurationError
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.models.attribute_search import AttributeSearchInput
from vss_core.search_core.primitives import _attribute_helpers as ah
from vss_core.search_core.primitives._frame_aggregation import aggregate_frame_lookups
from vss_core.search_core.primitives.attribute_search import AttributeSearch
from vss_core.search_core.runtime import SearchRuntime

START = datetime(2025, 1, 1, tzinfo=UTC)
END = datetime(2025, 1, 2, tzinfo=UTC)


def candidate(sensor="source", obj="0", vector=None):
    return {
        "_source": {
            "sensor": {"id": sensor},
            "object": {"id": obj},
            "embeddings": [{"vector": vector if vector is not None else [1.0, 0.0, 0.0]}],
        }
    }


def response(obj="0", vector=None, score=0.9):
    return {
        "hits": {
            "hits": [
                {
                    "_score": score,
                    "_source": {
                        "id": "frame",
                        "timestamp": "2025-01-01T00:00:01Z",
                        "objects": [
                            {"id": "distractor", "embedding": {"vector": [0.0, 0.0, 1.0]}},
                            {"id": obj, "embedding": {"vector": vector if vector is not None else [2.0, 0.0, 0.0]}},
                        ],
                    },
                }
            ]
        }
    }


class Backend:
    def __init__(self, replies=None):
        self.replies = replies
        self.calls = []

    async def msearch(self, **kwargs):
        self.calls.append(kwargs)
        return {"responses": self.replies if self.replies is not None else [response(), response()]}


def lookup(backend, query=None, candidates=None, weight=0.5):
    return aggregate_frame_lookups(
        candidates or [candidate()], query or [1.0, 0.0, 0.0], "raw-index", START, END, backend, weight
    )


@pytest.mark.parametrize("weight,expected", [(0.0, 0.9), (0.5, 0.95), (1.0, 1.0)])
def test_score_uses_normalized_matching_object_and_preserves_best_frame_evidence(weight, expected):
    out = asyncio.run(lookup(Backend(), weight=weight))
    assert out[0][0] == "frame" and out[0][2] == pytest.approx(expected)
    assert out[0][3] == "2025-01-01T00:00:01Z"


def test_representative_is_query_independent_and_scope_is_preserved():
    async def run():
        backend = Backend()
        await lookup(backend, query=[1.0, 0.0, 0.0])
        await lookup(backend, query=[0.0, 1.0, 0.0])
        representatives = []
        for call in backend.calls:
            assert call["index"] == "raw-index"
            maximum, representative = call["searches"][1::2]
            for body in (maximum, representative):
                filters = body["query"]["bool"]["filter"]
                assert filters[0] == {"term": {"sensorId.keyword": "source"}}
                assert filters[1]["range"]["timestamp"] == {
                    "gte": "2025-01-01T00:00:00Z",
                    "lte": "2025-01-02T00:00:00Z",
                }
            representatives.append(representative)
        assert representatives[0] == representatives[1]

    asyncio.run(run())


def test_concurrent_queries_have_no_hidden_query_cache_or_shared_track_state():
    async def run():
        first = Backend([response(score=0.8), response(vector=[2.0, 0.0, 0.0])])
        second = Backend([response(score=0.6), response(vector=[0.0, 3.0, 0.0])])
        a, b = await asyncio.gather(
            lookup(first), lookup(second, query=[1.0, 0.0, 0.0], candidates=[candidate(vector=[0.0, 1.0, 0.0])])
        )
        assert a[0][2] == pytest.approx(0.9)
        assert b[0][2] == pytest.approx(0.55)

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad", [{"timed_out": True}, {"_shards": {"failed": 1}}, {"error": {"reason": "script failed"}}]
)
def test_partial_or_failed_subsearches_are_not_successful_empty_results(bad):
    with pytest.raises(BackendUnreachableError):
        asyncio.run(lookup(Backend([bad, response()])))


def test_incomplete_response_and_mismatched_evidence_fail_explicitly():
    for replies in [[response()], [{"hits": {"hits": []}}, response()]]:
        with pytest.raises(BackendUnreachableError):
            asyncio.run(lookup(Backend(replies)))


def test_no_frames_retains_existing_behavior_fallback():
    empty = {"hits": {"hits": []}}
    assert asyncio.run(lookup(Backend([empty, empty]))) == [(None, None, None, None)]


@pytest.mark.parametrize("vector", [[0.0, 0.0, 0.0], [float("nan"), 1.0, 0.0], [1.0, 0.0]])
def test_invalid_representative_vectors_fail(vector):
    with pytest.raises(BackendUnreachableError):
        asyncio.run(lookup(Backend([response(), response(vector=vector)])))


def test_duplicate_tracklets_share_one_lookup_and_keep_input_alignment():
    backend = Backend()
    items = [candidate(vector=[1.0, 1.0, 0.0]), candidate(vector=[1.0, -1.0, 0.0]), {"_source": {}}]
    out = asyncio.run(lookup(backend, candidates=items))
    assert len(backend.calls[0]["searches"]) == 4
    assert out[0] == out[1] and out[2] is None


def test_single_search_backend_and_precomputed_query_embeddings_are_supported():
    class SingleBackend:
        async def search(self, **kwargs):
            assert kwargs["allow_partial_search_results"] is False
            return response()

    assert asyncio.run(lookup(SingleBackend()))[0][2] == pytest.approx(0.95)


def test_weight_is_explicit_finite_and_default_does_not_wrap_clients():
    embed = object()
    es = object()
    primitive = AttributeSearch.from_runtime(SearchRuntime(), es=es, embed=embed)
    assert primitive.embed_client is embed and primitive._es is es
    for bad in [-0.1, 1.1, float("nan"), float("inf")]:
        with pytest.raises(ConfigurationError):
            SearchRuntime(attribute_representative_weight=bad)
        with pytest.raises(ConfigurationError):
            AttributeSearch(
                es=es,
                embed=embed,
                behavior_index="b",
                behavior_index_wildcard="b-*",
                frames_index="f",
                representative_weight=bad,
            )


def test_representative_mode_requires_evidence_and_an_explicit_budget(monkeypatch):
    async def run():
        callback = AsyncMock(return_value=[])
        monkeypatch.setattr(ah, "search_attributes", callback)
        runtime = SearchRuntime(attribute_representative_weight=0.5, frames_index="f")
        primitive = AttributeSearch.from_runtime(runtime, es=object(), embed=object())
        with pytest.raises(InvalidInputError, match="timestamp_start"):
            await primitive.run(AttributeSearchInput(query="arbitrary language"))
        with pytest.raises(InvalidInputError, match="candidate_limit"):
            await primitive.run(
                AttributeSearchInput(query="arbitrary language", timestamp_start=START, timestamp_end=END)
            )
        request = AttributeSearchInput(
            query=["no ontology required"],
            query_embedding=[[1.0, 0.0, 0.0]],
            timestamp_start=START,
            timestamp_end=END,
            candidate_limit=20,
        )
        await primitive.run(request)
        assert callback.call_args.kwargs["representative_weight"] == 0.5
        assert callback.call_args.kwargs["search_input"].query == request.query
        assert request.candidate_limit == 20

    asyncio.run(run())


def test_vector_fields_requested_only_when_needed():
    async def run():
        es = AsyncMock()
        es.exhaustive_vector_search.return_value = {"hits": {"hits": []}}
        for include in [False, True]:
            await ah._search_behavior("b", [1.0, 0.0, 0.0], 10, 0.0, es, candidate_limit=20, include_embeddings=include)
            assert ("embeddings.vector" in es.exhaustive_vector_search.call_args.kwargs["source_includes"]) == include

    asyncio.run(run())


def test_query_threshold_does_not_change_the_track_center(monkeypatch):
    async def run():
        from vss_core.search_core.primitives import _frame_aggregation as aggregation

        high = candidate(vector=[1.0, 0.0, 0.0])
        high["_score"] = 0.9
        low = candidate(vector=[0.0, 1.0, 0.0])
        low["_score"] = 0.1
        scan = AsyncMock(return_value=[high, low])
        monkeypatch.setattr(ah, "_search_behavior", scan)
        backend = Backend()
        actual = aggregation.aggregate_frame_lookups

        async def checked(candidates, *args, **kwargs):
            assert candidates == [high]
            assert kwargs["representative_candidates"] == [high, low]
            return await actual(candidates, *args, **kwargs)

        monkeypatch.setattr(aggregation, "aggregate_frame_lookups", checked)
        monkeypatch.setattr(ah, "_safe_hit_to_result", lambda *_args: None)
        await ah.search_by_attributes(
            [1.0, 0.0, 0.0],
            "behavior",
            backend,
            timestamp_start=START,
            timestamp_end=END,
            frames_index="frames",
            min_similarity=0.5,
            candidate_limit=20,
            representative_weight=0.5,
        )
        assert scan.call_args.kwargs["min_similarity"] == 0
        representative = backend.calls[0]["searches"][3]
        script = representative["query"]["bool"]["must"][0]["nested"]["query"]["script_score"]["script"]
        assert script["params"]["query_vector"] == pytest.approx([2**-0.5, 2**-0.5, 0.0])

    asyncio.run(run())


def test_single_search_failure_cancels_sibling_requests():
    async def run():
        cancelled = asyncio.Event()

        class SingleBackend:
            def __init__(self):
                self.calls = 0

            async def search(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    await asyncio.sleep(0)
                    raise BackendUnreachableError("fixture", "failed")
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        with pytest.raises(BackendUnreachableError):
            await lookup(SingleBackend())
        assert cancelled.is_set()

    asyncio.run(run())


@pytest.mark.parametrize(
    "frames,enabled,source,allowed",
    [
        (None, True, "video_file", False),
        ("frames", False, "video_file", False),
        (None, True, "rtsp", True),
    ],
)
def test_frame_scope_validation_preserves_rtsp_wildcard_support(monkeypatch, frames, enabled, source, allowed):
    async def run():
        callback = AsyncMock(return_value=[])
        monkeypatch.setattr(ah, "search_attributes", callback)
        primitive = AttributeSearch.from_runtime(
            SearchRuntime(attribute_representative_weight=0.5, frames_index=frames, enable_frame_lookup=enabled),
            es=object(),
            embed=object(),
        )
        request = AttributeSearchInput(
            query="any attribute", source_type=source, timestamp_start=START, timestamp_end=END, candidate_limit=20
        )
        if allowed:
            await primitive.run(request)
            callback.assert_awaited_once()
        else:
            with pytest.raises(ConfigurationError):
                await primitive.run(request)

    asyncio.run(run())


def test_large_candidate_pools_use_bounded_msearch_batches():
    class BulkBackend:
        def __init__(self):
            self.sizes = []

        async def msearch(self, **kwargs):
            bodies = kwargs["searches"][1::2]
            self.sizes.append(len(bodies))
            assert kwargs["max_concurrent_searches"] == ah.FRAME_LOOKUP_CONCURRENCY
            replies = []
            for body in bodies:
                obj = body["query"]["bool"]["must"][0]["nested"]["query"]["script_score"]["query"]["bool"]["filter"][0][
                    "term"
                ]["objects.id.keyword"]
                replies.append(response(obj=obj))
            return {"responses": replies}

    backend = BulkBackend()
    items = [candidate(obj=str(i)) for i in range(300)]
    out = asyncio.run(lookup(backend, candidates=items))
    assert backend.sizes == [512, 88]
    assert len(out) == 300 and all(x[2] == pytest.approx(0.95) for x in out)
