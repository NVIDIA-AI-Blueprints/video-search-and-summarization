# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persistence and search boundary for memory entities."""

from typing import Protocol

from vss_unified_memory.application.models import (
    MemoryEmbeddings,
    MemoryQuery,
    MemorySearchResult,
    RepositoryWriteResult,
)
from vss_unified_memory.domain.models import MemoryEntity, RecordType


class MemoryRepository(Protocol):
    def save(
        self,
        memory: MemoryEntity,
        embeddings: MemoryEmbeddings | None = None,
    ) -> RepositoryWriteResult: ...

    def get(
        self,
        record_id: str,
        record_type: RecordType | None = None,
        include_related: bool = False,
    ) -> MemoryEntity | None: ...

    def search(self, query: MemoryQuery) -> tuple[MemorySearchResult, ...]: ...
