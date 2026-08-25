# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Implementation-neutral store contract for unified memory.

Concrete backends live under ``memory.backends`` (``InMemoryStore``,
``ElasticsearchMemoryStore``). Keep this module free of backend code so the
tree matches ``vss_core.knowledge`` (contract vs implementations).

Public identities:

* Parent: ``job_id``
* Child: ``(job_id, record_type, record_id)``

Internal storage IDs (backend document keys) use a shared helper:

* Parent: ``<job_id>``
* Child: ``<job_id>#<record_type>#<record_id>``

``#`` is a reserved delimiter — rejected in public ``record_id`` (and in
``job_id`` for storage-ID construction) so components never need encoding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Protocol

from vss_core._foundation.time import iso8601_to_datetime

from .models import JobStatus
from .models import MemoryGroup
from .models import RecordType
from .models import UnifiedMemoryRecord

STORAGE_ID_DELIMITER = "#"


def coerce_utc_instant(value: datetime | str | None) -> datetime | None:
    """Parse/normalize an optional UTC instant; reject naive or unparseable values."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware UTC ISO-8601")
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid ISO-8601 timestamp: {value!r}")
    return iso8601_to_datetime(value).astimezone(UTC)


def storage_id_for(record: UnifiedMemoryRecord) -> str:
    """Return the internal storage document id for ``record``."""
    return make_storage_id(
        job_id=record.job.job_id,
        record_type=record.job.record_type,
        record_id=record.job.record_id,
    )


def make_storage_id(
    *,
    job_id: str,
    record_type: RecordType | str | None = None,
    record_id: str | None = None,
) -> str:
    """Build the internal storage id for a parent or child.

    Parent: ``job_id``. Child: ``job_id#record_type#record_id``.
    ``#`` is reserved and rejected in ``job_id`` and ``record_id``.
    """
    if not job_id or not str(job_id).strip():
        raise ValueError("job_id must be non-empty")
    job_id = str(job_id).strip()
    if STORAGE_ID_DELIMITER in job_id:
        raise ValueError(f"job_id must not contain {STORAGE_ID_DELIMITER!r} (reserved storage delimiter)")
    if record_type is None and record_id is None:
        return job_id
    if record_type is None or record_id is None:
        raise ValueError("child storage ids require both record_type and record_id")
    record_id = str(record_id).strip()
    if not record_id:
        raise ValueError("record_id must be non-empty")
    if STORAGE_ID_DELIMITER in record_id:
        raise ValueError(f"record_id must not contain {STORAGE_ID_DELIMITER!r} (reserved storage delimiter)")
    return f"{job_id}{STORAGE_ID_DELIMITER}{record_type}{STORAGE_ID_DELIMITER}{record_id}"


def is_parent_storage_id(storage_id: str) -> bool:
    """Return True when ``storage_id`` identifies a parent job document."""
    return STORAGE_ID_DELIMITER not in storage_id


@dataclass(slots=True)
class MemoryQuery:
    """Free-form / filtered query over persisted unified-memory records.

    ``since`` / ``until`` filter on ``job.created_at`` when ``time_field`` is
    ``\"created_at\"`` (default), or on child event windows when
    ``time_field=\"window\"`` (``input.window.start`` / ``end`` overlap).
    """

    text: str | None = None
    group: MemoryGroup | None = None
    status: JobStatus | None = None
    sensor_id: str | None = None
    job_id: str | None = None
    record_type: RecordType | None = None
    record_id: str | None = None
    include_children: bool = True
    parents_only: bool = False
    since: datetime | str | None = None
    until: datetime | str | None = None
    time_field: str = "created_at"
    limit: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "since", coerce_utc_instant(self.since))
        object.__setattr__(self, "until", coerce_utc_instant(self.until))
        if self.time_field not in {"created_at", "window"}:
            raise ValueError("time_field must be 'created_at' or 'window'")
        if self.parents_only and self.record_type is not None:
            raise ValueError("parents_only cannot be combined with record_type")


@dataclass(slots=True)
class JobFilters:
    """Filters for ``list_jobs`` (parent job listing only)."""

    group: MemoryGroup | None = None
    status: JobStatus | None = None
    sensor_id: str | None = None
    since: datetime | str | None = None
    until: datetime | str | None = None
    limit: int = 50

    def __post_init__(self) -> None:
        object.__setattr__(self, "since", coerce_utc_instant(self.since))
        object.__setattr__(self, "until", coerce_utc_instant(self.until))


class MemoryStore(Protocol):
    """Durable store for unified memory parent and child records.

    Implementations must upsert by the shared internal storage id so parent
    lifecycle transitions update one document and children never collide with
    their parent or siblings.
    """

    def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord: ...

    def get(self, job_id: str) -> UnifiedMemoryRecord | None:
        """Return the parent record for ``job_id``, or ``None``."""
        ...

    def get_record(
        self,
        job_id: str,
        record_type: RecordType | str,
        record_id: str,
    ) -> UnifiedMemoryRecord | None:
        """Return a child record by public identity, or ``None``."""
        ...

    def query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]: ...

    def list_jobs(self, filters: JobFilters) -> list[UnifiedMemoryRecord]:
        """List parent job records only (exclude children)."""
        ...


__all__ = [
    "STORAGE_ID_DELIMITER",
    "JobFilters",
    "MemoryQuery",
    "MemoryStore",
    "coerce_utc_instant",
    "is_parent_storage_id",
    "make_storage_id",
    "storage_id_for",
]
