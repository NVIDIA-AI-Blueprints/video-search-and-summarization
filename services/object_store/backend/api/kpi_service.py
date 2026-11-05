"""KPI-set planning + fan-out — the BE side (mirrors the snapshot flow).

POST /v1/kpi mints a kpi_id, opens ONE shared PIT, compiles the selection into
an Elasticsearch query, publishes one work item per slice to the kpi-slices
topic (slice i on partition i), and records a _request.json marker. The sliced
search_after export + completion (kpi-summary) run in the KPI worker, not here.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from deft_common import Status

from backend.api.kpi_schemas import KpiRequest
from backend.api.snapshot_service import SORT_SPEC
from backend.config import settings
from backend.shared.elasticsearch import ElasticsearchService
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService

logger = logging.getLogger(__name__)

_TIMESTAMP_FIELD = "metadata.content_metadata.timestamp"


def kpi_key(kpi_id: str) -> str:
    return f"{settings.kpi_prefix}/{kpi_id}/kpi.json"


def request_key(kpi_id: str) -> str:
    # Written at POST time so an in-progress KPI job is distinguishable from an
    # unknown id (see get_kpi).
    return f"{settings.kpi_prefix}/{kpi_id}/_request.json"


class KpiEmptySelectionError(Exception):
    """The KPI selection matched 0 records in ES; refusing to create an empty set."""


class KpiFanoutError(Exception):
    """One or more slice work items could not be published; the KPI job cannot
    proceed and its PIT has been closed. The caller should retry."""


def compile_kpi_selection(selection: dict[str, Any]) -> dict[str, Any]:
    """Translate a KPI selection into an ES bool query.

    - created_after (optional) -> range filter on the timestamp field (>=).
    - every other selection key (including the required `tag`) -> term (scalar) /
      terms (list) filter on the matching metadata.content_metadata field.
    """
    filters: list[dict[str, Any]] = []
    for key, value in selection.items():
        if key == "created_after":
            filters.append({"range": {_TIMESTAMP_FIELD: {"gte": value}}})
        else:
            clause = "terms" if isinstance(value, list) else "term"
            filters.append({clause: {f"metadata.content_metadata.{key}": value}})
    return {"bool": {"filter": filters}}


async def create_kpi(
    es: ElasticsearchService,
    kafka: KafkaService,
    storage: StorageService,
    request: KpiRequest,
) -> str:
    """Mint a kpi_id, open the shared PIT, and fan out N slice work items."""
    kpi_id = uuid.uuid4().hex
    n = settings.kpi_partitions
    selection = request.selection.model_dump(exclude_none=True)
    query = compile_kpi_selection(selection)

    total_records, _ = await es.catalog_totals(query)
    if total_records == 0:
        raise KpiEmptySelectionError(f"KPI selection matched 0 records: {selection}")

    # One PIT for the whole job — every slice must read the same frozen view.
    pit_id = await es.open_pit()

    def build_work(i: int) -> dict[str, Any]:
        return {
            "kpi_id": kpi_id,
            "pit_id": pit_id,
            "query": query,
            "selection": selection,
            "slice": {"id": i, "max": n},
            "sort": SORT_SPEC,
            "keep_alive": settings.pit_keep_alive,
            "reason": request.reason,
            "metadata": request.metadata,
        }

    # Fan out concurrently; explicit partition=i so slice i lands on partition i.
    # return_exceptions keeps one failing send from cancelling the rest.
    results = await asyncio.gather(
        *(
            kafka.send(
                build_work(i),
                topic=settings.kpi_slices_topic,
                key=str(i),
                partition=i,
            )
            for i in range(n)
        ),
        return_exceptions=True,
    )

    failed = [(i, r) for i, r in enumerate(results) if isinstance(r, Exception)]
    if failed:
        for i, exc in failed:
            logger.error("kpi %s: slice %d publish failed: %s", kpi_id, i, exc)
        # The job can never complete with missing slices — close the shared PIT
        # so it does not leak. Best-effort: a close failure must not mask the
        # original fan-out failure.
        try:
            await es.close_pit(pit_id)
        except Exception as close_exc:
            logger.error(
                "kpi %s: PIT close failed during cleanup: %s", kpi_id, close_exc
            )
        raise KpiFanoutError(
            f"kpi {kpi_id}: {len(failed)}/{n} slice publishes failed"
        )

    # Only on successful fan-out: record the request so GET can report `creating`
    # (and distinguish an in-progress job from an unknown id).
    requested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await storage.put_bytes(
        request_key(kpi_id),
        json.dumps(
            {"kpi_id": kpi_id, "requested_at": requested_at, "slice_count": n}
        ).encode(),
    )

    return kpi_id


async def get_kpi(storage: StorageService, kpi_id: str) -> dict[str, Any] | None:
    """Resolve a KPI job's status from the object store.

    - kpi.json present -> the finalized document (status ready/failed).
    - else _request.json present -> still creating.
    - else None -> unknown kpi_id (the router turns this into a 404).
    """
    if await storage.exists(kpi_key(kpi_id)):
        return json.loads(await storage.get_bytes(kpi_key(kpi_id)))

    if await storage.exists(request_key(kpi_id)):
        req = json.loads(await storage.get_bytes(request_key(kpi_id)))
        return {
            "kpi_id": kpi_id,
            "status": Status.CREATING,
            "requested_at": req.get("requested_at"),
            "manifests": [],
        }

    return None
