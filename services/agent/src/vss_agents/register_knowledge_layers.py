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
"""Knowledge retrieval tool registration."""

from collections.abc import AsyncGenerator
import logging
from typing import Any
from typing import Literal

from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from lib.knowledge import RetrievalResult
from lib.knowledge import get_retriever

logger = logging.getLogger(__name__)

BackendType = Literal["frag_api", "es_caption"]

SUMMARIZE_SYSTEM_PROMPT = (
    "You are an analyst summarising retrieved knowledge-base excerpts. "
    "Produce a concise, faithful summary that answers the user's question "
    "strictly from the provided excerpts. Cite sources inline using the "
    "given citation tags. If the excerpts do not contain the answer, say so."
)


class KnowledgeRetrievalConfig(FunctionBaseConfig, name="knowledge_retrieval"):
    """Common fields + a `backend_config` block dispatched to the chosen backend.

    Backend-specific knobs live under `backend_config:` so the YAML cleanly
    separates routing-level fields (backend, collection, top_k) from the
    transport/connection details consumed by the adapter (rag_url, api_key,
    elasticsearch_url, …). Validation against the backend's own pydantic
    config happens at adapter construction time.
    """

    model_config = ConfigDict(extra="forbid")

    backend: BackendType = Field(
        default="frag_api",
        description=(
            "Knowledge backend: 'frag_api' = HTTP to a deployed FRAG rag-server; "
            "'es_caption' = BM25 over RT-VLM caption store in Elasticsearch."
        ),
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Default number of chunks to return.",
    )
    backend_config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Backend-specific config dict, validated against the chosen backend's "
            "pydantic model at boot. Shape depends on `backend`."
        ),
    )

    # ----- Summarization (applies to all backends) ---------------------------
    generate_summary: bool = Field(
        default=False,
        description=(
            "If true, run an LLM summarization pass over retrieved excerpts before returning. Requires `summary_model`."
        ),
    )
    summary_model: LLMRef | None = Field(
        default=None,
        description="LLM reference (from `llms:`) used when `generate_summary=true`.",
    )


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
            "Optional metadata filter dict. Accepted keys depend on the "
            "configured backend — see this tool's description for the exact "
            "shape and examples. Default: omit and let the backend search the "
            "whole collection with its default behaviour."
        ),
    )


def _setup_backend(config: KnowledgeRetrievalConfig, _builder: Builder) -> tuple[str, dict[str, Any]]:
    return config.backend, config.backend_config or {}


@register_function(config_type=KnowledgeRetrievalConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def knowledge_retrieval(config: KnowledgeRetrievalConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """Retrieve excerpts with citations from indexed knowledge sources."""
    backend, backend_config = _setup_backend(config, builder)
    retriever = await get_retriever(backend, backend_config)

    summary_llm: Any | None = None
    if config.generate_summary and config.summary_model:
        summary_llm = await builder.get_llm(config.summary_model, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        logger.info("knowledge_retrieval summary LLM resolved: %s", config.summary_model)

    logger.info(
        "knowledge_retrieval ready: backend=%s, default_top_k=%d, summarize=%s",
        config.backend,
        config.top_k,
        config.generate_summary,
    )

    async def _search(tool_input: KnowledgeRetrievalInput) -> str:
        # Per-query collection from the LLM, or empty -> the adapter
        # substitutes its own backend-configured default (e.g. FragApi's
        # `collection_name`) when the value is falsy.
        target_collection = tool_input.collection or ""
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

    base_description = (
        "Search indexed knowledge sources for passages relevant to the query. "
        "Returns excerpts with citation tags. Use this to ground responses in "
        f"cited source material rather than general knowledge. Returns up to "
        f"{config.top_k} excerpts by default."
    )
    backend_hint = getattr(retriever.__class__, "tool_description_hint", "") or ""
    description = f"{base_description}\n\n{backend_hint}".rstrip()

    yield FunctionInfo.create(
        single_fn=_search,
        description=description,
        input_schema=KnowledgeRetrievalInput,
        single_output_schema=str,
    )


async def _summarise_chunks(llm: Any, query: str, result: RetrievalResult) -> str:
    """Summarisation pass over retrieved excerpts using the provided LLM."""
    excerpts = "\n\n".join(
        f"{chunk.metadata.get('display_citation') or '[' + chunk.metadata.get('file_name', 'unknown') + ']'} {chunk.content.strip()}"
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
        file_name = chunk.metadata.get("file_name", "unknown")
        page_number = chunk.metadata.get("page_number")
        content_type = chunk.metadata.get("content_type")
        # Prefer the backend-supplied citation; fall back to the legacy
        # `<file_name>[, p.N]` shape so frag chunks render unchanged.
        citation = chunk.metadata.get("display_citation") or (
            f"{file_name}, p.{page_number}" if page_number and page_number > 0 else file_name
        )
        lines.append(f"--- Result {i} ---")
        lines.append(f"Source: {file_name}")
        if page_number and page_number > 0:
            lines.append(f"Page: {page_number}")
        lines.append(f"Citation: {citation}")
        if content_type:
            value = content_type.value if hasattr(content_type, "value") else content_type
            lines.append(f"Content Type: {value}")
        lines.append(f"Relevance Score: {chunk.score:.2f}")
        lines.append("")
        content = chunk.content
        if len(content) > 1500:
            content = content[:1500] + "... [truncated]"
        lines.append(content)
        lines.append("")
    return "\n".join(lines).rstrip()
