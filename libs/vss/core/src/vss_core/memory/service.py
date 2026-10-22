# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""In-process unified memory service — persist, recall, list, and events."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import UTC
from datetime import datetime
import logging
from typing import Any
from typing import Literal
from typing import Protocol
from typing import cast

from vss_core._foundation.errors import ConfigurationError

from .adapters import RecordBundle
from .backends.in_memory import InMemoryStore
from .models import PENDING_STATUSES
from .models import TERMINAL_STATUSES
from .models import UnifiedMemoryRecord
from .models import forbidden_ext_collections
from .store import JobFilters
from .store import MemoryQuery
from .store import MemoryStore
from .store import coerce_utc_instant
from .store import storage_id_for

logger = logging.getLogger(__name__)


class BackendReconciler(Protocol):
    """Optional one-shot poll of a still-pending backend job.

    Current summarize backend (POST /v1/summarize) has no pollable reference;
    reconcilers return ``None`` unless ``backend_ref`` is genuinely pollable.
    Only parent records participate in reconciliation.
    """

    def reconcile(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord | None: ...


class SemanticMemorySynchronizer(Protocol):
    """Derived-memory boundary invoked only after authoritative persistence."""

    def sync_record(self, record: UnifiedMemoryRecord) -> object: ...


class QueryEmbeddingProvider(Protocol):
    """Provider boundary needed by semantic retrieval."""

    def embed_query(self, text: str) -> list[float]: ...


class SemanticMemorySearcher(Protocol):
    """Companion-index boundary needed by semantic retrieval."""

    def semantic_search(
        self,
        query: MemoryQuery,
        query_vector: list[float],
        candidate_count: int,
    ) -> list[str]: ...


class MemoryNotFoundError(LookupError):
    """Raised when a requested job/asset/event handle is absent from memory."""


class NestedCollectionError(ValueError):
    """A record being written nests a child-owned collection in ``output.ext``.

    The rule is a write-side invariant, enforced here rather than on the model
    so it cannot reach the read path: earlier versions nested ``events`` in the
    parent, and validating that shape on load would make their documents
    unreadable rather than merely unwritable.
    """


def _reject_nested_collections(record: UnifiedMemoryRecord) -> None:
    forbidden = forbidden_ext_collections(record)
    if forbidden:
        raise NestedCollectionError(
            f"output.ext must not contain complete nested collections {forbidden}; persist those as child records"
        )


@dataclass(frozen=True)
class PersistFailure:
    """One failed write within a multi-record persistence attempt."""

    storage_id: str
    error: str
    record_type: str | None = None
    record_id: str | None = None
    is_parent: bool = False


@dataclass
class PersistResult:
    """Outcome of persisting a :class:`RecordBundle` (or equivalent)."""

    requested: int
    expected: int
    written: int
    failed: list[PersistFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.requested == self.expected == self.written and not self.failed

    @property
    def collapsed(self) -> int:
        """Number of requested records that shared another record's storage id."""
        return self.requested - self.expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "expected": self.expected,
            "written": self.written,
            "collapsed": self.collapsed,
            "failed": [
                {
                    **(
                        {"record_type": item.record_type, "record_id": item.record_id}
                        if item.record_type is not None
                        else {"parent": True}
                    ),
                    "error": item.error,
                }
                for item in self.failed
            ],
        }


#: Max child records scanned per ``events()`` call for one asset.
_EVENTS_RECORD_SCAN_CAP = 10_000

#: Parent ``output.ext`` counters keyed by child ``record_type``.
_CHILD_COUNT_EXT_KEYS: dict[str, str] = {
    "event": "event_count",
    "search_hit": "result_count",
    "incident": "incident_count",
}


def _parent_after_partial_children(
    parent: UnifiedMemoryRecord,
    written_children: list[UnifiedMemoryRecord],
) -> UnifiedMemoryRecord:
    """Downgrade a completed parent and align advertised child counts.

    When some children fail after the parent write succeeds, leaving
    ``status=completed`` with the full ``event_count`` / ``result_count`` /
    ``incident_count`` would advertise a complete result set that ``query`` /
    ``get_record`` cannot return. Mark the job ``partial`` and rewrite any
    known count keys to the number of **distinct** children that actually
    persisted (keyed by storage id, so colliding ``record_id`` values do not
    inflate the count).
    """
    counts: dict[str, int] = dict.fromkeys(_CHILD_COUNT_EXT_KEYS.values(), 0)
    seen_storage_ids: set[str] = set()
    for child in written_children:
        storage_id = storage_id_for(child)
        if storage_id in seen_storage_ids:
            continue
        seen_storage_ids.add(storage_id)
        count_key = _CHILD_COUNT_EXT_KEYS.get(child.job.record_type or "")
        if count_key is not None:
            counts[count_key] += 1

    updates: dict[str, Any] = {}
    if parent.job.status == "completed":
        updates["job"] = parent.job.model_copy(update={"status": "partial"})

    if parent.output is not None and parent.output.ext:
        ext = dict(parent.output.ext)
        changed = False
        for count_key, value in counts.items():
            if count_key in ext and ext[count_key] != value:
                ext[count_key] = value
                changed = True
        if changed:
            updates["output"] = parent.output.model_copy(update={"ext": ext})

    if not updates:
        return parent
    return parent.model_copy(update=updates)


def _deduplicate(storage_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(storage_ids))


def _record_recency(record: UnifiedMemoryRecord) -> datetime:
    return record.job.updated_at or record.job.created_at


def _matches_authoritative_filters(record: UnifiedMemoryRecord, query: MemoryQuery) -> bool:
    """Defensively reapply non-text selectors after companion-index resolution."""
    if query.job_id is not None and record.job.job_id != query.job_id:
        return False
    if (query.parents_only or not query.include_children) and record.job.is_child:
        return False
    if query.record_type is not None and record.job.record_type != query.record_type:
        return False
    if query.record_id is not None and record.job.record_id != query.record_id:
        return False
    if query.group is not None and record.job.group != query.group:
        return False
    if query.status is not None and record.job.status != query.status:
        return False
    if query.sensor_id is not None:
        sensors = (record.input.sensors if record.input is not None else None) or []
        if not any(
            sensor.id == query.sensor_id or (sensor.info is not None and sensor.info.get("name") == query.sensor_id)
            for sensor in sensors
        ):
            return False
    assert query.since is None or isinstance(query.since, datetime)
    assert query.until is None or isinstance(query.until, datetime)
    if query.time_field == "window":
        if query.since is None and query.until is None:
            return True
        if record.input is None or record.input.window is None:
            return False
        start = record.input.window.start.timestamp
        end = record.input.window.end.timestamp if record.input.window.end is not None else start
        return not (
            (query.until is not None and start > query.until) or (query.since is not None and end < query.since)
        )
    created_at = record.job.created_at
    return not (
        (query.since is not None and created_at < query.since) or (query.until is not None and created_at > query.until)
    )


class MemoryService:
    """Orchestrates memory writes and memory-first reads.

    Persistence never mutates the caller's primary stdout result — callers own
    presentation; this service only upserts records when asked.
    """

    def __init__(
        self,
        store: MemoryStore | None = None,
        *,
        reconciler: BackendReconciler | None = None,
        semantic_memory: SemanticMemorySynchronizer | None = None,
        embedding_provider: QueryEmbeddingProvider | None = None,
        retrieval_mode: Literal["keyword", "semantic", "hybrid"] = "keyword",
        semantic_candidate_count: int = 50,
        rrf_rank_constant: int = 60,
    ) -> None:
        if retrieval_mode not in {"keyword", "semantic", "hybrid"}:
            raise ValueError("retrieval_mode must be 'keyword', 'semantic', or 'hybrid'")
        if semantic_candidate_count <= 0:
            raise ValueError("semantic_candidate_count must be positive")
        if rrf_rank_constant <= 0:
            raise ValueError("rrf_rank_constant must be positive")
        self._store: MemoryStore = store if store is not None else InMemoryStore()
        self._reconciler = reconciler
        self._semantic_memory = semantic_memory
        self._embedding_provider = embedding_provider
        self._retrieval_mode = retrieval_mode
        self._semantic_candidate_count = semantic_candidate_count
        self._rrf_rank_constant = rrf_rank_constant

    @property
    def store(self) -> MemoryStore:
        return self._store

    @property
    def semantic_retrieval_available(self) -> bool:
        """Whether this service has both semantic retrieval dependencies."""
        return self._embedding_provider is not None and callable(
            getattr(self._semantic_memory, "semantic_search", None)
        )

    def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
        _reject_nested_collections(record)
        persisted = self._store.upsert(record)
        self._sync_nonfatal(persisted)
        return persisted

    def upsert_bundle(self, bundle: RecordBundle) -> PersistResult:
        """Idempotently upsert parent then children; never raises on partial failure.

        Parent is written first. If that write fails, children are **not**
        stored — otherwise ``query`` / ``events`` / ``get_record`` could return
        orphans for a job that ``get`` / ``status`` / ``list_jobs`` cannot see.

        If the parent succeeds but one or more children fail, the parent is
        re-upserted as ``partial`` with child-count ext fields aligned to the
        children that actually persisted, so job reads do not advertise a
        complete result set that record queries cannot return.

        ``requested`` counts upsert call sites. ``expected`` / ``written`` count
        **distinct** storage ids. Two children that collide on ``record_id``
        share one storage document; both calls succeed, but only one id is
        expected and written, so :attr:`PersistResult.ok` remains false.

        Callers must still return the paid-for operation result when persistence
        is incomplete. Use :attr:`PersistResult.ok` / :meth:`PersistResult.to_dict`
        for CLI exit-6 reporting.
        """
        failed: list[PersistFailure] = []
        written_ids: set[str] = set()
        records = bundle.all_records
        requested = len(records)
        # A bundle that nests its own children is the adapter's bug, not a
        # backend failure, so it raises here instead of being reported as a
        # partial write that a caller would be told to retry.
        for record in records:
            _reject_nested_collections(record)
        expected = len({storage_id_for(record) for record in records})
        # Last successful upsert per storage id — collisions overwrite in place.
        written_children_by_id: dict[str, UnifiedMemoryRecord] = {}

        try:
            persisted_parent = self._store.upsert(bundle.parent)
            written_ids.add(storage_id_for(bundle.parent))
        except Exception as error:
            failed.append(
                PersistFailure(
                    storage_id=storage_id_for(bundle.parent),
                    error=str(error),
                    record_type=None,
                    record_id=None,
                    is_parent=True,
                )
            )
            # Do not persist children without a parent document.
            for child in bundle.children:
                failed.append(
                    PersistFailure(
                        storage_id=storage_id_for(child),
                        error=f"skipped: parent upsert failed ({error})",
                        record_type=child.job.record_type,
                        record_id=child.job.record_id,
                        is_parent=False,
                    )
                )
            return PersistResult(
                requested=requested,
                expected=expected,
                written=len(written_ids),
                failed=failed,
            )

        for child in bundle.children:
            child_storage_id = storage_id_for(child)
            try:
                persisted_child = self._store.upsert(child)
                written_ids.add(child_storage_id)
                written_children_by_id[child_storage_id] = persisted_child
            except Exception as error:
                failed.append(
                    PersistFailure(
                        storage_id=child_storage_id,
                        error=str(error),
                        record_type=child.job.record_type,
                        record_id=child.job.record_id,
                        is_parent=False,
                    )
                )

        written_children = list(written_children_by_id.values())
        # Colliding children that share a storage id never appear in ``failed``,
        # but the parent may still advertise a higher event/result count than
        # distinct docs held. Correct counts whenever the persisted child set
        # is smaller than the bundle asked for.
        children_collapsed = len(written_children) < len(bundle.children)
        if failed or children_collapsed:
            corrected = _parent_after_partial_children(bundle.parent, written_children)
            if corrected != bundle.parent:
                try:
                    persisted_parent = self._store.upsert(corrected)
                except Exception as error:
                    failed.append(
                        PersistFailure(
                            storage_id=storage_id_for(bundle.parent),
                            error=f"partial parent correction failed ({error})",
                            record_type=None,
                            record_id=None,
                            is_parent=True,
                        )
                    )

        if self._semantic_memory is not None:
            # Derived synchronization starts only after every authoritative write
            # (including partial-parent correction) has finished. Read the parent
            # back so its final corrected status/counts are what gets embedded.
            try:
                final_parent = self._store.get(bundle.parent.job.job_id) or persisted_parent
            except Exception as error:
                # A post-write read is required only for derived indexing. Preserve
                # the authoritative persistence result if that read is unavailable.
                logger.warning(
                    "semantic memory parent refresh failed for %r (%s)",
                    storage_id_for(persisted_parent),
                    type(error).__name__,
                )
                final_parent = persisted_parent
            self._sync_nonfatal(final_parent)
            for child in written_children_by_id.values():
                self._sync_nonfatal(child)

        return PersistResult(
            requested=requested,
            expected=expected,
            written=len(written_ids),
            failed=failed,
        )

    def _sync_nonfatal(self, record: UnifiedMemoryRecord) -> None:
        if self._semantic_memory is None:
            return
        try:
            self._semantic_memory.sync_record(record)
        except Exception as error:
            # Embedding infrastructure is derived and must never alter the
            # authoritative result. Do not include backend messages: they may
            # contain endpoint credentials, payload text, or vector values.
            logger.warning(
                "semantic memory synchronization failed for %r (%s)",
                storage_id_for(record),
                type(error).__name__,
            )

    def get(
        self,
        job_id: str,
        *,
        reconcile: bool = True,
    ) -> UnifiedMemoryRecord:
        record = self._store.get(job_id)
        if record is None:
            raise MemoryNotFoundError(f"job_id not found: {job_id}")
        if record.job.is_child:
            raise MemoryNotFoundError(f"job_id not found: {job_id}")
        if reconcile and record.job.status in PENDING_STATUSES:
            updated = self._maybe_reconcile(record)
            if updated is not None:
                return self._store.upsert(updated)
        return record

    def get_record(
        self,
        job_id: str,
        record_type: str,
        record_id: str,
    ) -> UnifiedMemoryRecord:
        record = self._store.get_record(job_id, record_type, record_id)
        if record is None:
            raise MemoryNotFoundError(
                f"record not found: job_id={job_id!r} record_type={record_type!r} record_id={record_id!r}"
            )
        return record

    def status(
        self,
        job_id: str,
        *,
        reconcile: bool = True,
    ) -> UnifiedMemoryRecord:
        """Memory-first status. Reconcile at most once when pending + reconcilable."""
        return self.get(job_id, reconcile=reconcile)

    def list_jobs(self, filters: JobFilters | None = None) -> list[UnifiedMemoryRecord]:
        """Pure memory listing of parent jobs — never polls a backend."""
        return self._store.list_jobs(filters or JobFilters())

    def query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]:
        if not query.text:
            return self._store.query(query)

        mode = query.mode or self._retrieval_mode
        if mode == "keyword" or not self.semantic_retrieval_available:
            return self._store.query(query)
        if mode == "semantic":
            try:
                storage_ids = self._semantic_ids(query)
            except Exception as error:
                self._warn_semantic_fallback(error)
                return self._store.query(query)
            return self._resolve_semantic(query, storage_ids)
        return self._hybrid_query(query)

    def _semantic_ids(self, query: MemoryQuery) -> list[str]:
        assert query.text is not None
        assert self._embedding_provider is not None
        semantic_search = cast("SemanticMemorySearcher", self._semantic_memory).semantic_search
        vector = self._embedding_provider.embed_query(query.text)
        return semantic_search(query, vector, self._semantic_candidate_count)

    def _resolve_semantic(self, query: MemoryQuery, storage_ids: list[str]) -> list[UnifiedMemoryRecord]:
        records = self._store.get_many(_deduplicate(storage_ids))
        return [record for record in records if _matches_authoritative_filters(record, query)][: max(query.limit, 0)]

    def _hybrid_query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]:
        candidate_limit = max(self._semantic_candidate_count, query.limit, 0)
        keyword_records = self._store.query(replace(query, mode="keyword", limit=candidate_limit))
        try:
            semantic_ids = self._semantic_ids(query)
        except Exception as error:
            self._warn_semantic_fallback(error)
            return keyword_records[: max(query.limit, 0)]

        keyword_ids = [storage_id_for(record) for record in keyword_records]
        ordered_ids = _deduplicate([*keyword_ids, *semantic_ids])
        authoritative = {
            storage_id_for(record): record
            for record in self._store.get_many(ordered_ids)
            if _matches_authoritative_filters(record, query)
        }
        scores: dict[str, float] = {}
        best_ranks: dict[str, int] = {}
        for ranking in (keyword_ids, _deduplicate(semantic_ids)):
            for rank, storage_id in enumerate(ranking, start=1):
                if storage_id not in authoritative:
                    continue
                scores[storage_id] = scores.get(storage_id, 0.0) + 1.0 / (self._rrf_rank_constant + rank)
                best_ranks[storage_id] = min(best_ranks.get(storage_id, rank), rank)

        ranked_ids = sorted(
            scores,
            key=lambda storage_id: (
                -scores[storage_id],
                best_ranks[storage_id],
                -_record_recency(authoritative[storage_id]).timestamp(),
                storage_id,
            ),
        )
        return [authoritative[storage_id] for storage_id in ranked_ids[: max(query.limit, 0)]]

    @staticmethod
    def _warn_semantic_fallback(error: Exception) -> None:
        logger.warning(
            "semantic memory retrieval failed (%s); falling back to keyword retrieval",
            type(error).__name__,
        )

    def events(
        self,
        *,
        asset_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        anchor_event_id: str | None = None,
        direction: str | None = None,
        match: str | None = None,
        limit: int = 50,
        record_types: tuple[str, ...] = ("event", "incident", "search_hit"),
    ) -> list[dict[str, Any]]:
        """Recall first-class child result records for an asset.

        Does **not** scan nested ``output.ext.events|incidents|results``.
        Temporal filtering uses child ``input.window`` (event time).
        """
        collected: list[dict[str, Any]] = []
        for record_type in record_types:
            query = MemoryQuery(
                sensor_id=asset_id,
                record_type=record_type,  # type: ignore[arg-type]
                since=start_time,
                until=end_time,
                time_field="window",
                include_children=True,
                parents_only=False,
                limit=_EVENTS_RECORD_SCAN_CAP,
            )
            for record in self._store.query(query):
                if not record.job.is_child:
                    continue
                collected.append(_child_as_event_dict(record))

        if not collected:
            # Distinguish "no asset memory at all" from "no matching children".
            any_for_asset = self._store.query(MemoryQuery(sensor_id=asset_id, include_children=True, limit=1))
            if not any_for_asset:
                raise MemoryNotFoundError(f"no persisted memory for asset_id={asset_id!r}")
            # Asset exists but no children matched filters — empty list.
            if limit <= 0:
                return []
            if anchor_event_id:
                raise MemoryNotFoundError(f"anchor_event_id not found: {anchor_event_id!r}")
            return []

        if match:
            needle = match.casefold()
            collected = [item for item in collected if needle in str(item).casefold()]

        collected = _sort_events_by_time(collected)
        if limit <= 0:
            return []
        if anchor_event_id:
            return _adjacent_events(
                collected,
                anchor_event_id,
                direction=direction or "around",
                limit=limit,
            )
        newest = collected[-limit:]
        return list(reversed(newest))

    def _maybe_reconcile(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord | None:
        if record.job.is_child:
            return None
        if self._reconciler is None:
            return None
        if not record.job.backend_ref:
            return None
        updated = self._reconciler.reconcile(record)
        if updated is None:
            return None
        if updated.job.is_child:
            raise ValueError("reconciler must return a parent record")
        if updated.job.updated_at == record.job.updated_at:
            job = updated.job.model_copy(update={"updated_at": datetime.now(UTC)})
            updated = updated.model_copy(update={"job": job})
        return updated


def build_memory_service(
    *,
    es_endpoint: str | None = None,
    memory_index: str | None = None,
    store: MemoryStore | None = None,
    reconciler: BackendReconciler | None = None,
    semantic_memory: SemanticMemorySynchronizer | None = None,
) -> MemoryService:
    """Construct a memory service from explicit runtime settings (no process env)."""
    if store is not None:
        return MemoryService(store, reconciler=reconciler, semantic_memory=semantic_memory)
    if es_endpoint:
        from .backends.elasticsearch import DEFAULT_MEMORY_INDEX
        from .backends.elasticsearch import ElasticsearchMemoryStore

        es_store = ElasticsearchMemoryStore(
            endpoint=es_endpoint,
            index=memory_index or DEFAULT_MEMORY_INDEX,
        )
        return MemoryService(es_store, reconciler=reconciler, semantic_memory=semantic_memory)
    raise ConfigurationError("memory service requires --es-endpoint (or an injected store); process env is not read")


def _child_as_event_dict(record: UnifiedMemoryRecord) -> dict[str, Any]:
    """Project a child memory record into the legacy events() dict shape."""
    event: dict[str, Any] = {
        "event_id": record.job.record_id,
        "record_id": record.job.record_id,
        "record_type": record.job.record_type,
        "_source_job_id": record.job.job_id,
    }
    if record.output is not None and record.output.answer:
        event["description"] = record.output.answer
        event["answer"] = record.output.answer
    if record.input is not None and record.input.window is not None:
        event["timestamp"] = datetime_to_iso_safe(record.input.window.start.timestamp)
        if record.input.window.end is not None:
            event["end_time"] = datetime_to_iso_safe(record.input.window.end.timestamp)
    if record.input is not None and record.input.sensors:
        event["sensor_id"] = record.input.sensors[0].id
    if record.output is not None and record.output.ext:
        for key, value in record.output.ext.items():
            event.setdefault(key, value)
    if record.output is not None and record.output.handles and record.output.handles.media_urls:
        event.setdefault("media_urls", list(record.output.handles.media_urls))
    return event


def datetime_to_iso_safe(value: datetime) -> str:
    from vss_core._foundation.time import datetime_to_iso8601

    return datetime_to_iso8601(value)


def _event_stamp_value(event: dict[str, Any]) -> datetime | None:
    stamp = event.get("timestamp") or event.get("start_time") or event.get("start") or event.get("ts")
    if stamp is None:
        return None
    try:
        return coerce_utc_instant(str(stamp))
    except ValueError:
        return None


def _sort_events_by_time(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Oldest→newest by event timestamp; untimed/unparseable stamps sort first."""

    def sort_key(event: dict[str, Any]) -> tuple[int, datetime]:
        value = _event_stamp_value(event)
        if value is None:
            return (0, datetime.min.replace(tzinfo=UTC))
        return (1, value)

    return sorted(events, key=sort_key)


def _adjacent_events(
    events: list[dict[str, Any]],
    anchor_event_id: str,
    *,
    direction: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Return temporal neighbours. ``events`` must already be oldest→newest."""
    index = None
    for i, event in enumerate(events):
        for key in ("event_id", "record_id", "id", "uuid"):
            if str(event.get(key, "")) == anchor_event_id:
                index = i
                break
        if index is not None:
            break
    if index is None:
        raise MemoryNotFoundError(f"anchor_event_id not found: {anchor_event_id!r}")

    if direction == "before":
        neighbors = events[:index]
        if limit <= 0:
            return []
        return neighbors[-limit:]
    if direction == "after":
        neighbors = events[index + 1 :]
        if limit <= 0:
            return []
        return neighbors[:limit]

    start = max(0, index - 5)
    end = min(len(events), index + 6)
    windowed = events[start:end]
    if limit <= 0:
        return []
    if len(windowed) <= limit:
        return windowed
    anchor_in_window = index - start
    half = limit // 2
    left = max(0, anchor_in_window - half)
    right = left + limit
    if right > len(windowed):
        right = len(windowed)
        left = max(0, right - limit)
    return windowed[left:right]


__all__ = [
    "PENDING_STATUSES",
    "TERMINAL_STATUSES",
    "BackendReconciler",
    "MemoryNotFoundError",
    "MemoryService",
    "PersistFailure",
    "PersistResult",
    "SemanticMemorySynchronizer",
    "build_memory_service",
]
