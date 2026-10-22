import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from snapshot_worker.core import finalizer as fz

SNAPSHOT_KEY = "snapshots/snap1/snapshot.json"
LATEST_KEY = "snapshots/latest_snapshot.json"


def _report(slice_id, status="completed", n=3, records=10, error=None):
    return {
        "snapshot_id": "snap1",
        "slice_id": slice_id,
        "slice_max": n,
        "pit_id": "PIT-1",
        "status": status,
        "record_count": records,
        "byte_count": records * 100,
        "manifest_key": f"snapshots/snap1/manifest{slice_id}.jsonl",
        "manifest_size": 500,
        "manifest_sha256": "abc",
        "reason": "r",
        "metadata": {"k": "v"},
        "query": {"bool": {"filter": [{"term": {"x": 1}}]}},
        "error": error,
    }


def _marker(slice_id):
    return f"snapshots/snap1/_status/slice{slice_id}.json"


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


class TestBuildSnapshotJson:
    def test_all_completed_is_ready(self):
        snap = fz.build_snapshot_json(
            "snap1", [_report(0), _report(1)], slice_count=2, created_at="2026-07-14T00:00:00Z"
        )
        assert snap["status"] == "ready"
        assert snap["total_records"] == 20
        assert len(snap["manifests"]) == 2
        assert snap["created_at"] == "2026-07-14T00:00:00Z"

    def test_aggregates_completed_and_failed(self):
        snap = fz.build_snapshot_json(
            "snap1",
            [_report(0), _report(1, status="failed", error="e")],
            slice_count=2,
            created_at="2026-07-14T00:00:00Z",
        )
        assert snap["status"] == "failed"
        assert snap["total_records"] == 10  # only the completed slice counts
        assert snap["errors"] == [{"slice_id": 1, "error": "e"}]

    def test_no_previous_sets_since_to_totals(self):
        snap = fz.build_snapshot_json(
            "snap1", [_report(0), _report(1)], slice_count=2, created_at="t"
        )
        assert snap["records_since_last_snapshot"] == snap["total_records"] == 20
        assert snap["bytes_since_last_snapshot"] == snap["total_bytes"] == 2000
        assert snap["last_snapshot_id"] is None
        assert snap["last_snapshot_created_at"] is None

    def test_previous_computes_deltas(self):
        previous = {
            "snapshot_id": "prev1",
            "created_at": "2026-07-01T00:00:00Z",
            "total_records": 12,
            "total_bytes": 1200,
        }
        snap = fz.build_snapshot_json(
            "snap1", [_report(0), _report(1)], slice_count=2, created_at="t", previous=previous
        )
        assert snap["records_since_last_snapshot"] == 20 - 12
        assert snap["bytes_since_last_snapshot"] == 2000 - 1200
        assert snap["last_snapshot_id"] == "prev1"
        assert snap["last_snapshot_created_at"] == "2026-07-01T00:00:00Z"


@pytest.mark.asyncio
async def test_finalizes_and_promotes_latest_when_all_complete(es, storage, store):
    _wire_markers(store, [_report(i) for i in range(3)])
    await _finalizer(es, storage)._handle(_msg(_report(0)))

    es.close_pit.assert_awaited_once_with("PIT-1")
    snap = json.loads(store[SNAPSHOT_KEY])
    assert snap["status"] == "ready"
    assert snap["total_records"] == 30
    assert snap["created_at"].endswith("Z")
    # The compiled ES query is recorded for provenance.
    assert snap["query"] == {"bool": {"filter": [{"term": {"x": 1}}]}}
    # ready snapshot is promoted to the latest pointer (same content)
    assert json.loads(store[LATEST_KEY])["snapshot_id"] == "snap1"


@pytest.mark.asyncio
async def test_waits_when_markers_incomplete(es, storage, store):
    _wire_markers(store, [_report(0)])  # 1 of 3
    await _finalizer(es, storage)._handle(_msg(_report(0)))
    assert SNAPSHOT_KEY not in store
    es.close_pit.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_already_finalized(es, storage, store):
    _wire_markers(store, [_report(i) for i in range(3)])
    store[SNAPSHOT_KEY] = b'{"sentinel": true}'
    await _finalizer(es, storage)._handle(_msg(_report(0)))
    assert store[SNAPSHOT_KEY] == b'{"sentinel": true}'  # untouched
    es.close_pit.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_snapshot_is_not_promoted(es, storage, store):
    _wire_markers(store, [_report(0), _report(1), _report(2, status="failed", error="boom")])
    await _finalizer(es, storage)._handle(_msg(_report(2, status="failed", error="boom")))

    snap = json.loads(store[SNAPSHOT_KEY])
    assert snap["status"] == "failed"
    assert snap["errors"] == [{"slice_id": 2, "error": "boom"}]
    assert LATEST_KEY not in store  # failed snapshot must not become the baseline
    es.close_pit.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_snapshot_is_not_promoted(es, storage, store):
    # An empty baseline would make every record look new to the next snapshot.
    _wire_markers(store, [_report(i, records=0) for i in range(3)])
    await _finalizer(es, storage)._handle(_msg(_report(0, records=0)))

    snap = json.loads(store[SNAPSHOT_KEY])
    assert snap["status"] == "ready"
    assert snap["total_records"] == 0
    assert SNAPSHOT_KEY in store  # the snapshot itself is still recorded
    assert LATEST_KEY not in store


