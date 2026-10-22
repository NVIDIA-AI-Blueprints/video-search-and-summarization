"""Finalizer (mirrors snapshot Steps 6-7) — restart-safe, object-store driven.

The durable source of truth is the per-slice status markers under
<prefix>/<kpi_id>/_status/ (written by the slice consumers). A kpi-summary
message is only a wake-up trigger: on each one the finalizer lists the markers,
and once all N are present it writes kpi.json (ready or failed) and closes the
shared PIT. Because completion is derived from the object store rather than
in-memory counters, a finalizer restart loses no progress; and the kpi.json
existence check makes finalization idempotent (no double close). PIT lifecycle
is owned here.

Unlike snapshots, a KPI set has no "since last" deltas or latest pointer — a
KPI job is a one-shot export, so kpi.json is just the aggregate of its slices.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from deft_common import Status

from kpi_worker.config import settings
from kpi_worker.core.paths import kpi_key, status_prefix
from kpi_worker.shared.kafka import make_consumer
from kpi_worker.shared.storage import StorageService

logger = logging.getLogger(__name__)


def build_kpi_json(
    kpi_id: str,
    reports: list[dict[str, Any]],
    slice_count: int,
    created_at: str,
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
    return {
        "kpi_id": kpi_id,
        "status": Status.FAILED if failed else Status.READY,
        "created_at": created_at,
        "reason": reports[0]["reason"] if reports else None,
        "metadata": reports[0]["metadata"] if reports else None,
        "query": reports[0]["query"] if reports else None,
        "slice_count": slice_count,
        "total_records": sum(r["record_count"] for r in completed),
        "total_bytes": sum(r["byte_count"] for r in completed),
        "manifests": manifests,
        "errors": [{"slice_id": r["slice_id"], "error": r["error"]} for r in failed],
    }


class Finalizer:
    def __init__(self, es, storage: StorageService, consumer=None) -> None:
        self._es = es
        self._storage = storage
        self._consumer = consumer or make_consumer(
            settings.kpi_summary_topic, settings.summary_group_id
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
        kpi_id = report["kpi_id"]
        slice_count = report["slice_max"]

        markers = await self._storage.list_keys(status_prefix(kpi_id))
        if len(markers) < slice_count:
            return  # not every slice has reported yet
        if await self._storage.exists(kpi_key(kpi_id)):
            return  # already finalised — idempotent, avoids a double PIT close

        await self._finalize(kpi_id, slice_count, markers)

    async def _finalize(self, kpi_id: str, slice_count: int, markers: list[str]) -> None:
        reports = [
            json.loads(await self._storage.get_bytes(key)) for key in sorted(markers)
        ]
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        kpi = build_kpi_json(kpi_id, reports, slice_count, created_at)
        data = json.dumps(kpi, indent=2).encode()

        await self._storage.put_bytes(
            kpi_key(kpi_id), data, content_type="application/json"
        )

        pit_id = reports[0]["pit_id"]
        try:
            await self._es.close_pit(pit_id)
        except Exception:
            logger.exception("kpi %s: failed to close PIT", kpi_id)
        logger.info("kpi %s finalised: %s", kpi_id, kpi["status"])
