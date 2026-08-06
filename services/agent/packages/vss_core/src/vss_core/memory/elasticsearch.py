# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Elasticsearch-backed unified memory store.

Document ``_id`` equals ``job.job_id``. Lifecycle transitions upsert the same
document. This module uses the synchronous Elasticsearch client so the
``MemoryStore`` protocol stays sync and CLI-friendly.
"""

from __future__ import annotations

import logging
from typing import Any

from elasticsearch import Elasticsearch
from elasticsearch import NotFoundError as ESNotFoundError
from elasticsearch.exceptions import ConnectionError as ESConnectionError
from elasticsearch.exceptions import TransportError as ESTransportError

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError

from .models import UnifiedMemoryRecord
from .store import JobFilters
from .store import MemoryQuery

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_INDEX = "vss-memory"


class ElasticsearchMemoryStore:
    """Persist ``nv.vss.memory/1.0`` records in Elasticsearch."""

    def __init__(
        self,
        *,
        endpoint: str,
        index: str = DEFAULT_MEMORY_INDEX,
        client: Elasticsearch | None = None,
        request_timeout: int = 30,
    ) -> None:
        if not endpoint and client is None:
            raise ConfigurationError("Elasticsearch memory store requires an endpoint or injected client")
        self._endpoint = endpoint
        self._index = index
        self._owned = client is None
        self._client = client or Elasticsearch(endpoint, request_timeout=request_timeout)

    @property
    def index(self) -> str:
        return self._index

    def close(self) -> None:
        if self._owned:
            self._client.close()

    def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
        existing = self.get(record.job.job_id)
        if existing is not None:
            job = record.job.model_copy(update={"created_at": existing.job.created_at})
            record = record.model_copy(update={"job": job})
        body = record.model_dump_memory()
        try:
            self._client.index(index=self._index, id=record.job.job_id, document=body, refresh="wait_for")
        except (ESConnectionError, ESTransportError) as error:
            raise BackendUnreachableError(
                "elasticsearch", f"upsert failed for {record.job.job_id}", cause=error
            ) from error
        return record

    def get(self, job_id: str) -> UnifiedMemoryRecord | None:
        try:
            response = self._client.get(index=self._index, id=job_id)
        except ESNotFoundError:
            return None
        except (ESConnectionError, ESTransportError) as error:
            raise BackendUnreachableError("elasticsearch", f"get failed for {job_id}", cause=error) from error
        source = response.get("_source")
        if not isinstance(source, dict):
            return None
        return UnifiedMemoryRecord.model_validate(source)

    def query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]:
        body = self._build_search_body(
            group=query.group,
            status=query.status,
            sensor_id=query.sensor_id,
            job_id=query.job_id,
            since=query.since,
            until=query.until,
            text=query.text,
            limit=query.limit,
        )
        return self._search(body)

    def list_jobs(self, filters: JobFilters) -> list[UnifiedMemoryRecord]:
        body = self._build_search_body(
            group=filters.group,
            status=filters.status,
            sensor_id=filters.sensor_id,
            since=filters.since,
            until=filters.until,
            limit=filters.limit,
        )
        return self._search(body)

    def _search(self, body: dict[str, Any]) -> list[UnifiedMemoryRecord]:
        try:
            response = self._client.search(index=self._index, body=body)
        except (ESConnectionError, ESTransportError) as error:
            raise BackendUnreachableError("elasticsearch", "search failed", cause=error) from error
        hits = response.get("hits", {}).get("hits", [])
        records: list[UnifiedMemoryRecord] = []
        for hit in hits:
            source = hit.get("_source")
            if isinstance(source, dict):
                records.append(UnifiedMemoryRecord.model_validate(source))
        return records

    @staticmethod
    def _build_search_body(
        *,
        group: str | None = None,
        status: str | None = None,
        sensor_id: str | None = None,
        job_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        text: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        must: list[dict[str, Any]] = []
        filters: list[dict[str, Any]] = []
        if job_id:
            filters.append({"term": {"job.job_id.keyword": job_id}})
        if group:
            filters.append({"term": {"job.group.keyword": group}})
        if status:
            filters.append({"term": {"job.status.keyword": status}})
        if sensor_id:
            filters.append({"term": {"input.sensors.id.keyword": sensor_id}})
        if since or until:
            range_body: dict[str, Any] = {}
            if since:
                range_body["gte"] = since
            if until:
                range_body["lte"] = until
            filters.append({"range": {"job.created_at": range_body}})
        if text:
            must.append(
                {
                    "multi_match": {
                        "query": text,
                        "fields": [
                            "input.query",
                            "output.answer",
                            "output.ext.events",
                            "output.ext.results",
                            "output.ext.incidents",
                        ],
                    }
                }
            )
        bool_query: dict[str, Any] = {}
        if must:
            bool_query["must"] = must
        else:
            bool_query["must"] = [{"match_all": {}}]
        if filters:
            bool_query["filter"] = filters
        return {
            "size": max(limit, 0),
            "sort": [{"job.updated_at": {"order": "desc"}}],
            "query": {"bool": bool_query},
        }


__all__ = ["DEFAULT_MEMORY_INDEX", "ElasticsearchMemoryStore"]
