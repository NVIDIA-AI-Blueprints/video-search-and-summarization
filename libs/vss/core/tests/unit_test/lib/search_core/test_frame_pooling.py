# SPDX-License-Identifier: Apache-2.0
"""Temporal score pooling must retain object isolation and best-frame evidence."""

from datetime import UTC
from datetime import datetime
from unittest.mock import AsyncMock

from pydantic import ValidationError
import pytest

from vss_core.search_core.errors import ConfigurationError
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.models.attribute_search import AttributeSearchInput
from vss_core.search_core.models.search import SearchInput
from vss_core.search_core.primitives import _attribute_helpers as h
from vss_core.search_core.primitives import _search_helpers as s
from vss_core.search_core.primitives.attribute_search import AttributeSearch
from vss_core.search_core.runtime import SearchRuntime

START = datetime(2025, 1, 1, tzinfo=UTC)
END = datetime(2025, 1, 2, tzinfo=UTC)


def response(scores):
    return {
        "hits": {
            "hits": [
                {
                    "_score": score,
                    "_source": {
                        "id": i,
                        "timestamp": f"frame-{i}",
                        "objects": [{"id": "person", "bbox": {"leftX": i, "rightX": i + 10, "topY": 1, "bottomY": 20}}],
                    },
                }
                for i, score in enumerate(scores)
            ]
        }
    }


def test_consistent_matches_outrank_one_transient_match_and_keep_best_screenshot():
    transient = response([0.95, 0.10, 0.10])
    stable = response([0.80, 0.80, 0.80])
    assert h._parse_frame_lookup(transient, "person")[2] > h._parse_frame_lookup(stable, "person")[2]
    selected = h._parse_frame_lookup(transient, "person", 3)
    assert selected[2] == pytest.approx(1.15 / 3)
    assert selected[2] < h._parse_frame_lookup(stable, "person", 3)[2]
    assert selected[0] == 0 and selected[3] == "frame-0"
    assert selected[1]["leftX"] == 0


def test_short_tracks_average_available_embeddings_without_zero_padding():
    assert h._parse_frame_lookup(response([0.8, 0.6]), "person", 14)[2] == pytest.approx(0.7)


def test_pooled_query_requires_matching_objects_embedding_only_when_enabled():
    default = h._frame_lookup_body("camera", "person", "start", "end", [1.0, 0.0])
    pooled = h._frame_lookup_body("camera", "person", "start", "end", [1.0, 0.0], frame_pool_size=14)
    assert default["size"] == 1 and pooled["size"] == 14
    query = pooled["query"]["bool"]
    assert query["filter"][0] == {"term": {"sensorId.keyword": "camera"}}
    nested = query["must"][0]["nested"]["query"]["script_score"]["query"]["bool"]["filter"]
    assert nested == [{"term": {"objects.id.keyword": "person"}}, {"exists": {"field": "objects.embedding.vector"}}]


@pytest.mark.parametrize("value", [0, 33, True, 1.5])
def test_pool_size_is_strict_and_bounded(value):
    for model, kwargs in [
        (AttributeSearchInput, {"query": "person"}),
        (SearchInput, {"attributes": ["person"], "search_mode": "attribute"}),
    ]:
        with pytest.raises(ValidationError):
            model(attribute_frame_pool_size=value, **kwargs)
    with pytest.raises(ValueError):
        h._frame_lookup_body("camera", "person", "start", "end", [1.0], frame_pool_size=value)


@pytest.mark.asyncio
async def test_msearch_and_single_search_pool_the_same_frames():
    class Single:
        async def search(self, **kwargs):
            assert kwargs["body"]["size"] == 3
            return response([0.9, 0.8, 0.7])

    class Batch:
        async def msearch(self, **kwargs):
            assert kwargs["searches"][1]["size"] == 3
            return {"responses": [response([0.9, 0.8, 0.7])]}

    params = {
        "candidates": [{"_source": {"sensor": {"id": "camera"}, "object": {"id": "person"}}}],
        "query_embedding": [1.0, 0.0],
        "frames_index": "frames",
        "frame_pool_size": 3,
        "timestamp_start": START,
        "timestamp_end": END,
    }
    single = await h._perform_frame_lookups(es=Single(), **params)
    batch = await h._perform_frame_lookups(es=Batch(), **params)
    assert single == batch and batch[0][2] == pytest.approx(0.8)


@pytest.mark.asyncio
@pytest.mark.parametrize("pool_size", [1, 14])
async def test_orchestrator_passes_pooling_only_when_requested(pool_size):
    class Attribute:
        async def ainvoke(self, params):
            assert params.get("attribute_frame_pool_size", 1) == pool_size
            if pool_size == 1:
                assert "attribute_frame_pool_size" not in params
            return []

    inp = SearchInput(attributes=["person"], search_mode="attribute", attribute_frame_pool_size=pool_size)
    assert (
        await s._run_attribute_only_search(
            attribute_list=["person"], search_input=inp, attribute_search_fn=Attribute(), top_k=10, min_similarity=0
        )
        == []
    )


@pytest.mark.asyncio
async def test_representative_weight_and_pooling_cannot_silently_compete():
    with pytest.raises(InvalidInputError, match="cannot be combined"):
        await h.search_by_attributes(
            query_embedding=[1.0],
            index="behavior",
            es=None,
            representative_weight=0.5,
            frame_pool_size=14,
        )


def test_pooling_without_an_exact_budget_is_rejected_not_silently_unranked():
    # Without candidate_limit results keep behavior-score order, so a pooled
    # frame score would be reported without ever changing the ranking.
    with pytest.raises(InvalidInputError, match="candidate_limit"):
        AttributeSearchInput(query="person", attribute_frame_pool_size=14).validate_semantics()
    with pytest.raises(InvalidInputError, match="candidate_limit"):
        SearchInput(attributes=["person"], search_mode="attribute", attribute_frame_pool_size=14).validate_semantics()
    AttributeSearchInput(query="person", attribute_frame_pool_size=14, candidate_limit=10).validate_semantics()


@pytest.mark.asyncio
async def test_pooling_requires_frame_evidence_and_rejects_representative_conflict(monkeypatch):
    callback = AsyncMock(return_value=[])
    monkeypatch.setattr(h, "search_attributes", callback)
    pooled = {"query": "person", "attribute_frame_pool_size": 14, "candidate_limit": 10}
    no_frames = AttributeSearch.from_runtime(SearchRuntime(), es=object(), embed=object())
    with pytest.raises(ConfigurationError, match="frame index"):
        await no_frames.run(AttributeSearchInput(**pooled, timestamp_start=START, timestamp_end=END))
    primitive = AttributeSearch.from_runtime(SearchRuntime(frames_index="f"), es=object(), embed=object())
    with pytest.raises(InvalidInputError, match="timestamp_start"):
        await primitive.run(AttributeSearchInput(**pooled))
    weighted = AttributeSearch.from_runtime(
        SearchRuntime(frames_index="f", attribute_representative_weight=0.5), es=object(), embed=object()
    )
    # Raised before per-attribute execution, where failures are skipped as empty.
    with pytest.raises(InvalidInputError, match="cannot be combined"):
        await weighted.run(AttributeSearchInput(**pooled, timestamp_start=START, timestamp_end=END))
    callback.assert_not_awaited()
    await primitive.run(AttributeSearchInput(**pooled, timestamp_start=START, timestamp_end=END))
    callback.assert_awaited_once()
