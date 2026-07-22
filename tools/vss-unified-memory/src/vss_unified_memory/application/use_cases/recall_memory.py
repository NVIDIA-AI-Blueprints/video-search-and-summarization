# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Recall VSS memory by stable handle, filters, full text, or vector similarity."""

from dataclasses import replace

from vss_unified_memory.application.models import (
    LatencyMs,
    MemoryQuery,
    MemorySearchResult,
    RecallMemoryResult,
    RecallObservability,
)
from vss_unified_memory.application.observability import (
    OperationTelemetry,
    estimate_tokens_from_chars,
    query_text_observability_fields,
)
from vss_unified_memory.application.ports.embedding_provider import EmbeddingProvider
from vss_unified_memory.application.ports.memory_repository import MemoryRepository
from vss_unified_memory.domain.models import Event, Summary


def _count_returned_entities(results: tuple[MemorySearchResult, ...]) -> tuple[int, int, int]:
    summary_count = 0
    event_count = 0
    returned_chars = 0
    for item in results:
        memory = item.memory
        if isinstance(memory, Summary):
            summary_count += 1
            returned_chars += len(memory.description)
            event_count += len(memory.events)
            returned_chars += sum(len(event.description) for event in memory.events)
        elif isinstance(memory, Event):
            event_count += 1
            returned_chars += len(memory.description)
    return summary_count, event_count, returned_chars


class RecallMemoryUseCase:
    def __init__(
        self,
        repository: MemoryRepository,
        embedding_provider: EmbeddingProvider,
        *,
        telemetry: OperationTelemetry | None = None,
        include_query_preview: bool = False,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._telemetry = telemetry or OperationTelemetry()
        self._include_query_preview = include_query_preview

    @property
    def telemetry(self) -> OperationTelemetry:
        return self._telemetry

    def execute(self, query: MemoryQuery) -> RecallMemoryResult:
        if query.record_id is not None:
            memory = self._repository.get(query.record_id, query.record_type, query.include_related)
            results = () if memory is None else (MemorySearchResult(memory=memory),)
            return RecallMemoryResult(
                results=results,
                observability=self._build_observability(query, results, operation="get"),
            )

        resolved_query = query
        if query.semantic:
            assert query.query_text is not None
            with self._telemetry.measure("query_embedding"):
                vectors = self._embedding_provider.embed((query.query_text,))
            if len(vectors) != 1:
                raise ValueError("embedding provider returned an unexpected vector count")
            resolved_query = replace(query, query_vector=vectors[0])

        search_results: tuple[MemorySearchResult, ...] = self._repository.search(resolved_query)
        return RecallMemoryResult(
            results=search_results,
            observability=self._build_observability(resolved_query, search_results, operation="search"),
        )

    def _build_observability(
        self,
        query: MemoryQuery,
        results: tuple[MemorySearchResult, ...],
        *,
        operation: str,
    ) -> RecallObservability:
        summary_count, event_count, returned_chars = _count_returned_entities(results)
        query_fields = query_text_observability_fields(
            query.query_text,
            include_preview=self._include_query_preview,
        )
        candidate_summary_ids = self._telemetry.candidate_summary_ids or None
        if candidate_summary_ids == ():
            candidate_summary_ids = None
        return RecallObservability(
            operation=operation,
            semantic=query.semantic,
            record_id=query.record_id,
            record_type=query.record_type,
            include_related=query.include_related if query.record_id is not None else None,
            limit=query.limit if query.record_id is None else None,
            query_text_chars=query_fields.get("query_text_chars"),  # type: ignore[arg-type]
            query_text_hash=query_fields.get("query_text_hash"),  # type: ignore[arg-type]
            query_text_preview=query_fields.get("query_text_preview"),  # type: ignore[arg-type]
            candidate_summary_ids=candidate_summary_ids,
            returned_summary_count=summary_count,
            returned_event_count=event_count,
            returned_chars=returned_chars,
            estimated_returned_tokens=estimate_tokens_from_chars(returned_chars),
            latency_ms=LatencyMs(
                query_embedding=self._telemetry.get_latency("query_embedding"),
                es_exact_get=self._telemetry.get_latency("es_exact_get"),
                es_lexical_search=self._telemetry.get_latency("es_lexical_search"),
                es_summary_knn=self._telemetry.get_latency("es_summary_knn"),
                es_event_knn=self._telemetry.get_latency("es_event_knn"),
                es_parent_summary_hydration=self._telemetry.get_latency("es_parent_summary_hydration"),
                es_related_event_lookup=self._telemetry.get_latency("es_related_event_lookup"),
            ),
        )
