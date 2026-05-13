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
"""In-process adapter using the `nvidia-rag` library.

Milvus-backed; same `filter_expr` shape as `frag_api`. Choose `rag_lib` to
run the full RAG Blueprint pipeline in-process. Requires `vss-agents[rag_lib]`;
imports are deferred.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from pydantic import BaseModel
from pydantic import Field

from lib.knowledge.adapters.frag_api import _failure
from lib.knowledge.adapters.frag_api import _filters_to_expr
from lib.knowledge.adapters.frag_api import _normalise_search_result
from lib.knowledge.base import BackendAdapter
from lib.knowledge.factory import register_adapter
from lib.knowledge.schema import Chunk
from lib.knowledge.schema import RetrievalResult

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class RagLibConfig(BaseModel):
    """Caller-supplied knobs that override `NvidiaRAGConfig` defaults.

    Each field maps to a section of the upstream config — when unset, we
    leave the SDK default in place (which itself reads from
    `NVIDIA_RAG_*` env vars). Only the bits that vary per deployment.
    """

    llm_base_url: str | None = Field(default=None, description="LLM (NIM) `server_url` override.")
    llm_model_name: str | None = Field(default=None, description="LLM model name override.")
    embedder_base_url: str | None = Field(default=None, description="Embedder (NIM) `server_url` override.")
    embedder_model_name: str | None = Field(default=None, description="Embedder model name override.")
    milvus_uri: str | None = Field(default=None, description="Milvus `vector_store.url` override.")
    collection_name: str = Field(
        default="default",
        description=(
            "Default Milvus collection name; used when the caller passes an empty `collection_name` to `retrieve()`."
        ),
    )
    enable_citations: bool = Field(default=True)
    enable_guardrails: bool = Field(default=False)
    reranker_top_k: int = Field(default=10, description="Default reranker top_k when caller doesn't override.")


@register_adapter("rag_lib", config_type=RagLibConfig)
class RagLibAdapter(BackendAdapter):
    # Identical filter shape to frag_api (both backed by Milvus), so the
    # LLM-facing hint is the same.
    tool_description_hint: ClassVar[str] = (
        "Pass `filters` only when the user explicitly names a document; never "
        "invent a filename. Shape:\n"
        '  filters={"filter_expr": \'content_metadata["filename"] == "<name>"\'}'
    )

    def __init__(self, config: RagLibConfig) -> None:
        super().__init__(config)
        try:
            from nvidia_rag.rag_server.main import NvidiaRAG
            from nvidia_rag.utils.configuration import NvidiaRAGConfig
        except ImportError as e:
            raise ImportError(
                "rag_lib backend requires the `nvidia-rag>=2.4.0` package. "
                "Install via:\n"
                "  pip install 'vss-agents[rag_lib]'\n"
                "Or switch to the `frag_api` backend if you have a deployed rag-server."
            ) from e

        rag_config = NvidiaRAGConfig()

        if config.llm_base_url:
            updates: dict[str, Any] = {"server_url": config.llm_base_url}
            if config.llm_model_name:
                updates["model_name"] = config.llm_model_name
            rag_config.llm = rag_config.llm.model_copy(update=updates)

        if config.embedder_base_url:
            updates = {"server_url": config.embedder_base_url}
            if config.embedder_model_name:
                updates["model_name"] = config.embedder_model_name
            rag_config.embeddings = rag_config.embeddings.model_copy(update=updates)

        if config.milvus_uri:
            rag_config.vector_store.url = config.milvus_uri

        rag_config.enable_citations = config.enable_citations
        rag_config.enable_guardrails = config.enable_guardrails

        self._rag_client = NvidiaRAG(config=rag_config)
        self.collection_name: str = config.collection_name
        self._reranker_top_k: int = config.reranker_top_k
        logger.info(
            "rag_lib initialised: llm=%s embedder=%s milvus=%s collection_name=%s",
            config.llm_base_url,
            config.embedder_base_url,
            config.milvus_uri,
            self.collection_name,
        )

    async def retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        filters: Callable[[Chunk], bool] | dict[str, Any] | None = None,
    ) -> RetrievalResult:
        # Empty caller-supplied collection -> fall back to the configured default.
        target_collection = collection_name or self.collection_name
        search_kwargs: dict[str, Any] = {
            "query": query,
            "collection_names": [target_collection],
            "reranker_top_k": top_k or self._reranker_top_k,
        }
        # Same dict→Milvus expr translation as frag_api so the LLM filter
        # contract is interchangeable between the two.
        filter_expr = _filters_to_expr(filters)
        if filter_expr:
            search_kwargs["filter_expr"] = filter_expr

        try:
            citations = await self._rag_client.search(**search_kwargs)
        except Exception as e:
            logger.exception("rag_lib search failed")
            return _failure(query, self.backend_name, f"In-process RAG search failed: {str(e)[:100]}")

        chunks = _citations_to_chunks(citations)

        # Predicate filters always applied client-side.
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
        # `nvidia_rag.NvidiaRAG` doesn't expose a ping endpoint — successful
        # construction is the strongest signal we have.
        return self._rag_client is not None


def _citations_to_chunks(citations: Any) -> list[Chunk]:
    """Convert nvidia_rag `Citations` into our `Chunk` list.

    `Citations.results` is a list of `SourceResult`-shaped objects whose
    fields mirror the rag-server `/search` response, so we route through
    the same normaliser that `frag_api` uses by dumping each entry to a
    dict first.
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
