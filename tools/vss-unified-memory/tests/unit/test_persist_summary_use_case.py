# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Sequence
from datetime import datetime, timezone

from vss_unified_memory.application.models import MemoryEmbeddings, RepositoryWriteResult, TextPassage, WriteStatus
from vss_unified_memory.application.use_cases.persist_summary import PersistSummaryUseCase
from vss_unified_memory.domain.models import Event, MediaRef, MemoryEntity, Summary, TimeRange


class FakeEmbeddingProvider:
    calls: list[tuple[str, ...]] = []

    @property
    def model(self) -> str:
        return "embedding-model"

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.calls.append(tuple(texts))
        return tuple((float(index),) for index, _ in enumerate(texts))


class FakePassageChunker:
    @property
    def version(self) -> str:
        return "chunking-v1"

    def chunk(self, record_id: str, text: str) -> tuple[TextPassage, ...]:
        if text == "Event":
            return (
                TextPassage.create(
                    record_id=record_id,
                    ordinal=0,
                    start_char=0,
                    end_char=5,
                    token_count=3,
                    text="Event",
                ),
            )
        return (
            TextPassage.create(
                record_id=record_id,
                ordinal=0,
                start_char=0,
                end_char=3,
                token_count=3,
                text="Sum",
            ),
            TextPassage.create(
                record_id=record_id,
                ordinal=1,
                start_char=3,
                end_char=7,
                token_count=3,
                text="mary",
            ),
        )


class FakeRepository:
    saved: MemoryEntity | None = None
    embeddings: MemoryEmbeddings | None = None

    def save(self, memory: MemoryEntity, embeddings: MemoryEmbeddings | None = None) -> RepositoryWriteResult:
        self.saved = memory
        self.embeddings = embeddings
        return RepositoryWriteResult(WriteStatus.COMPLETE, 2, 2)


def test_use_case_embeds_summary_chunks_and_events_before_saving() -> None:
    summary = Summary(
        id="summary:1",
        description="Summary",
        media_ref=MediaRef("vst", "video-1"),
        created_at=datetime.now(timezone.utc),
        events=(Event("event:1", 1, TimeRange(1, 2), "Event", "activity"),),
    )
    repository = FakeRepository()
    embedding_provider = FakeEmbeddingProvider()
    result = PersistSummaryUseCase(repository, embedding_provider, FakePassageChunker()).execute(summary)
    assert result.status == WriteStatus.COMPLETE
    assert repository.saved == summary
    assert repository.embeddings is not None
    assert embedding_provider.calls == [("Sum", "mary", "Event")]
    assert tuple(item.passage.id for item in repository.embeddings.summary.passages) == (
        "summary:1:chunk:0000",
        "summary:1:chunk:0001",
    )
    assert tuple(item.record_id for item in repository.embeddings.events) == ("event:1",)
    assert repository.embeddings.events[0].passages[0].passage.text == "Event"
    assert repository.embeddings.model == "embedding-model"
