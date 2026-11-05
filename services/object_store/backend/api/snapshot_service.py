"""Snapshot planning + fan-out — the BE side (Steps 0-2 of the catalog design).

On a POST /v1/<catalog>/snapshot the BE resolves the snapshot_id, opens ONE shared PIT,
compiles the selection into an Elasticsearch query, and publishes one work item
per slice to the snapshot-slices topic with slice i on partition i. The actual
sliced search_after pagination runs in the consumer group, not here.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from deft_common import Status, Tag

from backend.api.snapshot_schemas import SnapshotRequest
from backend.config import settings
from backend.shared.catalog import SELECTION_FIELDS, Catalog, catalog_index
from backend.shared.elasticsearch import ElasticsearchService
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService

logger = logging.getLogger(__name__)

# Cheapest valid, unique cursor for search_after over a PIT (design §7 / App. A).
SORT_SPEC = [{"_shard_doc": "asc"}]

_TAG_FIELD = "metadata.content_metadata.tag"
# Smart City only: when the backend published the frame's metadata (the Kafka
# CreateTime of its sc-metadata record, set in sc-ingest.conf).
INGESTED_AT_FIELD = "metadata.content_metadata.ingestedAt"
# Smart City only: set by the retention pass (sc_gc) on documents whose frames
# are due for deletion. New ITS snapshots leave them out.
EXPIRED_AT_FIELD = "metadata.content_metadata.expiredAt"

# Tags whose records belong to the KPI-set pipeline and are always excluded from
# the general catalog snapshot (tag not in {...}).
_EXCLUDED_TAGS = (Tag.KPI, Tag.KPI_GOLDEN)


def _kpi_exclusion() -> list[dict[str, Any]]:
    """must_not clauses dropping KPI-set records from the general catalog.

    Snapshots are the general catalog; KPI-set tags belong to the KPI pipeline
    and are always excluded. Records with no tag are kept — must_not/term only
    matches docs that actually carry the value.
    """
    return [{"term": {_TAG_FIELD: tag}} for tag in _EXCLUDED_TAGS]


def snapshot_key(snapshot_id: str) -> str:
    return f"{settings.snapshot_prefix}/{snapshot_id}/snapshot.json"


def request_key(snapshot_id: str) -> str:
    # Written at POST time so an in-progress snapshot is distinguishable from an
    # unknown id (see get_snapshot).
    return f"{settings.snapshot_prefix}/{snapshot_id}/_request.json"


def latest_snapshot_key(catalog: Catalog) -> str:
    """Copy of the most recently finalised snapshot.json; the delta baseline.

    One key per catalog. A single shared pointer would let whichever snapshot
    finished last become the baseline for both, so AOI stats would be measured
    against an ITS snapshot and report deltas that are simply wrong.

    AOI keeps the original unqualified key: the object store outlives a deploy,
    and renaming it would strand the existing baseline.
    """
    if catalog == "aoi":
        return f"{settings.snapshot_prefix}/latest_snapshot.json"
    return f"{settings.snapshot_prefix}/latest_snapshot_{catalog}.json"


class SnapshotSelectionError(Exception):
    """The selection names fields the addressed catalog does not map."""

    def __init__(self, catalog: Catalog, unknown: list[str]) -> None:
        self.catalog = catalog
        self.unknown = unknown
        super().__init__(
            f"unknown selection field(s) for catalog {catalog}: {', '.join(unknown)}"
        )

    def as_detail(self) -> dict[str, Any]:
        """Body for the 422. The catalog's full field list is deliberately left
        out: it runs to 40+ names, only the offending keys are actionable, and
        the list belongs in the API docs rather than in every error response."""
        return {
            "error": "unknown selection field(s)",
            "catalog": self.catalog,
            "unknown_fields": self.unknown,
        }


class SnapshotEmptySelectionError(Exception):
    """The selection matched 0 records in ES; refusing to create an empty snapshot."""

    def __init__(self, catalog: Catalog, selection: dict[str, Any]) -> None:
        self.catalog = catalog
        self.selection = selection
        super().__init__(
            f"snapshot selection matched 0 records in catalog {catalog}: {selection}"
        )

    def as_detail(self) -> dict[str, Any]:
        # The selection is the caller's own input, so echoing it back is short
        # and tells them exactly which filter came up empty.
        return {
            "error": "selection matched 0 records",
            "catalog": self.catalog,
            "selection": self.selection,
        }


class SnapshotFanoutError(Exception):
    """One or more slice work items could not be published; the snapshot cannot
    proceed and its PIT has been closed. The caller should retry."""


def compile_selection(selection: dict[str, Any], catalog: Catalog) -> dict[str, Any]:
    """Translate a selection into an ES bool query.

    - created_after -> range filter on the timestamp field (>=).
    - every other selection key -> term (scalar) / terms (list) filter on the
      matching metadata.content_metadata field (passthrough).
    - a fixed must_not excludes the KPI-set tags (see _kpi_exclusion).
    - ITS only: a fixed must_not excludes the documents the retention pass has
      hidden (expiredAt), whose frames are due for deletion.

    Keys are checked against the catalog's mapped fields first: the indices are
    dynamic:strict, so an unmapped key compiles to a filter that silently
    matches nothing.
    """
    allowed = SELECTION_FIELDS[catalog]
    unknown = sorted(key for key in selection if key not in allowed)
    if unknown:
        raise SnapshotSelectionError(catalog, unknown)

    filters: list[dict[str, Any]] = [
        {"range": {"metadata.content_metadata.timestamp": {"gte": selection["created_after"]}}}
    ]
    for key, value in selection.items():
        if key == "created_after":
            continue
        clause = "terms" if isinstance(value, list) else "term"
        filters.append({clause: {f"metadata.content_metadata.{key}": value}})
    must_not = _kpi_exclusion()
    if catalog == "its":
        must_not.append({"exists": {"field": EXPIRED_AT_FIELD}})
    return {"bool": {"filter": filters, "must_not": must_not}}


async def create_snapshot(
    es: ElasticsearchService,
    kafka: KafkaService,
    storage: StorageService,
    request: SnapshotRequest,
    catalog: Catalog,
) -> str:
    """Resolve the snapshot_id, open the shared PIT, and fan out N slice work items."""
    # Trust a caller-supplied snapshot_id; otherwise generate one.
    snapshot_id = request.snapshot_id or uuid.uuid4().hex
    n = settings.snapshot_partitions
    selection = request.selection.model_dump()
    query = compile_selection(selection, catalog)

    total_records, _ = await es.catalog_totals(
        query,
        index=catalog_index(catalog),
        allow_missing=catalog != "aoi",
    )
    if total_records == 0:
        raise SnapshotEmptySelectionError(catalog, selection)

    # One PIT for the whole job — every slice must read the same frozen view,
    # over the index holding the catalog this request addressed.
    pit_id = await es.open_pit(index=catalog_index(catalog))

    def build_work(i: int) -> dict[str, Any]:
        return {
            "snapshot_id": snapshot_id,
            "pit_id": pit_id,
            "query": query,
            "catalog": catalog,
            "selection": selection,
            "slice": {"id": i, "max": n},
            "sort": SORT_SPEC,
            "keep_alive": settings.pit_keep_alive,
            "reason": request.reason,
            "metadata": request.metadata,
        }

    # Fan out concurrently; each slice targets a distinct partition, so the order
    # in which they land is irrelevant. Explicit partition=i so slice i lands on
    # partition i (keying alone would hash and NOT guarantee that mapping).
    # return_exceptions keeps one failing send from cancelling the rest and lets
    # us report exactly which slices failed (results are ordered by slice index).
    results = await asyncio.gather(
        *(
            kafka.send(
                build_work(i),
                topic=settings.snapshot_slices_topic,
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
            logger.error(
                "snapshot %s: slice %d publish failed: %s", snapshot_id, i, exc
            )
        # The snapshot can never complete with missing slices — close the shared
        # PIT so it does not leak. Best-effort: a close failure must not mask the
        # original fan-out failure.
        try:
            await es.close_pit(pit_id)
        except Exception as close_exc:
            logger.error(
                "snapshot %s: PIT close failed during cleanup: %s",
                snapshot_id,
                close_exc,
            )
        raise SnapshotFanoutError(
            f"snapshot {snapshot_id}: {len(failed)}/{n} slice publishes failed"
        )

    # Only on successful fan-out: record the request so GET can report `creating`
    # (and distinguish an in-progress snapshot from an unknown id). Aborted
    # snapshots leave no marker.
    requested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await storage.put_bytes(
        request_key(snapshot_id),
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "requested_at": requested_at,
                "slice_count": n,
                "catalog": catalog,
            }
        ).encode(),
    )

    return snapshot_id


async def get_snapshot(storage: StorageService, snapshot_id: str) -> dict[str, Any] | None:
    """Resolve a snapshot's status from the object store.

    - snapshot.json present -> the finalized document (status ready/failed).
    - else _request.json present -> still creating.
    - else None -> unknown snapshot_id (the router turns this into a 404).
    """
    if await storage.exists(snapshot_key(snapshot_id)):
        return json.loads(await storage.get_bytes(snapshot_key(snapshot_id)))

    if await storage.exists(request_key(snapshot_id)):
        req = json.loads(await storage.get_bytes(request_key(snapshot_id)))
        return {
            "snapshot_id": snapshot_id,
            "status": Status.CREATING,
            "requested_at": req.get("requested_at"),
            "catalog": req.get("catalog"),
            "manifests": [],
        }

    return None


async def get_catalog_stats(
    es: ElasticsearchService, storage: StorageService, catalog: Catalog
) -> dict[str, Any]:
    """Live catalog stats relative to the last snapshot.

    Reads the most recent snapshot (latest_snapshot.json), re-runs its query
    against Elasticsearch for the current totals, and derives the "since last
    snapshot" deltas.

    Before the first snapshot there is no baseline, so the totals are measured
    over the population a snapshot would have taken (the whole catalog minus the
    KPI-set tags) and every record counts as new: the deltas equal the totals and
    the last-snapshot pointers are null.

    For the ITS catalog the deltas count the records ingested since the last
    snapshot was requested, since retention removes old frames from the live
    total. A frame that is archived again counts as new under that rule.
    """
    has_baseline = await storage.exists(latest_snapshot_key(catalog))
    latest: dict[str, Any] = (
        json.loads(await storage.get_bytes(latest_snapshot_key(catalog)))
        if has_baseline
        else {}
    )
    # With a baseline, re-run that snapshot's own query so the totals and the
    # deltas describe the same population it measured.
    query = (
        (latest.get("query") or {"match_all": {}})
        if has_baseline
        else {"bool": {"must_not": _kpi_exclusion()}}
    )
    total_records, total_bytes = await es.catalog_totals(
        query,
        index=catalog_index(catalog),
        allow_missing=catalog != "aoi",
    )
    records_since = total_records - (latest.get("total_records") or 0)
    bytes_since = total_bytes - (latest.get("total_bytes") or 0)

    # Smart City frames are removed by retention (sc_gc), so the live total
    # shrinks and "live minus baseline" would under-count what arrived since
    # the last snapshot, or go negative, and stall a volume-based trigger.
    # Count the records ingested since that snapshot was requested instead,
    # or since it was finalised when the request marker cannot be read (which
    # errs towards fewer). gte because the marker has second precision and ES
    # rounds a gt bound up to the end of that second.
    if has_baseline and catalog == "its":
        try:
            requested_at = await _snapshot_requested_at(storage, latest.get("snapshot_id"))
        except Exception:  # counted like an unreadable marker
            logger.warning(
                "snapshot %s: request marker read failed",
                latest.get("snapshot_id"),
                exc_info=True,
            )
            requested_at = None
        since = requested_at or latest.get("created_at")
        if since:
            records_since, bytes_since = await es.catalog_totals(
                {
                    "bool": {
                        "filter": [
                            query,
                            {"range": {INGESTED_AT_FIELD: {"gte": since}}},
                        ]
                    }
                },
                index=catalog_index(catalog),
                allow_missing=True,
            )

    return {
        "status": "live",
        "total_records": total_records,
        "total_bytes": total_bytes,
        "records_since_last_snapshot": records_since,
        "bytes_since_last_snapshot": bytes_since,
        "last_snapshot_id": latest.get("snapshot_id"),
        "last_snapshot_created_at": latest.get("created_at"),
    }


async def _snapshot_requested_at(storage: StorageService, snapshot_id: str | None) -> str | None:
    """When a snapshot was requested, from its _request.json marker.

    The marker is written right after the snapshot's PIT is opened, so records
    ingested later are not in that snapshot. None when the marker is missing or
    unreadable. A storage error is raised: the retention cutoff (sc_gc) must
    not fall back to created_at alone.
    """
    if not snapshot_id:
        return None
    raw = await storage.get_object(request_key(snapshot_id))
    if raw is None:
        return None
    try:
        return json.loads(raw).get("requested_at")
    except (ValueError, AttributeError):
        logger.warning("snapshot %s: unreadable request marker", snapshot_id)
        return None
