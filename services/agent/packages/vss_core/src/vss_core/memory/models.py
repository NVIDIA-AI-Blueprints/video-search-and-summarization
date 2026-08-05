# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pydantic models for the ``nv.vss.memory/1.0`` unified memory record."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

SCHEMA_ID: Literal["nv.vss.memory/1.0"] = "nv.vss.memory/1.0"

MemoryGroup = Literal["summary", "search", "alert", "media", "vlm"]
KNOWN_GROUPS: frozenset[str] = frozenset({"summary", "search", "alert", "media", "vlm"})

JobOperation = Literal["run"]
JobStatus = Literal["submitted", "running", "completed", "failed", "partial", "timeout"]
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "partial", "timeout"})
PENDING_STATUSES: frozenset[str] = frozenset({"submitted", "running"})


class MemoryGroupEnum(StrEnum):
    """Enumerated job groups accepted by ``nv.vss.memory/1.0``."""

    SUMMARY = "summary"
    SEARCH = "search"
    ALERT = "alert"
    MEDIA = "media"
    VLM = "vlm"


class JobInfo(BaseModel):
    """Lifecycle identity and status for one job."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    group: MemoryGroup
    operation: JobOperation = "run"
    status: JobStatus
    created_at: str
    updated_at: str
    backend_ref: str | None = None

    @field_validator("group", mode="before")
    @classmethod
    def _reject_unknown_group(cls, value: object) -> object:
        if isinstance(value, str) and value not in KNOWN_GROUPS:
            raise ValueError(f"unknown job.group {value!r}; expected one of {sorted(KNOWN_GROUPS)}")
        return value


class SensorInfo(BaseModel):
    """Sensor / video source identity carried on a memory record."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    type: str = ""
    info: dict[str, Any] = Field(default_factory=dict)


class TimestampPoint(BaseModel):
    """A single UTC ISO-8601 timestamp point."""

    model_config = ConfigDict(extra="forbid")

    timestamp: str


class TimeWindow(BaseModel):
    """Temporal envelope for a job input."""

    model_config = ConfigDict(extra="forbid")

    start: TimestampPoint
    end: TimestampPoint


class MemoryInput(BaseModel):
    """Common request envelope shared by every group."""

    model_config = ConfigDict(extra="forbid")

    query: str | None = None
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

    @model_validator(mode="after")
    def _validate_job_id_prefix(self) -> UnifiedMemoryRecord:
        # Soft convention: prefer ``<group>-<ULID>``; do not hard-fail historical
        # ids that still round-trip, but reject empty ids (already Field-checked).
        return self

    def model_dump_memory(self) -> dict[str, Any]:
        """Serialize with the wire field name ``Embedding`` and ``schema``."""
        return self.model_dump(by_alias=True, mode="json")


__all__ = [
    "KNOWN_GROUPS",
    "PENDING_STATUSES",
    "SCHEMA_ID",
    "TERMINAL_STATUSES",
    "EmbeddingRef",
    "JobInfo",
    "JobOperation",
    "JobStatus",
    "MemoryError",
    "MemoryGroup",
    "MemoryGroupEnum",
    "MemoryInput",
    "MemoryOutput",
    "OutputHandles",
    "SensorInfo",
    "TimeWindow",
    "TimestampPoint",
    "UnifiedMemoryRecord",
]
