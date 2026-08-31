# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for bounded embedding backfill orchestration."""

from __future__ import annotations

from typing import Any

from vss_core.memory.backends.elasticsearch_embeddings import EmbeddingSyncFailure
from vss_core.memory.backends.elasticsearch_embeddings import EmbeddingSyncResult
from vss_core.memory.backends.in_memory import InMemoryStore
from vss_core.memory.backfill import EmbeddingBackfillService
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.store import storage_id_for


def _record(
    job_id: str,
    *,
    status: str = "completed",
    record_id: str | None = None,
) -> UnifiedMemoryRecord:
    job: dict[str, Any] = {
        "job_id": job_id,
        "group": "summary",
        "status": status,
        "created_at": "2026-08-31T12:00:00Z",
    }
    if record_id is not None:
        job.update(record_type="event", record_id=record_id)
    return UnifiedMemoryRecord.model_validate(
        {
            "job": job,
            "input": {"query": "What happened?"},
            "output": {"answer": f"answer {record_id or job_id}"},
        }
    )


class _Embeddings:
    def __init__(self, actions: dict[str, str] | None = None) -> None:
        self.actions = actions or {}
        self.calls: list[list[str]] = []

    def sync_records(
        self,
        records: list[UnifiedMemoryRecord],
    ) -> list[EmbeddingSyncResult | EmbeddingSyncFailure]:
        ids = [storage_id_for(record) for record in records]
        self.calls.append(ids)
        outcomes: list[EmbeddingSyncResult | EmbeddingSyncFailure] = []
        for storage_id, record in zip(ids, records, strict=True):
            action = self.actions.get(storage_id, "created")
            if action == "failed":
                outcomes.append(EmbeddingSyncFailure(storage_id, f"failure for {storage_id}"))
            else:
                outcomes.append(
                    EmbeddingSyncResult(
                        storage_id=storage_id,
                        index="embeddings",
                        action=action,  # type: ignore[arg-type]
                        record=record,
                    )
                )
        return outcomes


def _service(
    actions: dict[str, str] | None = None,
) -> tuple[EmbeddingBackfillService, InMemoryStore, _Embeddings]:
    store = InMemoryStore()
    embeddings = _Embeddings(actions)
    return EmbeddingBackfillService(store, embeddings), store, embeddings  # type: ignore[arg-type]


def test_dry_run_scans_parent_and_child_without_provider_or_writes() -> None:
    service, store, embeddings = _service()
    store.upsert(_record("b", record_id="child"))
    store.upsert(_record("a"))
    store.upsert(_record("c", status="failed"))
    writes_before = list(store.upsert_ids)

    result = service.run(batch_size=2, dry_run=True)

    assert result.to_dict() == {
        "scanned": 3,
        "eligible": 2,
        "embedded": 0,
        "reused": 0,
        "skipped": 1,
        "failed": 0,
        "failures": [],
    }
    assert embeddings.calls == []
    assert store.upsert_ids == writes_before


def test_batches_in_storage_id_order_counts_reuse_and_continues_failures() -> None:
    service, store, embeddings = _service(
        {
            "a": "reused",
            "a#event#child": "reembedded",
            "b": "failed",
            "c": "created",
        }
    )
    for record in (
        _record("c"),
        _record("a", record_id="child"),
        _record("b"),
        _record("a"),
    ):
        store.upsert(record)

    result = service.run(batch_size=2)

    assert embeddings.calls == [["a", "a#event#child"], ["b", "c"]]
    assert result.to_dict() == {
        "scanned": 4,
        "eligible": 4,
        "embedded": 2,
        "reused": 1,
        "skipped": 0,
        "failed": 1,
        "failures": [{"storage_id": "b", "error": "failure for b"}],
    }


def test_limit_is_applied_in_deterministic_storage_id_order() -> None:
    service, store, embeddings = _service()
    for job_id in ("c", "a", "b"):
        store.upsert(_record(job_id))

    result = service.run(batch_size=5, limit=2)

    assert result.scanned == 2
    assert embeddings.calls == [["a", "b"]]
