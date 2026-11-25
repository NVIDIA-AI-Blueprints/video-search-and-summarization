import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.api.kpi_schemas import KpiRequest
from backend.api.kpi_service import (
    KpiFanoutError,
    compile_kpi_selection,
    create_kpi,
    get_kpi,
    kpi_key,
    request_key,
)
from backend.config import settings


class TestCompileKpiSelection:
    def test_tag_becomes_term(self):
        q = compile_kpi_selection({"tag": "kpi"})
        assert q == {"bool": {"filter": [{"term": {"metadata.content_metadata.tag": "kpi"}}]}}

    def test_created_after_becomes_range(self):
        q = compile_kpi_selection({"tag": "kpi", "created_after": "2026-06-12T00:00:00Z"})
        assert {
            "range": {"metadata.content_metadata.timestamp": {"gte": "2026-06-12T00:00:00Z"}}
        } in q["bool"]["filter"]

    def test_list_becomes_terms(self):
        q = compile_kpi_selection({"tag": "kpi", "jobType": ["insp", "repair"]})
        assert {"terms": {"metadata.content_metadata.jobType": ["insp", "repair"]}} in q["bool"]["filter"]

    def test_no_created_after_no_range(self):
        q = compile_kpi_selection({"tag": "kpi"})
        assert all("range" not in f for f in q["bool"]["filter"])


@pytest.fixture
def mock_es():
    es = MagicMock()
    es.open_pit = AsyncMock(return_value="PIT-123")
    # create_kpi pre-checks the selection is non-empty before opening the PIT;
    # a non-zero default keeps that gate out of the way of the fan-out tests.
    es.catalog_totals = AsyncMock(return_value=(42, 4200))
    return es


@pytest.fixture
def mock_kafka():
    kafka = MagicMock()
    kafka.send = AsyncMock()
    return kafka


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.put_bytes = AsyncMock()
    storage.get_bytes = AsyncMock()
    storage.exists = AsyncMock(return_value=False)
    return storage


def _req(**sel):
    return KpiRequest(selection={"tag": "kpi", **sel})


@pytest.mark.asyncio
async def test_generates_kpi_id(mock_es, mock_kafka, mock_storage):
    kid = await create_kpi(mock_es, mock_kafka, mock_storage, _req())
    assert kid and isinstance(kid, str)


@pytest.mark.asyncio
async def test_pit_opened_once(mock_es, mock_kafka, mock_storage):
    await create_kpi(mock_es, mock_kafka, mock_storage, _req())
    mock_es.open_pit.assert_awaited_once()


@pytest.mark.asyncio
async def test_fans_out_one_per_partition(mock_es, mock_kafka, mock_storage):
    await create_kpi(mock_es, mock_kafka, mock_storage, _req())
    assert mock_kafka.send.await_count == settings.kpi_partitions
    partitions = sorted(c.kwargs["partition"] for c in mock_kafka.send.await_args_list)
    keys = sorted(c.kwargs["key"] for c in mock_kafka.send.await_args_list)
    assert partitions == list(range(settings.kpi_partitions))
    assert keys == sorted(str(i) for i in range(settings.kpi_partitions))
    assert all(
        c.kwargs["topic"] == settings.kpi_slices_topic
        for c in mock_kafka.send.await_args_list
    )


@pytest.mark.asyncio
async def test_all_slices_share_pit_and_slice_descriptor(mock_es, mock_kafka, mock_storage):
    await create_kpi(mock_es, mock_kafka, mock_storage, _req())
    works = [c.args[0] for c in mock_kafka.send.await_args_list]
    assert {w["pit_id"] for w in works} == {"PIT-123"}
    assert sorted(w["slice"]["id"] for w in works) == list(range(settings.kpi_partitions))
    assert all(w["slice"]["max"] == settings.kpi_partitions for w in works)


@pytest.mark.asyncio
async def test_writes_request_marker_on_success(mock_es, mock_kafka, mock_storage):
    kid = await create_kpi(mock_es, mock_kafka, mock_storage, _req())
    mock_storage.put_bytes.assert_awaited_once()
    key = mock_storage.put_bytes.call_args.args[0]
    payload = json.loads(mock_storage.put_bytes.call_args.args[1])
    assert key == request_key(kid)
    assert payload["kpi_id"] == kid
    assert payload["slice_count"] == settings.kpi_partitions
    assert payload["requested_at"].endswith("Z")


@pytest.mark.asyncio
async def test_fanout_failure_closes_pit_and_raises(mock_es, mock_kafka, mock_storage):
    mock_es.close_pit = AsyncMock()
    mock_kafka.send = AsyncMock(side_effect=RuntimeError("broker down"))
    with pytest.raises(KpiFanoutError):
        await create_kpi(mock_es, mock_kafka, mock_storage, _req())
    mock_es.close_pit.assert_awaited_once_with("PIT-123")
    mock_storage.put_bytes.assert_not_awaited()  # no marker on failure


@pytest.mark.asyncio
async def test_get_kpi_finalized(mock_storage):
    doc = {"kpi_id": "k1", "status": "ready", "total_records": 5}
    mock_storage.exists = AsyncMock(return_value=True)
    mock_storage.get_bytes = AsyncMock(return_value=json.dumps(doc).encode())
    result = await get_kpi(mock_storage, "k1")
    assert result == doc
    mock_storage.get_bytes.assert_awaited_once_with(kpi_key("k1"))


@pytest.mark.asyncio
async def test_get_kpi_creating(mock_storage):
    async def _exists(key):
        return key == request_key("k1")

    mock_storage.exists = AsyncMock(side_effect=_exists)
    mock_storage.get_bytes = AsyncMock(
        return_value=json.dumps({"kpi_id": "k1", "requested_at": "2026-07-23T00:00:00Z"}).encode()
    )
    result = await get_kpi(mock_storage, "k1")
    assert result["status"] == "creating"
    assert result["requested_at"] == "2026-07-23T00:00:00Z"
    assert result["manifests"] == []


@pytest.mark.asyncio
async def test_get_kpi_none_when_unknown(mock_storage):
    mock_storage.exists = AsyncMock(return_value=False)
    assert await get_kpi(mock_storage, "nope") is None
