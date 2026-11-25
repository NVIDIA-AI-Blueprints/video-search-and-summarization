"""Eval-set summary consumer (design Steps 6-7, track 11.3, group B) —
restart-safe, object-store driven.

The durable source of truth is the per-slice completion markers under
`<prefix>/<eval_id>/_status/` (written by the slice consumers). A Kafka
completion on the results topic is only a wake-up trigger: on each one the
consumer lists the markers, and once all N are present it aggregates them,
writes eval.json (ready or failed), flips status.json, and closes the shared
PIT. Because completion is derived from the object store rather than in-memory
counters, a summary-consumer restart loses no progress; and the eval.json
existence check makes finalisation idempotent (no double PIT close / double
count).
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from eval_set_worker.config import settings
from eval_set_worker.core import paths
from eval_set_worker.runtime import run_with_retries
from eval_set_worker.shared.kafka import make_consumer
from deft_common import Status

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def aggregate(eval_id: str, n: int, reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold the durable per-slice markers into one job aggregate."""
    records = 0
    total_bytes = 0
    class_counts: dict[str, int] = {}
    manifests_by_slice: list[tuple[int, dict[str, Any]]] = []
    failures: list[dict[str, Any]] = []
    pit_id: str | None = None

    for r in reports:
        pit_id = pit_id or r.get("pit_id")
        if r.get("status") == Status.COMPLETED:
            records += r.get("records", 0)
            total_bytes += r.get("bytes", 0)
            for cls, cnt in (r.get("class_counts") or {}).items():
                class_counts[cls] = class_counts.get(cls, 0) + cnt
            if r.get("manifest"):
                manifests_by_slice.append((r["slice_id"], r["manifest"]))
        else:
            failures.append({"slice_id": r.get("slice_id"), "error": r.get("error")})

    manifests = [m for _, m in sorted(manifests_by_slice, key=lambda x: x[0])]
    failures.sort(key=lambda f: f["slice_id"] if f["slice_id"] is not None else -1)
    return {
        "eval_id": eval_id, "n": n, "records": records, "bytes": total_bytes,
        "class_counts": class_counts, "manifests": manifests, "failures": failures,
        "pit_id": pit_id,
    }


def build_summary(job: dict[str, Any], status: dict[str, Any] | None) -> dict[str, Any]:
    base = status or {}
    return {
        "eval_id": job["eval_id"],
        "status": Status.FAILED if job["failures"] else Status.READY,
        "n": job["n"],
        "total_records": job["records"],
        "total_bytes": job["bytes"],
        "class_counts": job["class_counts"],
        "manifests": job["manifests"],  # contract manifests[], ordered by slice
        "failed_slices": len(job["failures"]),
        "failures": job["failures"],
        "reason": base.get("reason"),
        "metadata": base.get("metadata"),
        "created_at": base.get("created_at"),
        "finalized_at": _now(),
    }


async def finalize(storage, es, job: dict[str, Any]) -> dict[str, Any]:
    """Write eval.json, flip status.json, close the shared PIT (best-effort)."""
    eval_id = job["eval_id"]
    raw = await storage.get_object(paths.status_key(eval_id))
    status = json.loads(raw) if raw else None
    summary = build_summary(job, status)

    await storage.put_object(
        paths.summary_key(eval_id),
        json.dumps(summary).encode(),
        content_type="application/json",
    )
    new_status = {
        **(status or {"eval_id": eval_id, "n": job["n"]}),
        "status": summary["status"],
        "completed_slices": job["n"] - len(job["failures"]),
        "failed_slices": len(job["failures"]),
        "finalized_at": summary["finalized_at"],
    }
    await storage.put_object(
        paths.status_key(eval_id),
        json.dumps(new_status).encode(),
        content_type="application/json",
    )

    if es is not None and job.get("pit_id"):
        try:
            await es.close_pit(job["pit_id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("eval %s: PIT close on finalise failed: %s", eval_id, exc)

    # GC the internal per-slice markers — they are bookkeeping, not part of the
    # eval set the ML team consumes (only manifests + eval.json + status.json
    # remain). Done LAST so eval.json (the idempotency guard) is durable first;
    # best-effort — a partial delete is harmless since eval.json already exists.
    try:
        for key in await storage.list_keys(paths.status_prefix(eval_id)):
            await storage.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("eval %s: marker cleanup failed: %s", eval_id, exc)

    logger.info("eval %s finalised: status=%s records=%d", eval_id, summary["status"], job["records"])
    return summary


async def try_finalize(storage, es, completion: dict[str, Any]) -> dict[str, Any] | None:
    """Finalise eval_id iff all N durable slice markers are present.

    Stateless: aggregates from the object-store markers, so it recovers full
    progress after a restart. Idempotent via the eval.json existence check.
    Returns the summary when it finalises, else None.
    """
    eval_id = completion["eval_id"]
    n = completion["n"]
    if await storage.get_object(paths.summary_key(eval_id)) is not None:
        return None  # already finalised
    marker_keys = await storage.list_keys(paths.status_prefix(eval_id))
    if len(marker_keys) < n:
        return None  # not every slice has reported yet
    reports = [json.loads(await storage.get_object(k)) for k in sorted(marker_keys)]
    job = aggregate(eval_id, n, reports)
    return await finalize(storage, es, job)


class SummaryConsumer:
    """Consume eval-results wake-ups; finalise from the durable _status/ markers."""

    def __init__(self, storage, es, consumer=None) -> None:
        self._storage = storage
        self._es = es
        self._consumer = consumer or make_consumer(
            settings.eval_results_topic, settings.eval_summary_group
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
                completion = json.loads(msg.value.decode())
            except Exception:  # noqa: BLE001 — poison message: unrecoverable, skip it
                logger.exception("summary consumer: undecodable message; skipping")
                await self._consumer.commit()
                continue
            # try_finalize is idempotent; retry it in place on a transient blip so
            # the worker stays up, and commit only once it succeeds (or is skipped
            # after retries).
            ok = await run_with_retries(
                lambda: try_finalize(self._storage, self._es, completion),
                retries=settings.handler_max_retries,
                base_delay=settings.retry_base_delay_s,
                max_delay=settings.retry_max_delay_s,
                label="eval-summary-consumer",
            )
            if not ok:
                logger.error(
                    "summary consumer: gave up on a wake-up after retries; skipping"
                )
            await self._consumer.commit()
