import pytest
from unittest.mock import AsyncMock

from kpi_worker.core.slice_reader import read_slice


def _resp(hits):
    return {"hits": {"hits": hits}}


def _hit(sort_val, _id="d"):
    return {"_id": _id, "sort": [sort_val], "_source": {}}


async def _drain(es, **kwargs):
    params = dict(
        pit_id="P", query={}, slice_id=0, slice_max=3,
        sort=[{"_shard_doc": "asc"}], keep_alive="1m", page_size=2,
    )
    params.update(kwargs)
    return [page async for page in read_slice(es, **params)]


@pytest.mark.asyncio
async def test_pages_until_empty():
    es = AsyncMock()
    es.search.side_effect = [_resp([_hit(1), _hit(2)]), _resp([_hit(3)]), _resp([])]
    pages = await _drain(es)
    assert [len(p) for p in pages] == [2, 1]
    assert es.search.await_count == 3


@pytest.mark.asyncio
async def test_search_after_advances_to_last_sort():
    es = AsyncMock()
    es.search.side_effect = [_resp([_hit(1), _hit(2)]), _resp([])]
    await _drain(es)
    first, second = es.search.await_args_list
    assert "search_after" not in first.kwargs           # no cursor on first page
    assert second.kwargs["search_after"] == [2]         # last hit's sort


@pytest.mark.asyncio
async def test_slice_included_when_max_gt_1():
    es = AsyncMock()
    es.search.side_effect = [_resp([])]
    await _drain(es, slice_id=2, slice_max=5)
    assert es.search.await_args_list[0].kwargs["slice"] == {"id": 2, "max": 5}


@pytest.mark.asyncio
async def test_slice_omitted_when_max_is_1():
    es = AsyncMock()
    es.search.side_effect = [_resp([])]
    await _drain(es, slice_max=1)
    assert "slice" not in es.search.await_args_list[0].kwargs


@pytest.mark.asyncio
async def test_hot_path_flags_present():
    es = AsyncMock()
    es.search.side_effect = [_resp([])]
    await _drain(es)
    kw = es.search.await_args_list[0].kwargs
    assert kw["track_total_hits"] is False
    assert kw["source"] == ["metadata"]
    assert kw["pit"] == {"id": "P", "keep_alive": "1m"}
