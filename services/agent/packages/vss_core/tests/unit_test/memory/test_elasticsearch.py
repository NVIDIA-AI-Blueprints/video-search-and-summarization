# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ElasticsearchMemoryStore with an injected fake client."""

from __future__ import annotations

from typing import Any

from vss_core.memory.elasticsearch import ElasticsearchMemoryStore
from vss_core.memory.models import SCHEMA_ID
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.store import JobFilters
from vss_core.memory.store import MemoryQuery


def _record(job_id: str = "summary-1", status: str = "submitted") -> UnifiedMemoryRecord:
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
                "backend_ref": None,
            },
            "input": {"query": "q", "sensors": [], "window": None, "params": {}},
            "output": {
                "answer": None,
                "Embedding": [],
                "handles": {
                    "media_urls": [],
                    "related_job_ids": [],
                },
                "ext": {},
            },
            "error": None,
        }
    )


class _FakeES:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.indexed: list[str] = []

    def index(self, *, index: str, id: str, document: dict[str, Any], refresh: str | None = None) -> dict[str, Any]:
        existing = self.docs.get(id)
        if existing is not None:
            document = dict(document)
            document["job"] = dict(document["job"])
            document["job"]["created_at"] = existing["job"]["created_at"]
        self.docs[id] = document
        self.indexed.append(id)
        return {"result": "created"}

    def get(self, *, index: str, id: str) -> dict[str, Any]:
        from elasticsearch import NotFoundError as ESNotFoundError

        if id not in self.docs:
            raise ESNotFoundError("not found", {}, {"_id": id})
        return {"_id": id, "_source": self.docs[id]}

    def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
        hits = [{"_source": doc} for doc in self.docs.values()]
        return {"hits": {"hits": hits}}

    def close(self) -> None:
        return None


def test_elasticsearch_upsert_same_id_through_lifecycle() -> None:
    client = _FakeES()
    store = ElasticsearchMemoryStore(endpoint="http://unused", client=client, index="vss-memory")
    store.upsert(_record(status="submitted"))
    running = _record(status="running")
    running = running.model_copy(
        deep=True,
        update={
            "job": running.job.model_copy(
                update={"updated_at": "2026-07-22T12:01:00Z", "created_at": "2099-01-01T00:00:00Z"}
            )
        },
    )
    store.upsert(running)
    terminal = _record(status="completed")
    terminal = terminal.model_copy(
        deep=True,
        update={"job": terminal.job.model_copy(update={"updated_at": "2026-07-22T12:02:00Z"})},
    )
    store.upsert(terminal)

    assert client.indexed == ["summary-1", "summary-1", "summary-1"]
    got = store.get("summary-1")
    assert got is not None
    assert got.job.status == "completed"
    assert got.job.created_at == "2026-07-22T12:00:00Z"
    assert len(store.query(MemoryQuery(group="summary"))) == 1
    assert len(store.list_jobs(JobFilters(group="summary"))) == 1


def test_elasticsearch_get_missing_returns_none() -> None:
    store = ElasticsearchMemoryStore(endpoint="http://unused", client=_FakeES())
    assert store.get("missing") is None


def test_build_search_body_filters() -> None:
    body = ElasticsearchMemoryStore._build_search_body(
        group="search",
        status="completed",
        sensor_id="cam-1",
        text="forklift",
        since="2026-01-01T00:00:00Z",
        limit=5,
    )
    assert body["size"] == 5
    assert "bool" in body["query"]
