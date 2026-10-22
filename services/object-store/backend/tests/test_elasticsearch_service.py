"""Tests for ElasticsearchService.catalog_totals.

The index-availability flags are the interesting part: a catalog that has never
been written has no index, and the ITS/Smart City path depends on that reading
as zero rather than erroring.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.shared.elasticsearch import ElasticsearchService

_OK_RESPONSE = {
    "_shards": {"total": 1},
    "hits": {"total": {"value": 3}},
    "aggregations": {"total_bytes": {"value": 99}},
}


def _service(response=None):
    svc = ElasticsearchService.__new__(ElasticsearchService)
    svc._client = MagicMock()
    svc._client.search = AsyncMock(return_value=response or _OK_RESPONSE)
    return svc


def _availability(svc):
    kwargs = svc._client.search.await_args.kwargs
    return kwargs["allow_no_indices"], kwargs["ignore_unavailable"]


@pytest.mark.asyncio
async def test_returns_totals_and_summed_bytes():
    svc = _service()
    assert await svc.catalog_totals({"match_all": {}}, index="aoi_*") == (3, 99)


@pytest.mark.asyncio
async def test_allow_missing_tolerates_an_index_that_does_not_exist_yet():
    """Regression: the availability options were passed both explicitly and via
    **kwargs, so any allow_missing=True call (i.e. every ITS request) raised
    TypeError: got multiple values for keyword argument 'allow_no_indices'."""
    svc = _service()
    assert await svc.catalog_totals({"match_all": {}}, index="sc_*", allow_missing=True) == (3, 99)
    assert _availability(svc) == (True, True)


@pytest.mark.asyncio
async def test_without_allow_missing_a_missing_index_is_not_swallowed():
    svc = _service()
    await svc.catalog_totals({"match_all": {}}, index="aoi_*", allow_missing=False)
    # A wildcard matching nothing is still fine; a genuinely absent index is not.
    assert _availability(svc) == (True, False)


@pytest.mark.asyncio
async def test_zero_shards_reads_as_zero():
    """A fresh deployment has no catalog index until the first sample lands;
    Elasticsearch answers with zero shards and no aggregations."""
    svc = _service({"_shards": {"total": 0}})
    assert await svc.catalog_totals({"match_all": {}}, index="sc_*", allow_missing=True) == (0, 0)


@pytest.mark.asyncio
async def test_null_aggregation_reads_as_zero_bytes():
    svc = _service(
        {
            "_shards": {"total": 1},
            "hits": {"total": {"value": 0}},
            "aggregations": {"total_bytes": {"value": None}},
        }
    )
    assert await svc.catalog_totals({"match_all": {}}, index="aoi_*") == (0, 0)


# ------------------------------------------------ retention helpers (sc_gc)


def _hit(doc_id, seq=7, term=1):
    return {"_index": "sc_cam", "_id": doc_id, "_seq_no": seq, "_primary_term": term}


@pytest.mark.asyncio
async def test_delete_hits_is_conditional_on_the_version_read():
    svc = _service()
    svc._client.bulk = AsyncMock(return_value={"items": []})

    await svc.delete_hits([_hit("a", seq=3, term=2)])

    kwargs = svc._client.bulk.await_args.kwargs
    assert kwargs["operations"] == [
        {"delete": {"_index": "sc_cam", "_id": "a", "if_seq_no": 3, "if_primary_term": 2}}
    ]
    # The next reference lookup must not see the deleted documents.
    assert kwargs["refresh"] == "wait_for"


def _conflict(doc_id, reason):
    return {"_id": doc_id, "status": 409,
            "error": {"type": "version_conflict_engine_exception", "reason": reason}}


@pytest.mark.asyncio
async def test_delete_hits_reports_conflicts_and_errors_but_not_missing_documents():
    # The 409 reasons are the ones Elasticsearch 9 returns for a conditional
    # delete of a rewritten document and of one that no longer exists.
    svc = _service()
    svc._client.bulk = AsyncMock(
        return_value={
            "items": [
                {"delete": {"_id": "gone", "status": 200}},
                {"delete": _conflict(
                    "already",
                    "[sc_cam:x]: version conflict, required seqNo [1], primary term [1]."
                    " but no document was found")},
                {"delete": _conflict(
                    "rewritten",
                    "[sc_cam:y]: version conflict, required seqNo [1], primary term [1]."
                    " current document has seqNo [4] and primary term [1]")},
                {"delete": {"_id": "broken", "status": 500, "error": {"type": "x"}}},
            ]
        }
    )

    kept = await svc.delete_hits([_hit(i) for i in ("gone", "already", "rewritten", "broken")])

    assert kept == {"rewritten", "broken"}


@pytest.mark.asyncio
async def test_update_hits_is_conditional_and_reports_what_was_not_applied():
    svc = _service()
    svc._client.bulk = AsyncMock(
        return_value={
            "items": [
                {"update": {"_id": "a", "status": 200}},
                {"update": _conflict("b", "current document has seqNo [9]")},
            ]
        }
    )
    doc = {"metadata": {"content_metadata": {"expiredAt": "2026-09-29T00:00:00.000Z"}}}

    kept = await svc.update_hits([_hit("a", seq=1), _hit("b", seq=2)], doc)

    assert kept == {"b"}
    kwargs = svc._client.bulk.await_args.kwargs
    assert kwargs["operations"] == [
        {"update": {"_index": "sc_cam", "_id": "a", "if_seq_no": 1, "if_primary_term": 1}},
        {"doc": doc},
        {"update": {"_index": "sc_cam", "_id": "b", "if_seq_no": 2, "if_primary_term": 1}},
        {"doc": doc},
    ]
    assert kwargs["refresh"] == "wait_for"


@pytest.mark.asyncio
async def test_ensure_date_field_maps_it_under_content_metadata():
    svc = _service()
    svc._client.indices = MagicMock()
    svc._client.indices.put_mapping = AsyncMock()

    await svc.ensure_date_field("sc_*", "expiredAt")

    kwargs = svc._client.indices.put_mapping.await_args.kwargs
    assert kwargs["index"] == "sc_*"
    assert kwargs["properties"] == {
        "metadata": {"properties": {"content_metadata": {"properties": {
            "expiredAt": {"type": "date"}}}}}
    }


@pytest.mark.asyncio
async def test_delete_hits_with_nothing_to_delete_sends_nothing():
    svc = _service()
    svc._client.bulk = AsyncMock()

    assert await svc.delete_hits([]) == set()
    svc._client.bulk.assert_not_awaited()


@pytest.mark.asyncio
async def test_referenced_paths_returns_the_paths_with_a_document():
    svc = _service(
        {
            "_shards": {"total": 2},
            "aggregations": {"paths": {"buckets": [{"key": "deft-its/a/1.jpg", "doc_count": 2}]}},
        }
    )

    found = await svc.referenced_paths("sc_*", ["deft-its/a/1.jpg", "deft-its/b/2.jpg"])

    assert found == {"deft-its/a/1.jpg"}
    kwargs = svc._client.search.await_args.kwargs
    assert kwargs["query"] == {
        "terms": {"metadata.content_metadata.objStorePath": ["deft-its/a/1.jpg", "deft-its/b/2.jpg"]}
    }
    assert kwargs["aggregations"]["paths"]["terms"]["size"] == 2
    # A closed index or a failed shard must fail the search, not drop out.
    assert kwargs["expand_wildcards"] == "all"
    assert kwargs["ignore_unavailable"] is False
    assert kwargs["allow_partial_search_results"] is False


@pytest.mark.asyncio
async def test_referenced_paths_can_leave_out_given_documents():
    svc = _service({"_shards": {"total": 1}, "aggregations": {"paths": {"buckets": []}}})

    await svc.referenced_paths("sc_*", ["p"], exclude_ids=["d1", "d2"])

    assert svc._client.search.await_args.kwargs["query"] == {
        "bool": {
            "filter": [{"terms": {"metadata.content_metadata.objStorePath": ["p"]}}],
            "must_not": [{"ids": {"values": ["d1", "d2"]}}],
        }
    }


@pytest.mark.asyncio
async def test_referenced_paths_refuses_a_partial_answer():
    svc = _service({"_shards": {"total": 2, "failed": 1},
                    "aggregations": {"paths": {"buckets": []}}})

    with pytest.raises(LookupError):
        await svc.referenced_paths("sc_*", ["deft-its/a/1.jpg"])


@pytest.mark.asyncio
async def test_referenced_paths_refuses_to_answer_without_shards():
    """No shard means no answer, not "nothing is referenced"."""
    svc = _service({"_shards": {"total": 0}, "hits": {"hits": []}})

    with pytest.raises(LookupError):
        await svc.referenced_paths("sc_*", ["deft-its/a/1.jpg"])


@pytest.mark.asyncio
async def test_search_pit_page_asks_for_sequence_numbers():
    svc = _service({"hits": {"hits": [{"_id": "a"}]}})

    hits = await svc.search_pit_page("PIT", {"match_all": {}}, size=5, search_after=[9])

    assert hits == [{"_id": "a"}]
    kwargs = svc._client.search.await_args.kwargs
    assert kwargs["pit"]["id"] == "PIT"
    assert kwargs["seq_no_primary_term"] is True
    assert kwargs["search_after"] == [9]
    assert kwargs["sort"] == [{"_shard_doc": "asc"}]


@pytest.mark.asyncio
async def test_referenced_paths_turns_an_error_answer_into_lookup_error():
    """A closed sc_ index makes the search fail outright."""
    from elasticsearch import BadRequestError

    svc = _service()
    svc._client.search = AsyncMock(
        side_effect=BadRequestError("index_closed_exception", MagicMock(status=400), {})
    )

    with pytest.raises(LookupError):
        await svc.referenced_paths("sc_*", ["deft-its/a/1.jpg"])
