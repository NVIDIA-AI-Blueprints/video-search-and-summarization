# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pydantic models for the ``nv.vss.memory/1.0`` unified memory record.

One VSS operation may persist exactly one parent job record and zero or more
child result records. Parents omit ``job.record_id`` / ``job.record_type``;
children require both. Nested complete collections under ``output.ext``
(``events``, ``results``, ``incidents``) are prohibited.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Annotated
from typing import Any
from typing import Literal
from typing import Self

from pydantic import AwareDatetime
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import PlainSerializer
from pydantic import field_validator
from pydantic import model_validator

from vss_core._foundation.time import datetime_to_iso8601

SCHEMA_ID: Literal["nv.vss.memory/1.0"] = "nv.vss.memory/1.0"

MemoryGroup = Literal["summary", "search", "alert", "media", "vlm"]
KNOWN_GROUPS: frozenset[str] = frozenset({"summary", "search", "alert", "media", "vlm"})

RecordType = Literal["event", "search_hit", "incident"]
KNOWN_RECORD_TYPES: frozenset[str] = frozenset({"event", "search_hit", "incident"})

JobOperation = Literal["run"]
JobStatus = Literal["submitted", "running", "completed", "failed", "partial", "timeout"]
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "partial", "timeout"})
PENDING_STATUSES: frozenset[str] = frozenset({"submitted", "running"})

#: Nested collection keys that must never appear in ``output.ext`` (children own them).
FORBIDDEN_EXT_COLLECTIONS: frozenset[str] = frozenset({"events", "results", "incidents"})

#: Aware UTC instant on the model; JSON wire form stays ISO-8601 with ``Z``.
IsoInstant = Annotated[
    AwareDatetime,
    PlainSerializer(datetime_to_iso8601, return_type=str, when_used="json"),
]


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _is_empty_optional(value: object) -> bool:
    """Return True for values that must be omitted on the wire (not ``0``/``False``)."""
    if value is None:
        return True
    if value == "":
        return True
    return isinstance(value, (list, dict, tuple, set)) and len(value) == 0


def _omit_empties(payload: object) -> object:
    """Recursively drop empty optional containers/nulls/blank strings."""
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            nested = _omit_empties(value)
            if _is_empty_optional(nested):
                continue
            cleaned[key] = nested
        return cleaned
    if isinstance(payload, list):
        return [_omit_empties(item) for item in payload]
    return payload


class JobInfo(BaseModel):
    """Lifecycle identity for one parent job or child result record."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    record_id: str | None = None
    record_type: RecordType | None = None
    group: MemoryGroup
    operation: JobOperation = "run"
    status: JobStatus
    created_at: IsoInstant
    updated_at: IsoInstant | None = None
    backend_ref: str | None = None

    @field_validator("group", mode="before")
    @classmethod
    def _reject_unknown_group(cls, value: object) -> object:
        if isinstance(value, str) and value not in KNOWN_GROUPS:
            raise ValueError(f"unknown job.group {value!r}; expected one of {sorted(KNOWN_GROUPS)}")
        return value

    @field_validator("record_type", mode="before")
    @classmethod
    def _reject_unknown_record_type(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, str) and value not in KNOWN_RECORD_TYPES:
            raise ValueError(f"unknown job.record_type {value!r}; expected one of {sorted(KNOWN_RECORD_TYPES)}")
        return value

    @field_validator("record_id", mode="before")
    @classmethod
    def _normalize_record_id(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("job.record_id must be non-empty when present")
            if "#" in stripped:
                raise ValueError("job.record_id must not contain '#' (reserved storage delimiter)")
            return stripped
        return value

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _utc_instants(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _as_utc(value)

    @model_validator(mode="after")
    def _parent_or_child(self) -> Self:
        has_id = self.record_id is not None
        has_type = self.record_type is not None
        if has_id ^ has_type:
            raise ValueError("job.record_id and job.record_type must both be set (child) or both omitted (parent)")
        if has_id and self.status in PENDING_STATUSES:
            raise ValueError("child records are terminal; submitted/running statuses are parent-only")
        if has_id and self.backend_ref is not None:
            raise ValueError("child records must not carry job.backend_ref (parent-only)")
        return self

    @property
    def is_child(self) -> bool:
        return self.record_id is not None and self.record_type is not None

    @property
    def is_parent(self) -> bool:
        return not self.is_child


class SensorInfo(BaseModel):
    """Sensor / video source identity carried on a memory record."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str | None = None
    info: dict[str, Any] | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _require_non_empty_id(cls, value: object) -> object:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("input.sensors[].id must be a non-empty string")
        return value.strip()


