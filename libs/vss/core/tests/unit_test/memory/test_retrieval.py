# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Semantic and hybrid unified-memory retrieval tests."""

from __future__ import annotations

from typing import Any

import pytest

from vss_core.memory.backends.in_memory import InMemoryStore
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.service import MemoryService
from vss_core.memory.store import MemoryQuery
from vss_core.memory.store import storage_id_for


def _record(
    job_id: str,
    *,
    answer: str = "forklift",
    sensor: str = "camera-1",
    created_at: str = "2026-08-31T12:00:00Z",
    updated_at: str | None = None,
    record_id: str | None = None,
) -> UnifiedMemoryRecord:
    job: dict[str, Any] = {
        "job_id": job_id,
        "group": "summary",
        "status": "completed",
        "created_at": created_at,
    }
    if updated_at is not None:
        job["updated_at"] = updated_at
    if record_id is not None:
        job.update(record_type="event", record_id=record_id)
    return UnifiedMemoryRecord.model_validate(
        {
            "job": job,
            "input": {
                "query": answer,
                "sensors": [{"id": sensor}],
                "window": {
                    "start": {"timestamp": "2026-08-31T11:00:00Z"},
                    "end": {"timestamp": "2026-08-31T11:30:00Z"},
                },
            },
            "output": {"answer": answer},
        }
    )


class _Provider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        if self.error is not None:
            raise self.error
        return [0.1, 0.2, 0.3]


class _Semantic:
    def __init__(self, storage_ids: list[str], error: Exception | None = None) -> None:
        self.storage_ids = storage_ids
        self.error = error
        self.calls: list[tuple[MemoryQuery, list[float], int]] = []

    def sync_record(self, record: UnifiedMemoryRecord) -> None:
        return None

    def semantic_search(
        self,
        query: MemoryQuery,
        query_vector: list[float],
        candidate_count: int,
    ) -> list[str]:
        self.calls.append((query, query_vector, candidate_count))
        if self.error is not None:
            raise self.error
        return self.storage_ids


def _service(
    store: InMemoryStore,
    semantic: _Semantic,
    provider: _Provider,
    *,
    mode: str = "hybrid",
) -> MemoryService:
    return MemoryService(
        store,
        semantic_memory=semantic,
        embedding_provider=provider,
        retrieval_mode=mode,  # type: ignore[arg-type]
        semantic_candidate_count=50,
        rrf_rank_constant=60,
    )


def test_in_memory_get_many_preserves_order_duplicates_and_excludes_missing() -> None:
    store = InMemoryStore()
    first = store.upsert(_record("a"))
    second = store.upsert(_record("b"))

    assert store.get_many(["b", "missing", "a", "b"]) == [second, first, second]


def test_query_without_text_preserves_filtering_and_never_calls_provider() -> None:
    store = InMemoryStore()
    store.upsert(_record("a", sensor="camera-1"))
    expected = store.upsert(_record("b", sensor="camera-2"))
    provider = _Provider()
    semantic = _Semantic(["a"])
    service = _service(store, semantic, provider)

    assert service.query(MemoryQuery(sensor_id="camera-2", mode="semantic")) == [expected]
    assert provider.queries == []
    assert semantic.calls == []


def test_keyword_mode_is_unchanged_and_does_not_call_semantic_dependencies() -> None:
    store = InMemoryStore()
    older = store.upsert(_record("older", answer="forklift"))
    newer = store.upsert(_record("newer", answer="forklift forklift", created_at="2026-08-31T13:00:00Z"))
    provider = _Provider()
    semantic = _Semantic(["older"])
    service = _service(store, semantic, provider)

    assert service.query(MemoryQuery(text="forklift", mode="keyword")) == [newer, older]
    assert provider.queries == []
    assert semantic.calls == []


@pytest.mark.parametrize("mode", (None, "semantic", "hybrid"))
def test_service_without_semantic_dependencies_retains_keyword_behavior(mode: str | None) -> None:
    store = InMemoryStore()
    expected = store.upsert(_record("keyword", answer="forklift"))
    service = MemoryService(store, retrieval_mode="hybrid")

    assert service.query(MemoryQuery(text="forklift", mode=mode)) == [expected]  # type: ignore[arg-type]


def test_semantic_resolves_authoritative_ids_in_rank_order_and_skips_missing_or_stale() -> None:
    store = InMemoryStore()
    first = store.upsert(_record("first", sensor="camera-1"))
    store.upsert(_record("wrong-sensor", sensor="camera-2"))
    third = store.upsert(_record("third", sensor="camera-1"))
    provider = _Provider()
    semantic = _Semantic(["first", "missing", "wrong-sensor", "third"])
    service = _service(store, semantic, provider, mode="semantic")

    result = service.query(MemoryQuery(text="paraphrase", sensor_id="camera-1", limit=2))

    assert result == [first, third]
    assert provider.queries == ["paraphrase"]
    assert semantic.calls[0][2] == 50


def test_hybrid_uses_client_side_rrf_and_deduplicates_storage_ids() -> None:
    class RankedStore(InMemoryStore):
        def query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]:
            records = self.get_many(["a", "b"])
            return records[: query.limit]

    store = RankedStore()
    a = store.upsert(_record("a"))
    b = store.upsert(_record("b"))
    c = store.upsert(_record("c"))
    service = _service(store, _Semantic(["b", "c", "b"]), _Provider())

    assert service.query(MemoryQuery(text="anything", mode="hybrid", limit=3)) == [b, a, c]


@pytest.mark.parametrize(
    ("newer_b", "expected"),
    (
        (False, ["a", "b"]),
        (True, ["b", "a"]),
    ),
)
def test_hybrid_equal_scores_break_ties_by_recency_then_storage_id(newer_b: bool, expected: list[str]) -> None:
    class RankedStore(InMemoryStore):
        def query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]:
            return self.get_many(["a"])

    store = RankedStore()
    store.upsert(_record("a"))
    store.upsert(_record("b", updated_at="2026-08-31T13:00:00Z" if newer_b else None))
    service = _service(store, _Semantic(["b"]), _Provider())

    result = service.query(MemoryQuery(text="anything", mode="hybrid"))

    assert [storage_id_for(record) for record in result] == expected


@pytest.mark.parametrize("mode", ("semantic", "hybrid"))
@pytest.mark.parametrize("failure_at", ("provider", "index"))
def test_semantic_failures_warn_and_fall_back_to_keyword(
    mode: str,
    failure_at: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = InMemoryStore()
    expected = store.upsert(_record("keyword", answer="forklift"))
    provider = _Provider(RuntimeError("provider secret")) if failure_at == "provider" else _Provider()
    semantic = _Semantic([], RuntimeError("index secret")) if failure_at == "index" else _Semantic([])
    service = _service(store, semantic, provider)

    assert service.query(MemoryQuery(text="forklift", mode=mode)) == [expected]  # type: ignore[arg-type]
    assert "falling back to keyword" in caplog.text
    assert "secret" not in caplog.text


def test_keyword_failure_preserves_existing_exception_behavior() -> None:
    class FailingStore(InMemoryStore):
        def query(self, query: MemoryQuery) -> list[UnifiedMemoryRecord]:
            raise RuntimeError("authoritative unavailable")

    service = _service(FailingStore(), _Semantic([]), _Provider(RuntimeError("semantic unavailable")))
    with pytest.raises(RuntimeError, match="authoritative unavailable"):
        service.query(MemoryQuery(text="forklift", mode="semantic"))
