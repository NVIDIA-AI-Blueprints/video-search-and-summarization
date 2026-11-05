import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from kpi_worker.core import finalizer as fz

KPI_KEY = "kpi/kpi1/kpi.json"


def _report(slice_id, status="completed", n=3, records=10, error=None):
    return {
        "kpi_id": "kpi1",
        "slice_id": slice_id,
        "slice_max": n,
        "pit_id": "PIT-1",
        "status": status,
        "record_count": records,
        "byte_count": records * 100,
        "manifest_key": f"kpi/kpi1/manifest{slice_id}.jsonl",
        "manifest_size": 500,
        "manifest_sha256": "abc",
        "reason": "r",
        "metadata": {"k": "v"},
        "query": {"bool": {"filter": [{"term": {"metadata.content_metadata.tag": "kpi"}}]}},
        "error": error,
    }


def _marker(slice_id):
    return f"kpi/kpi1/_status/slice{slice_id}.json"


def _msg(report):
    m = MagicMock()
    m.value = json.dumps(report).encode()
    return m


def _wire_markers(store, reports):
    for r in reports:
        store[_marker(r["slice_id"])] = json.dumps(r).encode()


@pytest.fixture
def es():
    m = AsyncMock()
    m.close_pit = AsyncMock()
    return m


@pytest.fixture
def store():
    return {}


@pytest.fixture
def storage(store):
    """A small in-memory object store backing the finalizer's reads/writes."""
    m = MagicMock()

    async def put_bytes(key, data, content_type="application/json"):
        store[key] = data

    async def get_bytes(key):
        return store[key]

    async def list_keys(prefix):
        return [k for k in store if k.startswith(prefix)]

    async def exists(key):
        return key in store

    m.put_bytes = AsyncMock(side_effect=put_bytes)
    m.get_bytes = AsyncMock(side_effect=get_bytes)
    m.list_keys = AsyncMock(side_effect=list_keys)
    m.exists = AsyncMock(side_effect=exists)
    return m


def _finalizer(es, storage):
    return fz.Finalizer(es, storage, consumer=MagicMock())


class TestBuildKpiJson:
    def test_all_completed_is_ready(self):
        kpi = fz.build_kpi_json(
            "kpi1", [_report(0), _report(1)], slice_count=2, created_at="2026-07-14T00:00:00Z"
        )
        assert kpi["status"] == "ready"
        assert kpi["total_records"] == 20
        assert kpi["total_bytes"] == 2000
        assert len(kpi["manifests"]) == 2
        assert kpi["created_at"] == "2026-07-14T00:00:00Z"
        assert kpi["query"] == {
            "bool": {"filter": [{"term": {"metadata.content_metadata.tag": "kpi"}}]}
        }

    def test_aggregates_completed_and_failed(self):
        kpi = fz.build_kpi_json(
            "kpi1",
            [_report(0), _report(1, status="failed", error="e")],
            slice_count=2,
            created_at="2026-07-14T00:00:00Z",
        )
        assert kpi["status"] == "failed"
        assert kpi["total_records"] == 10  # only the completed slice counts
        assert kpi["errors"] == [{"slice_id": 1, "error": "e"}]

    def test_no_deltas_or_latest_fields(self):
        # KPI is a one-shot export: no since-last / latest pointers.
        kpi = fz.build_kpi_json("kpi1", [_report(0)], slice_count=1, created_at="t")
        for absent in (
            "records_since_last_snapshot",
            "bytes_since_last_snapshot",
            "last_snapshot_id",
            "last_kpi_id",
        ):
            assert absent not in kpi


@pytest.mark.asyncio
async def test_finalizes_when_all_complete(es, storage, store):
    _wire_markers(store, [_report(i) for i in range(3)])
    await _finalizer(es, storage)._handle(_msg(_report(0)))

    es.close_pit.assert_awaited_once_with("PIT-1")
    kpi = json.loads(store[KPI_KEY])
    assert kpi["status"] == "ready"
    assert kpi["total_records"] == 30
    assert kpi["created_at"].endswith("Z")


@pytest.mark.asyncio
async def test_waits_when_markers_incomplete(es, storage, store):
    _wire_markers(store, [_report(0)])  # 1 of 3
    await _finalizer(es, storage)._handle(_msg(_report(0)))
    assert KPI_KEY not in store
    es.close_pit.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_already_finalized(es, storage, store):
    _wire_markers(store, [_report(i) for i in range(3)])
    store[KPI_KEY] = b'{"sentinel": true}'
    await _finalizer(es, storage)._handle(_msg(_report(0)))
    assert store[KPI_KEY] == b'{"sentinel": true}'  # untouched
    es.close_pit.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_slice_makes_failed_kpi(es, storage, store):
    _wire_markers(store, [_report(0), _report(1), _report(2, status="failed", error="boom")])
    await _finalizer(es, storage)._handle(_msg(_report(2, status="failed", error="boom")))

    kpi = json.loads(store[KPI_KEY])
    assert kpi["status"] == "failed"
    assert kpi["errors"] == [{"slice_id": 2, "error": "boom"}]
    es.close_pit.assert_awaited_once()


@pytest.mark.asyncio
async def test_pit_close_failure_is_swallowed(es, storage, store):
    es.close_pit = AsyncMock(side_effect=Exception("es unreachable"))
    _wire_markers(store, [_report(i) for i in range(3)])
    await _finalizer(es, storage)._handle(_msg(_report(0)))
    assert KPI_KEY in store  # kpi.json still written
