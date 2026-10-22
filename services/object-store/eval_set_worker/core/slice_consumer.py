"""Eval-set slices consumer (design Steps 3-6, track 11.3, group A).

Each consumer owns one partition = one slice. For its work item it runs a
PIT + search_after loop over its slice, streams the records into one
manifest<i>.jsonl in the object store, then publishes a completion notification
to the results topic. Kafka offset is committed only after the manifest is
written and the completion is published (at-least-once + idempotent whole-object
PUT => effectively once-complete per slice).
"""

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from typing import Any, Awaitable, Callable

from eval_set_worker.config import settings
from eval_set_worker.core import paths
from eval_set_worker.core.manifest import manifest_line, record_class
from eval_set_worker.runtime import run_with_retries
from eval_set_worker.shared.kafka import make_consumer
from eval_set_worker.shared.storage import StorageService
from deft_common import Status

logger = logging.getLogger(__name__)

# publish(topic, key, payload) -> awaitable
Publish = Callable[[str, str, dict[str, Any]], Awaitable[None]]

MANIFEST_SOURCE = ["metadata.content_metadata"]


async def export_slice(
    es, storage: StorageService, work: dict[str, Any]
) -> tuple[int, int, dict[str, int], dict[str, Any]]:
    """Stream one slice's manifest to the object store with CONSTANT memory.

    Pages ES and writes each record's JSONL line straight to a temp file — never
    holding the whole slice in RAM — hashing incrementally, then uploads via a
    single managed (multipart) transfer. Constant memory regardless of slice size
    (only one ES page is resident). Returns (records, total_bytes, class_counts,
    manifest-descriptor). An empty slice still writes an empty manifest object so
    N manifests always exist. (Ref: snapshot_worker/core/slice_consumer.py.)
    """
    eval_id = work["eval_id"]
    pit_id = work["pit_id"]
    query = work["query"]
    slice_id = work["slice"]["id"]
    slice_max = work["slice"]["max"]
    sort = work["sort"]
    keep_alive = work.get("keep_alive", settings.pit_keep_alive)

    key = paths.manifest_key(eval_id, slice_id)
    hasher = hashlib.sha256()
    records = 0
    total_bytes = 0
    class_counts: dict[str, int] = {}
    after: list[Any] | None = None

    tmp = tempfile.NamedTemporaryFile("wb", delete=False)
    try:
        while True:
            hits = await es.search_slice_page(
                pit_id=pit_id, query=query, slice_id=slice_id, slice_max=slice_max,
                sort=sort, keep_alive=keep_alive, size=settings.eval_page_size,
                search_after=after, source=MANIFEST_SOURCE,
            )
            if not hits:
                break
            for h in hits:
                line = manifest_line(h)
                blob = (json.dumps(line) + "\n").encode()
                tmp.write(blob)
                hasher.update(blob)
                records += 1
                if line.get("size_byte"):
                    try:
                        total_bytes += int(line["size_byte"])
                    except (TypeError, ValueError):
                        pass
                cls = record_class(h)
                class_counts[cls] = class_counts.get(cls, 0) + 1
            after = hits[-1]["sort"]
        tmp.close()
        # Single managed upload = atomic promote; empty slice -> empty object.
        # Same PIT + sort => identical bytes => idempotent per slice.
        if records == 0:
            await storage.put_object(key, b"", content_type="application/x-ndjson")
        else:
            await storage.upload_file(key, tmp.name, content_type="application/x-ndjson")
        size_bytes = os.path.getsize(tmp.name)
    finally:
        if not tmp.closed:
            tmp.close()
        os.unlink(tmp.name)

    manifest = {
        "format": "jsonl",
        "uri": paths.manifest_uri(eval_id, slice_id),
        "size_bytes": size_bytes,
        "sha256": hasher.hexdigest(),
    }
    return records, total_bytes, class_counts, manifest


async def _report(storage, publish: Publish, results_topic: str, completion: dict[str, Any]) -> None:
    """Durable per-slice marker FIRST (restart-safe source of truth), then the
    Kafka wake-up. Idempotent whole-object PUT keyed by slice_id."""
    await storage.put_object(
        paths.slice_marker_key(completion["eval_id"], completion["slice_id"]),
        json.dumps(completion).encode(),
        content_type="application/json",
    )
    await publish(results_topic, completion["eval_id"], completion)


