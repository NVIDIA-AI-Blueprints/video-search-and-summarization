# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Elasticsearch-backed unified memory store.

Document ``_id`` is the shared internal storage id:

* Parent: ``job.job_id``
* Child: ``job.job_id#job.record_type#job.record_id``

Parent lifecycle transitions upsert the parent document. Each child has its
own compound ``_id``. ``list_jobs`` returns parents only
(``job.record_id`` must not exist).
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from elasticsearch import Elasticsearch
from elasticsearch import NotFoundError as ESNotFoundError
from elasticsearch.exceptions import ConnectionError as ESConnectionError
from elasticsearch.exceptions import TransportError as ESTransportError

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core._foundation.time import datetime_to_iso8601

from ..models import UnifiedMemoryRecord
from ..store import JobFilters
from ..store import MemoryQuery
from ..store import coerce_utc_instant
from ..store import make_storage_id
from ..store import storage_id_for

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_INDEX = "vss-memory"


class ElasticsearchMemoryStore:
    """Persist ``nv.vss.memory/1.0`` parent and child records in Elasticsearch."""

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
        doc_id = storage_id_for(record)
        existing = self._get_by_storage_id(doc_id)
        if existing is not None:
            job = record.job.model_copy(update={"created_at": existing.job.created_at})
            record = record.model_copy(update={"job": job})
        body = record.model_dump_memory()
        try:
            self._client.index(index=self._index, id=doc_id, document=body, refresh="wait_for")
        except (ESConnectionError, ESTransportError) as error:
            raise BackendUnreachableError("elasticsearch", f"upsert failed for {doc_id}", cause=error) from error
        return record

    def get(self, job_id: str) -> UnifiedMemoryRecord | None:
        return self._get_by_storage_id(make_storage_id(job_id=job_id))

    def get_record(
        self,
        job_id: str,
        record_type: str,
        record_id: str,
    ) -> UnifiedMemoryRecord | None:
        return self._get_by_storage_id(make_storage_id(job_id=job_id, record_type=record_type, record_id=record_id))

    def _get_by_storage_id(self, storage_id: str) -> UnifiedMemoryRecord | None:
        try:
            response = self._client.get(index=self._index, id=storage_id)
        except ESNotFoundError:
            return None
        except (ESConnectionError, ESTransportError) as error:
            raise BackendUnreachableError("elasticsearch", f"get failed for {storage_id}", cause=error) from error
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
            record_type=query.record_type,
            record_id=query.record_id,
            include_children=query.include_children,
            parents_only=query.parents_only,
            since=query.since,
            until=query.until,
            time_field=query.time_field,
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
            time_field="created_at",
            parents_only=True,
            include_children=False,
            limit=filters.limit,
        )
        return self._search(body)

    def _search(self, body: dict[str, Any]) -> list[UnifiedMemoryRecord]:
        try:
            response = self._client.search(index=self._index, body=body)
        except ESNotFoundError:
            # Nothing has been ingested yet, which is an empty result rather
            # than a failure -- the same reading `get` gives a missing document.
            # ES answers 404 as ApiError, not TransportError, so the clause
            # below never sees it.
            return []
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
        record_type: str | None = None,
        record_id: str | None = None,
        include_children: bool = True,
        parents_only: bool = False,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        time_field: str = "created_at",
        text: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        must: list[dict[str, Any]] = []
        filters: list[dict[str, Any]] = []
        must_not: list[dict[str, Any]] = []
        if job_id:
            filters.append({"term": {"job.job_id.keyword": job_id}})
        if group:
            filters.append({"term": {"job.group.keyword": group}})
        if status:
            filters.append({"term": {"job.status.keyword": status}})
        if sensor_id:
            filters.append({"term": {"input.sensors.id.keyword": sensor_id}})
        if record_type:
            filters.append({"term": {"job.record_type.keyword": record_type}})
        if record_id:
            filters.append({"term": {"job.record_id.keyword": record_id}})
        if parents_only or not include_children:
            # Parents omit record_id; children always have it.
            must_not.append({"exists": {"field": "job.record_id"}})
        since_dt = coerce_utc_instant(since)
        until_dt = coerce_utc_instant(until)
        if since_dt or until_dt:
            if time_field == "window":
                # Overlap: window.start <= until AND (window.end|start) >= since
                if until_dt is not None:
                    filters.append(
                        {
                            "range": {
                                "input.window.start.timestamp": {
                                    "lte": datetime_to_iso8601(until_dt),
                                }
                            }
                        }
                    )
                if since_dt is not None:
                    filters.append(
                        {
                            "bool": {
                                "should": [
                                    {
                                        "range": {
                                            "input.window.end.timestamp": {
                                                "gte": datetime_to_iso8601(since_dt),
                                            }
                                        }
                                    },
                                    {
                                        "bool": {
                                            "must_not": [{"exists": {"field": "input.window.end.timestamp"}}],
                                            "filter": [
                                                {
                                                    "range": {
                                                        "input.window.start.timestamp": {
                                                            "gte": datetime_to_iso8601(since_dt),
                                                        }
                                                    }
                                                }
                                            ],
                                        }
                                    },
                                ],
                                "minimum_should_match": 1,
                            }
                        }
                    )
            else:
                range_body: dict[str, Any] = {}
                if since_dt is not None:
                    range_body["gte"] = datetime_to_iso8601(since_dt)
                if until_dt is not None:
                    range_body["lte"] = datetime_to_iso8601(until_dt)
                filters.append({"range": {"job.created_at": range_body}})
        if text:
            must.append(
                {
                    "multi_match": {
                        "query": text,
                        "fields": [
                            "input.query",
                            "output.answer",
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
        if must_not:
            bool_query["must_not"] = must_not
        return {
            "size": max(limit, 0),
            "sort": [{"job.updated_at": {"order": "desc", "missing": "_last"}}, {"job.created_at": {"order": "desc"}}],
            "query": {"bool": bool_query},
        }


__all__ = ["DEFAULT_MEMORY_INDEX", "ElasticsearchMemoryStore"]
