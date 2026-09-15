# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Process-local ``MemoryStore`` used by hermetic tests."""

from __future__ import annotations

from collections.abc import Iterator
from collections.abc import Sequence
from datetime import datetime

from ..models import UnifiedMemoryRecord
from ..store import JobFilters
from ..store import MemoryQuery
from ..store import make_storage_id
from ..store import storage_id_for


def _sensor_match(record: UnifiedMemoryRecord, sensor_id: str | None) -> bool:
    if not sensor_id:
        return True
    sensors = (record.input.sensors if record.input is not None else None) or []
    return any(
        sensor.id == sensor_id or (sensor.info is not None and sensor.info.get("name") == sensor_id)
        for sensor in sensors
    )


def _time_in_range(value: datetime, since: datetime | None, until: datetime | None) -> bool:
    """Compare aware UTC instants (not lexicographic ISO strings)."""
    if since is not None and value < since:
        return False
    return not (until is not None and value > until)


def _window_overlaps(
    record: UnifiedMemoryRecord,
    since: datetime | None,
    until: datetime | None,
) -> bool:
    """True when ``input.window`` overlaps ``[since, until]`` (inclusive bounds)."""
    if since is None and until is None:
        return True
    if record.input is None or record.input.window is None:
        return False
    start = record.input.window.start.timestamp
    end = record.input.window.end.timestamp if record.input.window.end is not None else start
    if until is not None and start > until:
        return False
    return not (since is not None and end < since)


def _sort_key(record: UnifiedMemoryRecord) -> datetime:
    return record.job.updated_at or record.job.created_at


def _text_haystacks(record: UnifiedMemoryRecord) -> list[str]:
    haystacks: list[str] = []
    if record.input is not None and record.input.query:
        haystacks.append(record.input.query)
    if record.output is not None and record.output.answer:
        haystacks.append(record.output.answer)
    return haystacks


def _matches_query(record: UnifiedMemoryRecord, query: MemoryQuery) -> bool:
    if query.job_id is not None and record.job.job_id != query.job_id:
        return False
    if query.parents_only and record.job.is_child:
        return False
    if not query.include_children and record.job.is_child:
        return False
    if query.record_type is not None and (not record.job.is_child or record.job.record_type != query.record_type):
        return False
    if query.record_id is not None and (not record.job.is_child or record.job.record_id != query.record_id):
        return False
    if query.group is not None and record.job.group != query.group:
        return False
    if query.status is not None and record.job.status != query.status:
        return False
    if not _sensor_match(record, query.sensor_id):
        return False
    assert query.since is None or isinstance(query.since, datetime)
    assert query.until is None or isinstance(query.until, datetime)
    if query.time_field == "window":
        if not _window_overlaps(record, query.since, query.until):
            return False
    elif not _time_in_range(record.job.created_at, query.since, query.until):
        return False
    if query.text:
        haystacks = _text_haystacks(record)
        needle = query.text.casefold()
        if not any(needle in item.casefold() for item in haystacks):
            return False
    return True


def _text_score(record: UnifiedMemoryRecord, text: str) -> int:
    """Simple relevance score for parity with Elasticsearch ``_score`` ordering."""
    needle = text.casefold()
    return sum(haystack.casefold().count(needle) for haystack in _text_haystacks(record))


def _matches_filters(record: UnifiedMemoryRecord, filters: JobFilters) -> bool:
    if record.job.is_child:
        return False
    if filters.group is not None and record.job.group != filters.group:
        return False
    if filters.status is not None and record.job.status != filters.status:
        return False
    if not _sensor_match(record, filters.sensor_id):
        return False
    assert filters.since is None or isinstance(filters.since, datetime)
    assert filters.until is None or isinstance(filters.until, datetime)
    return _time_in_range(record.job.created_at, filters.since, filters.until)


class InMemoryStore:
    """Process-local store used by hermetic tests."""

    def __init__(self) -> None:
        self._records: dict[str, UnifiedMemoryRecord] = {}
        self.upsert_ids: list[str] = []

    def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
        key = storage_id_for(record)
        existing = self._records.get(key)
        if existing is not None:
            # Preserve immutable created_at across lifecycle writes of the same identity.
            job = record.job.model_copy(update={"created_at": existing.job.created_at})
            record = record.model_copy(update={"job": job})
        self._records[key] = record
        self.upsert_ids.append(key)
        return record

    def get(self, job_id: str) -> UnifiedMemoryRecord | None:
        return self._records.get(make_storage_id(job_id=job_id))

    def get_record(
        self,
        job_id: str,
        record_type: str,
        record_id: str,
    ) -> UnifiedMemoryRecord | None:
        return self._records.get(make_storage_id(job_id=job_id, record_type=record_type, record_id=record_id))

    def get_many(self, storage_ids: Sequence[str]) -> list[UnifiedMemoryRecord]:
        """Return existing records in caller-supplied storage-ID order."""
        return [record for storage_id in storage_ids if (record := self._records.get(storage_id)) is not None]

    def scan(self, *, batch_size: int, limit: int | None = None) -> Iterator[UnifiedMemoryRecord]:
        """Yield a stable snapshot in ascending storage-ID order."""
        if batch_size <= 0:
            raise ValueError("scan batch_size must be positive")
        if limit is not None and limit < 0:
            raise ValueError("scan limit must not be negative")
        storage_ids = sorted(self._records)
        if limit is not None:
            storage_ids = storage_ids[:limit]
        for storage_id in storage_ids:
            yield self._records[storage_id]

    def query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]:
        matched = [record for record in self._records.values() if _matches_query(record, query)]
        if query.text:
            matched.sort(key=lambda record: (_text_score(record, query.text or ""), _sort_key(record)), reverse=True)
        else:
            matched.sort(key=_sort_key, reverse=True)
        return matched[: max(query.limit, 0)]

    def list_jobs(self, filters: JobFilters) -> list[UnifiedMemoryRecord]:
        matched = [record for record in self._records.values() if _matches_filters(record, filters)]
        matched.sort(key=_sort_key, reverse=True)
        return matched[: max(filters.limit, 0)]

    def clear(self) -> None:
        self._records.clear()
        self.upsert_ids.clear()


__all__ = ["InMemoryStore"]
