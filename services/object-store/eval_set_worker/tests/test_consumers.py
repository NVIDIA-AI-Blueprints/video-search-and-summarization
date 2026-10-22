import hashlib
import json
import pytest

from unittest.mock import AsyncMock, MagicMock

from eval_set_worker.core import paths, slice_consumer, summary_consumer
from eval_set_worker.core.summary_consumer import aggregate, build_summary, finalize, try_finalize


def hit(path, machine, size, sort):
    return {
        "_id": path,
        "_source": {
            "metadata": {
                "content_metadata": {
                    "objStorePath": path,
                    "imageSize": size,
                    "machineInfo": {"machineId": machine},
                }
            }
        },
        "sort": sort,
    }


def work_item(eval_id="e1", slice_id=0, n=3):
    return {
        "eval_id": eval_id, "pit_id": "pit-1", "query": {"bool": {}},
        "slice": {"id": slice_id, "max": n}, "sort": [{"_shard_doc": "asc"}],
        "keep_alive": "10m", "results_topic": "eval-results", "n": n,
    }


class InMemStore:
    def __init__(self):
        self.objs: dict[str, bytes] = {}

    async def put_object(self, key, data, content_type="application/json"):
        self.objs[key] = data
        return f"url/{key}"

    async def upload_file(self, key, filepath, content_type="application/x-ndjson"):
        with open(filepath, "rb") as fh:
            self.objs[key] = fh.read()
        return f"url/{key}"

    async def get_object(self, key):
        return self.objs.get(key)

    async def list_keys(self, prefix):
        return [k for k in self.objs if k.startswith(prefix)]

    async def delete(self, key):
        self.objs.pop(key, None)


# ---------- slices consumer ----------

async def test_export_slice_streams_to_store_constant_memory():
    es = MagicMock()
    pages = [
        [hit("a.png", "m1", 10, [1]), hit("b.png", "m1", 20, [2])],
        [hit("c.png", "m2", 30, [3])],
        [],
    ]
    es.search_slice_page = AsyncMock(side_effect=pages)
    store = InMemStore()
    records, total_bytes, class_counts, manifest = await slice_consumer.export_slice(
        es, store, work_item()
    )

    assert records == 3
    assert total_bytes == 60
    assert class_counts == {"m1": 2, "m2": 1}
    # streamed straight to the object store (never held all lines in RAM)
    key = paths.manifest_key("e1", 0)
    written = store.objs[key].decode().splitlines()
    assert len(written) == 3
    assert json.loads(written[0])["file_path"] == "a.png"
    # manifest descriptor computed incrementally over the streamed bytes
    assert manifest["size_bytes"] == len(store.objs[key])
    assert manifest["sha256"] == hashlib.sha256(store.objs[key]).hexdigest()
    # search_after advanced to last hit's sort on the 2nd/3rd calls
    assert es.search_slice_page.await_args_list[1].kwargs["search_after"] == [2]
    assert es.search_slice_page.await_args_list[2].kwargs["search_after"] == [3]


async def test_process_slice_writes_manifest_and_publishes_success():
    es = MagicMock()
    es.search_slice_page = AsyncMock(side_effect=[[hit("a.png", "m1", 10, [1])], []])
    store = InMemStore()
    publish = AsyncMock()

    completion = await slice_consumer.process_slice(es, store, publish, work_item())

    assert completion["status"] == "completed"
    assert completion["records"] == 1
    key = paths.manifest_key("e1", 0)
    assert key in store.objs
    line = json.loads(store.objs[key].decode().strip())
    assert line["file_path"] == "a.png"
    # contract SetManifest for this slice
    m = completion["manifest"]
    assert m["format"] == "jsonl"
    assert m["uri"] == f"s3://aoi-samples/{key}"
    assert m["size_bytes"] == len(store.objs[key])
    assert len(m["sha256"]) == 64  # real sha256 hexdigest
    # durable per-slice marker written (restart-safe source of truth)
    marker = json.loads(store.objs[paths.slice_marker_key("e1", 0)])
    assert marker["status"] == "completed"
    assert marker["manifest"]["uri"] == m["uri"]
    publish.assert_awaited_once()
    assert publish.await_args.args[0] == "eval-results"


