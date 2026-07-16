# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Conversions across the JSON CLI boundary."""

from datetime import datetime, timezone

from pydantic import ValidationError

from vss_unified_memory.adapters.cli.input_models import GetMemoryInput, PersistSummaryInput, SearchMemoryInput
from vss_unified_memory.adapters.cli.output_models import (
    ErrorOutput,
    EventOutput,
    MediaRefOutput,
    PersistSummaryOutput,
    RecallItemOutput,
    RecallMemoryOutput,
    SummaryOutput,
    TimeRangeOutput,
)
from vss_unified_memory.application.errors import ApplicationError
from vss_unified_memory.application.models import MemoryQuery, PersistSummaryResult, RecallMemoryResult
from vss_unified_memory.domain.ids import event_id_from_summary_id, summary_id_from_completion_id
from vss_unified_memory.domain.models import Event, MediaRef, MemoryEntity, Summary, TimeRange


def map_input_to_summary(input_model: PersistSummaryInput) -> Summary:
    summary_id = summary_id_from_completion_id(input_model.completion_id)
    events = tuple(
        Event(
            id=event_id_from_summary_id(summary_id, ordinal),
            ordinal=ordinal,
            time_range=TimeRange(start_seconds=event.start_time, end_seconds=event.end_time),
            description=event.description,
            event_type=event.type,
        )
        for ordinal, event in enumerate(input_model.content.events, start=1)
    )
    return Summary(
        id=summary_id,
        description=input_model.content.video_summary,
        media_ref=MediaRef(
            source=input_model.media_ref.source,
            video_id=str(input_model.video_id),
            stream_id=input_model.media_ref.stream_id,
            name=input_model.media_ref.name,
        ),
        created_at=datetime.fromtimestamp(input_model.created, tz=timezone.utc),
        events=events,
    )


def map_recall_input_to_query(input_model: GetMemoryInput | SearchMemoryInput) -> MemoryQuery:
    if isinstance(input_model, GetMemoryInput):
        return MemoryQuery(
            record_id=input_model.record_id,
            record_type=input_model.record_type,
            include_related=input_model.include_related,
        )
    time_range = None
    if input_model.time_range is not None:
        time_range = TimeRange(input_model.time_range.start_seconds, input_model.time_range.end_seconds)
    return MemoryQuery(
        record_type=input_model.record_type,
        video_id=input_model.video_id,
        time_range=time_range,
        query_text=input_model.query_text,
        semantic=input_model.semantic,
        limit=input_model.limit,
    )


def _map_time_range(time_range: TimeRange | None) -> TimeRangeOutput | None:
    if time_range is None:
        return None
    return TimeRangeOutput(start_seconds=time_range.start_seconds, end_seconds=time_range.end_seconds)


def _map_event(event: Event) -> EventOutput:
    return EventOutput(
        id=event.id,
        ordinal=event.ordinal,
        time_range=TimeRangeOutput(
            start_seconds=event.time_range.start_seconds,
            end_seconds=event.time_range.end_seconds,
        ),
        description=event.description,
        event_type=event.event_type,
    )


def _map_entity(memory: MemoryEntity) -> SummaryOutput | EventOutput:
    if isinstance(memory, Event):
        return _map_event(memory)
    return SummaryOutput(
        id=memory.id,
        description=memory.description,
        media_ref=MediaRefOutput(
            source=memory.media_ref.source,
            video_id=memory.media_ref.video_id,
            stream_id=memory.media_ref.stream_id,
            name=memory.media_ref.name,
        ),
        created_at=memory.created_at,
        events=tuple(_map_event(event) for event in memory.events),
        event_count=memory.event_count,
        time_range=_map_time_range(memory.time_range),
    )


def map_persist_result_to_output(result: PersistSummaryResult) -> PersistSummaryOutput:
    return PersistSummaryOutput(
        status=result.status,
        summary_id=result.summary_id,
        event_ids=result.event_ids,
        attempted_records=result.attempted_records,
        successful_records=result.successful_records,
    )


def map_recall_result_to_output(result: RecallMemoryResult) -> RecallMemoryOutput:
    return RecallMemoryOutput(
        results=tuple(RecallItemOutput(memory=_map_entity(item.memory), score=item.score) for item in result.results)
    )


def map_validation_error(
    error: ValidationError | ValueError,
    *,
    error_code: str = "invalid_input",
) -> ErrorOutput:
    if isinstance(error, ValidationError):
        details = error.errors(include_url=False, include_input=False)
        message = "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in details)
    else:
        message = str(error)
    return ErrorOutput(
        error_code=error_code,
        message=message,
        retryable=False,
    )


def map_application_error(error: ApplicationError) -> ErrorOutput:
    return ErrorOutput(
        error_code=error.error_code,
        message=error.message,
        retryable=error.retryable,
    )
