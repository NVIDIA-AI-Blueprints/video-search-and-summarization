# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Focused companion-index and authoritative-write synchronization tests."""

from __future__ import annotations

from typing import Any

from elasticsearch import BadRequestError
from elasticsearch import NotFoundError as ESNotFoundError
import pytest

from vss_core._foundation.errors import ConfigurationError
from vss_core.memory.adapters import RecordBundle
from vss_core.memory.backends.elasticsearch_embeddings import EMBEDDING_SCHEMA
from vss_core.memory.backends.elasticsearch_embeddings import IMPLEMENTATION_VERSION
from vss_core.memory.backends.elasticsearch_embeddings import ElasticsearchEmbeddingStore
from vss_core.memory.backends.in_memory import InMemoryStore
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.service import MemoryService
from vss_core.memory.store import MemoryQuery
from vss_core.memory.store import storage_id_for

MODEL = "embed-model"
INDEX = "vss-memory-embeddings-v1"


def _record(
    *,
    job_id: str = "job-1",
    status: str = "completed",
    record_id: str | None = None,
    answer: str = "Forklift arrived.",
    embedding: list[dict[str, Any]] | None = None,
) -> UnifiedMemoryRecord:
    job: dict[str, Any] = {
        "job_id": job_id,
        "group": "summary",
        "status": status,
        "created_at": "2026-08-31T12:00:00Z",
        "updated_at": "2026-08-31T12:01:00Z",
    }
    if record_id is not None:
        job.update(record_id=record_id, record_type="event")
    return UnifiedMemoryRecord.model_validate(
        {
            "job": job,
            "input": {
                "query": "What happened?",
                "sensors": [{"id": "camera-1"}],
                "window": {
                    "start": {"timestamp": "2026-08-31T11:00:00Z"},
                    "end": {"timestamp": "2026-08-31T11:30:00Z"},
                },
            },
            "output": {"answer": answer, "embedding": embedding},
        }
    )


