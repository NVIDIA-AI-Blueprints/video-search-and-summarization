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
"""NAT bridge for the knowledge retrieval tool.

This is the only module in the agent service that imports `nat.*` for
knowledge retrieval. It exposes:

* `KnowledgeRetrievalConfig`   — flat schema (mirrors AIQ); fields are
                                 backend-specific and validated by a
                                 model_validator that warns when a field
                                 is set for a backend that doesn't use it.
* `knowledge_retrieval`        — async generator yielding the
                                 `search(query, top_k?, collection?, filters?)`
                                 NAT FunctionInfo.

The lib (`lib.knowledge.*`) is fully NAT-independent. This file resolves
NAT refs (LLMRef/EmbedderRef) into concrete URLs before handing the
adapter a plain config dict.
"""
from collections.abc import AsyncGenerator
import logging
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from nat.builder.builder import Builder
from nat.builder.context import Context
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import EmbedderRef, LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field, model_validator

from lib.knowledge import RetrievalResult, get_retriever

logger = logging.getLogger(__name__)

# Names match adapter modules under lib.knowledge.adapters.
BackendType = Literal["frag_api", "frag_lib"]

SUMMARIZE_SYSTEM_PROMPT = (
    "You are an analyst summarising retrieved knowledge-base excerpts. "
    "Produce a concise, faithful summary that answers the user's question "
    "strictly from the provided excerpts. Cite sources inline using the "
    "given citation tags. If the excerpts do not contain the answer, say so."
)


class KnowledgeRetrievalConfig(FunctionBaseConfig, name="knowledge_retrieval"):
    """Configuration for the knowledge retrieval tool.

    Flat schema (mirrors AIQ). `_setup_backend` picks the subset that
    applies to the selected backend; `model_validator` warns about fields
    set for an unused backend.
    """

    # ----- Common across all backends ----------------------------------------
    backend: BackendType = Field(
        default="frag_api",
        description=(
            "Knowledge backend: 'frag_api' = HTTP to a deployed FRAG rag-server, "
            "'frag_lib' = in-process via nvidia-rag>=2.4.0."
        ),
    )
    collection_name: str = Field(
        default="default",
        description="Default collection/index to search.",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Default number of chunks to return.",
    )

    # ----- Summarization (applies to all backends) ---------------------------
    generate_summary: bool = Field(
        default=False,
        description=(
            "If true, run an LLM summarization pass over retrieved excerpts "
            "before returning. Requires `summary_model`."
        ),
    )
    summary_model: LLMRef | None = Field(
        default=None,
        description="LLM reference (from `llms:`) used when `generate_summary=true`.",
    )

    # ----- frag_api ----------------------------------------------------------
    rag_url: str = Field(
        default="http://localhost:8081/v1",
        description="RAG query server URL (frag_api only).",
    )
    ingest_url: str = Field(
        default="http://localhost:8082/v1",
        description="RAG ingestion server URL (reserved; not consumed by retrieve).",
    )
    api_key: str | None = Field(
        default=None,
        description="Optional bearer token for the rag-server (frag_api only).",
    )
    timeout: int = Field(
        default=300,
        description="Request timeout in seconds (frag_api only).",
    )
    verify_ssl: bool = Field(
        default=True,
        description="Verify SSL certificates (frag_api only).",
    )

    # ----- frag_lib ----------------------------------------------------------
    llm: LLMRef | None = Field(
        default=None,
        description=(
            "LLM reference (from `llms:`) used by the in-process nvidia-rag "
            "pipeline (frag_lib only). Resolved via builder.get_llm_config()."
        ),
    )
    embedder: EmbedderRef | None = Field(
        default=None,
        description=(
            "Embedder reference (from `embedders:`) used by the in-process "
            "nvidia-rag pipeline (frag_lib only). Resolved via builder.get_embedder_config()."
        ),
    )
    milvus_uri: str | None = Field(
        default=None,
        description="Milvus URI (frag_lib only). Direct URI — no `retrievers:` ref.",
    )
    reranker_top_k: int = Field(
        default=10,
        description="Number of results after reranking (frag_lib only).",
    )
    enable_citations: bool | None = Field(
        default=None,
        description="Enable citations in nvidia_rag pipeline (frag_lib only).",
    )
    enable_guardrails: bool | None = Field(
        default=None,
        description="Enable guardrails in nvidia_rag pipeline (frag_lib only).",
    )
    enable_vlm_inference: bool | None = Field(
        default=None,
        description="Enable VLM inference in nvidia_rag pipeline (frag_lib only).",
    )

    @model_validator(mode="after")
    def validate_config(self) -> "KnowledgeRetrievalConfig":
        """Cross-field validation and warnings for unused fields."""
        if self.generate_summary and not self.summary_model:
            raise ValueError(
                "generate_summary=true requires summary_model to be set. "
                "Configure summary_model to reference an LLM from the llms: section."
            )

        if self.backend == "frag_api":
            if self.llm or self.embedder or self.milvus_uri:
                logger.warning(
                    "llm/embedder/milvus_uri are set but backend='frag_api' — ignored. "
                    "Switch backend to 'frag_lib' to use them."
                )
            if not self.verify_ssl:
                logger.warning(
                    "SSL verification disabled for frag_api. Use only in trusted environments."
                )
        elif self.backend == "frag_lib":
            if not (self.llm and self.embedder and self.milvus_uri):
                logger.warning(
                    "backend='frag_lib' typically requires llm, embedder, and milvus_uri "
                    "to be set."
                )
        return self


