# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exact candidate scans preserve precision, filters and release scroll state."""

from unittest.mock import AsyncMock

import pytest

from vss_core.search_core.clients.elastic import ElasticClient
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.models.embed_search import EmbedSearchInput
from vss_core.search_core.primitives._embed_helpers import score_to_cosine


def _page(hits, scroll_id="scan", total=None):
    return {
        "_scroll_id": scroll_id,
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "failed": 0},
        "hits": {"hits": hits, "total": {"value": len(hits) if total is None else total, "relation": "eq"}},
    }


@pytest.mark.asyncio
async def test_scan_beyond_1615_nested_negative_scores_and_filters():
    hits = [{"_id": str(i), "_index": "gallery", "_score": i / 2000, "_source": {}} for i in range(1701)]
    raw = AsyncMock()
    raw.search.return_value = _page(hits[:512], total=1701)
    raw.scroll.side_effect = [
        _page(hits[512:1024], total=1701),
        _page(hits[1024:1536], total=1701),
        _page(hits[1536:], total=1701),
        _page([], total=1701),
    ]
    es = ElasticClient(endpoint="http://es", client=raw)
    filters = [{"term": {"sensor.id": "cohort"}}]
    result = await es.exhaustive_vector_search(
        index="gallery",
        filters=filters,
        vector=[1.0, 0.0],
        field="llm.visionEmbeddings.vector",
        nested_path="llm.visionEmbeddings",
        candidate_limit=2000,
    )
    assert len(result["hits"]["hits"]) == 1701
    assert result["candidate_coverage"] == {"indexed": 1701, "exhaustive": True, "budget": 2000}
    assert score_to_cosine(result["hits"]["hits"][-1]["_score"]) == -1
    body = raw.search.call_args.kwargs["body"]
    assert body["query"]["bool"]["filter"] == [{"bool": {"filter": filters}}]
    assert body["query"]["bool"]["must"][0]["nested"]["score_mode"] == "max"
    assert body["track_scores"] is True
    raw.clear_scroll.assert_awaited_once_with(scroll_id="scan")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["overflow", "backend"])
async def test_scan_releases_latest_scroll_on_failure(failure):
    raw = AsyncMock()
    raw.search.return_value = _page([{"_id": "first", "_score": 0.2}], "first", total=2)
    raw.scroll.side_effect = (
        [_page([{"_id": "second", "_score": 0.3}], "latest", total=2)]
        if failure == "overflow"
        else RuntimeError("lost transport")
    )
    es = ElasticClient(endpoint="http://es", client=raw)
    with pytest.raises(InvalidInputError if failure == "overflow" else RuntimeError):
        await es.exhaustive_vector_search(
            index="gallery",
            filters=[],
            vector=[1.0],
            field="embeddings.vector",
            candidate_limit=1 if failure == "overflow" else 2,
        )
    raw.clear_scroll.assert_awaited_once_with(scroll_id="first")
    if failure == "overflow":
        raw.scroll.assert_not_awaited()


def test_unrounded_scores_and_explicit_large_budget():
    assert score_to_cosine(0.5001234) == pytest.approx(0.0002468)
    assert EmbedSearchInput(query="q", top_k=1701, candidate_limit=2000).top_k == 1701
    with pytest.raises(ValueError):
        EmbedSearchInput(query="q", top_k=1701)


@pytest.mark.asyncio
@pytest.mark.parametrize("later", [False, True])
@pytest.mark.parametrize("defect", ["timeout", "shard_failure", "inexact_total", "changed_total", "early_end"])
async def test_reject_partial_scan_and_release_scroll(later, defect):
    from vss_core.search_core.errors import BackendUnreachableError

    raw = AsyncMock()
    first = _page([{"_id": "first", "_score": 0.8}], "first", total=1)
    last = _page([], "latest", total=1)
    damaged = last if later else first
    if defect == "timeout":
        damaged["timed_out"] = True
    elif defect == "shard_failure":
        damaged["_shards"] = {"total": 2, "successful": 1, "failed": 1}
    elif defect == "inexact_total":
        damaged["hits"]["total"]["relation"] = "gte"
    elif defect == "changed_total":
        damaged["hits"]["total"]["value"] = 2
    else:
        damaged["hits"]["hits"] = []
        damaged["hits"]["total"]["value"] = 2
    raw.search.return_value = first
    raw.scroll.return_value = last
    es = ElasticClient(endpoint="http://es", client=raw)
    with pytest.raises(BackendUnreachableError):
        await es.exhaustive_vector_search(
            index="gallery", filters=[], vector=[1.0], field="embeddings.vector", candidate_limit=20
        )
    raw.clear_scroll.assert_awaited_once_with(scroll_id="latest" if later or defect == "changed_total" else "first")


