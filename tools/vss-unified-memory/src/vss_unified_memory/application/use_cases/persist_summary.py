# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persist a VSS summary aggregate and its event embeddings."""

from vss_unified_memory.application.models import MemoryEmbeddings, PersistSummaryResult
from vss_unified_memory.application.ports.embedding_provider import EmbeddingProvider
from vss_unified_memory.application.ports.memory_repository import MemoryRepository
from vss_unified_memory.application.ports.passage_chunker import PassageChunker
from vss_unified_memory.domain.models import Summary


class PersistSummaryUseCase:
    def __init__(
        self,
        repository: MemoryRepository,
        embedding_provider: EmbeddingProvider,
        passage_chunker: PassageChunker,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._passage_chunker = passage_chunker

    def execute(self, summary: Summary) -> PersistSummaryResult:
        passages = (
            *self._passage_chunker.chunk(summary.id, summary.description),
            *(
                passage
                for event in summary.events
                for passage in self._passage_chunker.chunk(event.id, event.description)
            ),
        )
        vectors = self._embedding_provider.embed(tuple(passage.text for passage in passages))
        embeddings = MemoryEmbeddings.from_summary(
            summary,
            passages,
            vectors,
            model=self._embedding_provider.model,
            chunking_version=self._passage_chunker.version,
        )
        write_result = self._repository.save(summary, embeddings)
        return PersistSummaryResult(
            status=write_result.status,
            summary_id=summary.id,
            event_ids=tuple(event.id for event in summary.events),
            attempted_records=write_result.attempted_records,
            successful_records=write_result.successful_records,
        )