class KnowledgeRetrievalInput(BaseModel):
    """Tool input — the surface agents see.

    Matches the design spec: `search(query, top_k?, collection?, filters?)`.
    """

    query: str = Field(..., description="Natural language query for the knowledge base.")
    top_k: int | None = Field(
        default=None,
        description="Override the default number of chunks. Optional.",
    )
    collection: str | None = Field(
        default=None,
        description="Override the default collection. Optional.",
    )
    filters: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional metadata filter. Use shape "
            "{\"filter_expr\": 'content_metadata[\"<field>\"] == \"<value>\"'}. "
            "Example: {\"filter_expr\": 'content_metadata[\"filename\"] == \"Forklift.pdf\"'}. "
            "Filterable fields are declared in the collection's metadata_schema "
            "(typically filename, page_number). Omit to search the full collection."
        ),
    )


def _setup_backend(
    config: KnowledgeRetrievalConfig, builder: Builder
) -> tuple[str, dict[str, Any]]:
    """Translate the flat config into a backend-specific config dict.

    Resolves `LLMRef`/`EmbedderRef` references via the NAT builder so the
    adapter sees concrete URLs/model names — the adapter itself does not
    depend on NAT.
    """
    if config.backend == "frag_api":
        return "frag_api", {
            "rag_url": config.rag_url,
            "api_key": config.api_key,
            "timeout": config.timeout,
            "verify_ssl": config.verify_ssl,
        }

    if config.backend == "frag_lib":
        backend_config: dict[str, Any] = {
            "milvus_uri": config.milvus_uri,
            "reranker_top_k": config.reranker_top_k,
        }
        if config.llm:
            llm_cfg = builder.get_llm_config(config.llm)
            backend_config["llm_base_url"] = getattr(llm_cfg, "base_url", None)
            backend_config["llm_model_name"] = getattr(llm_cfg, "model_name", None)
        if config.embedder:
            emb_cfg = builder.get_embedder_config(config.embedder)
            backend_config["embedder_base_url"] = getattr(emb_cfg, "base_url", None)
            backend_config["embedder_model_name"] = getattr(emb_cfg, "model_name", None)
        for key in ("enable_citations", "enable_guardrails", "enable_vlm_inference"):
            value = getattr(config, key)
            if value is not None:
                backend_config[key] = value
        return "frag_lib", backend_config

    raise ValueError(f"Unknown backend: {config.backend}")