class _Provider:
    model = MODEL
    dimensions = 3

    def __init__(self) -> None:
        self.passages: list[str] = []

    def embed_passages(self, texts: Any) -> list[list[float]]:
        self.passages.extend(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    def close(self) -> None:
        return None


class _Indices:
    def __init__(self, client: _FakeES) -> None:
        self.client = client

    def exists(self, *, index: str) -> bool:
        return self.client.mapping is not None

    def create(self, *, index: str, mappings: dict[str, Any]) -> dict[str, Any]:
        self.client.mapping = mappings
        self.client.created += 1
        return {"acknowledged": True}

    def get_mapping(self, *, index: str) -> dict[str, Any]:
        return {index: {"mappings": self.client.mapping}}


class _FakeES:
    def __init__(self) -> None:
        self.mapping: dict[str, Any] | None = None
        self.created = 0
        self.docs: dict[str, dict[str, Any]] = {}
        self.index_calls: list[str] = []
        self.update_calls: list[str] = []
        self.search_hits: list[str] = []
        self.search_body: dict[str, Any] | None = None
        self.indices = _Indices(self)

    def get(self, *, index: str, id: str, source_excludes: list[str] | None = None) -> dict[str, Any]:
        if id not in self.docs:
            raise ESNotFoundError("not found", {}, {"_id": id})
        source = dict(self.docs[id])
        for excluded in source_excludes or []:
            source.pop(excluded, None)
        return {"_source": source}

    def index(self, *, index: str, id: str, document: dict[str, Any], refresh: str | None = None) -> dict[str, Any]:
        self.docs[id] = dict(document)
        self.index_calls.append(id)
        return {"result": "created"}

    def update(self, *, index: str, id: str, doc: dict[str, Any], refresh: str | None = None) -> dict[str, Any]:
        self.docs[id].update(doc)
        self.update_calls.append(id)
        return {"result": "updated"}

    def delete(self, *, index: str, id: str, refresh: str | None = None) -> dict[str, Any]:
        if id not in self.docs:
            raise ESNotFoundError("not found", {}, {"_id": id})
        del self.docs[id]
        return {"result": "deleted"}

    def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.search_body = body
        return {
            "hits": {
                "hits": [{"_id": storage_id, "_source": {"storage_id": storage_id}} for storage_id in self.search_hits]
            }
        }

    def close(self) -> None:
        return None


def _backend(
    *,
    client: _FakeES | None = None,
    provider: _Provider | None = None,
    authoritative: InMemoryStore | None = None,
) -> tuple[ElasticsearchEmbeddingStore, _FakeES, _Provider, InMemoryStore]:
    es = client or _FakeES()
    embedder = provider or _Provider()
    raw = authoritative or InMemoryStore()
    return (
        ElasticsearchEmbeddingStore(
            endpoint="http://unused",
            index=INDEX,
            provider=embedder,
            authoritative_store=raw,
            client=es,  # type: ignore[arg-type]
        ),
        es,
        embedder,
        raw,
    )


def test_exact_strict_mapping_meta_and_complete_filter_document() -> None:
    backend, client, _, raw = _backend()
    record = _record(record_id="event-1")
    raw.upsert(record)
    result = backend.sync_record(record)

    assert result.storage_id == "job-1#event#event-1"
    assert client.created == 1
    assert client.mapping == backend.mapping
    assert client.mapping["dynamic"] == "strict"
    assert client.mapping["_meta"] == {
        "model": MODEL,
        "dimensions": 3,
        "schema": EMBEDDING_SCHEMA,
        "implementation_version": IMPLEMENTATION_VERSION,
    }
    assert client.mapping["properties"]["vector"] == {
        "type": "dense_vector",
        "dims": 3,
        "index": True,
        "similarity": "cosine",
    }
    document = client.docs[result.storage_id]
    assert document["vector"] == [0.1, 0.2, 0.3]
    assert document["storage_id"] == result.storage_id
    assert document["job_id"] == "job-1"
    assert document["record_id"] == "event-1"
    assert document["record_type"] == "event"
    assert document["group"] == "summary"
    assert document["status"] == "completed"
    assert document["is_child"] is True
    assert document["schema"] == EMBEDDING_SCHEMA
    assert document["content"].startswith("Group: summary")
    assert document["sensor_ids"] == ["camera-1"]
    assert document["window_start"] == "2026-08-31T11:00:00Z"
    assert document["window_end"] == "2026-08-31T11:30:00Z"


def test_concurrent_first_create_accepts_other_process_valid_mapping() -> None:
    backend, client, _, raw = _backend()

    class RacingIndices(_Indices):
        def create(self, *, index: str, mappings: dict[str, Any]) -> dict[str, Any]:
            self.client.mapping = mappings
            raise BadRequestError(
                "already exists",
                {},
                {"error": {"type": "resource_already_exists_exception"}},
            )

    client.indices = RacingIndices(client)
    record = _record()
    raw.upsert(record)
    assert backend.sync_record(record).action == "created"
    assert "job-1" in client.docs


@pytest.mark.parametrize(
    "mutate",
    [
        lambda mapping: mapping["_meta"].update(model="other"),
        lambda mapping: mapping["_meta"].update(dimensions=4),
        lambda mapping: mapping["properties"]["vector"].update(dims=4),
    ],
)
def test_mismatch_requires_new_versioned_index_and_backfill(mutate: Any) -> None:
    backend, client, _, _ = _backend()
    client.mapping = backend.mapping
    mutate(client.mapping)
    with pytest.raises(ConfigurationError, match="new versioned embedding index and backfill"):
        backend.sync_record(_record())
    assert not client.docs


def test_hash_reuse_updates_metadata_without_inline_vector_or_reembedding() -> None:
    backend, client, provider, raw = _backend()
    first = _record(embedding=[{"es_ref": "other-index", "doc_ids": ["other"]}])
    raw.upsert(first)
    backend.sync_record(first)
    assert len(provider.passages) == 1

    second = first.model_copy(update={"job": first.job.model_copy(update={"status": "partial"})})
    result = backend.sync_record(second)
    assert result.action == "reused"
    assert len(provider.passages) == 1
    assert client.update_calls == ["job-1"]
    assert client.docs["job-1"]["vector"] == [0.1, 0.2, 0.3]
    backend.sync_record(result.record)
    assert client.update_calls == ["job-1"]
    stored = raw.get("job-1")
    assert stored is not None and stored.output is not None
    assert [reference.es_ref for reference in stored.output.embedding or []] == ["other-index", f"{INDEX}/job-1"]
    assert all(reference.model_dump().get("vector") is None for reference in stored.output.embedding or [])


def test_changed_content_reembeds_and_ineligible_deletes_only_owned_reference() -> None:
    backend, client, provider, raw = _backend()
    record = _record(embedding=[{"es_ref": "other-index", "doc_ids": ["other"]}])
    raw.upsert(record)
    backend.sync_record(record)
    changed = _record(answer="Truck arrived.")
    assert backend.sync_record(changed).action == "reembedded"
    assert len(provider.passages) == 2

    failed = _record(
        status="failed",
        answer="Truck arrived.",
        embedding=[
            {"es_ref": "other-index", "doc_ids": ["other"]},
            {"es_ref": f"{INDEX}/job-1", "doc_ids": ["job-1"]},
        ],
    )
    raw.upsert(failed)
    assert backend.sync_record(failed).action == "deleted"
    assert "job-1" not in client.docs
    stored = raw.get("job-1")
    assert stored is not None and stored.output is not None
    assert [reference.es_ref for reference in stored.output.embedding or []] == ["other-index"]


def test_service_is_authoritative_first_nonfatal_and_does_not_recurse(caplog: pytest.LogCaptureFixture) -> None:
    events: list[str] = []

    class Store(InMemoryStore):
        def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
            events.append(f"write:{storage_id_for(record)}")
            return super().upsert(record)

    class Semantic:
        def sync_record(self, record: UnifiedMemoryRecord) -> None:
            events.append(f"sync:{storage_id_for(record)}")
            raise RuntimeError("secret [0.1, 0.2, 0.3]")

    service = MemoryService(Store(), semantic_memory=Semantic())
    persisted = service.upsert(_record())
    assert persisted.output is not None and persisted.output.embedding is None
    assert events == ["write:job-1", "sync:job-1"]
    assert "secret" not in caplog.text
    assert "0.1" not in caplog.text


def test_bundle_finishes_writes_then_syncs_final_partial_parent_and_successful_deduped_children() -> None:
    events: list[str] = []

    class Store(InMemoryStore):
        def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
            doc_id = storage_id_for(record)
            events.append(f"write:{doc_id}:{record.job.status}")
            if record.job.record_id == "bad":
                raise RuntimeError("child failed")
            return super().upsert(record)

    class Semantic:
        def __init__(self) -> None:
            self.records: list[UnifiedMemoryRecord] = []

        def sync_record(self, record: UnifiedMemoryRecord) -> None:
            events.append(f"sync:{storage_id_for(record)}:{record.job.status}")
            self.records.append(record)

    semantic = Semantic()
    service = MemoryService(Store(), semantic_memory=semantic)
    parent = _record()
    parent = parent.model_copy(
        update={"output": parent.output.model_copy(update={"ext": {"event_count": 3}}) if parent.output else None}
    )
    duplicate_a = _record(record_id="same", answer="first")
    duplicate_b = _record(record_id="same", answer="second")
    bad = _record(record_id="bad")
    result = service.upsert_bundle(RecordBundle(parent=parent, children=(duplicate_a, bad, duplicate_b)))

    assert not result.ok
    first_sync = next(index for index, event in enumerate(events) if event.startswith("sync:"))
    assert all(event.startswith("write:") for event in events[:first_sync])
    assert [storage_id_for(record) for record in semantic.records] == ["job-1", "job-1#event#same"]
    assert semantic.records[0].job.status == "partial"
    assert semantic.records[0].output is not None
    assert semantic.records[0].output.ext == {"event_count": 1}
    assert semantic.records[1].output is not None and semantic.records[1].output.answer == "second"


def test_semantic_search_builds_filtered_knn_and_returns_ordered_storage_ids() -> None:
    backend, client, _, _ = _backend()
    client.mapping = backend.mapping
    client.search_hits = ["job-2#event#event-2", "job-1"]
    query = MemoryQuery(
        text="forklift paraphrase",
        job_id="job-1",
        group="summary",
        status="completed",
        sensor_id="camera-1",
        record_type="event",
        record_id="event-1",
        since="2026-08-31T10:00:00Z",
        until="2026-08-31T12:00:00Z",
        time_field="window",
    )

    assert backend.semantic_search(query, [0.1, 0.2, 0.3], 25) == ["job-2#event#event-2", "job-1"]
    assert client.search_body is not None
    assert client.search_body["size"] == 25
    knn = client.search_body["knn"]
    assert knn["field"] == "vector"
    assert knn["query_vector"] == [0.1, 0.2, 0.3]
    assert knn["k"] == knn["num_candidates"] == 25
    filters = knn["filter"]["bool"]["filter"]
    assert {"term": {"job_id": "job-1"}} in filters
    assert {"term": {"group": "summary"}} in filters
    assert {"term": {"status": "completed"}} in filters
    assert {"term": {"sensor_ids": "camera-1"}} in filters
    assert {"term": {"record_type": "event"}} in filters
    assert {"term": {"record_id": "event-1"}} in filters
    assert any("window_start" in str(item) for item in filters)
    assert any("window_end" in str(item) for item in filters)


@pytest.mark.parametrize(("parents_only", "include_children"), ((True, True), (False, False)))
def test_semantic_search_filters_out_children(parents_only: bool, include_children: bool) -> None:
    backend, client, _, _ = _backend()
    client.mapping = backend.mapping

    backend.semantic_search(
        MemoryQuery(text="summary", parents_only=parents_only, include_children=include_children),
        [0.1, 0.2, 0.3],
        5,
    )

    assert client.search_body is not None
    assert {"term": {"is_child": False}} in client.search_body["knn"]["filter"]["bool"]["filter"]


def test_semantic_search_created_at_range_and_dimension_validation() -> None:
    backend, client, _, _ = _backend()
    client.mapping = backend.mapping

    backend.semantic_search(
        MemoryQuery(
            text="summary",
            since="2026-08-30T10:00:00Z",
            until="2026-08-31T12:00:00Z",
        ),
        [0.1, 0.2, 0.3],
        5,
    )

    assert client.search_body is not None
    filters = client.search_body["knn"]["filter"]["bool"]["filter"]
    assert {
        "range": {
            "created_at": {
                "gte": "2026-08-30T10:00:00Z",
                "lte": "2026-08-31T12:00:00Z",
            }
        }
    } in filters
    with pytest.raises(ConfigurationError, match="query embedding has 2 dimensions"):
        backend.semantic_search(MemoryQuery(text="summary"), [0.1, 0.2], 5)