async def process_slice(
    es,
    storage: StorageService,
    publish: Publish,
    work: dict[str, Any],
) -> dict[str, Any]:
    """Export one slice, write its manifest + a durable completion marker, then
    publish a Kafka wake-up. The marker (not the Kafka message) is the summary
    consumer's source of truth, so finalisation survives a restart.

    export_slice failures propagate — they are usually TRANSIENT (PIT expired, ES
    or S3 blip) and must be retried by SliceConsumer._run, not turned into a
    permanent slice failure. A slice is only marked terminally FAILED here when the
    job itself is no longer running, or by _run once retries are exhausted."""
    eval_id = work["eval_id"]
    slice_id = work["slice"]["id"]
    n = work["slice"]["max"]
    results_topic = work.get("results_topic", settings.eval_results_topic)

    raw_status = await storage.get_object(paths.status_key(eval_id))
    job_status = json.loads(raw_status).get("status") if raw_status is not None else None

    if raw_status is not None and job_status != Status.CREATING:
        logger.info(
            "eval %s: slice %d skipped (job status=%s)", eval_id, slice_id, job_status
        )
        completion = {
            "eval_id": eval_id, "slice_id": slice_id, "n": n, "status": Status.FAILED,
            "error": f"job not running (status={job_status})", "pit_id": work.get("pit_id"),
        }
    else:
        # May raise on a transient error -> propagates so _run retries the slice.
        records, total_bytes, class_counts, manifest = await export_slice(es, storage, work)
        completion = {
            "eval_id": eval_id, "slice_id": slice_id, "n": n, "status": Status.COMPLETED,
            "records": records, "bytes": total_bytes, "class_counts": class_counts,
            "manifest": manifest, "pit_id": work.get("pit_id"),
        }

    await _report(storage, publish, results_topic, completion)
    return completion


async def record_terminal_failure(storage, publish: Publish, work: dict[str, Any], error: str) -> None:
    """Record a terminal FAILED slice after _run exhausts its retries, so the job
    can finalise (as failed) instead of hanging without its Nth marker."""
    completion = {
        "eval_id": work["eval_id"], "slice_id": work["slice"]["id"],
        "n": work["slice"]["max"], "status": Status.FAILED,
        "error": error, "pit_id": work.get("pit_id"),
    }
    await _report(
        storage, publish, work.get("results_topic", settings.eval_results_topic), completion
    )


class SliceConsumer:
    """Consume eval-slices work items, export each slice, report completions."""

    def __init__(self, es, storage: StorageService, producer, consumer=None) -> None:
        self._es = es
        self._storage = storage
        self._producer = producer
        self._consumer = consumer or make_consumer(
            settings.eval_slices_topic, settings.eval_slices_group
        )
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._consumer.start()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._consumer.stop()

    async def _run(self) -> None:
        async for msg in self._consumer:
            try:
                work = json.loads(msg.value.decode())
            except Exception:  # noqa: BLE001 — poison message: unrecoverable, skip it
                logger.exception("slice consumer: undecodable message; skipping")
                await self._consumer.commit()
                continue
            # process_slice writes the durable marker before publishing. Retry it
            # in place (idempotent) so a transient blip never tears the worker
            # down; only commit once it succeeds (or is skipped after retries so
            # the worker keeps serving other slices).
            ok = await run_with_retries(
                lambda: process_slice(self._es, self._storage, self._producer.send, work),
                retries=settings.handler_max_retries,
                base_delay=settings.retry_base_delay_s,
                max_delay=settings.retry_max_delay_s,
                label="eval-slice-consumer",
            )
            if not ok:
                # Persistent failure -> record a terminal FAILED marker so the job
                # can finalise (as failed) instead of hanging without this slice.
                logger.error("slice consumer: gave up on a slice after retries; marking it failed")
                try:
                    await record_terminal_failure(
                        self._storage, self._producer.send, work, "slice failed after retries"
                    )
                except Exception:  # noqa: BLE001 — storage still down; job may stall (rare)
                    logger.exception("slice consumer: could not record terminal failure")
            await self._consumer.commit()
