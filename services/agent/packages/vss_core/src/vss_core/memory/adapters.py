# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Memory adapter contract — not group-specific domain mappers.

Command groups (search, summarize, alerts, …) own the translation from their
backend payloads into :class:`UnifiedMemoryRecord` / :class:`RecordBundle`.
This module only defines the lifecycle protocol, the multi-record write unit,
and small helpers that know nothing about LVS events or search hits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from hashlib import sha256
import json
from typing import Any
from typing import Protocol

from vss_core._foundation.time import datetime_to_iso8601

from .models import SCHEMA_ID
from .models import JobStatus
from .models import MemoryError
from .models import MemoryGroup
from .models import MemoryInput
from .models import MemoryOutput
from .models import RecordType
from .models import TimestampPoint
from .models import TimeWindow
from .models import UnifiedMemoryRecord
from .store import coerce_utc_instant

_ADAPTER_REGISTRY: dict[MemoryGroup, type[MemoryAdapter]] = {}

#: Row keys that may carry a result row's start instant, in precedence order.
START_INSTANT_KEYS: tuple[str, ...] = ("timestamp", "start_time", "start", "ts")

#: Row keys that may carry a result row's end instant, in precedence order.
END_INSTANT_KEYS: tuple[str, ...] = ("end_time", "end", "end_ts")


def utc_now_iso() -> str:
    """Return the current UTC instant as an ISO-8601 ``Z`` string."""
    return datetime_to_iso8601(datetime.now(UTC))


def utc_instant(value: str | datetime) -> datetime:
    """Parse a required UTC instant, rejecting empty/naive/unparseable values."""
    parsed = coerce_utc_instant(value)
    if parsed is None:
        raise ValueError("timestamp is required")
    return parsed


def row_instant(row: dict[str, Any], *, keys: tuple[str, ...] = START_INSTANT_KEYS) -> datetime | None:
    """First parseable instant among ``keys``; ``None`` when the row carries none.

    Group-agnostic: nested ``{"timestamp": ...}`` points and bare scalars both
    resolve, so adapters do not each re-implement row time extraction.
    """
    for key in keys:
        value = row.get(key)
        if isinstance(value, dict):
            value = value.get("timestamp")
        if value is None:
            continue
        if isinstance(value, datetime):
            return utc_instant(value)
        if str(value).strip():
            return utc_instant(str(value))
    return None


def window_from_row(
    row: dict[str, Any],
    *,
    start_keys: tuple[str, ...] = START_INSTANT_KEYS,
    end_keys: tuple[str, ...] = END_INSTANT_KEYS,
) -> TimeWindow | None:
    """Build a child ``input.window`` from a result row, or ``None`` if untimed."""
    start = row_instant(row, keys=start_keys)
    if start is None:
        return None
    end = row_instant(row, keys=end_keys)
    return TimeWindow(
        start=TimestampPoint(timestamp=start),
        end=TimestampPoint(timestamp=end) if end is not None else None,
    )


