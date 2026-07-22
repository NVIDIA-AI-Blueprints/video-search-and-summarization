# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persist a VSS summary aggregate and its event embeddings."""

from vss_unified_memory.application.models import (
    LatencyMs,
    MemoryEmbeddings,
    PersistObservability,
    PersistSummaryResult,
)
from vss_unified_memory.application.observability import OperationTelemetry
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
        *,
        telemetry: OperationTelemetry | None = None,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._passage_chunker = passage_chunker
        self._telemetry = telemetry or OperationTelemetry()

    @property
    def telemetry(self) -> OperationTelemetry:
        return self._telemetry

    def execute(self, summary: Summary) -> PersistSummaryResult:
        with self._telemetry.measure("chunking"):
            summary_passages = self._passage_chunker.chunk(summary.id, summary.description)
            event_passages = tuple(
                passage
                for event in summary.events
                for passage in self._passage_chunker.chunk(event.id, event.description)
            )
            passages = (*summary_passages, *event_passages)

        with self._telemetry.measure("embedding"):
            vectors = self._embedding_provider.embed(tuple(passage.text for passage in passages))

        embeddings = MemoryEmbeddings.from_summary(
            summary,
            passages,
            vectors,
            model=self._embedding_provider.model,
            chunking_version=self._passage_chunker.version,
        )
        write_result = self._repository.save(summary, embeddings)

        summary_chars = len(summary.description)
        event_chars_total = sum(len(event.description) for event in summary.events)
        estimated_input_tokens = sum(passage.token_count for passage in passages)
        observability = PersistObservability(
            summary_id=summary.id,
            event_count=len(summary.events),
            summary_chars=summary_chars,
            event_chars_total=event_chars_total,
            summary_chunk_count=len(summary_passages),
            event_chunk_count=len(event_passages),
            total_chunk_count=len(passages),
            estimated_input_tokens=estimated_input_tokens,
            embedding_model=self._embedding_provider.model,
            embedding_vector_count=len(passages),
            es_attempted_records=write_result.attempted_records,
            es_successful_records=write_result.successful_records,
            latency_ms=LatencyMs(
                chunking=self._telemetry.get_latency("chunking"),
                embedding=self._telemetry.get_latency("embedding"),
                es_bulk_index=self._telemetry.get_latency("es_bulk_index"),
            ),
        )
        return PersistSummaryResult(
            status=write_result.status,
            summary_id=summary.id,
            event_ids=tuple(event.id for event in summary.events),
            attempted_records=write_result.attempted_records,
            successful_records=write_result.successful_records,
            observability=observability,
        )
