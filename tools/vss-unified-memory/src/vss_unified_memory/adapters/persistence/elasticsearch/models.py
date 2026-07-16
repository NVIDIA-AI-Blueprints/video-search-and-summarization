# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Elasticsearch-specific summary and event documents."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vss_unified_memory.domain.models import RecordType


@dataclass(frozen=True, slots=True)
class PassageDocument:
    chunk_id: str
    ordinal: int
    start_char: int
    end_char: int
    token_count: int
    text_hash: str
    text: str
    embedding: tuple[float, ...]

    def to_source(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "ordinal": self.ordinal,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "token_count": self.token_count,
            "text_hash": self.text_hash,
            "text": self.text,
            "embedding": list(self.embedding),
        }


@dataclass(frozen=True, slots=True)
class SummaryDocument:
    id: str
    summary_id: str
    description: str
    source: str
    video_id: str
    stream_id: str | None
    media_name: str | None
    created_at: datetime
    event_count: int
    event_ids: tuple[str, ...]
    start_seconds: float | None
    end_seconds: float | None
    content_hash: str
    embedding_model: str | None
    chunking_version: str | None
    summary_chunks: tuple[PassageDocument, ...]
    record_type: RecordType = RecordType.VIDEO_SUMMARY

    def to_source(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type.value,
            "id": self.id,
            "summary_id": self.summary_id,
            "description": self.description,
            "source": self.source,
            "video_id": self.video_id,
            "stream_id": self.stream_id,
            "media_name": self.media_name,
            "created_at": self.created_at.isoformat(),
            "event_count": self.event_count,
            "event_ids": list(self.event_ids),
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "content_hash": self.content_hash,
            "embedding_model": self.embedding_model,
            "chunking_version": self.chunking_version,
            "summary_chunks": [chunk.to_source() for chunk in self.summary_chunks],
        }


@dataclass(frozen=True, slots=True)
class EventDocument:
    id: str
    event_id: str
    summary_id: str
    ordinal: int
    event_type: str
    description: str
    source: str
    video_id: str
    stream_id: str | None
    media_name: str | None
    created_at: datetime
    start_seconds: float
    end_seconds: float
    embedding_model: str | None
    chunking_version: str | None
    event_chunks: tuple[PassageDocument, ...]
    record_type: RecordType = RecordType.VIDEO_EVENT

    def to_source(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type.value,
            "id": self.id,
            "event_id": self.event_id,
            "summary_id": self.summary_id,
            "ordinal": self.ordinal,
            "event_type": self.event_type,
            "description": self.description,
            "source": self.source,
            "video_id": self.video_id,
            "stream_id": self.stream_id,
            "media_name": self.media_name,
            "created_at": self.created_at.isoformat(),
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "embedding_model": self.embedding_model,
            "chunking_version": self.chunking_version,
            "event_chunks": [chunk.to_source() for chunk in self.event_chunks],
        }


ElasticsearchDocument = SummaryDocument | EventDocument
