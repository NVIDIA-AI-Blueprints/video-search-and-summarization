# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ElasticsearchMemoryStore with an injected fake client."""

from __future__ import annotations

from typing import Any

import pytest

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
        self.last_mget_ids: list[str] | None = None

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
        hits = [{"_id": doc_id, "_source": doc} for doc_id, doc in self.docs.items()]
        return {"hits": {"hits": hits}}

    def mget(self, *, index: str, ids: list[str]) -> dict[str, Any]:
        self.last_mget_ids = ids
        return {
            "docs": [
                {
                    "_id": doc_id,
                    "found": doc_id in self.docs,
                    **({"_source": self.docs[doc_id]} if doc_id in self.docs else {}),
                }
                for doc_id in reversed(ids)
            ]
        }

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


def test_elasticsearch_get_many_uses_mget_and_preserves_input_order() -> None:
    client = _FakeES()
    store = ElasticsearchMemoryStore(endpoint="http://unused", client=client)
    first = store.upsert(_parent("first", status="completed"))
    second = store.upsert(_parent("second", status="completed"))

    assert store.get_many(["second", "missing", "first", "second"]) == [second, first, second]
    assert client.last_mget_ids == ["second", "missing", "first", "second"]


def test_elasticsearch_scan_uses_bounded_search_after_in_storage_id_order() -> None:
    class PagedES(_FakeES):
        def __init__(self) -> None:
            super().__init__()
            self.bodies: list[dict[str, Any]] = []

        def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
            self.bodies.append(body)
            ordered = sorted(self.docs.items())
            offset = int(body.get("search_after", [-1])[0]) + 1
            page = ordered[offset : offset + body["size"]]
            return {
                "hits": {
                    "hits": [
                        {"_id": storage_id, "_source": source, "sort": [offset + index]}
                        for index, (storage_id, source) in enumerate(page)
                    ]
                }
            }

    client = PagedES()
    store = ElasticsearchMemoryStore(endpoint="http://unused", client=client)
    for record in (_parent("b", "completed"), _child("a"), _parent("a", "completed"), _parent("c", "completed")):
        store.upsert(record)

    assert [record.job.record_id or record.job.job_id for record in store.scan(batch_size=2, limit=3)] == [
        "a",
        "evt-001",
        "b",
    ]
    assert [body["size"] for body in client.bodies] == [2, 1]
    assert "search_after" not in client.bodies[0]
    assert client.bodies[1]["search_after"] == [1]
    assert client.bodies[0]["sort"] == [
        {"job.job_id.keyword": {"order": "asc"}},
        {"job.record_type.keyword": {"order": "asc", "missing": "_first"}},
        {"job.record_id.keyword": {"order": "asc", "missing": "_first"}},
    ]


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


def test_reads_load_documents_written_before_children_existed() -> None:
    """A parent nesting ``output.ext.events`` predates child records but still reads.

    Earlier versions wrote the events inline. Rejecting that shape on load
    would not merely refuse the one document -- ``list``/``query`` page over
    whole result sets, so a single old job made every read fail.
    """
    client = _FakeES()
    client.docs["legacy-1"] = {
        "schema": SCHEMA_ID,
        "job": {
            "job_id": "legacy-1",
            "group": "summary",
            "operation": "run",
            "status": "completed",
            "created_at": "2026-07-22T12:00:00Z",
        },
        "output": {"answer": "done", "ext": {"events": [{"id": "evt-1"}], "event_count": 1}},
    }
    store = ElasticsearchMemoryStore(endpoint="http://unused", client=client)

    got = store.get("legacy-1")
    assert got is not None
    assert got.output is not None
    assert got.output.ext is not None
    assert got.output.ext["events"] == [{"id": "evt-1"}]
    assert [record.job.job_id for record in store.list_jobs(JobFilters())] == ["legacy-1"]


def test_undecodable_document_is_named_rather_than_raised_as_validation() -> None:
    """A document that will not load names itself, so the caller can go find it.

    Pydantic's own error is a ``ValueError``, which callers map to "your input
    was malformed" -- the wrong diagnosis for a record already in the store.
    """
    from vss_core.memory.store import MemoryDecodeError

    client = _FakeES()
    client.docs["broken-1"] = {
        "schema": SCHEMA_ID,
        "job": {
            "job_id": "broken-1",
            "group": "summary",
            "operation": "run",
            "status": "completed",
            "created_at": "2026-07-22T12:00:00Z",
        },
        "output": {"answer": "done", "unknown_field": True},
    }
    store = ElasticsearchMemoryStore(endpoint="http://unused", client=client)

    for read in (lambda: store.get("broken-1"), lambda: store.list_jobs(JobFilters())):
        with pytest.raises(MemoryDecodeError) as caught:
            read()
        assert caught.value.storage_id == "broken-1"
        assert "broken-1" in str(caught.value)
        assert not isinstance(caught.value, ValueError)


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
    assert {
        "bool": {
            "should": [
                {"term": {"input.sensors.id.keyword": "cam-1"}},
                {"term": {"input.sensors.info.name.keyword": "cam-1"}},
            ],
            "minimum_should_match": 1,
        }
    } in bool_query["filter"]
    assert body["sort"][0] == {"_score": {"order": "desc"}}


def test_build_search_body_without_text_sorts_only_by_recency() -> None:
    body = ElasticsearchMemoryStore._build_search_body(sensor_id="cam-1")
    assert body["sort"] == [
        {"job.updated_at": {"order": "desc", "missing": "_last"}},
        {"job.created_at": {"order": "desc"}},
    ]


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
