# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""In-process unified memory service — persist, recall, list, and events."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from typing import Protocol

from vss_core._foundation.errors import ConfigurationError

from .adapters import utc_now_iso
from .models import PENDING_STATUSES
from .models import TERMINAL_STATUSES
from .models import UnifiedMemoryRecord
from .store import InMemoryStore
from .store import JobFilters
from .store import MemoryQuery
from .store import MemoryStore


class BackendReconciler(Protocol):
    """Optional one-shot poll of a still-pending backend job.

    Current summarize backend (POST /v1/summarize) has no pollable reference;
    reconcilers return ``None`` unless ``backend_ref`` is genuinely pollable.
    """

    def reconcile(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord | None: ...


class MemoryNotFoundError(LookupError):
    """Raised when a requested job/asset/event handle is absent from memory."""


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
    ) -> None:
        self._store: MemoryStore = store if store is not None else InMemoryStore()
        self._reconciler = reconciler

    @property
    def store(self) -> MemoryStore:
        return self._store

    def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
        return self._store.upsert(record)

    def get(
        self,
        job_id: str,
        *,
        reconcile: bool = True,
    ) -> UnifiedMemoryRecord:
        record = self._store.get(job_id)
        if record is None:
            raise MemoryNotFoundError(f"job_id not found: {job_id}")
        if reconcile and record.job.status in PENDING_STATUSES:
            updated = self._maybe_reconcile(record)
            if updated is not None:
                return self._store.upsert(updated)
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
        """Pure memory listing — never polls a backend."""
        return self._store.list_jobs(filters or JobFilters())

    def query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]:
        return self._store.query(query)

    def events(
        self,
        *,
        asset_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        anchor_event_id: str | None = None,
        direction: str | None = None,
        window: str | None = None,
        match: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Extract event collections from persisted records for an asset.

        Reads ``output.ext.events``, ``output.ext.incidents``, and
        ``output.ext.results`` only — never pipeline Elasticsearch indices.
        """
        del window  # reserved for duration windows per design FR-5
        records = self._store.query(MemoryQuery(sensor_id=asset_id, limit=max(limit * 4, 50)))
        if not records:
            raise MemoryNotFoundError(f"no persisted memory for asset_id={asset_id!r}")
        collected: list[dict[str, Any]] = []
        for record in records:
            for key in ("events", "incidents", "results"):
                items = record.output.ext.get(key)
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    event = dict(item)
                    event.setdefault("_source_job_id", record.job.job_id)
                    event.setdefault("_source_ext_key", key)
                    collected.append(event)

        if match:
            needle = match.casefold()
            collected = [item for item in collected if needle in str(item).casefold()]

        if start_time or end_time:
            collected = [item for item in collected if _event_in_window(item, start_time, end_time)]

        if anchor_event_id:
            collected = _adjacent_events(collected, anchor_event_id, direction=direction or "around")

        return collected[: max(limit, 0)]

    def _maybe_reconcile(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord | None:
        if self._reconciler is None:
            return None
        if not record.job.backend_ref:
            # No pollable backend reference — status/get remain memory-only.
            return None
        updated = self._reconciler.reconcile(record)
        if updated is None:
            return None
        # Ensure updated_at advances on write-through reconciliation.
        if updated.job.updated_at == record.job.updated_at:
            job = updated.job.model_copy(update={"updated_at": utc_now_iso()})
            updated = updated.model_copy(update={"job": job})
        return updated


def build_memory_service(
    *,
    es_endpoint: str | None = None,
    memory_index: str | None = None,
    store: MemoryStore | None = None,
    reconciler: BackendReconciler | None = None,
) -> MemoryService:
    """Construct a memory service from explicit runtime settings (no process env)."""
    if store is not None:
        return MemoryService(store, reconciler=reconciler)
    if es_endpoint:
        from .elasticsearch import DEFAULT_MEMORY_INDEX
        from .elasticsearch import ElasticsearchMemoryStore

        es_store = ElasticsearchMemoryStore(
            endpoint=es_endpoint,
            index=memory_index or DEFAULT_MEMORY_INDEX,
        )
        return MemoryService(es_store, reconciler=reconciler)
    raise ConfigurationError("memory service requires --es-endpoint (or an injected store); process env is not read")


def _event_in_window(event: dict[str, Any], start: str | None, end: str | None) -> bool:
    stamp = event.get("timestamp") or event.get("start_time") or event.get("start") or event.get("ts")
    if stamp is None:
        return True
    value = str(stamp)
    if start is not None and value < start:
        return False
    return not (end is not None and value > end)


def _adjacent_events(
    events: list[dict[str, Any]],
    anchor_event_id: str,
    *,
    direction: str,
) -> list[dict[str, Any]]:
    index = None
    for i, event in enumerate(events):
        for key in ("event_id", "id", "uuid"):
            if str(event.get(key, "")) == anchor_event_id:
                index = i
                break
        if index is not None:
            break
    if index is None:
        return []
    if direction == "before":
        return events[:index]
    if direction == "after":
        return events[index + 1 :]
    # around
    start = max(0, index - 5)
    end = min(len(events), index + 6)
    return events[start:end]


# Silence unused-import style for Callable used by type checkers in stubs.
_PersistCallback = Callable[[UnifiedMemoryRecord], Any]

__all__ = [
    "PENDING_STATUSES",
    "TERMINAL_STATUSES",
    "BackendReconciler",
    "MemoryNotFoundError",
    "MemoryService",
    "build_memory_service",
]
