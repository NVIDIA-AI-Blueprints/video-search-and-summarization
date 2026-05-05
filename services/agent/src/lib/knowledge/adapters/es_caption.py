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
"""HTTP adapter for Elasticsearch caption stores written by the RT-VLM pipeline.

Each stream lands in its own index (`default_<uuid_with_underscores>`),
holding three doc_types: `raw_events` (per chunk, with NTP time bounds),
`structured_events` (merged event batches), and `aggregated_summary` (one
per video). Retrieval is BM25 over the `text` field with bool filters on
`metadata.content_metadata.*`. Time-range filtering is only meaningful
for `raw_events`. Vector search and reranking are out of scope for v1.
"""
from __future__ import annotations

import logging
import os
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar
from typing import Literal

import aiohttp
from pydantic import BaseModel
from pydantic import Field

from lib.knowledge.base import BackendAdapter
from lib.knowledge.factory import register_adapter
from lib.knowledge.schema import Chunk
from lib.knowledge.schema import RetrievalResult

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

DocType = Literal["raw_events", "structured_events", "aggregated_summary"]
META_PREFIX = "metadata.content_metadata"  # path to RT-VLM caption fields in ES


class EsCaptionConfig(BaseModel):
    elasticsearch_url: str = Field(
        # Reuse `ELASTIC_SEARCH_ENDPOINT` — the env var already used by
        # dev-profile-search's vss-agent config to point at ES. Default falls
        # back to the docker-network DNS that LVS itself writes to.
        default_factory=lambda: os.environ.get(
            "ELASTIC_SEARCH_ENDPOINT", "http://elasticsearch:9200"
        ),
    )
    index: str = Field(
        default="default_*",
        description=(
            "ES index or wildcard pattern. RT-VLM writes one index per stream "
            "(`default_<uuid_with_underscores>`); the wildcard searches across all."
        ),
    )
    default_doc_type: DocType = Field(
        default="aggregated_summary",
        description=(
            "doc_type used when callers don't override via filters. "
            "`aggregated_summary` is the chronological narrative per video — "
            "timestamps are embedded in the prose, so it covers both general "
            "and time-windowed Q&A. `raw_events` / `structured_events` are "
            "escape hatches for JSON-grained retrieval."
        ),
    )
    api_key: str | None = None
    timeout: int = 30
    verify_ssl: bool = True


@register_adapter("es_caption", config_type=EsCaptionConfig)
class EsCaptionAdapter(BackendAdapter):
    tool_description_hint: ClassVar[str] = (
        "Use for follow-up Q&A on summarized RTSP live streams. Resolve the "
        "named stream's `stream_id` via `vst_video_list` and pass it as "
        "`collection` (it's the VST sensor_id that LVS uses end-to-end as the "
        "ES doc uuid). Default returns the stream's timestamped narrative — "
        "answers most general and time-windowed questions directly. For "
        "per-chunk JSON in a window:\n"
        '  filters={"doc_type": "raw_events", '
        '"time_range": {"start": <epoch_s>, "end": <epoch_s>}}\n'
        "`time_range` is Unix-epoch seconds (RTSP wall-clock)."
    )

    def __init__(self, config: EsCaptionConfig) -> None:
        super().__init__(config)
        self.elasticsearch_url: str = config.elasticsearch_url.rstrip("/")
        self.index: str = config.index
        self.default_doc_type: DocType = config.default_doc_type
        self.api_key: str | None = config.api_key
        self.timeout: int = config.timeout
        self.verify_ssl: bool = config.verify_ssl
        logger.info(
            "es_caption initialised: elasticsearch_url=%s index=%s default_doc_type=%s",
            self.elasticsearch_url,
            self.index,
            self.default_doc_type,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"ApiKey {self.api_key}"
        return headers

    async def retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        filters: Callable[[Chunk], bool] | dict[str, Any] | None = None,
    ) -> RetrievalResult:
        body = self._build_query(query, collection_name, top_k, filters)
        endpoint = f"{self.elasticsearch_url}/{self.index}/_search"
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                endpoint,
                json=body,
                headers=self._headers(),
                ssl=self.verify_ssl,
            ) as response:
                response.raise_for_status()
                data = (await response.json()) or {}
        except aiohttp.ClientConnectionError as e:
            return _failure(query, self.backend_name, f"Cannot connect to Elasticsearch: {str(e)[:100]}")
        except TimeoutError:
            return _failure(query, self.backend_name, f"Request timed out after {self.timeout}s")
        except aiohttp.ClientResponseError as e:
            return _failure(query, self.backend_name, f"Server error: {str(e)[:100]}")
        except aiohttp.ClientError as e:
            return _failure(query, self.backend_name, f"Request failed: {str(e)[:100]}")

        hits = ((data.get("hits") or {}).get("hits")) or []
        chunks = [c for c in (_normalise_hit(h) for h in hits) if c is not None]

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
        endpoint = f"{self.elasticsearch_url}/_cluster/health"
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
                endpoint,
                headers=self._headers(),
                ssl=self.verify_ssl,
            ) as response:
                return response.status == 200
        except Exception as e:
            logger.warning("es_caption health check failed: %s", e)
            return False

    def _build_query(
        self,
        query: str,
        collection_name: str,
        top_k: int,
        filters: Callable[[Chunk], bool] | dict[str, Any] | None,
    ) -> dict[str, Any]:
        f = filters if isinstance(filters, dict) else {}

        # Raw escape hatch: caller supplies a fully-formed ES query body.
        if "es_query" in f:
            return {"size": top_k, **f["es_query"]}

        must: list[dict[str, Any]] = [{"match": {"text": query}}] if query else []
        bool_filters: list[dict[str, Any]] = []

        # collection -> stream uuid (RT-VLM partitions per stream).
        if collection_name:
            bool_filters.append({"term": {f"{META_PREFIX}.uuid": collection_name}})

        # doc_type: caller override or adapter default.
        doc_type = f.get("doc_type", self.default_doc_type)
        bool_filters.append({"term": {f"{META_PREFIX}.doc_type": doc_type}})

        # camera scoping is common enough to lift out.
        if "camera_id" in f:
            bool_filters.append({"term": {f"{META_PREFIX}.camera_id": f["camera_id"]}})

        # time_range (seconds, overlap semantics) — only raw_events have these fields.
        time_range = f.get("time_range")
        if isinstance(time_range, dict):
            start_s = time_range.get("start")
            end_s = time_range.get("end")
            if end_s is not None:
                bool_filters.append(
                    {"range": {f"{META_PREFIX}.start_ntp_float": {"lte": end_s}}}
                )
            if start_s is not None:
                bool_filters.append(
                    {"range": {f"{META_PREFIX}.end_ntp_float": {"gte": start_s}}}
                )

        # Anything else: treat as term equality on content_metadata.<field>.
        reserved = {"doc_type", "camera_id", "time_range", "es_query"}
        for k, v in f.items():
            if k in reserved:
                continue
            bool_filters.append({"term": {f"{META_PREFIX}.{k}": v}})

        return {
            "size": top_k,
            "query": {"bool": {"must": must, "filter": bool_filters}},
        }


