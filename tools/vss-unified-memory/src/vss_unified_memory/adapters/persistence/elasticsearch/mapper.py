# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mapping between domain aggregates and typed Elasticsearch documents."""

from hashlib import sha256

from typing_extensions import assert_never

from vss_unified_memory.adapters.persistence.elasticsearch.models import (
    ElasticsearchReadDocument,
    EventDocument,
    EventReadDocument,
    PassageDocument,
    SummaryDocument,
    SummaryReadDocument,
)
from vss_unified_memory.application.errors import RepositoryError
from vss_unified_memory.application.models import EmbeddedRecordPassages, MemoryEmbeddings
from vss_unified_memory.domain.models import Event, MediaRef, MemoryEntity, Summary, TimeRange


def summary_to_documents(
    summary: Summary,
    embeddings: MemoryEmbeddings | None,
) -> tuple[SummaryDocument, tuple[EventDocument, ...]]:
    time_range = summary.time_range
    summary_document = SummaryDocument(
        id=summary.id,
        summary_id=summary.id,
        description=summary.description,
        source=summary.media_ref.source,
        video_id=summary.media_ref.video_id,
        stream_id=summary.media_ref.stream_id,
        media_name=summary.media_ref.name,
        created_at=summary.created_at,
        event_count=summary.event_count,
        event_ids=tuple(event.id for event in summary.events),
        start_seconds=time_range.start_seconds if time_range else None,
        end_seconds=time_range.end_seconds if time_range else None,
        content_hash=sha256(summary.description.encode()).hexdigest(),
        embedding_model=embeddings.model if embeddings else None,
        chunking_version=embeddings.chunking_version if embeddings else None,
        summary_chunks=_passage_documents(embeddings.summary if embeddings else None),
    )
    event_documents = tuple(
        EventDocument(
            id=event.id,
            event_id=event.id,
            summary_id=summary.id,
            ordinal=event.ordinal,
            event_type=event.event_type,
            description=event.description,
            source=summary.media_ref.source,
            video_id=summary.media_ref.video_id,
            stream_id=summary.media_ref.stream_id,
            media_name=summary.media_ref.name,
            created_at=summary.created_at,
            start_seconds=event.time_range.start_seconds,
            end_seconds=event.time_range.end_seconds,
            embedding_model=embeddings.model if embeddings else None,
            chunking_version=embeddings.chunking_version if embeddings else None,
            event_chunks=_passage_documents(embeddings.for_event(event.id) if embeddings else None),
        )
        for event in summary.events
    )
    return summary_document, event_documents


def _passage_documents(record: EmbeddedRecordPassages | None) -> tuple[PassageDocument, ...]:
    if record is None:
        return ()
    return tuple(
        PassageDocument(
            chunk_id=item.passage.id,
            ordinal=item.passage.ordinal,
            start_char=item.passage.start_char,
            end_char=item.passage.end_char,
            token_count=item.passage.token_count,
            text_hash=item.passage.text_hash,
            text=item.passage.text,
            embedding=item.vector,
        )
        for item in record.passages
    )


def read_document_to_domain(
    document: ElasticsearchReadDocument,
    related_events: tuple[EventReadDocument, ...] | None = None,
) -> MemoryEntity:
    """Map a validated read document into a storage-independent domain object."""

    match document:
        case SummaryReadDocument():
            return _summary_read_document_to_domain(document, related_events)
        case EventReadDocument():
            if related_events is not None:
                raise RepositoryError("related events can only be attached to a summary", retryable=False)
            return _event_read_document_to_domain(document)
        case _ as unreachable:
            assert_never(unreachable)


def _summary_read_document_to_domain(
    document: SummaryReadDocument,
    related_events: tuple[EventReadDocument, ...] | None,
) -> Summary:
    event_documents = () if related_events is None else tuple(sorted(related_events, key=lambda event: event.ordinal))
    if related_events is not None:
        _validate_related_events(document, event_documents)
    return Summary(
        id=document.id,
        description=document.description,
        media_ref=MediaRef(
            source=document.source,
            video_id=document.video_id,
            stream_id=document.stream_id,
            name=document.media_name,
        ),
        created_at=document.created_at,
        events=tuple(_event_read_document_to_domain(event) for event in event_documents),
    )


def _event_read_document_to_domain(document: EventReadDocument) -> Event:
    return Event(
        id=document.id,
        ordinal=document.ordinal,
        time_range=TimeRange(document.start_seconds, document.end_seconds),
        description=document.description,
        event_type=document.event_type,
    )


def _validate_related_events(
    summary: SummaryReadDocument,
    events: tuple[EventReadDocument, ...],
) -> None:
    if any(event.summary_id != summary.summary_id for event in events):
        raise RepositoryError("related event references a different summary", retryable=False)
    if tuple(event.event_id for event in events) != summary.event_ids:
        raise RepositoryError("related event IDs do not match the summary", retryable=False)
    expected_ordinals = tuple(range(1, len(events) + 1))
    if tuple(event.ordinal for event in events) != expected_ordinals:
        raise RepositoryError("related event ordinals must be contiguous and one-based", retryable=False)
    if any(
        (
            event.source,
            event.video_id,
            event.stream_id,
            event.media_name,
            event.created_at,
        )
        != (
            summary.source,
            summary.video_id,
            summary.stream_id,
            summary.media_name,
            summary.created_at,
        )
        for event in events
    ):
        raise RepositoryError("related event media metadata does not match the summary", retryable=False)
    if not events:
        return
    if summary.start_seconds != min(event.start_seconds for event in events):
        raise RepositoryError("related events do not match the summary start time", retryable=False)
    if summary.end_seconds != max(event.end_seconds for event in events):
        raise RepositoryError("related events do not match the summary end time", retryable=False)