async def test_process_slice_propagates_transient_export_error():
    # A transient export error must PROPAGATE (so _run retries) rather than be
    # swallowed into a permanent FAILED slice. No marker/manifest is written.
    es = MagicMock()
    es.search_slice_page = AsyncMock(side_effect=RuntimeError("es down"))
    store = InMemStore()
    publish = AsyncMock()

    with pytest.raises(RuntimeError):
        await slice_consumer.process_slice(es, store, publish, work_item())

    assert paths.manifest_key("e1", 0) not in store.objs
    assert paths.slice_marker_key("e1", 0) not in store.objs
    publish.assert_not_awaited()


async def test_record_terminal_failure_writes_failed_marker():
    # After retries are exhausted, a terminal FAILED marker lets the job finalise.
    store = InMemStore()
    publish = AsyncMock()
    await slice_consumer.record_terminal_failure(store, publish, work_item(), "boom")
    marker = json.loads(store.objs[paths.slice_marker_key("e1", 0)])
    assert marker["status"] == "failed"
    assert marker["error"] == "boom"
    publish.assert_awaited_once()


async def test_process_slice_skips_when_job_not_running():
    es = MagicMock()
    store = InMemStore()
    store.objs[paths.status_key("e1")] = json.dumps(
        {"eval_id": "e1", "status": "failed"}
    ).encode()
    publish = AsyncMock()

    completion = await slice_consumer.process_slice(es, store, publish, work_item())

    assert completion["status"] == "failed"
    assert "not running" in completion["error"]
    assert not es.search_slice_page.called
    publish.assert_awaited_once()


async def test_export_slice_empty_writes_empty_manifest():
    es = MagicMock()
    es.search_slice_page = AsyncMock(return_value=[])
    store = InMemStore()
    records, total_bytes, class_counts, manifest = await slice_consumer.export_slice(
        es, store, work_item()
    )
    assert records == 0
    key = paths.manifest_key("e1", 0)
    assert store.objs[key] == b""  # empty object still written so N manifests exist
    assert manifest["size_bytes"] == 0
    assert manifest["sha256"] == hashlib.sha256(b"").hexdigest()


# ---------- summary consumer ----------

def completion(eval_id="e1", slice_id=0, n=3, status="completed", records=1, **kw):
    c = {
        "eval_id": eval_id, "slice_id": slice_id, "n": n, "status": status,
        "records": records, "bytes": records * 10,
        "class_counts": {"m1": records}, "pit_id": "pit-1",
    }
    if status == "completed":
        c["manifest"] = {
            "format": "jsonl",
            "uri": f"s3://aoi-samples/evals/{eval_id}/manifest{slice_id}.jsonl",
            "size_bytes": records * 10, "sha256": f"sha{slice_id}",
        }
    c.update(kw)
    return c


async def write_markers(store, comps):
    """Simulate slice consumers writing their durable per-slice markers."""
    for c in comps:
        await store.put_object(
            paths.slice_marker_key(c["eval_id"], c["slice_id"]), json.dumps(c).encode()
        )


async def test_try_finalize_aggregates_all_markers():
    store = InMemStore()
    store.objs[paths.status_key("e1")] = json.dumps(
        {"eval_id": "e1", "status": "creating", "n": 3, "reason": "r", "created_at": "t0"}
    ).encode()
    es = MagicMock(); es.close_pit = AsyncMock()
    await write_markers(store, [completion(slice_id=i) for i in range(3)])

    summary = await try_finalize(store, es, completion(slice_id=2))
    assert summary["status"] == "ready"
    assert summary["total_records"] == 3
    assert summary["class_counts"] == {"m1": 3}
    assert [m["uri"] for m in summary["manifests"]] == [
        "s3://aoi-samples/evals/e1/manifest0.jsonl",
        "s3://aoi-samples/evals/e1/manifest1.jsonl",
        "s3://aoi-samples/evals/e1/manifest2.jsonl",
    ]
    status = json.loads(store.objs[paths.status_key("e1")])
    assert status["status"] == "ready" and status["completed_slices"] == 3
    es.close_pit.assert_awaited_once_with("pit-1")
    # internal markers cleaned up — the eval set holds only manifests + snapshot + status
    assert not [k for k in store.objs if paths.status_prefix("e1") in k]


async def test_try_finalize_waits_until_all_markers():
    store = InMemStore()
    es = MagicMock(); es.close_pit = AsyncMock()
    await write_markers(store, [completion(slice_id=0), completion(slice_id=1)])  # 2/3
    assert await try_finalize(store, es, completion(slice_id=1)) is None
    assert paths.summary_key("e1") not in store.objs
    es.close_pit.assert_not_awaited()


