# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Recall VSS memory by stable handle, filters, full text, or vector similarity."""

from dataclasses import replace

from vss_unified_memory.application.models import MemoryQuery, MemorySearchResult, RecallMemoryResult
from vss_unified_memory.application.ports.embedding_provider import EmbeddingProvider
from vss_unified_memory.application.ports.memory_repository import MemoryRepository


class RecallMemoryUseCase:
    def __init__(self, repository: MemoryRepository, embedding_provider: EmbeddingProvider) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider

    def execute(self, query: MemoryQuery) -> RecallMemoryResult:
        if query.record_id is not None:
            memory = self._repository.get(query.record_id, query.record_type, query.include_related)
            results = () if memory is None else (MemorySearchResult(memory=memory),)
            return RecallMemoryResult(results=results)

        resolved_query = query
        if query.semantic:
            assert query.query_text is not None
            vectors = self._embedding_provider.embed((query.query_text,))
            if len(vectors) != 1:
                raise ValueError("embedding provider returned an unexpected vector count")
            resolved_query = replace(query, query_vector=vectors[0])

        return RecallMemoryResult(results=self._repository.search(resolved_query))
