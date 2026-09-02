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
"""CA-RAG ``db_query`` knowledge-retrieval adapter.

Wraps Context-Aware RAG's storage ``db_query`` function
(https://github.com/NVIDIA/context-aware-rag/tree/3.1.1rc1) so VSS Agent can
run backend-native queries against neo4j, arango, milvus, or elasticsearch via
the existing ``knowledge_retrieval`` tool.

Uses CA-RAG's core ``ContextManagerHandler`` + ``db_query`` function only —
not the optional CA-RAG NAT plugin package.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from vss_core.knowledge.base import BackendAdapter
from vss_core.knowledge.factory import register_adapter
from vss_core.knowledge.schema import Chunk
from vss_core.knowledge.schema import ContentType
from vss_core.knowledge.schema import RetrievalResult

logger = logging.getLogger(__name__)

DbType = Literal["neo4j", "arango", "milvus", "elasticsearch"]

_CA_RAG_INSTALL_HINT = (
    "db_query requires context-aware-rag 3.1.1rc1. Install with:\n"
    '  uv pip install "vss-ctx-rag @ git+https://github.com/NVIDIA/context-aware-rag.git@3.1.1rc1"\n'
    "For ArangoDB also install:\n"
    '  uv pip install "vss-ctx-rag-arango @ '
    "git+https://github.com/NVIDIA/context-aware-rag.git@3.1.1rc1"
    '#subdirectory=packages/vss_ctx_rag_arango"'
)

_QUERY_LANGUAGE_HINTS: dict[str, str] = {
    "neo4j": "Cypher",
    "arango": "AQL",
    "milvus": "Milvus filter expression (e.g. 'pk > 0')",
    "elasticsearch": "Elasticsearch Query DSL body (JSON object or JSON string)",
}

_BIND_PARAM_HINTS: dict[str, str] = {
    "neo4j": (
        "Put bind values in `filters.params` and reference them with `$name` in Cypher "
        '(e.g. query: "MATCH (c:Case {id: $case_id}) RETURN c", '
        'filters: {"params": {"case_id": "case_123"}}).'
    ),
    "arango": (
        "Put bind values in `filters.params` and reference them with `@name` in AQL "
        '(e.g. query: "FOR c IN inspection_case FILTER c.case_id == @case_id RETURN c", '
        'filters: {"params": {"case_id": "case_123"}}).'
    ),
    "milvus": (
        "Pass a Milvus filter expression as `query`. Optional kwargs go in `filters.params` "
        "(e.g. options supported by the CA-RAG milvus tool)."
    ),
    "elasticsearch": (
        "Pass an Elasticsearch Query DSL object (or JSON string) as `query`. "
        "Extra search kwargs may go in `filters.params`."
    ),
}

_DEFAULT_PORTS: dict[str, str] = {
    "neo4j": "7687",
    "arango": "8529",
    "milvus": "19530",
    "elasticsearch": "9200",
}


class DbQueryConfig(BaseModel):
    """Backend config for CA-RAG ``db_query``.

    Select the storage engine with ``db_type``. That choice also determines which
    native query language the agent must generate for ``query``.
    """

    model_config = ConfigDict(extra="forbid")

    db_type: DbType = Field(
        default="neo4j",
        description=(
            "Storage backend selector for CA-RAG db_query. "
            "'neo4j' → Cypher; 'arango' → AQL; 'milvus' → filter expression; "
            "'elasticsearch' → Query DSL JSON. Requires the matching CA-RAG "
            "storage plugin (arango needs vss-ctx-rag-arango)."
        ),
    )
    db_host: str = Field(default="localhost", description="Database host.")
    db_port: str = Field(
        default="",
        description=(
            "Database port. When empty, defaults by db_type: "
            "neo4j=7687, arango=8529, milvus=19530, elasticsearch=9200."
        ),
    )
    db_user: str = Field(default="", description="Database username (empty when unused).")
    db_password: str = Field(default="", description="Database password (empty when unused).")
    collection_name: str | None = Field(
        default=None,
        description=(
            "Optional CA-RAG collection/index/graph base name. When omitted, CA-RAG "
            "derives a default from ``uuid``. For Arango this is the graph base name "
            "(CA-RAG connects to the `_system` database)."
        ),
    )
    uuid: str = Field(
        default="default",
        description="CA-RAG context uuid used for collection namespacing when collection_name is unset.",
    )
    schema_description: str = Field(
        default="",
        description=(
            "Schema / query-language hints for the selected db_type, appended to the "
            "tool description so the agent can author correct native queries."
        ),
    )
    embedding_enable: bool = Field(
        default=False,
        description=(
            "CA-RAG storage tools require an embedding tool at construction time. "
            "Leave false to use CA-RAG's NullEmbedding (sufficient for query-only use)."
        ),
    )
    embedding_model: str = Field(
        default="nvidia/llama-3.2-nv-embedqa-1b-v2",
        description="Embedding model name when embedding_enable=true.",
    )
    embedding_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        description="Embedding endpoint base URL when embedding_enable=true.",
    )
    embedding_api_key_env: str = Field(
        default="NVIDIA_API_KEY",
        description="Env var holding the embedding API key when embedding_enable=true.",
    )

    @field_validator("db_port", "db_user", "db_password", mode="before")
    @classmethod
    def _coerce_optional_str(cls, value: Any) -> str:
        # YAML/env may yield None or ints (e.g. empty CARAG_DB_USER, port 9200).
        if value is None:
            return ""
        return str(value).strip()

    @model_validator(mode="after")
    def _apply_default_port(self) -> DbQueryConfig:
        if not self.db_port:
            self.db_port = _DEFAULT_PORTS[self.db_type]
        return self


def build_ca_rag_db_query_config(config: DbQueryConfig) -> dict[str, Any]:
    """Build a minimal CA-RAG ContextManager config with only ``db_query`` enabled."""
    db_params: dict[str, Any] = {
        "host": config.db_host,
        "port": str(config.db_port),
        "username": config.db_user,
        "password": config.db_password,
    }
    if config.collection_name:
        db_params["collection_name"] = config.collection_name

    return {
        "tools": {
            "nvidia_embedding": {
                "type": "embedding",
                "params": {
                    "enable": config.embedding_enable,
                    "model": config.embedding_model,
                    "base_url": config.embedding_base_url,
                    "api_key": os.environ.get(config.embedding_api_key_env, ""),
                },
            },
            "db": {
                "type": config.db_type,
                "params": db_params,
                "tools": {"embedding": "nvidia_embedding"},
            },
        },
        "functions": {
            "db_query": {
                "type": "db_query",
                "params": {},
                "tools": {"db": "db"},
            },
        },
        "context_manager": {
            "functions": ["db_query"],
            "uuid": config.uuid,
        },
    }


def _ensure_ca_rag_imports(db_type: DbType) -> None:
    """Import CA-RAG modules so tool/function registries are populated."""
    try:
        import importlib

        importlib.import_module("vss_ctx_rag.functions.storage")
        importlib.import_module("vss_ctx_rag.tools")
    except ImportError as exc:
        raise ImportError(_CA_RAG_INSTALL_HINT) from exc

    if db_type == "arango":
        try:
            importlib.import_module("vss_ctx_rag.plugins.arango")
        except ImportError as exc:
            raise ImportError(
                "db_type='arango' requires the vss-ctx-rag-arango plugin.\n" + _CA_RAG_INSTALL_HINT
            ) from exc


def _parse_query(query: str, db_type: DbType) -> str | dict[str, Any]:
    """Pass through string queries; parse JSON for elasticsearch DSL bodies."""
    if db_type != "elasticsearch":
        return query
    stripped = query.strip()
    if not stripped.startswith(("{", "[")):
        return query
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return query
    if isinstance(parsed, (dict, list)):
        return parsed  # type: ignore[return-value]
    return query


def _extract_params(filters: dict[str, Any] | None) -> dict[str, Any]:
    if not filters:
        return {}
    params = filters.get("params")
    if isinstance(params, dict):
        return params
    # Allow callers to pass bind params at the top level of filters.
    return {k: v for k, v in filters.items() if k != "params"}


# CA-RAG Milvus query uses output_fields=["*"], which includes dense vectors.
# Drop them before returning content to the agent (large + unused for filter queries).
_MILVUS_VECTOR_KEYS = frozenset({"vector", "embedding", "dense_vector"})


def _strip_milvus_vectors(value: Any) -> Any:
    """Remove embedding/vector fields from Milvus query results (recursively)."""
    if isinstance(value, list):
        return [_strip_milvus_vectors(item) for item in value]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in _MILVUS_VECTOR_KEYS:
                continue
            if isinstance(key, str) and key.endswith("_vector"):
                continue
            cleaned[key] = _strip_milvus_vectors(item)
        return cleaned
    return value


def _normalise_payload(
    payload: Any,
    *,
    db_type: DbType | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Return (success, content, metadata) from a CA-RAG db_query response."""
    if not isinstance(payload, dict):
        return False, f"Unexpected db_query response type: {type(payload).__name__}", {}

    if "error" in payload and "result" not in payload:
        return False, str(payload["error"]), {"raw": payload}

    result = payload.get("result", payload)
    if db_type == "milvus":
        result = _strip_milvus_vectors(result)
    try:
        content = json.dumps(result, default=str, indent=2)
    except TypeError:
        content = str(result)
    # Keep metadata raw without vectors for milvus to avoid huge logs/citations.
    meta_raw = payload
    if db_type == "milvus" and isinstance(payload, dict):
        meta_raw = {**payload, "result": result} if "result" in payload else result
    return True, content, {"raw": meta_raw}