@pytest.mark.asyncio
async def test_empty_snapshot_leaves_the_existing_baseline(es, storage, store):
    store[LATEST_KEY] = json.dumps(
        {"snapshot_id": "prev1", "created_at": "2026-07-01T00:00:00Z",
         "total_records": 12, "total_bytes": 1200}
    ).encode()
    _wire_markers(store, [_report(i, records=0) for i in range(3)])
    await _finalizer(es, storage)._handle(_msg(_report(0, records=0)))

    assert json.loads(store[LATEST_KEY])["snapshot_id"] == "prev1"


@pytest.mark.asyncio
async def test_uses_previous_latest_for_deltas(es, storage, store):
    store[LATEST_KEY] = json.dumps(
        {"snapshot_id": "prev1", "created_at": "2026-07-01T00:00:00Z",
         "total_records": 12, "total_bytes": 1200}
    ).encode()
    _wire_markers(store, [_report(i) for i in range(3)])
    await _finalizer(es, storage)._handle(_msg(_report(0)))

    snap = json.loads(store[SNAPSHOT_KEY])
    assert snap["last_snapshot_id"] == "prev1"
    assert snap["last_snapshot_created_at"] == "2026-07-01T00:00:00Z"
    assert snap["records_since_last_snapshot"] == 30 - 12
    assert snap["bytes_since_last_snapshot"] == 3000 - 1200
    # latest pointer now advances to this snapshot
    assert json.loads(store[LATEST_KEY])["snapshot_id"] == "snap1"


@pytest.mark.asyncio
async def test_pit_close_failure_is_swallowed(es, storage, store):
    es.close_pit = AsyncMock(side_effect=Exception("es unreachable"))
    _wire_markers(store, [_report(i) for i in range(3)])
    await _finalizer(es, storage)._handle(_msg(_report(0)))
    assert SNAPSHOT_KEY in store  # snapshot.json still written


class TestCatalogBaseline:
    """The latest-snapshot pointer is the baseline for every later delta. One
    shared pointer would let an ITS snapshot become the baseline AOI is
    measured against, which reports wrong numbers rather than failing."""

    @pytest.mark.asyncio
    async def test_its_promotes_its_own_pointer(self, es, store, storage):
        reports = [dict(_report(i), catalog="its") for i in range(3)]
        _wire_markers(store, reports)
        await _finalizer(es, storage)._handle(_msg(reports[0]))

        assert "snapshots/latest_snapshot_its.json" in store
        assert LATEST_KEY not in store, "an ITS snapshot overwrote the AOI baseline"

    @pytest.mark.asyncio
    async def test_the_two_baselines_do_not_overwrite_each_other(self, es, store, storage):
        store[LATEST_KEY] = json.dumps(
            {"snapshot_id": "aoi-old", "total_records": 100, "total_bytes": 1000}
        ).encode()
        reports = [dict(_report(i), catalog="its") for i in range(3)]
        _wire_markers(store, reports)
        await _finalizer(es, storage)._handle(_msg(reports[0]))

        # The AOI baseline is untouched, and the ITS deltas were measured from
        # nothing rather than from AOI's 100 records.
        assert json.loads(store[LATEST_KEY])["snapshot_id"] == "aoi-old"
        assert json.loads(store["snapshots/latest_snapshot_its.json"])[
            "records_since_last_snapshot"
        ] == 30

    @pytest.mark.asyncio
    async def test_a_report_without_a_catalog_stays_on_aoi(self, es, store, storage):
        # Work items published before the catalog field existed are still in
        # flight during a rolling upgrade; they must behave exactly as before.
        reports = [_report(i) for i in range(3)]
        _wire_markers(store, reports)
        await _finalizer(es, storage)._handle(_msg(reports[0]))

        assert LATEST_KEY in store
        assert json.loads(store[SNAPSHOT_KEY])["catalog"] == "aoi"
