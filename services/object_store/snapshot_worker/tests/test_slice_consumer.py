import hashlib
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from snapshot_worker.core import slice_consumer as sc


def _work():
    return {
        "snapshot_id": "snap1",
        "pit_id": "PIT-1",
        "query": {"match_all": {}},
        "slice": {"id": 0, "max": 3},
        "sort": [{"_shard_doc": "asc"}],
        "keep_alive": "10m",
        "reason": "r",
        "metadata": {"k": "v"},
    }


def _resp(hits):
    return {"hits": {"hits": hits}}


def _hit(i, size=100):
    return {
        "_id": f"d{i}",
        "sort": [i],
        "_source": {"metadata": {"content_metadata": {"objStorePath": f"p{i}", "imageSize": size}}},
    }


def _msg(work):
    m = MagicMock()
    m.value = json.dumps(work).encode()
    return m


@pytest.fixture
def es():
    m = AsyncMock()
    m.search.side_effect = [_resp([_hit(1), _hit(2)]), _resp([])]
    return m


@pytest.fixture
def storage():
    m = MagicMock()
    m.upload_file = AsyncMock()
    m.put_bytes = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_process_slice_writes_manifest_and_returns_completed(es, storage):
    summary = await sc.process_slice(es, storage, _work())
    assert summary["status"] == "completed"
    assert summary["record_count"] == 2
    assert summary["byte_count"] == 200
    assert summary["manifest_key"] == "snapshots/snap1/manifest0.jsonl"
    assert summary["manifest_sha256"]
    # The compiled query is forwarded from the work item for provenance.
    assert summary["query"] == {"match_all": {}}


@pytest.mark.asyncio
async def test_empty_slice_manifest_metadata_matches_upload(storage):
    """A 0-record slice must still produce a manifest whose recorded size/sha256
    describe what was actually uploaded."""
    es = AsyncMock()
    es.search.side_effect = [_resp([])]  # zero records

    uploaded = {}

    async def _capture_upload(key, filepath, content_type="application/x-ndjson"):
        with open(filepath, "rb") as f:
            uploaded["bytes"] = f.read()

    async def _capture_put(key, data, content_type="application/json"):
        uploaded["bytes"] = data

    storage.upload_file = AsyncMock(side_effect=_capture_upload)
    storage.put_bytes = AsyncMock(side_effect=_capture_put)

    summary = await sc.process_slice(es, storage, _work())

    assert summary["status"] == "completed"
    assert summary["record_count"] == 0
    assert summary["byte_count"] == 0
    # The summary's size/sha256 must match the bytes actually written to the store.
    assert summary["manifest_size"] == len(uploaded["bytes"])
    assert summary["manifest_sha256"] == hashlib.sha256(uploaded["bytes"]).hexdigest()
    storage.put_bytes.assert_awaited_once()   # empty manifest via a plain put
    storage.upload_file.assert_not_called()   # not the managed transfer



@pytest.mark.asyncio
async def test_handle_reports_completed_keyed_by_snapshot_id(es, storage):
    producer = MagicMock()
    producer.send = AsyncMock()
    consumer = sc.SliceConsumer(es, storage, producer, consumer=MagicMock())
    await consumer._handle(_msg(_work()))
    producer.send.assert_awaited_once()
    summary = producer.send.call_args.args[0]
    assert summary["status"] == "completed"
    assert producer.send.call_args.kwargs["key"] == "snap1"
    # A durable marker is written before the report is emitted.
    storage.put_bytes.assert_awaited_once()
    assert storage.put_bytes.call_args.args[0] == "snapshots/snap1/_status/slice0.json"


@pytest.mark.asyncio
async def test_handle_failed_slice_reports_failure(storage):
    es = AsyncMock()
    es.search.side_effect = RuntimeError("es down")
    producer = MagicMock()
    producer.send = AsyncMock()
    consumer = sc.SliceConsumer(es, storage, producer, consumer=MagicMock())
    with patch.object(sc.asyncio, "sleep", new_callable=AsyncMock):
        await consumer._handle(_msg(_work()))
    summary = producer.send.call_args.args[0]
    assert summary["status"] == "failed"
    assert "es down" in summary["error"]
    # No manifest for a failed slice, but a failed marker is still recorded.
    storage.upload_file.assert_not_called()
    storage.put_bytes.assert_awaited_once()
    marker = json.loads(storage.put_bytes.call_args.args[1])
    assert marker["status"] == "failed"


@pytest.mark.asyncio
async def test_retries_then_succeeds(storage):
    es = AsyncMock()
    # First attempt raises; second attempt pages successfully.
    es.search.side_effect = [RuntimeError("blip"), _resp([_hit(1)]), _resp([])]
    with patch.object(sc.asyncio, "sleep", new_callable=AsyncMock) as sleep:
        summary = await sc.process_slice_with_retries(es, storage, _work())
    assert summary["status"] == "completed"
    assert summary["record_count"] == 1
    sleep.assert_awaited_once()  # one backoff between the two attempts


@pytest.mark.asyncio
async def test_exhausts_retries_then_fails(storage, monkeypatch):
    monkeypatch.setattr(sc.settings, "max_retries", 2)
    es = AsyncMock()
    es.search.side_effect = RuntimeError("es down")
    with patch.object(sc.asyncio, "sleep", new_callable=AsyncMock) as sleep:
        summary = await sc.process_slice_with_retries(es, storage, _work())
    assert summary["status"] == "failed"
    assert "es down" in summary["error"]
    assert es.search.await_count == 3   # max_retries + 1 attempts
    assert sleep.await_count == 2       # backoff between each attempt


def test_backoff_delay_respects_cap(monkeypatch):
    monkeypatch.setattr(sc.settings, "retry_jitter", False)
    monkeypatch.setattr(sc.settings, "retry_base_delay_s", 1.0)
    monkeypatch.setattr(sc.settings, "retry_max_delay_s", 5.0)
    assert sc._backoff_delay(0) == 1.0
    assert sc._backoff_delay(1) == 2.0
    assert sc._backoff_delay(2) == 4.0
    assert sc._backoff_delay(10) == 5.0  # capped
