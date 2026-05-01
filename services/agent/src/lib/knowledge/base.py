# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Backend adapter contract.

Implementers subclass `BackendAdapter` and register via `@register_adapter`.
Each adapter normalises its native response into the universal `Chunk` /
`RetrievalResult` schema so the search tool surface stays stable across
backends.
"""
from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from .schema import Chunk
from .schema import RetrievalResult

# A filter is a predicate over a Chunk: True keeps, False rejects.
ChunkFilter = Callable[[Chunk], bool]


class BackendAdapter(ABC):
    """Pluggable retrieval backend."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = config or {}

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Name used in the registry and emitted on RetrievalResult.backend."""

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        filters: ChunkFilter | dict[str, Any] | None = None,
    ) -> RetrievalResult:
        """Retrieve top_k chunks for `query` from `collection_name`.

        `filters` accepts either:
          * a predicate `Chunk -> bool` (post-filter, applied after retrieve)
          * a dict the backend may translate into a native server-side filter
            (e.g. Milvus filter_expr)

        Adapters that cannot push filters down should still honour the
        predicate form client-side.
        """

    async def summarize(
        self,
        _query: str,
        chunks: list[Chunk],
        _llm: Any | None = None,
    ) -> str:
        """Optional: backend-specific summarization of retrieved chunks.

        Default implementation concatenates chunk contents. Backends with
        their own summarization pipeline (e.g. nvidia-rag's `generate`) can
        override. The NAT tool layer can also drive summarization via an
        externally-resolved LLM, in which case adapters need not override.
        """
        return "\n\n".join(c.content for c in chunks if c.content)

    async def health_check(self) -> bool:
        """Optional liveness probe. Default assumes healthy."""
        return True
