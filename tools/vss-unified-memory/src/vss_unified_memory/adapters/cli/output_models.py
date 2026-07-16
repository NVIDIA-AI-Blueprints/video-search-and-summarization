# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Machine-readable stdout contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from vss_unified_memory.application.models import WriteStatus
from vss_unified_memory.domain.models import RecordType


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRangeOutput(OutputModel):
    start_seconds: float
    end_seconds: float


class MediaRefOutput(OutputModel):
    source: str
    video_id: str
    stream_id: str | None
    name: str | None


class EventOutput(OutputModel):
    record_type: Literal[RecordType.VIDEO_EVENT] = RecordType.VIDEO_EVENT
    id: str
    ordinal: int
    time_range: TimeRangeOutput
    description: str
    event_type: str


class SummaryOutput(OutputModel):
    record_type: Literal[RecordType.VIDEO_SUMMARY] = RecordType.VIDEO_SUMMARY
    id: str
    description: str
    media_ref: MediaRefOutput
    created_at: datetime
    events: tuple[EventOutput, ...]
    event_count: int
    time_range: TimeRangeOutput | None


class PersistSummaryOutput(OutputModel):
    status: WriteStatus
    summary_id: str
    event_ids: tuple[str, ...]
    attempted_records: int
    successful_records: int


class RecallItemOutput(OutputModel):
    memory: SummaryOutput | EventOutput
    score: float | None


class RecallMemoryOutput(OutputModel):
    status: Literal["complete"] = "complete"
    results: tuple[RecallItemOutput, ...]


class ErrorOutput(OutputModel):
    status: Literal["failed"] = "failed"
    error_code: str
    message: str
    retryable: bool
