from unittest.mock import AsyncMock, MagicMock

from eval_set_worker.shared.elasticsearch import ElasticsearchService


def make_service(search_return):
    es_client = MagicMock()
    es_client.search = AsyncMock(return_value=search_return)
    svc = ElasticsearchService(client=es_client)
    return svc, es_client


async def test_search_slice_page_uses_explicit_kwargs_not_body():
    svc, es_client = make_service({"hits": {"hits": [{"sort": [7]}]}})
    hits = await svc.search_slice_page(
        pit_id="p1", query={"bool": {}}, slice_id=1, slice_max=3,
        sort=[{"_shard_doc": "asc"}], keep_alive="10m", size=1000,
        search_after=[5], source=["metadata.content_metadata"],
    )
    assert hits == [{"sort": [7]}]
    kwargs = es_client.search.await_args.kwargs
    assert "body" not in kwargs  # 9.x removed/deprecated body=
    assert kwargs["pit"] == {"id": "p1", "keep_alive": "10m"}
    assert kwargs["slice"] == {"id": 1, "max": 3}
    assert kwargs["source"] == ["metadata.content_metadata"]
    assert kwargs["track_total_hits"] is False
    assert kwargs["search_after"] == [5]
    assert "index" not in kwargs  # PIT search must omit index


async def test_search_slice_page_single_slice_omits_slice_param():
    svc, es_client = make_service({"hits": {"hits": []}})
    await svc.search_slice_page(
        pit_id="p1", query={"bool": {}}, slice_id=0, slice_max=1,
        sort=[{"_shard_doc": "asc"}], keep_alive="10m", size=1000,
    )
    kwargs = es_client.search.await_args.kwargs
    assert "slice" not in kwargs  # ES rejects slice with max=1
    assert "search_after" not in kwargs  # omitted on first page
