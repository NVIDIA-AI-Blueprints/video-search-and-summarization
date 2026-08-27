# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Strict data contracts and grounding validation for memory introspection."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any
from typing import Self

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

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
    start: str
    end: str

    @field_validator("question", "sensor", mode="after")
    @classmethod
    def _require_nonempty(cls, value: str, info: Any) -> str:
        return _nonempty(value, info.field_name)

    @field_validator("start", "end", mode="after")
    @classmethod
    def _require_utc(cls, value: str) -> str:
        parse_utc_instant(value)
        return value.strip()

    @model_validator(mode="after")
    def _ordered_window(self) -> Self:
        if parse_utc_instant(self.start) > parse_utc_instant(self.end):
            raise ValueError("gap start must be before or equal to end")
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
            start = parse_utc_instant(gap.start)
            end = parse_utc_instant(gap.end)
            matching_records = [record for record in records if gap.sensor in _sensor_names(record)]
            if not matching_records:
                raise ValueError(f"gap sensor {gap.sensor!r} is not present in retrieved records")
            if not any(_record_window_overlaps(record, start, end) for record in matching_records):
                raise ValueError(
                    f"gap window {gap.start!r} to {gap.end!r} does not overlap a retrieved record "
                    f"for sensor {gap.sensor!r}"
                )
        return self


class VLMEvidence(_StrictModel):
    """Text evidence returned by a grounded introspection VLM query."""

    sensor: str
    start: str
    end: str
    prompt: str
    intent: str
    content: str

    @field_validator("sensor", "prompt", "intent", "content", mode="after")
    @classmethod
    def _require_text(cls, value: str, info: Any) -> str:
        return _nonempty(value, info.field_name)

    @field_validator("start", "end", mode="after")
    @classmethod
    def _require_utc(cls, value: str) -> str:
        parse_utc_instant(value)
        return value.strip()

    @model_validator(mode="after")
    def _ordered_window(self) -> Self:
        if parse_utc_instant(self.start) > parse_utc_instant(self.end):
            raise ValueError("VLM evidence start must be before or equal to end")
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
    records: list[UnifiedMemoryRecord]

    @field_validator("query", mode="after")
    @classmethod
    def _require_query(cls, value: str) -> str:
        return _nonempty(value, "query")


class IntrospectionResult(_StrictModel):
    """Final text answer and the evidence used to produce it."""

    answer: str
    decision: SufficiencyDecision
    memory_evidence: list[UnifiedMemoryRecord] = Field(default_factory=list)
    vlm_evidence: list[VLMEvidence] = Field(default_factory=list)
    unresolved_gaps: list[GroundedGap] = Field(default_factory=list)

    @field_validator("answer", mode="after")
    @classmethod
    def _require_answer(cls, value: str) -> str:
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
    "SufficiencyDecision",
    "VLMEvidence",
    "parse_utc_instant",
]