@pytest.mark.asyncio
async def test_attribute_scan_honors_deployed_nested_schema_and_excludes_vectors():
    from pathlib import Path

    from vss_core.search_core.primitives._attribute_helpers import _search_behavior

    repo = next(parent for parent in Path(__file__).resolve().parents if (parent / "deploy").is_dir())
    template = (
        repo / "deploy/docker/services/infra/elk/elasticsearch/init-scripts/elasticsearch-template-creation.sh"
    ).read_text()
    behavior = template.split('create_index_template "mdx_behavior_template"', 1)[1].split('"embeddings": {', 1)[1]
    assert behavior.lstrip().startswith('"type": "nested"')
    source = {"sensor": {"id": "cam"}, "object": {"id": "7"}, "timestamp": "2025-01-01T00:00:00Z"}
    raw = AsyncMock()

    def search(**kwargs):
        body = kwargs["body"]
        # A vector declared nested in the real template must be scored within
        # that nested path, while returned hits retain root behavior metadata.
        nested = body["query"]["bool"]["must"][0]["nested"]
        assert nested["path"] == "embeddings"
        assert nested["query"]["script_score"]["query"] == {"exists": {"field": "embeddings.vector"}}
        assert "embeddings.vector" in body["_source"]["excludes"]
        assert "llm.visionEmbeddings.vector" in body["_source"]["excludes"]
        assert kwargs["allow_partial_search_results"] is False
        assert body["track_total_hits"] is True
        return _page([{"_id": "7", "_score": 0.8, "_source": source}], total=1)

    raw.search.side_effect = search
    raw.scroll.return_value = _page([], total=1)
    hits = await _search_behavior(
        index="mdx-behavior-2025-01-01",
        query_embedding=[1.0],
        top_k=20,
        min_similarity=0,
        es=ElasticClient(endpoint="http://es", client=raw),
        candidate_limit=20,
    )
    assert len(hits) == 1 and hits[0]["_source"] == source


@pytest.mark.asyncio
async def test_scan_rejects_duplicate_scroll_documents_even_when_total_matches():
    from vss_core.search_core.errors import BackendUnreachableError

    raw = AsyncMock()
    duplicate = {"_index": "gallery", "_id": "same", "_score": 0.8}
    raw.search.return_value = _page([duplicate], "first", total=2)
    raw.scroll.return_value = _page([duplicate], "latest", total=2)
    with pytest.raises(BackendUnreachableError, match="duplicate"):
        await ElasticClient(endpoint="http://es", client=raw).exhaustive_vector_search(
            index="gallery",
            filters=[],
            vector=[1.0],
            field="embeddings.vector",
            candidate_limit=20,
        )
    raw.clear_scroll.assert_awaited_once_with(scroll_id="latest")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_total", [-1, True])
async def test_scan_rejects_invalid_exact_totals(invalid_total):
    from vss_core.search_core.errors import BackendUnreachableError

    raw = AsyncMock()
    raw.search.return_value = _page([], total=invalid_total)
    with pytest.raises(BackendUnreachableError, match="exact hit count"):
        await ElasticClient(endpoint="http://es", client=raw).exhaustive_vector_search(
            index="gallery",
            filters=[],
            vector=[1.0],
            field="embeddings.vector",
            candidate_limit=20,
        )
    raw.clear_scroll.assert_awaited_once_with(scroll_id="scan")
