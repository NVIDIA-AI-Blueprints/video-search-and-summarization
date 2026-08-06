# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Implementation-neutral store interface and in-memory backend for tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from typing import runtime_checkable

from .models import JobStatus
from .models import MemoryGroup
from .models import UnifiedMemoryRecord


@dataclass(slots=True)
class MemoryQuery:
    """Free-form / filtered query over persisted unified-memory records."""

    text: str | None = None
    group: MemoryGroup | None = None
    status: JobStatus | None = None
    sensor_id: str | None = None
    job_id: str | None = None
    since: str | None = None
    until: str | None = None
    limit: int = 20


@dataclass(slots=True)
class JobFilters:
    """Filters for ``list_jobs`` (group-scoped job listing)."""

    group: MemoryGroup | None = None
    status: JobStatus | None = None
    sensor_id: str | None = None
    since: str | None = None
    until: str | None = None
    limit: int = 50


@runtime_checkable
class MemoryStore(Protocol):
    """Durable store for unified memory records.

    Implementations must upsert by ``job.job_id`` (document ``_id``) so
    lifecycle transitions update one document rather than creating duplicates.
    """

    def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord: ...

    def get(self, job_id: str) -> UnifiedMemoryRecord | None: ...

    def query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]: ...

    def list_jobs(self, filters: JobFilters) -> list[UnifiedMemoryRecord]: ...


def _sensor_match(record: UnifiedMemoryRecord, sensor_id: str | None) -> bool:
    if not sensor_id:
        return True
    return any(sensor.id == sensor_id for sensor in record.input.sensors)


def _time_in_range(value: str, since: str | None, until: str | None) -> bool:
    if since is not None and value < since:
        return False
    return not (until is not None and value > until)


def _matches_query(record: UnifiedMemoryRecord, query: MemoryQuery) -> bool:
    if query.job_id is not None and record.job.job_id != query.job_id:
        return False
    if query.group is not None and record.job.group != query.group:
        return False
    if query.status is not None and record.job.status != query.status:
        return False
    if not _sensor_match(record, query.sensor_id):
        return False
    if not _time_in_range(record.job.created_at, query.since, query.until):
        return False
    if query.text:
        haystacks: list[str] = []
        if record.input.query:
            haystacks.append(record.input.query)
        if record.output.answer:
            haystacks.append(record.output.answer)
        ext = record.output.ext
        for key in ("events", "results", "incidents"):
            value = ext.get(key)
            if value is not None:
                haystacks.append(str(value))
        needle = query.text.casefold()
        if not any(needle in item.casefold() for item in haystacks):
            return False
    return True


def _matches_filters(record: UnifiedMemoryRecord, filters: JobFilters) -> bool:
    if filters.group is not None and record.job.group != filters.group:
        return False
    if filters.status is not None and record.job.status != filters.status:
        return False
    if not _sensor_match(record, filters.sensor_id):
        return False
    return _time_in_range(record.job.created_at, filters.since, filters.until)


class InMemoryStore:
    """Process-local store used by hermetic tests."""

    def __init__(self) -> None:
        self._records: dict[str, UnifiedMemoryRecord] = {}
        self.upsert_ids: list[str] = []

    def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
        existing = self._records.get(record.job.job_id)
        if existing is not None:
            # Preserve immutable created_at across lifecycle writes.
            job = record.job.model_copy(update={"created_at": existing.job.created_at})
            record = record.model_copy(update={"job": job})
        self._records[record.job.job_id] = record
        self.upsert_ids.append(record.job.job_id)
        return record

    def get(self, job_id: str) -> UnifiedMemoryRecord | None:
        return self._records.get(job_id)

    def query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]:
        matched = [record for record in self._records.values() if _matches_query(record, query)]
        matched.sort(key=lambda item: item.job.updated_at, reverse=True)
        return matched[: max(query.limit, 0)]

    def list_jobs(self, filters: JobFilters) -> list[UnifiedMemoryRecord]:
        matched = [record for record in self._records.values() if _matches_filters(record, filters)]
        matched.sort(key=lambda item: item.job.updated_at, reverse=True)
        return matched[: max(filters.limit, 0)]

    def clear(self) -> None:
        self._records.clear()
        self.upsert_ids.clear()


__all__ = [
    "InMemoryStore",
    "JobFilters",
    "MemoryQuery",
    "MemoryStore",
]
