# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mapping between domain aggregates and Elasticsearch documents."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from hashlib import sha256
from typing import Any

from vss_unified_memory.adapters.persistence.elasticsearch.models import (
    EventDocument,
    PassageDocument,
    SummaryDocument,
)
from vss_unified_memory.application.errors import RepositoryError
from vss_unified_memory.application.models import EmbeddedRecordPassages, MemoryEmbeddings
from vss_unified_memory.domain.models import Event, MediaRef, MemoryEntity, RecordType, Summary, TimeRange


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


def source_to_entity(source: Mapping[str, Any], related_sources: Sequence[Mapping[str, Any]] = ()) -> MemoryEntity:
    try:
        record_type = RecordType(str(source["record_type"]))
        if record_type == RecordType.VIDEO_EVENT:
            return _source_to_event(source)
        if record_type != RecordType.VIDEO_SUMMARY:
            raise RepositoryError(f"record type {record_type.value!r} is not implemented", retryable=False)
        events = sorted(
            (
                _source_to_event(item)
                for item in related_sources
                if item.get("record_type") == RecordType.VIDEO_EVENT.value
            ),
            key=lambda event: event.ordinal,
        )
        return Summary(
            id=str(source["id"]),
            description=str(source["description"]),
            media_ref=MediaRef(
                source=str(source["source"]),
                video_id=str(source["video_id"]),
                stream_id=_optional_string(source.get("stream_id")),
                name=_optional_string(source.get("media_name")),
            ),
            created_at=datetime.fromisoformat(str(source["created_at"])),
            events=tuple(events),
        )
    except RepositoryError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise RepositoryError("Elasticsearch returned an invalid memory document", retryable=False) from error


def _source_to_event(source: Mapping[str, Any]) -> Event:
    return Event(
        id=str(source["id"]),
        ordinal=int(source["ordinal"]),
        time_range=TimeRange(float(source["start_seconds"]), float(source["end_seconds"])),
        description=str(source["description"]),
        event_type=str(source["event_type"]),
    )


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)
