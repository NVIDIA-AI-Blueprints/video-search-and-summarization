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


class LatencyMsOutput(OutputModel):
    chunking: float | None = None
    embedding: float | None = None
    es_bulk_index: float | None = None
    query_embedding: float | None = None
    es_exact_get: float | None = None
    es_lexical_search: float | None = None
    es_summary_knn: float | None = None
    es_event_knn: float | None = None
    es_parent_summary_hydration: float | None = None
    es_related_event_lookup: float | None = None
    total: float | None = None


class PersistObservabilityOutput(OutputModel):
    summary_id: str
    event_count: int
    summary_chars: int
    event_chars_total: int
    summary_chunk_count: int
    event_chunk_count: int
    total_chunk_count: int
    estimated_input_tokens: int
    embedding_model: str
    embedding_vector_count: int
    es_attempted_records: int
    es_successful_records: int
    latency_ms: LatencyMsOutput


class RecallObservabilityOutput(OutputModel):
    operation: Literal["get", "search"]
    semantic: bool
    returned_summary_count: int
    returned_event_count: int
    returned_chars: int
    estimated_returned_tokens: int
    latency_ms: LatencyMsOutput
    record_id: str | None = None
    record_type: RecordType | None = None
    include_related: bool | None = None
    limit: int | None = None
    query_text_chars: int | None = None
    query_text_hash: str | None = None
    query_text_preview: str | None = None
    candidate_summary_ids: tuple[str, ...] | None = None


class PersistSummaryOutput(OutputModel):
    status: WriteStatus
    summary_id: str
    event_ids: tuple[str, ...]
    attempted_records: int
    successful_records: int
    observability: PersistObservabilityOutput | None = None


class RecallItemOutput(OutputModel):
    memory: SummaryOutput | EventOutput
    score: float | None


class RecallMemoryOutput(OutputModel):
    status: Literal["complete"] = "complete"
    results: tuple[RecallItemOutput, ...]
    observability: RecallObservabilityOutput | None = None


class ErrorOutput(OutputModel):
    status: Literal["failed"] = "failed"
    error_code: str
    message: str
    retryable: bool
