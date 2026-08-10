# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed Elasticsearch write documents and read responses."""

from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_serializer, field_validator, model_validator

from vss_unified_memory.domain.models import RecordType


class PersistenceModel(BaseModel):
    """Immutable, strict model for the Elasticsearch adapter boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class TimestampedDocument(PersistenceModel):
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class CommonMemoryDocument(TimestampedDocument):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    stream_id: str | None = Field(default=None, min_length=1)
    media_name: str | None = Field(default=None, min_length=1)
    embedding_model: str | None = Field(default=None, min_length=1)
    chunking_version: str | None = Field(default=None, min_length=1)


class PassageDocument(PersistenceModel):
    """Complete nested passage written to Elasticsearch."""

    chunk_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    token_count: int = Field(gt=0)
    text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = Field(min_length=1)
    embedding: tuple[float, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_passage(self) -> "PassageDocument":
        if self.end_char <= self.start_char:
            raise ValueError("passage end_char must follow start_char")
        if sha256(self.text.encode()).hexdigest() != self.text_hash:
            raise ValueError("passage text_hash does not match text")
        return self


class SummaryDocumentFields(CommonMemoryDocument):
    summary_id: str = Field(min_length=1)
    event_count: int = Field(ge=0)
    event_ids: tuple[str, ...]
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_summary_fields(self) -> "SummaryDocumentFields":
        if self.id != self.summary_id:
            raise ValueError("summary id must equal summary_id")
        if self.event_count != len(self.event_ids):
            raise ValueError("event_count must equal the number of event_ids")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("event_ids must be unique")
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError("summary time range must provide both endpoints or neither")
        if self.event_count == 0 and self.start_seconds is not None:
            raise ValueError("summary without events cannot have a time range")
        if self.event_count > 0 and self.start_seconds is None:
            raise ValueError("summary with events must have a time range")
        if self.start_seconds is not None and self.end_seconds is not None and self.end_seconds < self.start_seconds:
            raise ValueError("summary end_seconds must follow start_seconds")
        if sha256(self.description.encode()).hexdigest() != self.content_hash:
            raise ValueError("summary content_hash does not match description")
        return self


class SummaryDocument(SummaryDocumentFields):
    """Complete summary document written to Elasticsearch."""

    record_type: Literal[RecordType.VIDEO_SUMMARY] = RecordType.VIDEO_SUMMARY
    summary_chunks: tuple[PassageDocument, ...]

    @model_validator(mode="after")
    def validate_embedding_metadata(self) -> "SummaryDocument":
        if self.summary_chunks and (self.embedding_model is None or self.chunking_version is None):
            raise ValueError("summary passages require embedding metadata")
        return self


class SummaryReadDocument(SummaryDocumentFields):
    """Summary fields returned after Elasticsearch excludes nested passages."""

    record_type: Literal[RecordType.VIDEO_SUMMARY] = RecordType.VIDEO_SUMMARY


class EventDocumentFields(CommonMemoryDocument):
    event_id: str = Field(min_length=1)
    summary_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    event_type: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_event_fields(self) -> "EventDocumentFields":
        if self.id != self.event_id:
            raise ValueError("event id must equal event_id")
        if self.end_seconds < self.start_seconds:
            raise ValueError("event end_seconds must follow start_seconds")
        return self


class EventDocument(EventDocumentFields):
    """Complete event document written to Elasticsearch."""

    record_type: Literal[RecordType.VIDEO_EVENT] = RecordType.VIDEO_EVENT
    event_chunks: tuple[PassageDocument, ...]

    @model_validator(mode="after")
    def validate_embedding_metadata(self) -> "EventDocument":
        if self.event_chunks and (self.embedding_model is None or self.chunking_version is None):
            raise ValueError("event passages require embedding metadata")
        return self


class EventReadDocument(EventDocumentFields):
    """Event fields returned after Elasticsearch excludes nested passages."""

    record_type: Literal[RecordType.VIDEO_EVENT] = RecordType.VIDEO_EVENT


ElasticsearchDocument = SummaryDocument | EventDocument

ElasticsearchReadDocument = Annotated[
    SummaryReadDocument | EventReadDocument,
    Field(discriminator="record_type"),
]
elasticsearch_read_document_parser: TypeAdapter[ElasticsearchReadDocument] = TypeAdapter(ElasticsearchReadDocument)

# Future persistence types extend ElasticsearchReadDocument and the exhaustive domain mapper together:
# - AlertReadDocument
# - SearchSessionReadDocument
# - SearchHitReadDocument