@register_adapter("db_query", config_type=DbQueryConfig)
class DbQueryAdapter(BackendAdapter):
    """Execute CA-RAG ``db_query`` against a configured storage backend.

    Natural-language → query translation is the **agent's** job. This adapter
    only forwards a backend-native query (plus optional bind params) to CA-RAG.
    """

    def __init__(self, config: DbQueryConfig, handler: Any | None = None) -> None:
        super().__init__(config)
        self.collection_name = config.collection_name or ""
        language = _QUERY_LANGUAGE_HINTS[config.db_type]
        hint_parts = [
            "Use this tool for structured / relationship questions over the configured "
            "database. CA-RAG only executes queries — it does not translate natural language.",
            f"Configured db_type={config.db_type!r} → YOU must generate valid {language} "
            "from the user question and pass it as `query`. "
            "Never pass English/natural language as `query`.",
            _BIND_PARAM_HINTS[config.db_type],
            "Read-only queries only.",
            f"Endpoint: {config.db_host}:{config.db_port}.",
        ]
        if config.schema_description.strip():
            hint_parts.append(
                "Use this schema when authoring the query:\n" + config.schema_description.strip()
            )
        self.tool_description_hint = "\n".join(hint_parts)

        self._handler = handler

        logger.info(
            "db_query adapter constructed: db_type=%s host=%s:%s uuid=%s",
            config.db_type,
            config.db_host,
            config.db_port,
            config.uuid,
        )

    async def _get_handler(self) -> Any:
        if self._handler is not None:
            return self._handler

        _ensure_ca_rag_imports(self.config.db_type)
        from vss_ctx_rag.context_manager.context_manager_handler import ContextManagerHandler

        ca_config = build_ca_rag_db_query_config(self.config)
        # Avoid ContextManagerHandler.__init__ -> configure() -> asyncio.run(...),
        # which fails inside NAT's running event loop.
        handler = ContextManagerHandler.create_minimal(ca_config, process_index=0)
        await handler.aconfigure(ca_config)
        self._handler = handler
        logger.info("db_query CA-RAG handler ready: db_type=%s", self.config.db_type)
        return handler

    async def retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        del top_k  # native DB query controls result size; top_k is unused
        filter_dict = filters if isinstance(filters, dict) else None
        native_query = _parse_query(query, self.config.db_type)
        params = _extract_params(filter_dict)

        # Prefer per-call collection override when the backend config left it empty.
        if collection_name and not self.config.collection_name:
            # Rebuild is avoided; pass collection hint via params metadata only.
            params = {**params, "collection_name_hint": collection_name}

        try:
            handler = await self._get_handler()
            raw = await handler.call(
                {
                    "db_query": {
                        "query": native_query,
                        "params": params,
                    }
                }
            )
        except Exception as exc:
            logger.exception("db_query retrieve failed")
            return RetrievalResult(
                chunks=[],
                query=query,
                backend=self.backend_name,
                success=False,
                error_message=f"CA-RAG db_query failed: {str(exc)[:200]}",
            )

        if not isinstance(raw, dict):
            return RetrievalResult(
                chunks=[],
                query=query,
                backend=self.backend_name,
                success=False,
                error_message=f"Unexpected db_query response type: {type(raw).__name__}",
            )

        if "error" in raw and "db_query" not in raw:
            return RetrievalResult(
                chunks=[],
                query=query,
                backend=self.backend_name,
                success=False,
                error_message=str(raw["error"]),
            )

        payload = raw.get("db_query", raw)
        ok, content, metadata = _normalise_payload(payload, db_type=self.config.db_type)
        if not ok:
            return RetrievalResult(
                chunks=[],
                query=query,
                backend=self.backend_name,
                success=False,
                error_message=content,
            )

        scope = collection_name or self.collection_name or self.config.db_type
        chunk = Chunk(
            chunk_id=f"db_query_{abs(hash(query)) % 10_000_000}",
            content=content,
            score=1.0,
            metadata={
                "file_name": "db_query",
                "display_citation": f"[db_query:{self.config.db_type}]",
                "content_type": ContentType.TEXT,
                "db_type": self.config.db_type,
                "collection_name": scope,
                "source_metadata": metadata,
            },
        )
        return RetrievalResult(
            chunks=[chunk],
            query=query,
            backend=self.backend_name,
            success=True,
            total_tokens=len(content.split()),
            summary=content,
        )

    async def health_check(self) -> bool:
        try:
            await self._get_handler()
        except Exception:
            return False
        return True