async def test_try_finalize_idempotent_when_already_finalized():
    store = InMemStore()
    store.objs[paths.summary_key("e1")] = b'{"eval_id":"e1"}'  # already finalised
    es = MagicMock(); es.close_pit = AsyncMock()
    await write_markers(store, [completion(slice_id=i) for i in range(3)])
    assert await try_finalize(store, es, completion(slice_id=2)) is None
    es.close_pit.assert_not_awaited()  # no double PIT close


async def test_try_finalize_restart_safe_from_markers_only():
    # No in-memory state — markers alone drive finalisation, proving a
    # summary-consumer restart recovers full progress.
    store = InMemStore()
    store.objs[paths.status_key("e1")] = json.dumps(
        {"eval_id": "e1", "status": "creating", "n": 3, "created_at": "t0"}
    ).encode()
    es = MagicMock(); es.close_pit = AsyncMock()
    await write_markers(store, [completion(slice_id=i) for i in range(3)])
    summary = await try_finalize(store, es, completion(slice_id=0))  # any wake-up
    assert summary["status"] == "ready" and summary["total_records"] == 3


async def test_try_finalize_records_failures():
    store = InMemStore()
    es = MagicMock(); es.close_pit = AsyncMock()
    await write_markers(store, [
        completion(slice_id=0),
        completion(slice_id=1, status="failed", error="boom"),
        completion(slice_id=2),
    ])
    summary = await try_finalize(store, es, completion(slice_id=2))
    assert summary["status"] == "failed"
    assert summary["failed_slices"] == 1
    assert summary["total_records"] == 2  # only the 2 completed slices


def test_aggregate_orders_manifests_and_sums():
    reports = [completion(slice_id=2), completion(slice_id=0), completion(slice_id=1)]
    job = aggregate("e1", 3, reports)
    assert job["records"] == 3
    assert [m["uri"] for m in job["manifests"]] == [
        "s3://aoi-samples/evals/e1/manifest0.jsonl",
        "s3://aoi-samples/evals/e1/manifest1.jsonl",
        "s3://aoi-samples/evals/e1/manifest2.jsonl",
    ]


# ---------- manifest / export edge cases ----------

def test_manifest_line_and_record_class_fallbacks():
    from eval_set_worker.core.manifest import manifest_line, record_class

    # objStorePath missing -> file_path falls back to _id; size from imageSize.
    h = {"_id": "doc1", "_source": {"metadata": {"content_metadata": {"imageSize": 5}}}}
    line = manifest_line(h)
    assert line["file_path"] == "doc1"
    assert line["size_byte"] == 5
    assert record_class(h) == "unknown"  # no machineInfo / machineId

    # machineId at top level (not nested under machineInfo).
    h2 = {"_id": "d", "_source": {"metadata": {"content_metadata": {"machineId": "m9"}}}}
    assert record_class(h2) == "m9"

    # content_metadata not a dict -> treated as {}, still path-safe.
    h3 = {"_id": "d3", "_source": {"metadata": {"content_metadata": "oops"}}}
    assert manifest_line(h3)["file_path"] == "d3"
    assert record_class(h3) == "unknown"


async def test_export_slice_tolerates_non_numeric_size():
    es = MagicMock()
    h = {
        "_id": "a", "sort": [1],
        "_source": {"metadata": {"content_metadata": {
            "objStorePath": "a", "imageSize": "NaN", "machineInfo": {"machineId": "m1"},
        }}},
    }
    es.search_slice_page = AsyncMock(side_effect=[[h], []])
    records, total_bytes, class_counts, _ = await slice_consumer.export_slice(
        es, InMemStore(), work_item()
    )
    assert records == 1
    assert total_bytes == 0  # non-numeric size ignored, not fatal
    assert class_counts == {"m1": 1}


async def test_export_slice_single_slice_forwards_slice_max_1():
    es = MagicMock()
    es.search_slice_page = AsyncMock(side_effect=[[], []])
    await slice_consumer.export_slice(es, InMemStore(), work_item(n=1))
    assert es.search_slice_page.await_args.kwargs["slice_max"] == 1


# ---------- summary consumer: finalize edge case ----------

