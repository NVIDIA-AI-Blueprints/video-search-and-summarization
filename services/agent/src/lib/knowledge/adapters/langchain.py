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
"""In-process adapter using LangChain over a ChromaDB-backed vector store.

Same shape as the `llama_index` adapter — retrieval-only, embedded
Chroma persist dir, NVIDIA embedding NIM via LangChain's NVIDIA
integration. The difference is the framework: LangChain's vector store
abstraction (`langchain_chroma.Chroma`) and retriever surface
(`vectorstore.as_retriever()`).

Ingestion is out of scope — populate Chroma out-of-band. Summarization
stays at the bridge layer (`summary_model` in `KnowledgeRetrievalConfig`),
same as every other adapter.

Requires the optional dependency group `vss-agents[langchain]`. Imports
are deferred to adapter construction so the agent image stays small for
deployments that don't use this backend.
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


class LangChainConfig(BaseModel):
    """Caller-supplied knobs for the LangChain/Chroma adapter.

    Mirrors `LlamaIndexConfig` field-for-field so operators can swap
    `backend: llama_index` ↔ `backend: langchain` without changing the
    rest of the config.
    """

    persist_dir: str = Field(
        default_factory=lambda: os.environ.get("VSS_CHROMA_DIR", "/tmp/chroma_data"),
        description=(
            "ChromaDB persist directory (embedded mode). Defaults to the "
            "`VSS_CHROMA_DIR` env var, then `/tmp/chroma_data` if unset."
        ),
    )

    collection_name: str = Field(
        default="default",
        description="Default Chroma collection; substituted when the LLM omits `collection`.",
    )

    embed_model: str = Field(
        default="nvidia/llama-nemotron-embed-vl-1b-v2",
        description="NVIDIA embedding model name (passed to NVIDIAEmbeddings).",
    )
    embed_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        description="Embedding NIM base URL.",
    )
    embed_api_key: str | None = Field(
        default=None,
        description="NVIDIA API key. Falls back to NVIDIA_API_KEY env if unset.",
    )


@register_adapter("langchain", config_type=LangChainConfig)
class LangChainAdapter(BackendAdapter):
    # Same as the llama_index hint: Chroma doesn't expose Milvus-style
    # filter_expr at this layer; v1 doesn't push filters through.
    tool_description_hint: ClassVar[str] = (
        "Filter pushdown is not yet supported for this backend. Pass only "
        "`query` and (optionally) `collection` and `top_k`."
    )

    def __init__(self, config: LangChainConfig) -> None:
        super().__init__(config)
        # Deferred imports — keep langchain-chroma out of the default image.
        try:
            from langchain_chroma import Chroma
            from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings
        except ImportError as e:
            raise ImportError(
                "langchain backend requires the `vss-agents[langchain]` extra. "
                "Install via:\n"
                "  pip install 'vss-agents[langchain]'\n"
                "Or pick a different backend (`frag_api` / `llama_index` / `es_caption`)."
            ) from e

        # Auto-create persist dir on first boot so a clean host doesn't
        # crash before ingestion has happened.
        os.makedirs(config.persist_dir, exist_ok=True)

        api_key = config.embed_api_key or os.environ.get("NVIDIA_API_KEY")
        self._embed_model = NVIDIAEmbeddings(
            model=config.embed_model,
            base_url=config.embed_base_url,
            api_key=api_key,
        )

        # Cache one Chroma vector store per collection (lazy build).
        self._Chroma = Chroma
        self._persist_dir: str = config.persist_dir
        self._vs_cache: dict[str, Any] = {}

        self.collection_name: str = config.collection_name
        logger.info(
            "langchain initialised: persist_dir=%s embed_model=%s default_collection=%s",
            config.persist_dir,
            config.embed_model,
            self.collection_name,
        )

    def _vectorstore_for_collection(self, collection_name: str) -> Any:
        """Return a `langchain_chroma.Chroma` for the named collection, cached."""
        vs = self._vs_cache.get(collection_name)
        if vs is not None:
            return vs
        vs = self._Chroma(
            collection_name=collection_name,
            embedding_function=self._embed_model,
            persist_directory=self._persist_dir,
        )
        self._vs_cache[collection_name] = vs
        return vs

    async def retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        filters: Callable[[Chunk], bool] | dict[str, Any] | None = None,
    ) -> RetrievalResult:
        target_collection = collection_name or self.collection_name
        try:
            vs = self._vectorstore_for_collection(target_collection)
            # LangChain's similarity_search_with_score returns [(Document, score), ...]
            # which is the shape we need to fill Chunk.score. .invoke() on the
            # retriever drops the score, so we go direct to the vector store.
            results = vs.similarity_search_with_score(query, k=top_k)
        except Exception as e:
            logger.exception("langchain retrieve failed")
            return _failure(query, self.backend_name, f"LangChain retrieval failed: {str(e)[:100]}")

        chunks = [_doc_to_chunk(doc, score, idx=i) for i, (doc, score) in enumerate(results)]
        chunks = [c for c in chunks if c is not None]

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
        # No native ping; "can I open the persist dir?" is the strongest signal.
        try:
            # Touching the Chroma class as a smoke test — instantiation already
            # happened in __init__ for the default collection.
            return self._Chroma is not None and os.path.isdir(self._persist_dir)
        except Exception as e:
            logger.warning("langchain health check failed: %s", e)
            return False


def _failure(query: str, backend: str, message: str) -> RetrievalResult:
    logger.error("%s: %s", backend, message)
    return RetrievalResult(
        chunks=[],
        query=query,
        backend=backend,
        success=False,
        error_message=message,
    )


def _doc_to_chunk(doc: Any, score: float, idx: int = 0) -> Chunk | None:
    """Convert a LangChain `Document` + similarity score to our `Chunk` schema.

    LangChain `Document` shape:
        doc.page_content : str
        doc.metadata     : dict (whatever the ingestor stamped)

    Chroma reports distance, not similarity; for cosine distance,
    similarity ≈ 1 - distance. Normalise so higher = better, matching
    our other adapters.
    """
    try:
        content = doc.page_content or ""
        meta = dict(doc.metadata or {})
    except AttributeError:
        return None

    # Cosine distance → similarity. Other metrics would need different math;
    # we treat anything > 1 as already-similarity and pass through.
    sim = float(1.0 - score) if 0.0 <= float(score) <= 2.0 else float(score)

    file_name = meta.get("file_name") or meta.get("source") or "unknown"
    page_label = meta.get("page") or meta.get("page_number") or meta.get("page_label")
    page_number: int | None = None
    if isinstance(page_label, int) and page_label > 0:
        page_number = page_label
    elif isinstance(page_label, str) and page_label.isdigit():
        as_int = int(page_label)
        if as_int > 0:
            page_number = as_int

    chunk_id = meta.get("id") or f"{file_name}_{idx}"
    display_citation = f"[{file_name}, p.{page_number}]" if page_number else f"[{file_name}]"

    return Chunk(
        chunk_id=str(chunk_id),
        content=content,
        score=sim,
        metadata={
            "file_name": file_name,
            "page_number": page_number,
            "display_citation": display_citation,
            "content_type": ContentType.TEXT,
            "document_id": meta.get("source") or file_name,
            "source_metadata": meta,
        },
    )
