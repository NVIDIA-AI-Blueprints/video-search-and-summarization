# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pydantic models for the ``nv.vss.memory/1.0`` unified memory record."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Annotated
from typing import Any
from typing import Literal

from pydantic import AwareDatetime
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import PlainSerializer
from pydantic import field_validator

from vss_core._foundation.time import datetime_to_iso8601

SCHEMA_ID: Literal["nv.vss.memory/1.0"] = "nv.vss.memory/1.0"

MemoryGroup = Literal["summary", "search", "alert", "vlm"]
KNOWN_GROUPS: frozenset[str] = frozenset({"summary", "search", "alert", "vlm"})

JobOperation = Literal["run"]
JobStatus = Literal["submitted", "running", "completed", "failed", "partial", "timeout"]
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "partial", "timeout"})
PENDING_STATUSES: frozenset[str] = frozenset({"submitted", "running"})

#: Aware UTC instant on the model; JSON wire form stays ISO-8601 with ``Z`` (§5.2).
IsoInstant = Annotated[
    AwareDatetime,
    PlainSerializer(datetime_to_iso8601, return_type=str, when_used="json"),
]


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


class JobInfo(BaseModel):
    """Lifecycle identity and status for one job."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    group: MemoryGroup
    operation: JobOperation = "run"
    status: JobStatus
    created_at: IsoInstant
    updated_at: IsoInstant
    backend_ref: str | None = None

    @field_validator("group", mode="before")
    @classmethod
    def _reject_unknown_group(cls, value: object) -> object:
        if isinstance(value, str) and value not in KNOWN_GROUPS:
            raise ValueError(f"unknown job.group {value!r}; expected one of {sorted(KNOWN_GROUPS)}")
        return value

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _utc_instants(cls, value: datetime) -> datetime:
        return _as_utc(value)


class SensorInfo(BaseModel):
    """Sensor / video source identity carried on a memory record.

    Wire shape is ``{id, type, info}`` only (``extra="forbid"``). nvschema
    ``nv.Sensor`` also carries ``description``, ``location``, and
    ``coordinate``; fold those into ``info`` before
    ``UnifiedMemoryRecord.model_validate`` — see
    ``SearchAdapter.build_input``, which does this for raw sensor dicts.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    type: str = ""
    info: dict[str, Any] = Field(default_factory=dict)


class TimestampPoint(BaseModel):
    """A single UTC ISO-8601 timestamp point."""

    model_config = ConfigDict(extra="forbid")

    timestamp: IsoInstant

    @field_validator("timestamp", mode="after")
    @classmethod
    def _utc_instant(cls, value: datetime) -> datetime:
        return _as_utc(value)


class TimeWindow(BaseModel):
    """Temporal envelope for a job input."""

    model_config = ConfigDict(extra="forbid")

    start: TimestampPoint
    end: TimestampPoint


class MemoryInput(BaseModel):
    """Common request envelope shared by every group.

    ``sensors`` entries must already be wire-shaped ``SensorInfo`` values;
    adapters are responsible for folding full nvschema sensor dicts into
    ``{id, type, info}`` before construction.
    """

    model_config = ConfigDict(extra="forbid")

    query: str | None = None
    intent: str | None = None
    sensors: list[SensorInfo] = Field(default_factory=list)
    window: TimeWindow | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class OutputHandles(BaseModel):
    """Machine-usable identifiers promoted from group-specific results.

    Group-specific id lists (``event_ids``, ``object_ids``, ``frame_ids``) live
    under ``output.ext``, not here.
    """

    model_config = ConfigDict(extra="forbid")

    media_urls: list[str] = Field(default_factory=list)
    related_job_ids: list[str] = Field(default_factory=list)


class EmbeddingRef(BaseModel):
    """Embedding reference only — vectors are never inlined (UM-11)."""

    model_config = ConfigDict(extra="forbid")

    es_ref: str | None = None
    doc_ids: list[str] = Field(default_factory=list)
    kind: str | None = None
    info: dict[str, Any] = Field(default_factory=dict)


class MemoryOutput(BaseModel):
    """Common result envelope shared by every group."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    answer: str | None = None
    Embedding: list[EmbeddingRef] = Field(default_factory=list, alias="Embedding")
    handles: OutputHandles = Field(default_factory=OutputHandles)
    ext: dict[str, Any] = Field(default_factory=dict)


class MemoryError(BaseModel):
    """Structured error payload for failed/partial/timeout records."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = None
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class UnifiedMemoryRecord(BaseModel):
    """Canonical ``nv.vss.memory/1.0`` record — one document per job lifecycle."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["nv.vss.memory/1.0"] = Field(default=SCHEMA_ID, alias="schema")
    job: JobInfo
    input: MemoryInput = Field(default_factory=MemoryInput)
    output: MemoryOutput = Field(default_factory=MemoryOutput)
    error: MemoryError | None = None

    def model_dump_memory(self) -> dict[str, Any]:
        """Serialize with the wire field name ``Embedding`` and ``schema``."""
        return self.model_dump(by_alias=True, mode="json")


__all__ = [
    "KNOWN_GROUPS",
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
    "SensorInfo",
    "TimeWindow",
    "TimestampPoint",
    "UnifiedMemoryRecord",
]