async def test_finalize_when_status_missing_still_writes_ready():
    store = InMemStore()  # no status.json present
    es = MagicMock(); es.close_pit = AsyncMock()
    job = aggregate("e1", 3, [completion(slice_id=i) for i in range(3)])
    summary = await finalize(store, es, job)
    assert summary["status"] == "ready"
    status = json.loads(store.objs[paths.status_key("e1")])
    assert status["status"] == "ready"
    assert status["completed_slices"] == 3


# ---------- consumer class loops (SliceConsumer / SummaryConsumer) ----------

class FakeMsg:
    def __init__(self, value: bytes):
        self.value = value


class FakeConsumer:
    def __init__(self, messages):
        self.messages = messages
        self.commits = 0

    async def start(self):
        pass

    async def stop(self):
        pass

    async def commit(self):
        self.commits += 1

    def __aiter__(self):
        self._it = iter(self.messages)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeProducer:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, topic, key, payload):
        if self.fail:
            raise RuntimeError("kafka down")
        self.sent.append((topic, key, payload))


async def test_slice_consumer_loop_processes_and_commits():
    es = MagicMock()
    es.search_slice_page = AsyncMock(side_effect=[[hit("a.png", "m1", 10, [1])], []])
    store = InMemStore()
    producer = FakeProducer()
    msgs = [FakeMsg(json.dumps(work_item()).encode())]
    sc = slice_consumer.SliceConsumer(es, store, producer, consumer=FakeConsumer(msgs))
    await sc._run()
    assert producer.sent and producer.sent[0][0] == "eval-results"
    assert paths.manifest_key("e1", 0) in store.objs
    assert sc._consumer.commits == 1


async def test_slice_consumer_retries_then_skips_and_stays_up(monkeypatch):
    # A persistently failing message is retried in place then skipped — the
    # long-running worker never exits and keeps processing the next message.
    monkeypatch.setattr(slice_consumer.settings, "handler_max_retries", 2)
    monkeypatch.setattr(slice_consumer.settings, "retry_base_delay_s", 0.0)
    es = MagicMock()
    es.search_slice_page = AsyncMock(return_value=[])
    store = InMemStore()
    producer = FakeProducer(fail=True)  # publish always raises
    msgs = [FakeMsg(json.dumps(work_item()).encode()),
            FakeMsg(json.dumps(work_item(slice_id=1)).encode())]
    sc = slice_consumer.SliceConsumer(es, store, producer, consumer=FakeConsumer(msgs))
    await sc._run()  # does NOT raise — worker stays up
    assert sc._consumer.commits == 2  # both skipped after retries, loop kept going


async def test_slice_consumer_skips_poison_message():
    # An undecodable message is committed + skipped (it can never succeed).
    es = MagicMock()
    store = InMemStore()
    producer = FakeProducer()
    sc = slice_consumer.SliceConsumer(
        es, store, producer, consumer=FakeConsumer([FakeMsg(b"not-json")])
    )
    await sc._run()  # does not raise
    assert sc._consumer.commits == 1


async def test_summary_consumer_loop_finalizes_and_commits():
    store = InMemStore()
    store.objs[paths.status_key("e1")] = json.dumps(
        {"eval_id": "e1", "status": "creating", "n": 3}
    ).encode()
    es = MagicMock(); es.close_pit = AsyncMock()
    comps = [completion(slice_id=i) for i in range(3)]
    await write_markers(store, comps)  # slice consumers wrote durable markers
    msgs = [FakeMsg(json.dumps(c).encode()) for c in comps]  # Kafka wake-ups
    sc = summary_consumer.SummaryConsumer(store, es, consumer=FakeConsumer(msgs))
    await sc._run()
    assert paths.summary_key("e1") in store.objs
    assert json.loads(store.objs[paths.status_key("e1")])["status"] == "ready"
    assert sc._consumer.commits == 3


async def test_summary_consumer_survives_bad_message():
    # BUG-2: a malformed message must not kill the consumer group.
    store = InMemStore()
    es = MagicMock(); es.close_pit = AsyncMock()
    await write_markers(store, [completion(slice_id=0, n=1)])
    msgs = [FakeMsg(b"not-json"),
            FakeMsg(json.dumps(completion(slice_id=0, n=1)).encode())]
    sc = summary_consumer.SummaryConsumer(store, es, consumer=FakeConsumer(msgs))
    await sc._run()  # bad json swallowed, loop continues
    assert sc._consumer.commits == 2
    assert paths.summary_key("e1") in store.objs  # 2nd message finalised (n=1)
