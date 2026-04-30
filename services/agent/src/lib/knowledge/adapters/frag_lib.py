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
"""Foundational RAG adapter — in-process via the `nvidia-rag` library.

Runs the full NVIDIA RAG Blueprint pipeline (rerank, query rewrite,
reflection, guardrails, citations) inside the agent process. Requires
the optional dependency `nvidia-rag>=2.4.0` (install via the
`vss_agents[nvidia_rag]` extra).

Reference: NeMo-Agent-Toolkit nvidia_nat_rag/client.py.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from lib.knowledge.adapters.frag_api import _failure, _normalise_search_result
from lib.knowledge.base import BackendAdapter
from lib.knowledge.factory import register_adapter
from lib.knowledge.schema import Chunk, RetrievalResult

logger = logging.getLogger(__name__)


@register_adapter("frag_lib")
class FragLibAdapter(BackendAdapter):
    """In-process adapter using `nvidia-rag>=2.4.0`."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        try:
            from nvidia_rag.rag_server.main import NvidiaRAG
            from nvidia_rag.utils.configuration import NvidiaRAGConfig
        except ImportError as e:
            raise ImportError(
                "frag_lib requires the 'nvidia-rag>=2.4.0' package. Install it via "
                "`pip install vss_agents[nvidia_rag]`, or switch to the 'frag_api' backend "
                "and point at a deployed rag-server."
            ) from e

        rag_config = NvidiaRAGConfig()

        # LLM endpoint (NIM)
        llm_url = self.config.get("llm_base_url")
        llm_model = self.config.get("llm_model_name")
        if llm_url:
            updates: dict[str, Any] = {"server_url": llm_url}
            if llm_model:
                updates["model_name"] = llm_model
            rag_config.llm = rag_config.llm.model_copy(update=updates)

        # Embedder endpoint (NIM)
        embedder_url = self.config.get("embedder_base_url")
        embedder_model = self.config.get("embedder_model_name")
        if embedder_url:
            updates = {"server_url": embedder_url}
            if embedder_model:
                updates["model_name"] = embedder_model
            rag_config.embeddings = rag_config.embeddings.model_copy(update=updates)

        # Vector store (Milvus)
        milvus_uri = self.config.get("milvus_uri")
        if milvus_uri:
            rag_config.vector_store.url = milvus_uri

        # Pipeline toggles — preserve NvidiaRAGConfig defaults when unset.
        for key in ("enable_citations", "enable_guardrails", "enable_vlm_inference"):
            if key in self.config and self.config[key] is not None:
                setattr(rag_config, key, self.config[key])

        self._rag_client = NvidiaRAG(config=rag_config)
        self._reranker_top_k: int = self.config.get("reranker_top_k", 10)
        logger.info(
            "frag_lib initialised: llm=%s, embedder=%s, milvus=%s",
            llm_url,
            embedder_url,
            milvus_uri,
        )

    @property
    def backend_name(self) -> str:
        return "frag_lib"

    async def retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        filters: Callable[[Chunk], bool] | dict[str, Any] | None = None,
    ) -> RetrievalResult:
        try:
            citations = await self._rag_client.search(
                query=query,
                collection_names=[collection_name],
                reranker_top_k=top_k or self._reranker_top_k,
            )
        except Exception as e:
            logger.exception("frag_lib search failed")
            return _failure(query, self.backend_name, f"In-process RAG search failed: {str(e)[:100]}")

        chunks = _citations_to_chunks(citations)

        # Predicate filters applied client-side; nvidia-rag filter_expr
        # would have been pushed at construction time if supported.
        if callable(filters):
            chunks = [c for c in chunks if filters(c)]

        return RetrievalResult(
            chunks=chunks,
            query=query,
            backend=self.backend_name,
            success=True,
            total_tokens=sum(len(c.content.split()) for c in chunks),
        )

    async def health_check(self) -> bool:
        return self._rag_client is not None


def _citations_to_chunks(citations: Any) -> list[Chunk]:
    """Convert nvidia_rag Citations into our Chunk list.

    `Citations.results` is a list of `SourceResult`-shaped objects whose
    fields mirror the rag-server /search response, so we route through the
    same normaliser by dumping each entry to a dict.
    """
    chunks: list[Chunk] = []
    if citations is None:
        return chunks
    results = getattr(citations, "results", None) or []
    for i, r in enumerate(results):
        if hasattr(r, "model_dump"):
            payload = r.model_dump()
        elif isinstance(r, dict):
            payload = r
        else:
            payload = {"content": str(r)}
        chunk = _normalise_search_result(payload, idx=i)
        if chunk:
            chunks.append(chunk)
    return chunks
