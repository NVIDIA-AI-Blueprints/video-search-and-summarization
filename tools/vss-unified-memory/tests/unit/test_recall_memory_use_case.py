# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Sequence
from datetime import datetime, timezone

from vss_unified_memory.application.models import MemoryQuery, MemorySearchResult
from vss_unified_memory.application.use_cases.recall_memory import RecallMemoryUseCase
from vss_unified_memory.domain.models import MediaRef, MemoryEntity, RecordType, Summary


class FakeEmbeddingProvider:
    calls: list[tuple[str, ...]]

    def __init__(self) -> None:
        self.calls = []

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.calls.append(tuple(texts))
        return ((0.1, 0.2),)


class FakeRepository:
    def __init__(self, summary: Summary) -> None:
        self.summary = summary
        self.search_query: MemoryQuery | None = None

    def get(
        self,
        record_id: str,
        record_type: RecordType | None = None,
        include_related: bool = False,
    ) -> MemoryEntity | None:
        return self.summary if record_id == self.summary.id else None

    def search(self, query: MemoryQuery) -> tuple[MemorySearchResult, ...]:
        self.search_query = query
        return (MemorySearchResult(self.summary, 0.9),)


def test_get_by_id_does_not_generate_embedding() -> None:
    summary = Summary("summary:1", "Summary", MediaRef("vst", "video-1"), datetime.now(timezone.utc), ())
    embeddings = FakeEmbeddingProvider()
    result = RecallMemoryUseCase(FakeRepository(summary), embeddings).execute(MemoryQuery(record_id="summary:1"))
    assert result.results[0].memory == summary
    assert embeddings.calls == []


def test_semantic_search_embeds_query() -> None:
    summary = Summary("summary:1", "Summary", MediaRef("vst", "video-1"), datetime.now(timezone.utc), ())
    repository = FakeRepository(summary)
    result = RecallMemoryUseCase(repository, FakeEmbeddingProvider()).execute(
        MemoryQuery(query_text="forklift near miss", semantic=True)
    )
    assert result.results[0].score == 0.9
    assert repository.search_query is not None
    assert repository.search_query.query_vector == (0.1, 0.2)
