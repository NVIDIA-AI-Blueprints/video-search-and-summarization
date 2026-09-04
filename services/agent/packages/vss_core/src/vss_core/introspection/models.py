# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Strict data contracts and grounding validation for memory introspection."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any
from typing import Literal
from typing import Self

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from vss_core.memory.models import MemoryGroup  # noqa: TC001 - Pydantic resolves this model at runtime.
from vss_core.memory.models import RecordType  # noqa: TC001 - Pydantic resolves this model at runtime.
from vss_core.memory.models import UnifiedMemoryRecord  # noqa: TC001 - Pydantic resolves this model at runtime.


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _nonempty(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must be non-empty")
    return stripped


def parse_utc_instant(value: str) -> datetime:
    """Parse a strict ISO-8601 UTC instant without accepting naive/local time."""
    stripped = value.strip()
    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("must be an ISO-8601 UTC instant") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("must include the UTC timezone (Z or +00:00)")
    return parsed.astimezone(UTC)


class GroundedGap(_StrictModel):
    """One targeted VLM question grounded in retrieved sensor/time metadata."""

    question: str
    sensor: str
    start_time: str
    end_time: str

    @field_validator("question", "sensor", mode="after")
    @classmethod
    def _require_nonempty(cls, value: str, info: Any) -> str:
        return _nonempty(value, info.field_name)

    @field_validator("start_time", "end_time", mode="after")
    @classmethod
    def _require_utc(cls, value: str) -> str:
        parse_utc_instant(value)
        return value.strip()

    @model_validator(mode="after")
    def _ordered_window(self) -> Self:
        if parse_utc_instant(self.start_time) > parse_utc_instant(self.end_time):
            raise ValueError("gap start_time must be before or equal to end_time")
        return self


class SufficiencyDecision(_StrictModel):
    """Judge result constrained to approved evidence and grounded follow-ups."""

    sufficient: bool
    reason: str
    evidence_record_ids: list[str]
    gaps: list[GroundedGap]

    @field_validator("reason", mode="after")
    @classmethod
    def _require_reason(cls, value: str) -> str:
        return _nonempty(value, "reason")

    @field_validator("evidence_record_ids", mode="after")
    @classmethod
    def _require_nonempty_unique_evidence_ids(cls, value: list[str]) -> list[str]:
        normalized = [_nonempty(item, "evidence_record_ids item") for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("evidence_record_ids must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def _sufficient_has_no_gaps(self) -> Self:
        if self.sufficient and self.gaps:
            raise ValueError("a sufficient decision must not contain gaps")
        return self

    def validate_grounding(self, records: list[UnifiedMemoryRecord]) -> Self:
        """Reject evidence IDs, sensors, and windows not grounded in ``records``."""
        record_ids = {_record_id(record) for record in records}
        unknown_ids = sorted(set(self.evidence_record_ids) - record_ids)
        if unknown_ids:
            raise ValueError(f"unknown evidence_record_ids: {unknown_ids}")

        for gap in self.gaps:
            start_time = parse_utc_instant(gap.start_time)
            end_time = parse_utc_instant(gap.end_time)
            matching_records = [record for record in records if gap.sensor in _sensor_names(record)]
            if not matching_records:
                raise ValueError(f"gap sensor {gap.sensor!r} is not present in retrieved records")
            if not any(_record_window_overlaps(record, start_time, end_time) for record in matching_records):
                raise ValueError(
                    f"gap window {gap.start_time!r} to {gap.end_time!r} does not overlap a retrieved record "
                    f"for sensor {gap.sensor!r}"
                )
        return self


class VLMEvidence(_StrictModel):
    """Text evidence returned by a grounded introspection VLM query."""

    job_id: str
    persisted: bool
    sensor: str
    start_time: str
    end_time: str
    question: str
    answer: str
    intent: str = "introspection"
    model: str | None = None
    num_frames: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)

    @field_validator("job_id", "sensor", "question", "answer", "intent", mode="after")
    @classmethod
    def _require_text(cls, value: str, info: Any) -> str:
        return _nonempty(value, info.field_name)

    @field_validator("start_time", "end_time", mode="after")
    @classmethod
    def _require_utc(cls, value: str) -> str:
        parse_utc_instant(value)
        return value.strip()

    @model_validator(mode="after")
    def _ordered_window(self) -> Self:
        if parse_utc_instant(self.start_time) > parse_utc_instant(self.end_time):
            raise ValueError("VLM evidence start_time must be before or equal to end_time")
        return self


class IntrospectionSettings(_StrictModel):
    """Resource bounds for one introspection workflow."""

    max_memory_records: int = Field(default=10, ge=1)
    max_vlm_queries: int = Field(default=3, ge=0)
    max_clip_duration_seconds: int = Field(default=60, ge=1)
    timeout_seconds: int = Field(default=180, ge=1)
    sufficiency_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class IntrospectionRequest(_StrictModel):
    """Input to a bounded memory introspection workflow."""

    query: str
    sensor: str | None = None
    job_id: str | None = None
    record_id: str | None = None
    record_type: RecordType | None = None
    group: MemoryGroup | None = None
    start_time: str | None = None
    end_time: str | None = None

    @field_validator("query", "sensor", "job_id", "record_id", mode="after")
    @classmethod
    def _require_nonempty_selector(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _nonempty(value, info.field_name)

    @field_validator("start_time", "end_time", mode="after")
    @classmethod
    def _require_utc_window(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parse_utc_instant(value)
        return value.strip()

    @model_validator(mode="after")
    def _paired_ordered_window(self) -> Self:
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time and end_time must be provided together")
        if (
            self.start_time is not None
            and self.end_time is not None
            and parse_utc_instant(self.start_time) > parse_utc_instant(self.end_time)
        ):
            raise ValueError("start_time must be before or equal to end_time")
        return self

    @model_validator(mode="after")
    def _complete_child_identity(self) -> Self:
        if self.record_id is not None and (self.job_id is None or self.record_type is None):
            raise ValueError("child identity requires job_id, record_type, and record_id together")
        return self


class MemoryEvidence(_StrictModel):
    """Stable public identity for one memory record used in synthesis."""

    record_id: str
    job_id: str

    @field_validator("record_id", "job_id", mode="after")
    @classmethod
    def _require_identity(cls, value: str, info: Any) -> str:
        return _nonempty(value, info.field_name)


class IntrospectionResult(_StrictModel):
    """Strict stdout-safe result of one bounded introspection workflow."""

    status: Literal["completed", "partial", "no_memory"]
    sufficient_from_memory: bool
    answer: str | None
    memory_evidence: list[MemoryEvidence] = Field(default_factory=list)
    sufficiency: SufficiencyDecision | None = None
    vlm_evidence: list[VLMEvidence] = Field(default_factory=list)
    unresolved_gaps: list[str] = Field(default_factory=list)
    failure_kind: Literal["backend_unreachable", "timeout"] | None = Field(default=None, exclude=True)

    @field_validator("answer", mode="after")
    @classmethod
    def _require_answer(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _nonempty(value, "answer")


def _record_id(record: UnifiedMemoryRecord) -> str:
    return record.job.record_id or record.job.job_id


def _sensor_names(record: UnifiedMemoryRecord) -> set[str]:
    names: set[str] = set()
    if record.input is None or not record.input.sensors:
        return names
    for sensor in record.input.sensors:
        names.add(sensor.id)
        if sensor.info is not None:
            legacy_name = sensor.info.get("name")
            if isinstance(legacy_name, str) and legacy_name.strip():
                names.add(legacy_name.strip())
    return names


def _record_window_overlaps(record: UnifiedMemoryRecord, start: datetime, end: datetime) -> bool:
    if record.input is None or record.input.window is None:
        return False
    record_start = record.input.window.start.timestamp.astimezone(UTC)
    record_end = (
        record.input.window.end.timestamp.astimezone(UTC) if record.input.window.end is not None else record_start
    )
    return record_start <= end and record_end >= start


__all__ = [
    "GroundedGap",
    "IntrospectionRequest",
    "IntrospectionResult",
    "IntrospectionSettings",
    "MemoryEvidence",
    "SufficiencyDecision",
    "VLMEvidence",
    "parse_utc_instant",
]
