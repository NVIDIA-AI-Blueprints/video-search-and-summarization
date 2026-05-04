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
"""HTTP adapter for a deployed NVIDIA RAG Blueprint rag-server."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

import aiohttp
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

# vdb_top_k oversamples vs the reranker's final top_k for better recall.
VDB_TOP_K_MULTIPLIER = 10
MAX_VDB_TOP_K = 100


class FragApiConfig(BaseModel):
    rag_url: str = Field(
        default_factory=lambda: os.environ.get("RAG_SERVER_URL", "http://localhost:8081/v1"),
    )
    api_key: str | None = Field(
        default_factory=lambda: os.environ.get("RAG_API_KEY"),
    )
    timeout: int = 300
    verify_ssl: bool = True


@register_adapter("frag_api", config_type=FragApiConfig)
class FragApiAdapter(BackendAdapter):
    tool_description_hint: ClassVar[str] = (
        "Pass `filters` only when the user explicitly names a document; never "
        "invent a filename. Shape:\n"
        '  filters={"filter_expr": \'content_metadata["filename"] == "<name>"\'}'
    )

    def __init__(self, config: FragApiConfig) -> None:
        super().__init__(config)
        # Local convenience accessors with stripped trailing slash on the URL.
        self.rag_url: str = config.rag_url.rstrip("/")
        self.api_key: str | None = config.api_key
        self.timeout: int = config.timeout
        self.verify_ssl: bool = config.verify_ssl
        logger.info("frag_api initialised: rag_url=%s", self.rag_url)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        filters: Callable[[Chunk], bool] | dict[str, Any] | None = None,
    ) -> RetrievalResult:
        payload: dict[str, Any] = {
            "query": query,
            "collection_names": [collection_name],
            "reranker_top_k": top_k,
            "vdb_top_k": min(top_k * VDB_TOP_K_MULTIPLIER, MAX_VDB_TOP_K),
            "enable_reranker": True,
        }
        filter_expr = _filters_to_expr(filters)
        if filter_expr:
            payload["filter_expr"] = filter_expr

        endpoint = f"{self.rag_url}/search"
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                endpoint,
                json=payload,
                headers=self._headers(),
                ssl=self.verify_ssl,
            ) as response:
                response.raise_for_status()
                data = (await response.json()) or {}
        except aiohttp.ClientConnectionError as e:
            return _failure(query, self.backend_name, f"Cannot connect to RAG server: {str(e)[:100]}")
        except TimeoutError:
            return _failure(query, self.backend_name, f"Request timed out after {self.timeout}s")
        except aiohttp.ClientResponseError as e:
            return _failure(query, self.backend_name, f"Server error: {str(e)[:100]}")
        except aiohttp.ClientError as e:
            return _failure(query, self.backend_name, f"Request failed: {str(e)[:100]}")

        chunks = [_normalise_search_result(r, idx=i) for i, r in enumerate(data.get("results", []))]
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
        endpoint = f"{self.rag_url}/health"
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
                endpoint,
                headers=self._headers(),
                ssl=self.verify_ssl,
            ) as response:
                return response.status == 200
        except Exception as e:
            logger.warning("frag_api health check failed: %s", e)
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


def _filters_to_expr(filters: Any) -> str | None:
    """Translate a dict-of-equalities into a Milvus filter_expr."""
    if not isinstance(filters, dict):
        return None
    if "filter_expr" in filters:
        return filters["filter_expr"]
    parts: list[str] = []
    for k, v in filters.items():
        if isinstance(v, str):
            parts.append(f'{k} == "{v}"')
        else:
            parts.append(f"{k} == {v}")
    return " and ".join(parts) if parts else None


def _normalise_search_result(result: dict[str, Any], idx: int = 0) -> Chunk | None:
    """Convert a single rag-server /search hit into our Chunk schema."""
    if not isinstance(result, dict):
        return None

    document_name_raw = result.get("document_name", "unknown")
    document_type = (result.get("document_type") or "text").lower()
    content = result.get("content", "") or ""
    score = result.get("score", 0.0)
    metadata = result.get("metadata") or {}
    content_metadata = metadata.get("content_metadata") or {}

    # Strip ingestion-time tmp prefix (tmp + 8 chars + _) for display.
    display_name = re.sub(r"^tmp.{8}_", "", document_name_raw)

    page_number = (
        result.get("page_number")
        or metadata.get("page_number")
        or content_metadata.get("page_number")
    )
    if page_number in (-1, 0, None):
        page_number = None

    doc_base = Path(display_name).stem if display_name != "unknown" else "doc"
    chunk_id = result.get("chunk_id") or (
        f"{doc_base}_p{page_number}_{idx}" if page_number else f"{doc_base}_{idx}"
    )

    if "image" in document_type:
        content_type = ContentType.IMAGE
    elif "table" in document_type:
        content_type = ContentType.TABLE
    elif "chart" in document_type:
        content_type = ContentType.CHART
    else:
        content_type = ContentType.TEXT

    display_citation = (
        f"[{display_name}, p.{page_number}]" if page_number and page_number > 0 else f"[{display_name}]"
    )

    return Chunk(
        chunk_id=chunk_id,
        content=content,
        score=float(score),
        metadata={
            "file_name": display_name,
            "page_number": page_number,
            "display_citation": display_citation,
            "content_type": content_type,
            "document_id": result.get("document_id") or document_name_raw,
            "collection_name": result.get("collection_name", ""),
            "source_metadata": metadata,
        },
    )
