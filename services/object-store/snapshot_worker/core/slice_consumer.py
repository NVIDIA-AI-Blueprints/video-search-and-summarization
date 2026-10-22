"""Slice consumer (design Steps 3-5).

Consumes one slice work item from snapshot-slices, drives the sliced
search_after loop, streams its manifest<i>.jsonl to the object store, and
reports the outcome to snapshot-summary. Offsets are committed only after the
report is sent (at-least-once; the manifest write is idempotent per slice).
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import tempfile
from typing import Any

from snapshot_worker.shared.storage import StorageService
from snapshot_worker.config import settings
from snapshot_worker.core.manifest import hit_size_bytes, manifest_line_bytes
from snapshot_worker.core.paths import manifest_key, status_key
from snapshot_worker.core.slice_reader import read_slice
from deft_common import Status
from snapshot_worker.shared.kafka import make_consumer

logger = logging.getLogger(__name__)


def _summary(
    work: dict[str, Any],
    *,
    status: Status,
    record_count: int = 0,
    byte_count: int = 0,
    manifest_key: str | None = None,
    manifest_size: int = 0,
    manifest_sha256: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    slc = work["slice"]
    return {
        "snapshot_id": work["snapshot_id"],
        "slice_id": slc["id"],
        "slice_max": slc["max"],
        "pit_id": work["pit_id"],
        "status": status,
        "record_count": record_count,
        "byte_count": byte_count,
        "manifest_key": manifest_key,
        "manifest_size": manifest_size,
        "manifest_sha256": manifest_sha256,
        "reason": work.get("reason"),
        "metadata": work.get("metadata"),
        "query": work.get("query"),
        # Absent on work items published before the catalog segment existed.
        "catalog": work.get("catalog", "aoi"),
        "error": error,
    }


async def process_slice(es, storage: StorageService, work: dict[str, Any]) -> dict[str, Any]:
    """Page the slice, stream its manifest to the object store, return a summary."""
    snapshot_id = work["snapshot_id"]
    slc = work["slice"]
    key = manifest_key(snapshot_id, slc["id"])

    hasher = hashlib.sha256()
    record_count = 0
    byte_count = 0
    tmp = tempfile.NamedTemporaryFile("wb", delete=False)
    try:
        async for hits in read_slice(
            es,
            pit_id=work["pit_id"],
            query=work["query"],
            slice_id=slc["id"],
            slice_max=slc["max"],
            sort=work["sort"],
            keep_alive=work["keep_alive"],
            page_size=settings.page_size,
        ):
            for hit in hits:
                line = manifest_line_bytes(hit)
                tmp.write(line)
                hasher.update(line)
                record_count += 1
                byte_count += hit_size_bytes(hit)
        tmp.close()
        if record_count == 0:
            await storage.put_bytes(key, b"", content_type="application/x-ndjson")
        else:
            # A single managed upload = atomic promote: the manifest key only appears
            # once the whole slice is written (a mid-slice crash leaves no object).
            await storage.upload_file(key, tmp.name)
        manifest_size = os.path.getsize(tmp.name)
    finally:
        if not tmp.closed:
            tmp.close()
        os.unlink(tmp.name)

    return _summary(
        work,
        status=Status.COMPLETED,
        record_count=record_count,
        byte_count=byte_count,
        manifest_key=key,
        manifest_size=manifest_size,
        manifest_sha256=hasher.hexdigest(),
    )


def _backoff_delay(attempt: int) -> float:
    delay = min(settings.retry_base_delay_s * (2**attempt), settings.retry_max_delay_s)
    if settings.retry_jitter:
        delay *= 0.5 + random.random() * 0.5
    return delay


async def process_slice_with_retries(es, storage: StorageService, work: dict[str, Any]) -> dict[str, Any]:
    """Run process_slice with bounded retries on transient errors.

    A failed summary is returned only after max_retries+1 attempts. Retrying is
    safe: each attempt re-pages the slice from the start and overwrites the same
    manifest key, so no partial or duplicated output survives. Note this blocks
    the partition's other queued slices for at most max_retries * max_delay.
    """
    last_exc: Exception | None = None
    for attempt in range(settings.max_retries + 1):
        try:
            return await process_slice(es, storage, work)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "snapshot %s slice %s attempt %d/%d failed: %s",
                work.get("snapshot_id"),
                work.get("slice"),
                attempt + 1,
                settings.max_retries + 1,
                exc,
            )
            if attempt < settings.max_retries:
                await asyncio.sleep(_backoff_delay(attempt))
    return _summary(work, status=Status.FAILED, error=str(last_exc))


class SliceConsumer:
    def __init__(self, es, storage: StorageService, producer, consumer=None) -> None:
        self._es = es
        self._storage = storage
        self._producer = producer
        self._consumer = consumer or make_consumer(
            settings.snapshot_slices_topic, settings.slices_group_id
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
            await self._handle(msg)
            await self._consumer.commit()

    async def _handle(self, msg) -> None:
        work = json.loads(msg.value.decode())
        summary = await process_slice_with_retries(self._es, self._storage, work)
        # Durable per-slice marker first — this is the finalizer's source of truth;
        # the Kafka report below is only a wake-up trigger.
        await self._storage.put_bytes(
            status_key(summary["snapshot_id"], summary["slice_id"]),
            json.dumps(summary).encode(),
        )
        # Always report — the finalizer needs a completion OR failure per slice.
        await self._producer.send(summary, key=summary["snapshot_id"])
