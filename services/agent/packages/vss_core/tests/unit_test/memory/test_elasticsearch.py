# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ElasticsearchMemoryStore with an injected fake client."""

from __future__ import annotations

from typing import Any

from vss_core._foundation.time import iso8601_to_datetime
from vss_core.memory.backends.elasticsearch import ElasticsearchMemoryStore
from vss_core.memory.models import SCHEMA_ID
from vss_core.memory.models import JobInfo
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.store import JobFilters
from vss_core.memory.store import MemoryQuery


def _parent(job_id: str = "summary-1", status: str = "submitted") -> UnifiedMemoryRecord:
    return UnifiedMemoryRecord.model_validate(
        {
            "schema": SCHEMA_ID,
            "job": {
                "job_id": job_id,
                "group": "summary",
                "operation": "run",
                "status": status,
                "created_at": "2026-07-22T12:00:00Z",
                "updated_at": "2026-07-22T12:00:00Z",
            },
            "input": {"query": "q", "sensors": [{"id": "cam-1"}]},
            "output": {"answer": "done"} if status == "completed" else None,
        }
    )


def _child(
    job_id: str = "summary-1",
    *,
    record_id: str = "evt-001",
    record_type: str = "event",
) -> UnifiedMemoryRecord:
    return UnifiedMemoryRecord.model_validate(
        {
            "schema": SCHEMA_ID,
            "job": {
                "job_id": job_id,
                "record_id": record_id,
                "record_type": record_type,
                "group": "summary",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-07-22T12:00:00Z",
            },
            "input": {
                "sensors": [{"id": "cam-1"}],
                "window": {
                    "start": {"timestamp": "2026-07-22T11:00:00Z"},
                    "end": {"timestamp": "2026-07-22T11:01:00Z"},
                },
            },
            "output": {"answer": "child"},
        }
    )


class _FakeES:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.indexed: list[str] = []
        self.last_body: dict[str, Any] | None = None

    def index(self, *, index: str, id: str, document: dict[str, Any], refresh: str | None = None) -> dict[str, Any]:
        self.docs[id] = document
        self.indexed.append(id)
        return {"result": "created"}

    def get(self, *, index: str, id: str) -> dict[str, Any]:
        from elasticsearch import NotFoundError as ESNotFoundError

        if id not in self.docs:
            raise ESNotFoundError("not found", {}, {"_id": id})
        return {"_id": id, "_source": self.docs[id]}

    def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.last_body = body
        hits = [{"_source": doc} for doc in self.docs.values()]
        return {"hits": {"hits": hits}}

    def close(self) -> None:
        return None


def test_elasticsearch_parent_lifecycle_same_storage_id() -> None:
    client = _FakeES()
    store = ElasticsearchMemoryStore(endpoint="http://unused", client=client, index="vss-memory")
    store.upsert(_parent(status="submitted"))
    running = _parent(status="running")
    running = running.model_copy(
        deep=True,
        update={
            "job": JobInfo.model_validate(
                {
                    **running.job.model_dump(mode="json", exclude_none=True),
                    "updated_at": "2026-07-22T12:01:00Z",
                    "created_at": "2099-01-01T00:00:00Z",
                }
            )
        },
    )
    store.upsert(running)
    store.upsert(_parent(status="completed"))

    assert client.indexed == ["summary-1", "summary-1", "summary-1"]
    got = store.get("summary-1")
    assert got is not None
    assert got.job.status == "completed"
    assert got.job.created_at == iso8601_to_datetime("2026-07-22T12:00:00Z")


def test_elasticsearch_child_has_compound_id() -> None:
    client = _FakeES()
    store = ElasticsearchMemoryStore(endpoint="http://unused", client=client)
    store.upsert(_parent(status="completed"))
    store.upsert(_child())
    assert "summary-1" in client.indexed
    assert "summary-1#event#evt-001" in client.indexed
    child = store.get_record("summary-1", "event", "evt-001")
    assert child is not None
    assert child.job.record_id == "evt-001"
    assert store.get("summary-1") is not None


def test_elasticsearch_get_missing_returns_none() -> None:
    store = ElasticsearchMemoryStore(endpoint="http://unused", client=_FakeES())
    assert store.get("missing") is None
    assert store.get_record("missing", "event", "x") is None


def test_elasticsearch_list_before_anything_is_ingested_is_empty() -> None:
    """A missing index means nothing has been written, not that reading broke.

    ES answers `index_not_found_exception` as an `ApiError`, which is not a
    `TransportError`, so an unguarded search escapes the store untyped and
    surfaces as a traceback in whatever called it.
    """
    from elasticsearch import NotFoundError as ESNotFoundError

    class _NoIndex(_FakeES):
        def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
            raise ESNotFoundError("index_not_found_exception", {}, {"index": index})

    store = ElasticsearchMemoryStore(endpoint="http://unused", client=_NoIndex())
    assert store.list_jobs(JobFilters()) == []
    assert store.query(MemoryQuery(group="summary")) == []


def test_build_search_body_parent_filters() -> None:
    body = ElasticsearchMemoryStore._build_search_body(
        group="search",
        status="completed",
        sensor_id="cam-1",
        text="forklift",
        since="2026-01-01T00:00:00Z",
        until="2026-12-31T23:59:59Z",
        parents_only=True,
        include_children=False,
        limit=5,
    )
    assert body["size"] == 5
    bool_query = body["query"]["bool"]
    assert {"exists": {"field": "job.record_id"}} in bool_query["must_not"]
    assert {"term": {"job.group.keyword": "search"}} in bool_query["filter"]
    assert {"term": {"input.sensors.id.keyword": "cam-1"}} in bool_query["filter"]


def test_build_search_body_child_window_filters() -> None:
    body = ElasticsearchMemoryStore._build_search_body(
        job_id="summary-1",
        record_type="event",
        record_id="evt-001",
        since="2026-07-22T10:00:00Z",
        until="2026-07-22T12:00:00Z",
        time_field="window",
        limit=20,
    )
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"job.job_id.keyword": "summary-1"}} in filters
    assert {"term": {"job.record_type.keyword": "event"}} in filters
    assert {"term": {"job.record_id.keyword": "evt-001"}} in filters
    assert any("input.window.start.timestamp" in str(item) for item in filters)


def test_list_jobs_requests_parents_only() -> None:
    client = _FakeES()
    store = ElasticsearchMemoryStore(endpoint="http://unused", client=client)
    store.list_jobs(JobFilters(group="summary"))
    assert client.last_body is not None
    assert {"exists": {"field": "job.record_id"}} in client.last_body["query"]["bool"]["must_not"]
