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
"""Retrieval-only LlamaIndex adapter over a ChromaDB persist dir.

Ingestion is out of scope; summarization stays at the bridge layer.
Requires `vss-agents[llama_index]`; imports are deferred.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from pydantic import BaseModel
from pydantic import Field

from lib.knowledge.base import BackendAdapter
from lib.knowledge.factory import register_adapter
from lib.knowledge.schema import Chunk
from lib.knowledge.schema import ContentType
from lib.knowledge.schema import RetrievalResult

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Cap on top_k; matches frag_api's vdb cap.
MAX_TOP_K = 100
# Widen candidate pool when a client-side predicate filter is supplied.
FILTER_OVERFETCH_MULTIPLIER = 4


class LlamaIndexConfig(BaseModel):
    persist_dir: str = Field(
        default_factory=lambda: os.environ.get("VSS_CHROMA_DIR", "/tmp/chroma_data"),
        description="ChromaDB persist directory. Defaults to `VSS_CHROMA_DIR`, then `/tmp/chroma_data`.",
    )
    collection_name: str = Field(
        default="default",
        description="Default Chroma collection; used when the LLM omits `collection`.",
    )
    embed_model: str = Field(
        default="nvidia/llama-nemotron-embed-vl-1b-v2",
        description="NVIDIA embedding model name.",
    )
    embed_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        description="Embedding NIM base URL.",
    )
    embed_api_key: str | None = Field(
        default=None,
        description="NVIDIA API key; falls back to NVIDIA_API_KEY env.",
    )


@register_adapter("llama_index", config_type=LlamaIndexConfig)
class LlamaIndexAdapter(BackendAdapter):
    tool_description_hint: ClassVar[str] = (
        "Filter pushdown is not yet supported for this backend. Pass only "
        "`query` and (optionally) `collection` and `top_k`."
    )

    def __init__(self, config: LlamaIndexConfig) -> None:
        super().__init__(config)
        try:
            import chromadb
            from llama_index.core import StorageContext
            from llama_index.core import VectorStoreIndex
            from llama_index.embeddings.nvidia import NVIDIAEmbedding
            from llama_index.vector_stores.chroma import ChromaVectorStore
        except ImportError as e:
            raise ImportError(
                "llama_index backend requires the `vss-agents[llama_index]` extra. "
                "Install via:\n"
                "  pip install 'vss-agents[llama_index]'\n"
                "Or pick a different backend (`frag_api` / `rag_lib` / `es_caption`)."
            ) from e

        # Auto-create so first retrieve() against empty path returns [] rather than crashing.
        os.makedirs(config.persist_dir, exist_ok=True)
        self._chroma_client = chromadb.PersistentClient(path=config.persist_dir)

        api_key = config.embed_api_key or os.environ.get("NVIDIA_API_KEY")
        self._embed_model = NVIDIAEmbedding(
            model=config.embed_model,
            base_url=config.embed_base_url,
            nvidia_api_key=api_key,
        )

        self._VectorStoreIndex = VectorStoreIndex
        self._ChromaVectorStore = ChromaVectorStore
        self._StorageContext = StorageContext
        self._index_cache: dict[str, Any] = {}

        self.collection_name: str = config.collection_name
        logger.info(
            "llama_index initialised: persist_dir=%s embed_model=%s default_collection=%s",
            config.persist_dir,
            config.embed_model,
            self.collection_name,
        )

    def _index_for_collection(self, collection_name: str) -> Any:
        index = self._index_cache.get(collection_name)
        if index is not None:
            return index
        chroma_collection = self._chroma_client.get_or_create_collection(collection_name)
        vector_store = self._ChromaVectorStore(chroma_collection=chroma_collection)
        storage_context = self._StorageContext.from_defaults(vector_store=vector_store)
        index = self._VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            embed_model=self._embed_model,
            storage_context=storage_context,
        )
        self._index_cache[collection_name] = index
        return index

    async def retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        filters: Callable[[Chunk], bool] | dict[str, Any] | None = None,
    ) -> RetrievalResult:
        target_collection = collection_name or self.collection_name
        effective_top_k = max(1, min(top_k, MAX_TOP_K))
        # Over-fetch on predicate filter so post-filter result still ~= top_k.
        fetch_k = (
            min(effective_top_k * FILTER_OVERFETCH_MULTIPLIER, MAX_TOP_K) if callable(filters) else effective_top_k
        )
        try:
            index = self._index_for_collection(target_collection)
            retriever = index.as_retriever(similarity_top_k=fetch_k)
            nodes = await retriever.aretrieve(query)
        except Exception as e:
            logger.exception("llama_index retrieve failed")
            return _failure(query, self.backend_name, f"LlamaIndex retrieval failed: {str(e)[:100]}")

        chunks = [_node_to_chunk(n, idx=i) for i, n in enumerate(nodes)]
        chunks = [c for c in chunks if c is not None]

        if callable(filters):
            chunks = [c for c in chunks if filters(c)]
        chunks = chunks[:effective_top_k]

        return RetrievalResult(
            chunks=chunks,
            query=query,
            backend=self.backend_name,
            success=True,
            total_tokens=sum(len(c.content.split()) for c in chunks),
        )

    async def health_check(self) -> bool:
        try:
            self._chroma_client.list_collections()
        except Exception as e:
            logger.warning("llama_index health check failed: %s", e)
            return False
        return True


def _failure(query: str, backend: str, message: str) -> RetrievalResult:
    logger.error("%s: %s", backend, message)
    return RetrievalResult(
        chunks=[],
        query=query,
        backend=backend,
        success=False,
        error_message=message,
    )


def _node_to_chunk(node: Any, idx: int = 0) -> Chunk | None:
    try:
        inner = node.node
        content = inner.text or ""
        # LlamaIndex's ChromaVectorStore returns a similarity-like score
        # (exp(-distance) for cosine), so we passthrough — no `1 - x` flip.
        # LC's path needs inversion because Chroma exposes raw distance there.
        score = float(getattr(node, "score", 0.0) or 0.0)
        meta = dict(inner.metadata or {})
    except AttributeError:
        return None

    file_name = meta.get("file_name") or meta.get("file_path") or "unknown"
    page_label = meta.get("page_label")
    page_number: int | None = None
    if isinstance(page_label, int) and page_label > 0:
        page_number = page_label
    elif isinstance(page_label, str) and page_label.isdigit():
        as_int = int(page_label)
        if as_int > 0:
            page_number = as_int

    chunk_id = getattr(inner, "node_id", None) or f"{file_name}_{idx}"
    display_citation = f"[{file_name}, p.{page_number}]" if page_number else f"[{file_name}]"

    return Chunk(
        chunk_id=str(chunk_id),
        content=content,
        score=score,
        metadata={
            "file_name": file_name,
            "page_number": page_number,
            "display_citation": display_citation,
            "content_type": ContentType.TEXT,
            "document_id": meta.get("file_path") or file_name,
            "source_metadata": meta,
        },
    )