def _failure(query: str, backend: str, message: str) -> RetrievalResult:
    logger.error("%s: %s", backend, message)
    return RetrievalResult(
        chunks=[],
        query=query,
        backend=backend,
        success=False,
        error_message=message,
    )


def _derive_stream_name(sensor: dict[str, Any]) -> str | None:
    """Extract a human-readable stream name from a hit's `sensor` block.

    RTSP streams carry a `description` (e.g. `"warehouse_stream_2"`); uploaded
    videos embed a filename in `sensor.info.url` / `.path`. Returned as-is —
    the on-disk filename is the source of truth.
    """
    if not isinstance(sensor, dict):
        return None
    description = (sensor.get("description") or "").strip()
    if description:
        return description
    info = sensor.get("info") or {}
    path = info.get("url") or info.get("path")
    if not isinstance(path, str) or not path:
        return None
    return PurePosixPath(path).stem or None


def _normalise_hit(hit: dict[str, Any]) -> Chunk | None:
    """Convert one ES hit into our Chunk schema."""
    if not isinstance(hit, dict):
        return None
    src = hit.get("_source") or {}
    cm = ((src.get("metadata") or {}).get("content_metadata")) or {}
    sensor = src.get("sensor") or {}

    content = src.get("text") or ""
    score = float(hit.get("_score") or 0.0)
    uuid = cm.get("uuid") or cm.get("streamId") or "unknown"
    doc_type = cm.get("doc_type") or "unknown"
    camera_id = cm.get("camera_id")
    stream_name = _derive_stream_name(sensor)
    source_url = (sensor.get("info") or {}).get("url")

    # NTP fields (seconds, float) — populated on raw_events only.
    start_ntp = cm.get("start_ntp_float")
    end_ntp = cm.get("end_ntp_float")
    start_s = start_ntp if isinstance(start_ntp, (int, float)) else None
    end_s = end_ntp if isinstance(end_ntp, (int, float)) else None

    # chunkIdx (per-chunk raw_events) or batch_i (structured_events) or hit id.
    seq = cm.get("chunkIdx")
    if not isinstance(seq, int) or seq < 0:
        seq = cm.get("batch_i")
    chunk_id = (
        f"{uuid}_{doc_type}_{seq}" if isinstance(seq, int) else f"{uuid}_{doc_type}"
    )

    # Prefer the human-readable stream name; fall back to camera_id, then uuid.
    label = stream_name or camera_id or uuid
    parts = [label]
    if start_s is not None and end_s is not None:
        parts.append(f"{start_s:g}-{end_s:g}s")
    display_citation = "[" + ", ".join(parts) + "]"

    return Chunk(
        chunk_id=chunk_id,
        content=content,
        score=score,
        metadata={
            "uuid": uuid,
            "stream_name": stream_name,
            "source_url": source_url,
            "camera_id": camera_id,
            "doc_type": doc_type,
            "start_seconds": start_s,
            "end_seconds": end_s,
            "display_citation": display_citation,
            "source_metadata": cm,
        },
    )
