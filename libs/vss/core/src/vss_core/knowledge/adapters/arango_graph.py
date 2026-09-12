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
"""LangChain-backed ArangoDB graph retrieval adapter.

This backend is retrieval-only from the agent's point of view. It assumes an
ArangoDB graph already exists, runs a LangChain graph QA chain over that graph,
and normalises the graph answer/evidence into the common ``RetrievalResult``
shape used by the VSS knowledge tool.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from typing import ClassVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from vss_core.knowledge.base import BackendAdapter
from vss_core.knowledge.factory import register_adapter
from vss_core.knowledge.schema import Chunk
from vss_core.knowledge.schema import ContentType
from vss_core.knowledge.schema import RetrievalResult

logger = logging.getLogger(__name__)


class GraphSemantic(BaseModel):
    name: str = Field(description="Short semantic relationship name, e.g. example_relationship.")
    user_terms: list[str] = Field(
        default_factory=list,
        description="User-facing terms that should map to this graph relationship.",
    )
    source_collection: str = Field(description="Source vertex collection.")
    edge_collection: str = Field(description="Edge collection.")
    target_collection: str = Field(description="Target vertex collection.")
    meaning: str = Field(default="", description="Human-readable relationship meaning.")


class ArangoGraphConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arango_url: str = Field(
        default_factory=lambda: os.environ.get("ARANGO_URL", "http://arango-db:8529"),
        description="ArangoDB HTTP endpoint.",
    )
    arango_database: str = Field(
        default_factory=lambda: os.environ.get("ARANGO_DB", "example_graphs"),
        description="ArangoDB database name.",
    )
    arango_username: str = Field(
        default_factory=lambda: os.environ.get("ARANGO_USER", os.environ.get("ARANGO_DB_USERNAME", "vss_graph_reader")),
        description="ArangoDB username. Use a read-only graph user in production.",
    )
    arango_password: str | None = Field(
        default_factory=lambda: os.environ.get("ARANGO_PASSWORD", os.environ.get("ARANGO_DB_PASSWORD")),
    )
    arango_graph_name: str = Field(
        default_factory=lambda: os.environ.get("ARANGO_GRAPH_NAME", "example_graph"),
        description="ArangoDB named graph.",
    )

    # Query safety and output shape.
    allow_dangerous_requests: bool = Field(
        default=False,
        description=(
            "Required LangChain opt-in before generated AQL can run. "
            "This is not a security boundary; enforce read-only access with ArangoDB credentials."
        ),
    )
    return_intermediate_steps: bool = Field(
        default=True,
        description="Include generated AQL/evidence metadata when supported by LangChain.",
    )
    max_aql_generation_attempts: int = Field(
        default=3,
        ge=1,
        description="Maximum AQL generation/fix attempts.",
    )

    default_collection_name: str = Field(
        default="",
        description="Optional logical graph scope used when callers omit collection.",
    )
    schema_description: str = Field(
        default="",
        description="Human-readable graph schema contract appended to graph retrieval queries.",
    )
    allowed_vertex_collections: list[str] = Field(
        default_factory=list,
        description="Optional allowlist of vertex collections the chain should use.",
    )
    allowed_edge_collections: list[str] = Field(
        default_factory=list,
        description="Optional allowlist of edge collections the chain should use.",
    )
    graph_semantics: list[GraphSemantic] = Field(
        default_factory=list,
        description=(
            "Compact semantic mapping from user terms to graph relationships. "
            "The adapter uses this to guide generated graph queries without hardcoded domain AQL."
        ),
    )
    arango_aql_examples: str = Field(
        default="",
        description=(
            "Optional few-shot AQL examples passed to the Arango query-generation prompt. "
            "Prefer graph_semantics for compact domain guidance; use this only for hard query-generation cases."
        ),
    )


@register_adapter("arango_graph", config_type=ArangoGraphConfig)
class ArangoGraphAdapter(BackendAdapter):
    tool_description_hint: ClassVar[str] = (
        "Use this backend for relationship-aware questions over an existing ArangoDB graph, "
        "such as finding linked records, shared attributes, source artifacts, external "
        "references, and connection paths. Optional filters may include domain scope "
        "hints such as `record_id`, `entity_id`, `allowed_vertex_collections`, or "
        "`allowed_edge_collections`."
    )

    def __init__(self, config: ArangoGraphConfig, llm: Any | None = None) -> None:
        super().__init__(config)
        if llm is None:
            raise ValueError("arango_graph requires a resolved LangChain LLM from the VSS `llms` config.")
        self._chain = self._build_chain(llm)
        self.collection_name = config.default_collection_name
        logger.info("arango_graph initialised: graph=%s database=%s", config.arango_graph_name, config.arango_database)

    async def retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        scoped_query = _apply_scope_hints(
            query=query,
            collection_name=collection_name or self.collection_name,
            top_k=top_k,
            filters=filters if isinstance(filters, dict) else None,
        )
        try:
            result = await asyncio.to_thread(_invoke_chain, self._chain, scoped_query)
        except Exception as e:
            logger.exception("arango_graph retrieve failed")
            return _failure(query, self.backend_name, f"Arango graph retrieval failed: {str(e)[:100]}")

        answer, metadata = _normalise_chain_result(result)
        if not answer:
            answer = "Arango graph retrieval completed, but the provider returned no answer text."

        chunk = Chunk(
            chunk_id=_chunk_id(metadata),
            content=answer,
            score=1.0,
            metadata={
                "file_name": "arango_graph",
                "display_citation": "[arango_graph]",
                "content_type": ContentType.TEXT,
                "graph_provider": "arango_graph",
                "collection_name": collection_name or self.collection_name,
                "source_metadata": metadata,
            },
        )
        return RetrievalResult(
            chunks=[chunk],
            query=query,
            backend=self.backend_name,
            success=True,
            total_tokens=len(answer.split()),
            summary=answer,
        )

    async def health_check(self) -> bool:
        return self._chain is not None

    def _build_chain(self, llm: Any) -> Any:
        try:
            from langchain_classic.chains import ArangoGraphQAChain
        except ImportError as e:
            raise ImportError(_missing_dependency_message()) from e

        return ArangoGraphQAChain.from_llm(
            llm=llm,
            graph=self._build_graph(),
            aql_examples=self._query_guidance(),
            allow_dangerous_requests=self.config.allow_dangerous_requests,
            return_aql_query=self.config.return_intermediate_steps,
            return_aql_result=self.config.return_intermediate_steps,
            max_aql_generation_attempts=self.config.max_aql_generation_attempts,
        )

    def _build_graph(self) -> Any:
        try:
            from langchain_community.graphs import ArangoGraph
        except ImportError as e:
            raise ImportError(_missing_dependency_message()) from e

        db = self._connect_db()
        try:
            return ArangoGraph(db, graph_name=self.config.arango_graph_name)
        except TypeError as e:
            if "graph_name" not in str(e):
                raise
            # langchain-community versions differ here: some accept a named
            # graph, while current releases infer graph metadata from the DB.
            return ArangoGraph(db)

    def _connect_db(self) -> Any:
        try:
            from arango import ArangoClient
        except ImportError as e:
            raise ImportError(_missing_dependency_message()) from e

        client = ArangoClient(hosts=self.config.arango_url)
        return client.db(
            self.config.arango_database,
            username=self.config.arango_username,
            password=self.config.arango_password,
        )

    def _query_guidance(self) -> str:
        sections = []
        if self.config.schema_description.strip():
            sections.append("Graph schema description:\n" + self.config.schema_description.strip())
        if self.config.allowed_vertex_collections:
            sections.append(f"Allowed vertex collections: {self.config.allowed_vertex_collections!r}")
        if self.config.allowed_edge_collections:
            sections.append(f"Allowed edge collections: {self.config.allowed_edge_collections!r}")
        semantic_guidance = self._format_graph_semantics()
        if semantic_guidance:
            sections.append(
                "Graph semantic mappings. Use these mappings to translate user language into graph traversals:\n"
                f"{semantic_guidance}"
            )
        if self.config.arango_aql_examples.strip():
            sections.append("AQL examples:\n" + self.config.arango_aql_examples.strip())
        return "\n\n".join(sections)

    def _format_graph_semantics(self) -> str:
        lines = []
        for semantic in self.config.graph_semantics:
            terms = ", ".join(semantic.user_terms) if semantic.user_terms else "n/a"
            line = (
                f"- {semantic.name}: user terms [{terms}] map "
                f"{semantic.source_collection} --{semantic.edge_collection}-> {semantic.target_collection}"
            )
            if semantic.meaning:
                line += f"; meaning: {semantic.meaning}"
            lines.append(line)
        return "\n".join(lines)


def _apply_scope_hints(
    *,
    query: str,
    collection_name: str,
    top_k: int,
    filters: dict[str, Any] | None,
) -> str:
    hints: list[str] = [f"Return at most {top_k} concise evidence item(s)."]
    if collection_name:
        hints.append(f"Use graph scope or collection named {collection_name!r} when applicable.")
    if filters:
        for key in (
            "case_id",
            "entity_id",
            "defect_type",
            "component_id",
            "allowed_vertex_collections",
            "allowed_edge_collections",
        ):
            if key in filters:
                hints.append(f"{key}: {filters[key]!r}")
    return f"{query}\n\nGraph retrieval constraints:\n- " + "\n- ".join(hints)


def _invoke_chain(chain: Any, query: str) -> Any:
    if hasattr(chain, "invoke"):
        return chain.invoke({"query": query})
    if callable(chain):
        return chain({"query": query})
    raise TypeError("LangChain graph chain does not expose invoke() or __call__()")


def _normalise_chain_result(result: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(result, dict):
        answer = result.get("result") or result.get("answer") or result.get("output") or result.get("text") or ""
        metadata = {k: v for k, v in result.items() if k not in {"result", "answer", "output", "text"}}
        return str(answer).strip(), metadata
    return str(result).strip(), {}


def _chunk_id(metadata: dict[str, Any]) -> str:
    query = metadata.get("query") or metadata.get("generated_query") or metadata.get("aql") or metadata.get("aql_query")
    if query:
        return f"arango_graph_{abs(hash(str(query))) % 10_000_000}"
    return "arango_graph_result"


def _failure(query: str, backend: str, message: str) -> RetrievalResult:
    logger.error("%s: %s", backend, message)
    return RetrievalResult(
        chunks=[],
        query=query,
        backend=backend,
        success=False,
        error_message=message,
    )


def _missing_dependency_message() -> str:
    return (
        "arango_graph requires `python-arango`, `langchain-community`, and `langchain-classic`. Install via:\n"
        "  pip install 'nvidia-vss[arango_graph]'"
    )
