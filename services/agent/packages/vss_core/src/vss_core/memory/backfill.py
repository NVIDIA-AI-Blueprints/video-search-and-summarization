# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reusable bounded backfill orchestration for derived memory embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from .backends.elasticsearch_embeddings import ElasticsearchEmbeddingStore
from .backends.elasticsearch_embeddings import EmbeddingSyncFailure
from .backends.elasticsearch_embeddings import EmbeddingSyncResult
from .embeddings import is_embedding_eligible
from .models import UnifiedMemoryRecord
from .store import MemoryStore
from .store import storage_id_for


@dataclass(frozen=True, slots=True)
class EmbeddingBackfillFailure:
    """Stable public representation of one failed record."""

    storage_id: str
    error: str


@dataclass(slots=True)
class EmbeddingBackfillResult:
    """Aggregate counts emitted by the CLI and available to library callers."""

    scanned: int = 0
    eligible: int = 0
    embedded: int = 0
    reused: int = 0
    skipped: int = 0
    failures: list[EmbeddingBackfillFailure] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return len(self.failures)

    def to_dict(self) -> dict[str, object]:
        """Return the exact stable JSON-compatible result shape."""
        return {
            "scanned": self.scanned,
            "eligible": self.eligible,
            "embedded": self.embedded,
            "reused": self.reused,
            "skipped": self.skipped,
            "failed": self.failed,
            "failures": [{"storage_id": failure.storage_id, "error": failure.error} for failure in self.failures],
        }


class EmbeddingBackfillService:
    """Scan authoritative memory and synchronize its eligible records."""

    def __init__(
        self,
        store: MemoryStore,
        embeddings: ElasticsearchEmbeddingStore,
    ) -> None:
        self._store = store
        self._embeddings = embeddings

    def run(
        self,
        *,
        batch_size: int,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> EmbeddingBackfillResult:
        """Backfill in bounded batches without materializing the full store."""
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if limit is not None and limit < 0:
            raise ValueError("limit must not be negative")

        result = EmbeddingBackfillResult()
        pending: list[UnifiedMemoryRecord] = []
        for record in self._store.scan(batch_size=batch_size, limit=limit):
            result.scanned += 1
            if not is_embedding_eligible(record):
                result.skipped += 1
                continue
            result.eligible += 1
            if dry_run:
                continue
            pending.append(record)
            if len(pending) == batch_size:
                self._sync_batch(pending, result)
                pending = []
        if pending:
            self._sync_batch(pending, result)
        return result

    def _sync_batch(
        self,
        records: list[UnifiedMemoryRecord],
        result: EmbeddingBackfillResult,
    ) -> None:
        try:
            outcomes = self._embeddings.sync_records(records)
        except Exception as error:
            outcomes = [EmbeddingSyncFailure(storage_id=storage_id_for(record), error=str(error)) for record in records]
        for outcome in outcomes:
            if isinstance(outcome, EmbeddingSyncFailure):
                result.failures.append(EmbeddingBackfillFailure(storage_id=outcome.storage_id, error=outcome.error))
            elif isinstance(outcome, EmbeddingSyncResult) and outcome.action == "reused":
                result.reused += 1
            elif isinstance(outcome, EmbeddingSyncResult):
                result.embedded += 1


__all__ = [
    "EmbeddingBackfillFailure",
    "EmbeddingBackfillResult",
    "EmbeddingBackfillService",
]
