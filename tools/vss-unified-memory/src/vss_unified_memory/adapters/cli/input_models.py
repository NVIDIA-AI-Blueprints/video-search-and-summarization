# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Strict JSON input contracts accepted by the allowlisted executables."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vss_unified_memory.domain.models import RecordType


class StrictInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VssSummaryEventInput(StrictInputModel):
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    type: str = Field(min_length=1)
    description: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> "VssSummaryEventInput":
        if self.end_time < self.start_time:
            raise ValueError("end_time must follow start_time")
        return self


class VssSummaryContentInput(StrictInputModel):
    video_summary: str = Field(min_length=1)
    events: list[VssSummaryEventInput]


class MediaRefInput(StrictInputModel):
    source: str = Field(min_length=1)
    stream_id: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)


class PersistSummaryInput(StrictInputModel):
    completion_id: UUID
    video_id: UUID
    created: int = Field(ge=0)
    model: str = Field(min_length=1)
    media_ref: MediaRefInput
    content: VssSummaryContentInput


class TimeRangeInput(StrictInputModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_time_range(self) -> "TimeRangeInput":
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must follow start_seconds")
        return self


class GetMemoryInput(StrictInputModel):
    operation: Literal["get"]
    record_id: str = Field(min_length=1)
    record_type: RecordType | None = None
    include_related: bool = True


class SearchMemoryInput(StrictInputModel):
    operation: Literal["search"]
    query_text: str | None = Field(default=None, min_length=1)
    record_type: RecordType | None = None
    video_id: str | None = Field(default=None, min_length=1)
    time_range: TimeRangeInput | None = None
    semantic: bool = False
    limit: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def validate_semantic_query(self) -> "SearchMemoryInput":
        if self.semantic and self.query_text is None:
            raise ValueError("semantic search requires query_text")
        return self


RecallMemoryInput = Annotated[GetMemoryInput | SearchMemoryInput, Field(discriminator="operation")]