class TimestampPoint(BaseModel):
    """A single UTC ISO-8601 timestamp point."""

    model_config = ConfigDict(extra="forbid")

    timestamp: IsoInstant

    @field_validator("timestamp", mode="after")
    @classmethod
    def _utc_instant(cls, value: datetime) -> datetime:
        return _as_utc(value)


class TimeWindow(BaseModel):
    """Temporal envelope for a job or child result.

    ``end`` may be omitted for point-in-time or open-ended records.
    """

    model_config = ConfigDict(extra="forbid")

    start: TimestampPoint
    end: TimestampPoint | None = None


class MemoryInput(BaseModel):
    """Common request envelope shared by every group."""

    model_config = ConfigDict(extra="forbid")

    query: str | None = None
    intent: str | None = None
    sensors: list[SensorInfo] | None = None
    window: TimeWindow | None = None
    params: dict[str, Any] | None = None


class OutputHandles(BaseModel):
    """Machine-usable identifiers promoted from group-specific results."""

    model_config = ConfigDict(extra="forbid")

    media_urls: list[str] | None = None
    related_job_ids: list[str] | None = None


class EmbeddingRef(BaseModel):
    """Embedding reference only — vectors are never inlined."""

    model_config = ConfigDict(extra="forbid")

    es_ref: str | None = None
    doc_ids: list[str] | None = None
    kind: str | None = None
    info: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_inline_vectors(cls, value: object) -> object:
        if isinstance(value, dict):
            for banned in ("vector", "values", "embedding", "dense_vector"):
                if banned in value and value[banned] is not None:
                    raise ValueError("inline embedding vectors are prohibited; use references only")
        return value


class MemoryOutput(BaseModel):
    """Common result envelope shared by every group."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    answer: str | None = None
    embedding: list[EmbeddingRef] | None = Field(default=None, alias="embedding")
    handles: OutputHandles | None = None
    ext: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_embedding_alias(cls, value: object) -> object:
        if isinstance(value, dict) and "Embedding" in value and "embedding" not in value:
            payload = dict(value)
            payload["embedding"] = payload.pop("Embedding")
            return payload
        return value


class MemoryError(BaseModel):
    """Structured error payload for failed/partial/timeout records."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = None
    message: str | None = None
    details: dict[str, Any] | None = None


class UnifiedMemoryRecord(BaseModel):
    """Canonical ``nv.vss.memory/1.0`` parent or child record."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["nv.vss.memory/1.0"] = Field(default=SCHEMA_ID, alias="schema")
    job: JobInfo
    input: MemoryInput | None = None
    output: MemoryOutput | None = None
    error: MemoryError | None = None

    def model_dump_memory(self) -> dict[str, Any]:
        """Serialize with lowercase ``embedding`` / ``schema``, omitting empties."""
        raw = self.model_dump(by_alias=True, mode="json", exclude_none=True)
        cleaned = _omit_empties(raw)
        if not isinstance(cleaned, dict):
            raise TypeError("memory dump must be an object")
        # Required discriminator and job block must survive omission.
        cleaned["schema"] = SCHEMA_ID
        if "job" not in cleaned or not isinstance(cleaned["job"], dict):
            raise ValueError("serialized memory record missing required job block")
        return cleaned


def forbidden_ext_collections(record: UnifiedMemoryRecord) -> list[str]:
    """Child-owned collection keys wrongly nested in ``record.output.ext``.

    Checked when a record is written, never when one is read: documents
    persisted before children existed nest ``events`` here, and a read that
    rejected them would make every stored job unreadable after an upgrade.
    """
    if record.output is None or not record.output.ext:
        return []
    return sorted(FORBIDDEN_EXT_COLLECTIONS.intersection(record.output.ext))


__all__ = [
    "FORBIDDEN_EXT_COLLECTIONS",
    "KNOWN_GROUPS",
    "KNOWN_RECORD_TYPES",
    "PENDING_STATUSES",
    "SCHEMA_ID",
    "TERMINAL_STATUSES",
    "EmbeddingRef",
    "IsoInstant",
    "JobInfo",
    "JobOperation",
    "JobStatus",
    "MemoryError",
    "MemoryGroup",
    "MemoryInput",
    "MemoryOutput",
    "OutputHandles",
    "RecordType",
    "SensorInfo",
    "TimeWindow",
    "TimestampPoint",
    "UnifiedMemoryRecord",
    "forbidden_ext_collections",
]
