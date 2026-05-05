# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Enterprise RAG tool (Blueprint integration) for VSS 3.0.

This tool provides a minimal wrapper around the NVIDIA RAG Blueprint search API
via `nvidia_rag.rag_server.main.NvidiaRAG`.

Design goals:
- Keep this tool purely retrieval: input query -> returned context string.
- Make it safe to call from other tools (e.g., HITL-enabled LVS tool) without
  requiring the top-level agent to have direct access to it.
- Keep configuration explicit (collections, vdb endpoint, embedding + reranker endpoints).
"""

import asyncio
from collections.abc import AsyncGenerator
import json
import logging
import os
from typing import Any
from typing import Literal

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    """Parse an int environment variable safely."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid int for %s=%r; using default %s", name, raw, default)
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r; using default %s", name, raw, default)
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _json_list_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = json.loads(raw)
        if isinstance(val, list) and all(isinstance(x, str) for x in val):
            return val
        logger.warning("Invalid JSON list for %s=%r; using default %r", name, raw, default)
        return default
    except Exception:
        logger.warning("Invalid JSON for %s=%r; using default %r", name, raw, default)
        return default


class EnterpriseRAGConfig(FunctionBaseConfig, name="enterprise_rag"):
    """Configuration for the Enterprise RAG (Blueprint) retrieval tool."""

    # Collection(s) to query; allow comma-separated for convenience.
    collection_names: str = Field(
        default_factory=lambda: os.getenv("ENTERPRISE_RAG_COLLECTION_NAMES", ""),
        description=(
            'Comma-separated collection names in the Enterprise RAG Blueprint (e.g., "policies,manuals"). '
            "If empty, this tool returns an empty context string."
        ),
    )

    # Vector DB endpoint (Milvus) the RAG server should query.
    vdb_endpoint: str = Field(
        default_factory=lambda: os.getenv("ENTERPRISE_RAG_VDB_ENDPOINT", ""),
        description=(
            'Vector DB endpoint/URI (e.g., "http://milvus:19530" or "tcp://milvus:19530"). '
            "If empty, this tool returns an empty context string."
        ),
    )

    # Embedding model + endpoint.
    embedding_model: str = Field(
        default_factory=lambda: os.getenv("ENTERPRISE_RAG_EMBEDDING_MODEL", "nvidia/llama-3.2-nv-embedqa-1b-v2"),
        description="Embedding model name to use for query embedding.",
    )
    embedding_base_url: str = Field(
        default_factory=lambda: os.getenv("ENTERPRISE_RAG_EMBEDDING_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        description="Embedding service base URL (commonly the NVIDIA Integrate API base URL).",
    )
    embedding_endpoint: str | None = Field(
        default_factory=lambda: os.getenv("ENTERPRISE_RAG_EMBEDDING_ENDPOINT") or None,
        description=(
            "Optional full embedding endpoint. If omitted and embedding_base_url ends with '/v1', "
            "this tool will use '{embedding_base_url}/embeddings'. Otherwise it will use embedding_base_url directly."
        ),
    )

    # Optional reranker model + endpoint.
    reranker_model: str | None = Field(
        default_factory=lambda: os.getenv("ENTERPRISE_RAG_RERANKER_MODEL") or None,
        description="Optional reranker model name. If set, reranking is enabled.",
    )
    reranker_endpoint: str | None = Field(
        default_factory=lambda: os.getenv("ENTERPRISE_RAG_RERANKER_ENDPOINT") or None,
        description="Optional reranker endpoint URL. Required if reranker_model is set.",
    )

    enable_query_rewriting: bool = Field(
        default_factory=lambda: _bool_env("ENTERPRISE_RAG_ENABLE_QUERY_REWRITING", True),
        description="Enable query rewriting in the RAG Blueprint search call.",
    )

    filter_expr: str = Field(
        default_factory=lambda: os.getenv("ENTERPRISE_RAG_FILTER_EXPR", ""),
        description="Optional filter expression for Enterprise RAG search.",
    )

    # Defaults used when the caller doesn't provide overrides.
    default_vdb_top_k: int = Field(
        default_factory=lambda: _int_env("ENTERPRISE_RAG_VDB_TOP_K", 10),
        ge=1,
        description="Default top-k for vector DB retrieval.",
    )
    default_reranker_top_k: int = Field(
        default_factory=lambda: _int_env("ENTERPRISE_RAG_RERANKER_TOP_K", 5),
        ge=1,
        description="Default top-k for reranking.",
    )

    # Whether to enable reranking for remote /v1/generate. If env var is unset, infer from model/endpoint presence.
    enable_reranker: bool = Field(
        default_factory=lambda: _bool_env(
            "ENTERPRISE_RAG_ENABLE_RERANKER",
            bool(os.getenv("ENTERPRISE_RAG_RERANKER_MODEL") and os.getenv("ENTERPRISE_RAG_RERANKER_ENDPOINT")),
        ),
        description="Enable reranker in the Enterprise RAG call.",
    )

    timeout_sec: int = Field(
        default=15,
        ge=1,
        description="Timeout in seconds for the Enterprise RAG search call.",
    )

    model_config = ConfigDict(extra="forbid")


class EnterpriseRAGInput(BaseModel):
    """Input schema for an Enterprise RAG query."""

    query: str = Field(..., description="The enterprise query to search for.", min_length=1)
    vdb_top_k: int | None = Field(default=None, ge=1, description="Optional override for vector DB top-k.")
    reranker_top_k: int | None = Field(default=None, ge=1, description="Optional override for reranker top-k.")

    model_config = ConfigDict(extra="forbid")


class EnterpriseRAGOutput(BaseModel):
    """Structured output for Enterprise RAG retrieval."""

    status: Literal[
        "ok",
        "no_results",
        "not_configured",
        "timeout",
        "unreachable",
        "error",
        "dependency_missing",
    ] = Field(..., description="Outcome status of the retrieval attempt.")
    query: str = Field(default="", description="The query that was attempted (after trimming).")
    context: str = Field(default="", description="Retrieved context string (may be empty).")
    error: str = Field(default="", description="Error details when status is not ok/no_results.")

    model_config = ConfigDict(extra="forbid")


def _parse_search_results(search_results: Any) -> str:
    """
    Extract a single context string from NvidiaRAG search results.

    NvidiaRAG returns an object with a `.results` attribute; each item typically has `.content`.
    """

    doc_list: list[str] = []
    results = getattr(search_results, "results", None)
    if not results:
        return ""

    for result in results:
        content = getattr(result, "content", "")
        if content:
            doc_list.append(str(content))

    return "\n".join(doc_list)


@register_function(config_type=EnterpriseRAGConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def enterprise_rag(config: EnterpriseRAGConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    Query the Enterprise RAG Blueprint and return retrieved context.

    This tool is intended to be called from other tools (e.g., `lvs_video_understanding`)
    after a HITL turn that collects the enterprise query.
    """

    # VSS 2.4 behavior: call NvidiaRAG.search(...) for retrieval context.
    #
    # IMPORTANT:
    # - Do NOT import or instantiate NvidiaRAG at NAT startup time.
    #   In some environments, importing NvidiaRAG triggers imports of optional dependencies,
    #   and constructing NvidiaRAG() may validate runtime connectivity / env vars.
    # - We defer both import and construction until the first time the tool is called.
    rag_instance = None
    rag_init_error: str = ""

    def _get_rag():
        nonlocal rag_instance, rag_init_error
        if rag_instance is not None:
            return rag_instance
        if rag_init_error:
            return None
        try:
            from nvidia_rag.rag_server.main import NvidiaRAG  # type: ignore

            rag_instance = NvidiaRAG()
            return rag_instance
        except Exception as e:
            rag_init_error = str(e)
            logger.warning(
                "Enterprise RAG tool invoked but failed to import/initialize NvidiaRAG (%s).",
                e,
                exc_info=True,
            )
            return None

    def _resolve_embedding_endpoint() -> str:
        if config.embedding_endpoint:
            return config.embedding_endpoint
        # IMPORTANT: langchain-nvidia-ai-endpoints expects the base URL (ending in /v1),
        # not the full /embeddings endpoint, because it performs model listing calls
        # relative to this base. Appending /embeddings manually causes 404s on model listing.
        return config.embedding_base_url

    collection_names = [c.strip() for c in config.collection_names.split(",") if c.strip()]
    embedding_endpoint = _resolve_embedding_endpoint()

    async def _enterprise_rag(input_data: EnterpriseRAGInput) -> EnterpriseRAGOutput:
        query = input_data.query.strip()
        if not query:
            return EnterpriseRAGOutput(status="error", query="", context="", error="Empty query")

        rag = _get_rag()
        if rag is None:
            return EnterpriseRAGOutput(
                status="dependency_missing",
                query=query,
                context="",
                error=rag_init_error or "'nvidia-rag' package is not installed/available",
            )

        if not config.collection_names.strip() or not config.vdb_endpoint.strip():
            logger.warning(
                "Enterprise RAG is not configured (collection_names/vdb_endpoint missing). Returning empty context."
            )
            return EnterpriseRAGOutput(
                status="not_configured",
                query=query,
                context="",
                error="Missing ENTERPRISE_RAG_COLLECTION_NAMES and/or ENTERPRISE_RAG_VDB_ENDPOINT",
            )

        vdb_top_k = input_data.vdb_top_k or config.default_vdb_top_k
        reranker_top_k = input_data.reranker_top_k or config.default_reranker_top_k

        enable_reranker = bool(config.enable_reranker and config.reranker_model and config.reranker_endpoint)

        search_kwargs: dict[str, Any] = {
            "query": query,
            "messages": [],
            "reranker_top_k": reranker_top_k,
            "vdb_top_k": vdb_top_k,
            "collection_names": collection_names,
            "vdb_endpoint": config.vdb_endpoint,
            "enable_query_rewriting": config.enable_query_rewriting,
            "embedding_model": config.embedding_model,
            "embedding_endpoint": embedding_endpoint,
            "enable_reranker": enable_reranker,
        }

        if config.filter_expr.strip():
            search_kwargs["filter_expr"] = config.filter_expr

        if enable_reranker:
            search_kwargs.update(
                {
                    "reranker_model": config.reranker_model,
                    "reranker_endpoint": config.reranker_endpoint,
                }
            )

        logger.info(
            "Enterprise RAG query: collections=%s, vdb_top_k=%s, reranker_top_k=%s, reranker=%s",
            collection_names,
            vdb_top_k,
            reranker_top_k,
            "enabled" if enable_reranker else "disabled",
        )

        try:
            # FIX: NvidiaRAG.search is async in the installed version of nvidia-rag.
            # We must await it directly instead of running it in an executor.
            # If the library version changes to sync in future, this await might need adjustment,
            # but current evidence (RuntimeWarning: coroutine never awaited) proves it is async.
            search_results = await asyncio.wait_for(
                rag.search(**search_kwargs),
                timeout=config.timeout_sec,
            )
        except TimeoutError:
            logger.warning("Enterprise RAG query timed out after %ss", config.timeout_sec)
            return EnterpriseRAGOutput(
                status="timeout",
                query=query,
                context="",
                error=f"Timed out after {config.timeout_sec}s",
            )
        except Exception as e:
            logger.exception("Enterprise RAG query failed: %s", e)
            # The exact exception types depend on deployment network/HTTP stack inside nvidia-rag.
            # We report a generic "unreachable" when it looks like a connectivity issue; otherwise "error".
            err = str(e)
            status = (
                "unreachable"
                if any(s in err.lower() for s in ("connection", "connect", "timeout", "refused", "unreachable"))
                else "error"
            )
            return EnterpriseRAGOutput(status=status, query=query, context="", error=err)

        context = _parse_search_results(search_results)
        if context:
            logger.info("Enterprise RAG returned context (first 200 chars): %s", context[:200])
            return EnterpriseRAGOutput(status="ok", query=query, context=context, error="")
        logger.info("Enterprise RAG returned no context")
        return EnterpriseRAGOutput(status="no_results", query=query, context="", error="")

    yield FunctionInfo.create(
        single_fn=_enterprise_rag,
        description=_enterprise_rag.__doc__,
        input_schema=EnterpriseRAGInput,
        single_output_schema=EnterpriseRAGOutput,
    )
