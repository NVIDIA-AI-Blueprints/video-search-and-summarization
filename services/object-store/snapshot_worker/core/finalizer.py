"""Finalizer (design Steps 6-7) — restart-safe, object-store driven.

The durable source of truth is the per-slice status markers under
<prefix>/<snapshot_id>/_status/ (written by the slice consumers). A
snapshot-summary message is only a wake-up trigger: on each one the finalizer
lists the markers, and once all N are present it writes snapshot.json (ready or
failed) and closes the shared PIT. Because completion is derived from the object
store rather than in-memory counters, a finalizer restart loses no progress; and
the snapshot.json existence check makes finalization idempotent (no double
close). PIT lifecycle is owned here (decision #1).
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from snapshot_worker.config import settings
from snapshot_worker.core.paths import (
    latest_snapshot_key,
    snapshot_key,
    status_prefix,
)
from deft_common import Status
from snapshot_worker.shared.storage import StorageService
from snapshot_worker.shared.kafka import make_consumer

logger = logging.getLogger(__name__)


def build_snapshot_json(
    snapshot_id: str,
    reports: list[dict[str, Any]],
    slice_count: int,
    created_at: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    completed = sorted(
        (r for r in reports if r["status"] == Status.COMPLETED),
        key=lambda r: r["slice_id"],
    )
    failed = sorted(
        (r for r in reports if r["status"] != Status.COMPLETED),
        key=lambda r: r["slice_id"],
    )
    manifests = [
        {
            "format": "jsonl",
            "uri": f"s3://{settings.aws_bucket}/{r['manifest_key']}",
            "size_bytes": r["manifest_size"],
            "sha256": r["manifest_sha256"],
            "record_count": r["record_count"],
        }
        for r in completed
    ]
    total_records = sum(r["record_count"] for r in completed)
    total_bytes = sum(r["byte_count"] for r in completed)
    # No previous snapshot -> deltas are just the current totals (subtract 0).
    prev_records = previous["total_records"] if previous else 0
    prev_bytes = previous["total_bytes"] if previous else 0
    return {
        "snapshot_id": snapshot_id,
        "status": Status.FAILED if failed else Status.READY,
        "created_at": created_at,
        "reason": reports[0]["reason"] if reports else None,
        "metadata": reports[0]["metadata"] if reports else None,
        "query": reports[0]["query"] if reports else None,
        "catalog": reports[0].get("catalog", "aoi") if reports else "aoi",
        "slice_count": slice_count,
        "total_records": total_records,
        "total_bytes": total_bytes,
        "records_since_last_snapshot": total_records - prev_records,
        "bytes_since_last_snapshot": total_bytes - prev_bytes,
        "last_snapshot_id": previous["snapshot_id"] if previous else None,
        "last_snapshot_created_at": previous["created_at"] if previous else None,
        "manifests": manifests,
        "errors": [{"slice_id": r["slice_id"], "error": r["error"]} for r in failed],
        # class_counts intentionally omitted (open question #4).
    }


class Finalizer:
    def __init__(self, es, storage :StorageService, consumer=None) -> None:
        self._es = es
        self._storage = storage
        self._consumer = consumer or make_consumer(
            settings.snapshot_summary_topic, settings.summary_group_id
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
        report = json.loads(msg.value.decode())
        snapshot_id = report["snapshot_id"]
        slice_count = report["slice_max"]

        markers = await self._storage.list_keys(status_prefix(snapshot_id))
        if len(markers) < slice_count:
            return  # not every slice has reported yet
        if await self._storage.exists(snapshot_key(snapshot_id)):
            return  # already finalised — idempotent, avoids a double PIT close

        await self._finalize(snapshot_id, slice_count, markers)

    async def _load_latest(self, catalog: str) -> dict[str, Any] | None:
        """The most recently finalised snapshot.json for one catalog, or None."""
        if not await self._storage.exists(latest_snapshot_key(catalog)):
            return None
        return json.loads(await self._storage.get_bytes(latest_snapshot_key(catalog)))

    async def _finalize(self, snapshot_id: str, slice_count: int, markers: list[str]) -> None:
        reports = [
            json.loads(await self._storage.get_bytes(key)) for key in sorted(markers)
        ]
        catalog = reports[0].get("catalog", "aoi") if reports else "aoi"
        previous = await self._load_latest(catalog)
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        snapshot = build_snapshot_json(snapshot_id, reports, slice_count, created_at, previous)
        data = json.dumps(snapshot, indent=2).encode()

        await self._storage.put_bytes(
            snapshot_key(snapshot_id), data, content_type="application/json"
        )
        # Promote to the latest-snapshot pointer only on success — a failed or
        # empty snapshot must not become the baseline for the next snapshot's
        # deltas (an empty baseline would make every record look new).
        if snapshot["status"] == Status.READY and snapshot["total_records"] > 0:
            await self._storage.put_bytes(
                latest_snapshot_key(catalog), data, content_type="application/json"
            )

        pit_id = reports[0]["pit_id"]
        try:
            await self._es.close_pit(pit_id)
        except Exception:
            logger.exception("snapshot %s: failed to close PIT", snapshot_id)
        logger.info("snapshot %s finalised: %s", snapshot_id, snapshot["status"])