@register_function(config_type=KnowledgeRetrievalConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def knowledge_retrieval(
    config: KnowledgeRetrievalConfig, builder: Builder
) -> AsyncGenerator[FunctionInfo]:
    """Retrieve grounded excerpts with citations from indexed knowledge sources.

    Use this when the user's question references domain knowledge that
    isn't in the conversation — SOPs, manuals, ingested documents, prior
    incident reports. Returns excerpts with citation tags.
    """
    backend, backend_config = _setup_backend(config, builder)
    retriever = get_retriever(backend, backend_config)

    summary_llm: Any | None = None
    if config.generate_summary and config.summary_model:
        summary_llm = await builder.get_llm(
            config.summary_model, wrapper_type=LLMFrameworkEnum.LANGCHAIN
        )
        logger.info("knowledge_retrieval summary LLM resolved: %s", config.summary_model)

    logger.info(
        "knowledge_retrieval ready: backend=%s, default_collection=%s, default_top_k=%d, summarize=%s",
        config.backend,
        config.collection_name,
        config.top_k,
        config.generate_summary,
    )

    async def _search(tool_input: KnowledgeRetrievalInput) -> str:
        # Prefer per-conversation collection from NAT context, then explicit
        # tool input, then config default.
        try:
            ctx = Context.get()
            session_collection = ctx.conversation_id if ctx else None
        except Exception:
            session_collection = None
        target_collection = tool_input.collection or session_collection or config.collection_name
        top_k = tool_input.top_k or config.top_k

        result = await retriever.retrieve(
            query=tool_input.query,
            collection_name=target_collection,
            top_k=top_k,
            filters=tool_input.filters,
        )

        if config.generate_summary and result.success and result.chunks and summary_llm is not None:
            try:
                result.summary = await _summarise_chunks(summary_llm, tool_input.query, result)
            except Exception as e:
                logger.warning("Summary generation failed (returning unsummarised): %s", e)

        return _format_results(result, tool_input.query)

    yield FunctionInfo.create(
        single_fn=_search,
        description=(
            "Search indexed knowledge sources (SOPs, manuals, ingested documents) for "
            "passages relevant to the query. Returns excerpts with citation tags. "
            "Use this to ground responses in cited source material rather than general "
            f"knowledge. Returns up to {config.top_k} excerpts by default."
        ),
        input_schema=KnowledgeRetrievalInput,
        single_output_schema=str,
    )


async def _summarise_chunks(llm: Any, query: str, result: RetrievalResult) -> str:
    """Summarisation pass over retrieved excerpts using the provided LLM."""
    excerpts = "\n\n".join(
        f"{chunk.display_citation or '[' + chunk.file_name + ']'} {chunk.content.strip()}"
        for chunk in result.chunks
        if chunk.content
    )
    messages = [
        SystemMessage(content=SUMMARIZE_SYSTEM_PROMPT),
        HumanMessage(content=f"Question: {query}\n\nExcerpts:\n{excerpts}\n\nSummary:"),
    ]
    response = await llm.ainvoke(messages)
    return str(getattr(response, "content", response)).strip()


def _format_results(result: RetrievalResult, query: str) -> str:
    """Render a RetrievalResult as a human/agent-readable string."""
    if not result.success:
        return f"Knowledge retrieval failed: {result.error_message or 'unknown error'}\n\nQuery: {query!r}"
    if not result.chunks:
        return f"No relevant documents found for query: {query!r}"

    lines: list[str] = []
    if result.summary:
        lines.append("Summary:")
        lines.append(result.summary)
        lines.append("")
    lines.append(f"Found {len(result.chunks)} relevant excerpt(s):")
    lines.append("")

    for i, chunk in enumerate(result.chunks, start=1):
        citation = (
            f"{chunk.file_name}, p.{chunk.page_number}"
            if chunk.page_number and chunk.page_number > 0
            else chunk.file_name
        )
        lines.append(f"--- Result {i} ---")
        lines.append(f"Source: {chunk.file_name}")
        if chunk.page_number and chunk.page_number > 0:
            lines.append(f"Page: {chunk.page_number}")
        lines.append(f"Citation: {citation}")
        lines.append(f"Content Type: {chunk.content_type.value}")
        lines.append(f"Relevance Score: {chunk.score:.2f}")
        lines.append("")
        content = chunk.content
        if len(content) > 1500:
            content = content[:1500] + "... [truncated]"
        lines.append(content)
        lines.append("")
    return "\n".join(lines).rstrip()
