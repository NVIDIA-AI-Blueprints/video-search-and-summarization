# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Storage-independent inputs and results used by application services."""

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256

from vss_unified_memory.domain.models import MemoryEntity, RecordType, Summary, TimeRange


class WriteStatus(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class TextPassage:
    record_id: str
    id: str
    ordinal: int
    start_char: int
    end_char: int
    token_count: int
    text: str
    text_hash: str

    @classmethod
    def create(
        cls,
        *,
        record_id: str,
        ordinal: int,
        start_char: int,
        end_char: int,
        token_count: int,
        text: str,
    ) -> "TextPassage":
        return cls(
            record_id=record_id,
            id=f"{record_id}:chunk:{ordinal:04d}",
            ordinal=ordinal,
            start_char=start_char,
            end_char=end_char,
            token_count=token_count,
            text=text,
            text_hash=sha256(text.encode()).hexdigest(),
        )

    def __post_init__(self) -> None:
        if not self.record_id.strip():
            raise ValueError("passage record_id cannot be empty")
        if not self.id.strip():
            raise ValueError("passage id cannot be empty")
        if self.ordinal < 0:
            raise ValueError("summary chunk ordinal cannot be negative")
        if self.start_char < 0 or self.end_char <= self.start_char:
            raise ValueError("summary chunk character offsets are invalid")
        if self.token_count < 1:
            raise ValueError("summary chunk token_count must be positive")
        if not self.text.strip():
            raise ValueError("summary chunk text cannot be empty")
        if len(self.text_hash) != 64:
            raise ValueError("summary chunk text_hash must be SHA-256")


@dataclass(frozen=True, slots=True)
class EmbeddedTextPassage:
    passage: TextPassage
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddedRecordPassages:
    record_id: str
    passages: tuple[EmbeddedTextPassage, ...]


@dataclass(frozen=True, slots=True)
class MemoryEmbeddings:
    model: str
    chunking_version: str
    summary: EmbeddedRecordPassages
    events: tuple[EmbeddedRecordPassages, ...]

    @classmethod
    def from_summary(
        cls,
        summary: Summary,
        passages: tuple[TextPassage, ...],
        vectors: tuple[tuple[float, ...], ...],
        *,
        model: str,
        chunking_version: str,
    ) -> "MemoryEmbeddings":
        expected = len(passages)
        if len(vectors) != expected:
            raise ValueError(f"expected {expected} embedding vectors, received {len(vectors)}")
        embedded = tuple(
            EmbeddedTextPassage(passage, vector) for passage, vector in zip(passages, vectors, strict=True)
        )
        expected_record_ids = (summary.id, *(event.id for event in summary.events))
        records = tuple(
            EmbeddedRecordPassages(
                record_id,
                tuple(item for item in embedded if item.passage.record_id == record_id),
            )
            for record_id in expected_record_ids
        )
        if any(not record.passages for record in records):
            raise ValueError("every summary and event must have at least one embedded passage")
        unknown_record = next(
            (item.passage.record_id for item in embedded if item.passage.record_id not in expected_record_ids),
            None,
        )
        if unknown_record is not None:
            raise ValueError(f"passage references unknown record {unknown_record!r}")
        return cls(
            model=model,
            chunking_version=chunking_version,
            summary=records[0],
            events=records[1:],
        )

    def for_event(self, event_id: str) -> EmbeddedRecordPassages | None:
        return next((record for record in self.events if record.record_id == event_id), None)


@dataclass(frozen=True, slots=True)
class RepositoryWriteResult:
    status: WriteStatus
    attempted_records: int
    successful_records: int


@dataclass(frozen=True, slots=True)
class PersistSummaryResult:
    status: WriteStatus
    summary_id: str
    event_ids: tuple[str, ...]
    attempted_records: int
    successful_records: int


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    record_id: str | None = None
    record_type: RecordType | None = None
    include_related: bool = False
    video_id: str | None = None
    time_range: TimeRange | None = None
    query_text: str | None = None
    semantic: bool = False
    query_vector: tuple[float, ...] | None = None
    limit: int = 10

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.semantic and not self.query_text:
            raise ValueError("semantic search requires query_text")


@dataclass(frozen=True, slots=True)
class MemorySearchResult:
    memory: MemoryEntity
    score: float | None = None


@dataclass(frozen=True, slots=True)
class RecallMemoryResult:
    results: tuple[MemorySearchResult, ...]