def collect_values(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[str]:
    """Ordered, de-duplicated string values pulled from ``keys`` (or ``metadata``)."""
    found: list[str] = []
    for row in rows:
        for key in keys:
            value = row.get(key)
            if value is None and isinstance(row.get("metadata"), dict):
                value = row["metadata"].get(key)
            if value is None:
                continue
            if isinstance(value, list):
                found.extend(str(item) for item in value)
            else:
                found.append(str(value))
    seen: set[str] = set()
    ordered: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


@dataclass(frozen=True)
class RecordBundle:
    """One parent job record plus zero or more terminal child result records."""

    parent: UnifiedMemoryRecord
    children: tuple[UnifiedMemoryRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.parent.job.is_child:
            raise ValueError("RecordBundle.parent must be a parent job record")
        for child in self.children:
            if not child.job.is_child:
                raise ValueError("RecordBundle.children must be child records")
            if child.job.job_id != self.parent.job.job_id:
                raise ValueError("child job_id must match parent job_id")

    @property
    def all_records(self) -> tuple[UnifiedMemoryRecord, ...]:
        return (self.parent, *self.children)


class MemoryAdapter(Protocol):
    """Per-group mapper between domain payloads and unified memory records.

    Concrete adapters live with their command groups / domain packages, not
    in this module. They register via :func:`register_adapter` when a lookup
    registry is useful (tests, optional discovery).
    """

    group: MemoryGroup

    def submitted_record(
        self,
        *,
        job_id: str,
        created_at: str,
        input_data: MemoryInput,
        backend_ref: str | None = None,
    ) -> UnifiedMemoryRecord: ...

    def running_record(
        self,
        *,
        job_id: str,
        created_at: str,
        input_data: MemoryInput,
        backend_ref: str | None = None,
        updated_at: str | None = None,
    ) -> UnifiedMemoryRecord: ...

    def terminal_record(
        self,
        *,
        job_id: str,
        created_at: str,
        status: JobStatus,
        input_data: MemoryInput,
        output: MemoryOutput | None = None,
        error: MemoryError | None = None,
        backend_ref: str | None = None,
        updated_at: str | None = None,
    ) -> UnifiedMemoryRecord: ...


def register_adapter(adapter_cls: type[MemoryAdapter]) -> type[MemoryAdapter]:
    """Register a group adapter (optional discovery / tests)."""
    group = adapter_cls.group
    _ADAPTER_REGISTRY[group] = adapter_cls
    return adapter_cls


def get_adapter(group: MemoryGroup) -> MemoryAdapter:
    """Return a fresh adapter instance for ``group``."""
    if group not in _ADAPTER_REGISTRY:
        raise KeyError(f"no memory adapter registered for group {group!r}")
    return _ADAPTER_REGISTRY[group]()


def clear_adapter_registry() -> None:
    """Reset the adapter registry (test isolation). Does not re-register builtins."""
    _ADAPTER_REGISTRY.clear()


def _dump_optional_input(input_data: MemoryInput | None) -> dict[str, Any] | None:
    if input_data is None:
        return None
    payload = input_data.model_dump(mode="json", exclude_none=True)
    return payload or None


def _dump_optional_output(output: MemoryOutput | None) -> dict[str, Any] | None:
    if output is None:
        return None
    payload = output.model_dump(by_alias=True, mode="json", exclude_none=True)
    return payload or None


def build_record(
    *,
    job_id: str,
    group: MemoryGroup,
    status: JobStatus,
    created_at: str,
    updated_at: str | None = None,
    input_data: MemoryInput | None = None,
    output: MemoryOutput | None = None,
    error: MemoryError | None = None,
    backend_ref: str | None = None,
    record_id: str | None = None,
    record_type: RecordType | None = None,
) -> UnifiedMemoryRecord:
    """Build one parent or child record from schema fields (no domain mapping)."""
    job: dict[str, Any] = {
        "job_id": job_id,
        "group": group,
        "operation": "run",
        "status": status,
        "created_at": created_at,
    }
    if updated_at is not None:
        job["updated_at"] = updated_at
    if backend_ref is not None:
        job["backend_ref"] = backend_ref
    if record_id is not None:
        job["record_id"] = record_id
    if record_type is not None:
        job["record_type"] = record_type
    payload: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "job": job,
    }
    input_payload = _dump_optional_input(input_data)
    if input_payload is not None:
        payload["input"] = input_payload
    output_payload = _dump_optional_output(output)
    if output_payload is not None:
        payload["output"] = output_payload
    if error is not None:
        payload["error"] = error.model_dump(mode="json", exclude_none=True)
    return UnifiedMemoryRecord.model_validate(payload)


def child_record(
    *,
    job_id: str,
    group: MemoryGroup,
    record_id: str,
    record_type: RecordType,
    created_at: str,
    input_data: MemoryInput | None = None,
    output: MemoryOutput | None = None,
    status: JobStatus = "completed",
) -> UnifiedMemoryRecord:
    """Construct a terminal child result record."""
    if status in {"submitted", "running"}:
        raise ValueError("child records must be terminal")
    return build_record(
        job_id=job_id,
        group=group,
        status=status,
        created_at=created_at,
        updated_at=None,
        input_data=input_data,
        output=output,
        record_id=record_id,
        record_type=record_type,
    )


def deterministic_record_id(*, prefix: str, payload: dict[str, Any]) -> str:
    """Stable digest-based child id when no upstream identifier exists."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def resolve_child_record_id(
    row: dict[str, Any],
    *,
    preferred_keys: tuple[str, ...] = ("event_id", "id", "uuid", "_id"),
    prefix: str,
    digest_payload: dict[str, Any] | None = None,
) -> str:
    """Prefer a stable upstream id; otherwise derive a deterministic digest id."""
    for key in preferred_keys:
        value = row.get(key)
        if value is None and isinstance(row.get("metadata"), dict):
            value = row["metadata"].get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return deterministic_record_id(prefix=prefix, payload=digest_payload or row)


class LifecycleAdapter:
    """Shared parent lifecycle helpers for group adapters to subclass.

    Fills only ``job.*`` / envelope fields. Domain ``build_input`` /
    ``terminal_bundle`` logic belongs in the owning command group.
    """

    group: MemoryGroup

    def submitted_record(
        self,
        *,
        job_id: str,
        created_at: str,
        input_data: MemoryInput,
        backend_ref: str | None = None,
    ) -> UnifiedMemoryRecord:
        return build_record(
            job_id=job_id,
            group=self.group,
            status="submitted",
            created_at=created_at,
            updated_at=created_at,
            input_data=input_data,
            backend_ref=backend_ref,
        )

    def running_record(
        self,
        *,
        job_id: str,
        created_at: str,
        input_data: MemoryInput,
        backend_ref: str | None = None,
        updated_at: str | None = None,
    ) -> UnifiedMemoryRecord:
        stamp = updated_at or utc_now_iso()
        return build_record(
            job_id=job_id,
            group=self.group,
            status="running",
            created_at=created_at,
            updated_at=stamp,
            input_data=input_data,
            backend_ref=backend_ref,
        )

    def terminal_record(
        self,
        *,
        job_id: str,
        created_at: str,
        status: JobStatus,
        input_data: MemoryInput,
        output: MemoryOutput | None = None,
        error: MemoryError | None = None,
        backend_ref: str | None = None,
        updated_at: str | None = None,
    ) -> UnifiedMemoryRecord:
        if status not in {"completed", "failed", "partial", "timeout"}:
            raise ValueError(f"status {status!r} is not terminal")
        stamp = updated_at or utc_now_iso()
        return build_record(
            job_id=job_id,
            group=self.group,
            status=status,
            created_at=created_at,
            updated_at=stamp,
            input_data=input_data,
            output=output,
            error=error,
            backend_ref=backend_ref,
        )


__all__ = [
    "END_INSTANT_KEYS",
    "START_INSTANT_KEYS",
    "LifecycleAdapter",
    "MemoryAdapter",
    "RecordBundle",
    "build_record",
    "child_record",
    "clear_adapter_registry",
    "collect_values",
    "deterministic_record_id",
    "get_adapter",
    "register_adapter",
    "resolve_child_record_id",
    "row_instant",
    "utc_instant",
    "utc_now_iso",
    "window_from_row",
]
