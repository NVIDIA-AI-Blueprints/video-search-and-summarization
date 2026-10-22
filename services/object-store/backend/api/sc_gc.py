"""Retention for archived Smart City frames.

Every frame sc_service archives stays in the object store and in the sc_*
catalog for good, so a long-running deployment fills its disk. A pass bounds
that growth in two phases.

Hide. Catalog documents ingested before the cutoff get expiredAt = now. ITS
snapshots exclude documents that carry expiredAt (compile_selection), so from
then on no new snapshot lists those frames. The cutoff is the retention age,
and by default no later than the latest ITS snapshot request, so a frame is not
hidden before a snapshot was requested after it arrived.

Delete. Documents hidden for longer than SC_GC_HOLD_S are deleted, then their
objects, unless another document still points to the object, a KPI manifest
lists it, or it was written again after its frame was first archived (a frame
archived anew whose fresh document is still on its way through Logstash).

A snapshot taken before a frame was hidden may list it, and fine-tuning reads
snapshot frames straight from the object store. Because an object is deleted
only after its document has been hidden for the hold, every snapshot's frames
stay readable for at least the hold after the snapshot was taken, however
passes and snapshots interleave. A hidden document whose frame was archived
again (a newer ingestedAt) is made visible again instead of being deleted.

After a pass the SeaweedFS master is asked to vacuum. SeaweedFS only marks
deleted objects as garbage; the space comes back to the disk when a volume is
compacted. Read-only volumes (a nearly full disk) are not compacted, and the
report says so.

ITS snapshot directories (snapshots/<id>/: manifests listing the whole pool)
are deleted once every object in them is older than the hold, except the
latest ITS snapshot, whose request time stats and this pass read. A snapshot
therefore stays readable, manifests and frames, for at least the hold.

An optional sweep deletes objects under the ITS prefix that no document points
to, which failed deletes and documents removed by other means leave behind.
Only keys shaped like the ones sc_service writes are ever deleted as frames,
so anything else stored under the prefix (an archive, a checkpoint) is left.

Documents go before objects because the reverse order leaves a document whose
object is missing, which a snapshot would hand to training.

A pass that is not a dry run also syncs the bucket lifecycle rule that backs
it up (sc_lifecycle), so a rule refused at startup because the object store
was not up yet is written by the next pass.

Run with ``python -m backend.api.sc_gc [--dry-run] [--orphan-sweep]`` for a
single pass; it exits 1 when the pass reports an error.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import httpx

from backend.api.snapshot_service import (
    EXPIRED_AT_FIELD,
    INGESTED_AT_FIELD,
    _kpi_exclusion,
    _snapshot_requested_at,
    latest_snapshot_key,
)
from backend.api.sc_lifecycle import sync_lifecycle_rule
from backend.config import settings
from backend.shared.elasticsearch import OBJ_STORE_PATH_FIELD, ElasticsearchService
from backend.shared.storage import StorageService

logger = logging.getLogger(__name__)

# Logstash writes every Smart City document to sc_<sensor>. Retention works on
# the whole family, whatever SC_ES_INDEX narrows snapshots to, so a reference
# from any sensor's index keeps an object alive.
SC_INDEX_FAMILY = "sc_*"

# A frame is indexed shortly after its ingestedAt, but a snapshot's PIT only
# holds what was indexed when it opened. Keep a margin before the snapshot.
INDEXING_MARGIN = timedelta(hours=1)

# An object is written just before its metadata is published, so its
# LastModified sits at its document's ingestedAt. One modified well after that
# was written again (the same frame archived anew).
REWRITE_MARGIN = timedelta(hours=1)

# Minimum number of grace-aged keys on a listing page before the orphan ratio
# guard applies, so a handful of real orphans on a small page does not trip it.
ORPHAN_RATIO_MIN_SAMPLE = 20

_MANIFEST = re.compile(r"/manifest[^/]*\.jsonl$")
_CM = "metadata.content_metadata"
_SOURCE = [OBJ_STORE_PATH_FIELD, f"{_CM}.imageSize", INGESTED_AT_FIELD, EXPIRED_AT_FIELD]

# KPI manifests are written once and never change, so each is parsed once per
# process and kept by key.
_kpi_manifest_paths: dict[str, frozenset[str]] = {}


@dataclass
class GcReport:
    dry_run: bool
    cutoff: str | None = None
    hide_skipped: str | None = None
    hidden: int = 0
    unhidden: int = 0
    documents_deleted: int = 0
    documents_kept: int = 0
    objects_deleted: int = 0
    bytes_deleted: int = 0
    objects_still_referenced: int = 0
    objects_protected: int = 0
    objects_rewritten: int = 0
    orphans_deleted: int = 0
    snapshots_deleted: int = 0
    snapshot_objects_deleted: int = 0
    object_delete_errors: int = 0
    vacuum: str | None = None
    lifecycle: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(
            self.errors
            or self.object_delete_errors
            or (self.vacuum or "").startswith("failed")
        )


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _cm(hit: dict[str, Any]) -> dict[str, Any]:
    return hit.get("_source", {}).get("metadata", {}).get("content_metadata", {})


def _frame_prefix() -> str:
    return f"{settings.its_object_prefix}/"


def _frame_key_pattern() -> re.Pattern[str]:
    """The keys sc_service.object_key writes: <prefix>/<16 hex>/<frameId>.<ext>."""
    return re.compile(
        rf"^{re.escape(_frame_prefix())}[0-9a-f]{{16}}/[A-Za-z0-9._-]+\.(?:jpg|png|bmp|bin)$"
    )


def _protected_prefixes() -> list[str]:
    """Prefixes the frame prefix must not overlap: AOI images and everything
    the backend and the fine-tuning side write next to the frames."""
    names = [
        "deft-aoi",
        settings.snapshot_prefix,
        settings.kpi_prefix,
        settings.eval_prefix,
        settings.upload_folder,
        "models",
    ]
    return [f"{n.strip('/')}/" for n in names if n.strip("/")]


def _hide_query(cutoff: datetime) -> dict[str, Any]:
    """Visible documents old enough to hide. KPI-set tags are never selected,
    and a document without ingestedAt never matches the range."""
    return {
        "bool": {
            "filter": [{"range": {INGESTED_AT_FIELD: {"lt": _iso(cutoff)}}}],
            "must_not": [{"exists": {"field": EXPIRED_AT_FIELD}}, *_kpi_exclusion()],
        }
    }


def _delete_query(hidden_before: datetime) -> dict[str, Any]:
    return {
        "bool": {
            "filter": [{"range": {EXPIRED_AT_FIELD: {"lt": _iso(hidden_before)}}}],
            "must_not": _kpi_exclusion(),
        }
    }


async def _cutoff(storage: StorageService, now: datetime) -> tuple[datetime | None, str | None]:
    """The ingestedAt before which frames are hidden, or (None, why not)."""
    cutoff = now - timedelta(days=settings.sc_gc_retention_days)
    if not settings.sc_gc_require_snapshot:
        return cutoff, None
    raw = await storage.get_object(latest_snapshot_key("its"))
    if raw is None:
        return None, "no ITS snapshot has been taken yet"
    latest = json.loads(raw)
    # The earlier of the two: the request marker can be rewritten by a repeated
    # POST with the same snapshot_id, created_at cannot.
    anchors = [
        _parse(ts)
        for ts in (
            await _snapshot_requested_at(storage, latest.get("snapshot_id")),
            latest.get("created_at"),
        )
        if ts
    ]
    if not anchors:
        return None, f"latest ITS snapshot {latest.get('snapshot_id')} has no timestamp"
    return min(cutoff, min(anchors) - INDEXING_MARGIN), None


async def _kpi_protected_paths(storage: StorageService) -> set[str]:
    """Frame keys listed in any KPI manifest.

    KPI frames are also excluded by tag, but two frames with the same sensorId
    and frameId share a catalog document, so the tag of the later one wins and
    the KPI copy's object has no document. The manifests are the list that
    fine-tuning actually reads.
    """
    prefix = f"{settings.kpi_prefix.strip('/')}/"
    frame_prefix = _frame_prefix()
    protected: set[str] = set()
    token: str | None = None
    while True:
        objects, token = await storage.list_page(prefix, token)
        for obj in objects:
            key = obj["key"]
            if not _MANIFEST.search(key):
                continue
            if key not in _kpi_manifest_paths:
                raw = await storage.get_object(key) or b""
                paths = set()
                for line in raw.splitlines():
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    path = record.get("file_path") if isinstance(record, dict) else None
                    if isinstance(path, str) and path.startswith(frame_prefix):
                        paths.add(path)
                _kpi_manifest_paths[key] = frozenset(paths)
            protected |= _kpi_manifest_paths[key]
        if token is None:
            return protected


async def _scan(
    es: ElasticsearchService, query: dict[str, Any], budget: int
) -> AsyncIterator[list[dict[str, Any]]]:
    """Pages of a PIT scan over the Smart City family, at most budget hits."""
    pit_id = await es.open_pit(index=SC_INDEX_FAMILY)
    try:
        search_after: list[Any] | None = None
        while budget > 0:
            hits = await es.search_pit_page(
                pit_id,
                query,
                size=min(settings.sc_gc_batch_size, budget),
                search_after=search_after,
                source_includes=_SOURCE,
            )
            if not hits:
                return
            search_after = hits[-1]["sort"]
            budget -= len(hits)
            yield hits
    finally:
        try:
            await es.close_pit(pit_id)
        except Exception as exc:  # noqa: BLE001 — the PIT expires on its own
            logger.warning("GC PIT close failed: %s", exc)


async def _hide(
    es: ElasticsearchService, cutoff: datetime, now: datetime, report: GcReport
) -> None:
    query = _hide_query(cutoff)
    total, _ = await es.catalog_totals(query, index=SC_INDEX_FAMILY, allow_missing=True)
    if total == 0:
        return
    doc = {"metadata": {"content_metadata": {"expiredAt": _iso(now)}}}
    async with contextlib.aclosing(_scan(es, query, settings.sc_gc_max_deletes_per_pass)) as pages:
        async for hits in pages:
            if report.dry_run:
                report.hidden += len(hits)
                continue
            report.hidden += len(hits) - len(await es.update_hits(hits, doc))


async def _delete_objects(
    storage: StorageService, keys: list[str], report: GcReport
) -> list[str]:
    """Delete keys in chunks of 1000; return the ones actually deleted."""
    deleted: list[str] = []
    for i in range(0, len(keys), 1000):
        chunk = keys[i : i + 1000]
        try:
            failed = set(await storage.delete_many(chunk))
        except Exception as exc:  # noqa: BLE001 — leave orphans for a later sweep
            report.errors.append(f"object delete failed: {exc}")
            failed = set(chunk)
        report.object_delete_errors += len(failed)
        deleted.extend(k for k in chunk if k not in failed)
    return deleted


async def _delete_batch(
    es: ElasticsearchService,
    storage: StorageService,
    hits: list[dict[str, Any]],
    protected: set[str],
    report: GcReport,
) -> None:
    # A frame archived again after it was hidden has a newer ingestedAt: keep
    # it, and make it visible to snapshots again.
    rearchived = [
        h for h in hits
        if _cm(h).get("ingestedAt") and _parse(_cm(h)["ingestedAt"]) > _parse(_cm(h)["expiredAt"])
    ]
    rearchived_ids = {h["_id"] for h in rearchived}
    doomed_docs = [h for h in hits if h["_id"] not in rearchived_ids]

    # Object key -> (bytes, newest ingestedAt among the documents to delete).
    # A document pointing anywhere but a frame key loses only itself.
    frame_key = _frame_key_pattern()
    objects: dict[str, tuple[int, datetime]] = {}
    for hit in doomed_docs:
        cm = _cm(hit)
        path = cm.get("objStorePath")
        if not isinstance(path, str) or not frame_key.match(path):
            continue
        ingested = _parse(cm["ingestedAt"])
        prior = objects.get(path)
        objects[path] = (cm.get("imageSize") or 0, max(ingested, prior[1]) if prior else ingested)

    # Ask which objects other documents still use before changing anything, so
    # an incomplete answer (a closed or failed sc_ index) stops the batch while
    # every document is still in place. A dry run asks too, and runs every
    # other read below, so its counts match what the pass would delete.
    still_referenced = await es.referenced_paths(
        SC_INDEX_FAMILY, list(objects), exclude_ids=[h["_id"] for h in doomed_docs]
    )

    if report.dry_run:
        report.unhidden += len(rearchived)
        report.documents_deleted += len(doomed_docs)
    else:
        if rearchived:
            unhide = {"metadata": {"content_metadata": {"expiredAt": None}}}
            report.unhidden += len(rearchived) - len(await es.update_hits(rearchived, unhide))
        kept_ids = await es.delete_hits(doomed_docs)
        report.documents_deleted += len(doomed_docs) - len(kept_ids)
        report.documents_kept += len(kept_ids)
        for hit in doomed_docs:
            if hit["_id"] in kept_ids:
                objects.pop(_cm(hit).get("objStorePath"), None)

    report.objects_still_referenced += len(still_referenced & objects.keys())
    doomed: list[str] = []
    for key, (_, ingested) in objects.items():
        if key in still_referenced:
            continue
        if key in protected:
            report.objects_protected += 1
            continue
        modified = await storage.last_modified(key)
        if modified is None:
            continue
        if modified > ingested + REWRITE_MARGIN:
            # Written again after it was archived; its new document may not be
            # indexed yet. Left for the orphan sweep to judge later.
            report.objects_rewritten += 1
            continue
        doomed.append(key)
    if report.dry_run:
        report.objects_deleted += len(doomed)
        report.bytes_deleted += sum(objects[key][0] for key in doomed)
        return
    for key in await _delete_objects(storage, doomed, report):
        report.objects_deleted += 1
        report.bytes_deleted += objects[key][0]


async def _delete_hidden(
    es: ElasticsearchService,
    storage: StorageService,
    now: datetime,
    protected: set[str],
    report: GcReport,
) -> None:
    query = _delete_query(now - timedelta(seconds=settings.sc_gc_hold_s))
    total, _ = await es.catalog_totals(query, index=SC_INDEX_FAMILY, allow_missing=True)
    if total == 0:
        return
    async with contextlib.aclosing(_scan(es, query, settings.sc_gc_max_deletes_per_pass)) as pages:
        async for hits in pages:
            try:
                await _delete_batch(es, storage, hits, protected, report)
            except LookupError as exc:
                report.errors.append(f"delete phase stopped: {exc}")
                return


async def _sweep_orphans(
    es: ElasticsearchService,
    storage: StorageService,
    now: datetime,
    protected: set[str],
    report: GcReport,
) -> None:
    """Delete frame objects that no catalog document points to.

    Only objects older than the orphan grace period are considered: a frame is
    written to the object store before its metadata is indexed. Every answer
    that decides a deletion must be complete, and the sweep stops at the first
    sign that the catalog does not describe these objects.
    """
    frame_prefix = _frame_prefix()
    for other in _protected_prefixes():
        if frame_prefix.startswith(other) or other.startswith(frame_prefix):
            report.errors.append(
                f"orphan sweep skipped: frame prefix {frame_prefix} overlaps {other}"
            )
            return
    anchored, _ = await es.catalog_totals(
        {"prefix": {OBJ_STORE_PATH_FIELD: frame_prefix}},
        index=SC_INDEX_FAMILY,
        allow_missing=True,
    )
    if anchored == 0:
        report.errors.append(
            f"orphan sweep skipped: no document in {SC_INDEX_FAMILY} points under {frame_prefix}"
        )
        return

    older_than = now - timedelta(seconds=settings.sc_gc_orphan_grace_s)
    frame_key = _frame_key_pattern()
    budget = settings.sc_gc_max_deletes_per_pass
    token: str | None = None
    while budget > 0:
        objects, token = await storage.list_page(frame_prefix, token)
        aged = [
            o["key"]
            for o in objects
            if o["last_modified"] < older_than and frame_key.match(o["key"])
        ]
        if aged:
            try:
                referenced = await es.referenced_paths(SC_INDEX_FAMILY, aged)
            except LookupError as exc:
                report.errors.append(f"orphan sweep stopped: {exc}")
                return
            unreferenced = [k for k in aged if k not in referenced]
            if (
                len(aged) >= ORPHAN_RATIO_MIN_SAMPLE
                and len(unreferenced) > settings.sc_gc_orphan_max_ratio * len(aged)
            ):
                report.errors.append(
                    f"orphan sweep stopped: {len(unreferenced)} of {len(aged)} listed "
                    "objects are unreferenced, above SC_GC_ORPHAN_MAX_RATIO"
                )
                return
            orphans = sorted(k for k in unreferenced if k not in protected)[:budget]
            budget -= len(orphans)
            if report.dry_run:
                report.orphans_deleted += len(orphans)
            else:
                report.orphans_deleted += len(await _delete_objects(storage, orphans, report))
        if token is None:
            return


async def _prune_snapshots(storage: StorageService, now: datetime, report: GcReport) -> None:
    """Delete ITS snapshot directories older than the hold.

    A directory goes only when every object in it is older than the hold (so
    one still being written is left), its snapshot.json or _request.json
    says catalog "its" (AOI snapshots and ones that say nothing are left),
    and it is not the latest ITS snapshot.
    """
    prefix = f"{settings.snapshot_prefix.strip('/')}/"
    latest_raw = await storage.get_object(latest_snapshot_key("its"))
    try:
        keep = {json.loads(latest_raw).get("snapshot_id")} if latest_raw else set()
    except ValueError:
        report.errors.append("snapshot pruning skipped: unreadable latest ITS snapshot")
        return
    older_than = now - timedelta(seconds=settings.sc_gc_hold_s)

    dirs: dict[str, list[dict[str, Any]]] = {}
    token: str | None = None
    while True:
        objects, token = await storage.list_page(prefix, token)
        for obj in objects:
            snapshot_id, sep, _ = obj["key"][len(prefix):].partition("/")
            if sep:  # the latest pointers sit directly under the prefix
                dirs.setdefault(snapshot_id, []).append(obj)
        if token is None:
            break

    for snapshot_id, objects in sorted(dirs.items()):
        if snapshot_id in keep or max(o["last_modified"] for o in objects) >= older_than:
            continue
        catalog = None
        for name in ("snapshot.json", "_request.json"):
            key = f"{prefix}{snapshot_id}/{name}"
            if not any(o["key"] == key for o in objects):
                continue
            try:
                catalog = json.loads(await storage.get_object(key) or b"{}").get("catalog")
            except (ValueError, AttributeError):
                catalog = None
            if catalog:
                break
        if catalog != "its":
            continue
        report.snapshots_deleted += 1
        if report.dry_run:
            report.snapshot_objects_deleted += len(objects)
            continue
        keys = [o["key"] for o in objects]
        report.snapshot_objects_deleted += len(await _delete_objects(storage, keys, report))


def _read_only_garbage(status: Any, threshold: float) -> list[int]:
    """Ids of read-only volumes whose garbage ratio is at least threshold."""
    found: set[int] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "ReadOnly" in node and "Id" in node:
                size = node.get("Size") or 0
                garbage = node.get("DeletedByteCount") or 0
                if node["ReadOnly"] and size and garbage / size >= threshold:
                    found.add(node["Id"])
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(status)
    return sorted(found)


async def _vacuum() -> str:
    """Compact SeaweedFS volumes holding at least the configured garbage ratio.

    The master's /vol/vacuum runs synchronously over every volume, so this can
    take minutes on a large store; the request is cancelled with the pass. The
    master skips read-only volumes, which a volume server marks once its disk
    is nearly full, so those are reported from /vol/status.
    """
    if not settings.seaweedfs_master_url:
        return "skipped: SEAWEEDFS_MASTER_URL is not set"
    base = settings.seaweedfs_master_url.rstrip("/")
    threshold = settings.sc_gc_vacuum_garbage_threshold
    try:
        async with httpx.AsyncClient(timeout=settings.sc_gc_vacuum_timeout_s) as client:
            resp = await client.get(f"{base}/vol/vacuum", params={"garbageThreshold": threshold})
            resp.raise_for_status()
            status = await client.get(f"{base}/vol/status")
            status.raise_for_status()
            stuck = _read_only_garbage(status.json(), threshold)
    except (httpx.HTTPError, ValueError) as exc:
        return f"failed: {exc!r}"
    if stuck:
        return (
            f"failed: read-only volumes {stuck} hold garbage and were not compacted; "
            "free disk space, then run weed shell volume.vacuum"
        )
    return f"ok (HTTP {resp.status_code})"


async def run_gc_pass(
    es: ElasticsearchService,
    storage: StorageService,
    *,
    now: datetime | None = None,
    dry_run: bool | None = None,
    orphan_sweep: bool | None = None,
) -> GcReport:
    """Run one retention pass over the ITS catalog and its frame objects."""
    now = now or datetime.now(timezone.utc)
    report = GcReport(dry_run=settings.sc_gc_dry_run if dry_run is None else dry_run)
    orphan_sweep = settings.sc_gc_orphan_sweep if orphan_sweep is None else orphan_sweep

    if not report.dry_run:
        await es.ensure_date_field(SC_INDEX_FAMILY, "expiredAt")

    protected = await _kpi_protected_paths(storage)
    # Delete before hiding, so a pass never deletes what it has just hidden,
    # whatever the hold.
    await _delete_hidden(es, storage, now, protected, report)

    cutoff, why_not = await _cutoff(storage, now)
    if cutoff is None:
        report.hide_skipped = why_not
    else:
        report.cutoff = _iso(cutoff)
        await _hide(es, cutoff, now, report)

    if settings.sc_gc_prune_snapshots:
        await _prune_snapshots(storage, now, report)

    if orphan_sweep:
        await _sweep_orphans(es, storage, now, protected, report)

    if not report.dry_run:
        report.vacuum = await _vacuum()
        try:
            report.lifecycle = await sync_lifecycle_rule(storage)
        except Exception as exc:  # noqa: BLE001 — the pass itself is done
            report.lifecycle = f"failed: {exc!r}"
        if report.lifecycle.startswith(("refused", "failed")):
            report.errors.append(f"lifecycle rule {report.lifecycle}")
    return report


async def gc_loop(es: ElasticsearchService, storage: StorageService) -> None:
    """Run a pass every sc_gc_interval_s, the first one shortly after startup.

    A failed pass is logged and retried on the next interval.
    """
    delay = min(settings.sc_gc_interval_s, 300)
    while True:
        await asyncio.sleep(delay)
        delay = settings.sc_gc_interval_s
        try:
            report = await run_gc_pass(es, storage)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ITS frame GC pass failed")
            continue
        # WARNING, not INFO: the backend does not configure logging, so INFO
        # from this module would never be seen, and this is once per interval.
        logger.warning("ITS frame GC: %s", json.dumps(asdict(report)))


async def _main(argv: list[str] | None = None) -> GcReport:
    parser = argparse.ArgumentParser(description="Run one ITS frame retention pass.")
    parser.add_argument("--dry-run", action="store_true", help="report without changing anything")
    parser.add_argument(
        "--orphan-sweep", action="store_true", help="also delete unreferenced objects"
    )
    args = parser.parse_args(argv)
    es = ElasticsearchService()
    try:
        return await run_gc_pass(
            es,
            StorageService(),
            dry_run=True if args.dry_run else None,
            orphan_sweep=True if args.orphan_sweep else None,
        )
    finally:
        await es.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(_main())
    print(json.dumps(asdict(result), indent=2))
    sys.exit(1 if result.failed else 0)
