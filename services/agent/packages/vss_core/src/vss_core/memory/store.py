# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Implementation-neutral store contract for unified memory.

Concrete backends live under ``memory.backends`` (``InMemoryStore``,
``ElasticsearchMemoryStore``). Keep this module free of backend code so the
tree matches ``vss_core.knowledge`` (contract vs implementations).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Protocol

from vss_core._foundation.time import iso8601_to_datetime

from .models import JobStatus
from .models import MemoryGroup
from .models import UnifiedMemoryRecord


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


@dataclass(slots=True)
class MemoryQuery:
    """Free-form / filtered query over persisted unified-memory records."""

    text: str | None = None
    group: MemoryGroup | None = None
    status: JobStatus | None = None
    sensor_id: str | None = None
    job_id: str | None = None
    since: datetime | str | None = None
    until: datetime | str | None = None
    limit: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "since", coerce_utc_instant(self.since))
        object.__setattr__(self, "until", coerce_utc_instant(self.until))


@dataclass(slots=True)
class JobFilters:
    """Filters for ``list_jobs`` (group-scoped job listing)."""

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
    """Durable store for unified memory records.

    Implementations must upsert by ``job.job_id`` (document ``_id``) so
    lifecycle transitions update one document rather than creating duplicates.
    """

    def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord: ...

    def get(self, job_id: str) -> UnifiedMemoryRecord | None: ...

    def query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]: ...

    def list_jobs(self, filters: JobFilters) -> list[UnifiedMemoryRecord]: ...


__all__ = [
    "JobFilters",
    "MemoryQuery",
    "MemoryStore",
    "coerce_utc_instant",
]
