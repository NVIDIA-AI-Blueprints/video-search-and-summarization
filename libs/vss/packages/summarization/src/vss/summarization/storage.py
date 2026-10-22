# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Chunk-store abstractions for deferred summarization."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from vss.ingestion.contracts import VlmChunkResponse
from vss.ingestion.persistence import ObservationStore
from vss.storage import FileStorageAdapter, StorageAdapter


class ChunkStore(ABC):
    """Storage interface used by ``summarize_from_store``."""

    @abstractmethod
    async def store_chunks(self, job_id: str, chunks: list[VlmChunkResponse]) -> None:
        """Persist chunks for a job."""

    @abstractmethod
    async def get_chunks(self, job_id: str) -> list[VlmChunkResponse]:
        """Retrieve chunks for a job."""


class InMemoryChunkStore(ChunkStore):
    """Concurrency-safe, process-local chunk storage."""

    def __init__(self) -> None:
        self._chunks: dict[str, list[VlmChunkResponse]] = {}
        self._lock = asyncio.Lock()

    async def store_chunks(self, job_id: str, chunks: list[VlmChunkResponse]) -> None:
        if not job_id:
            raise ValueError("job_id must not be empty")
        async with self._lock:
            self._chunks[job_id] = [chunk.model_copy(deep=True) for chunk in chunks]

    async def get_chunks(self, job_id: str) -> list[VlmChunkResponse]:
        async with self._lock:
            try:
                return [chunk.model_copy(deep=True) for chunk in self._chunks[job_id]]
            except KeyError as error:
                raise KeyError(f"unknown summarization job {job_id!r}") from error


class AdapterChunkStore(ChunkStore):
    """Complete-caption facade over a caller-owned storage adapter.

    ``store_chunks`` replaces a list under the shared per-job claim and preserves
    caller order. Reads return detached existing response models; missing jobs
    raise KeyError, corrupt data fails closed. Persistent projection strips signed
    URL credentials and transient artifact metadata. This facade never closes an
    injected adapter; its caller must keep it open until all operations drain.
    """

    def __init__(self, storage: StorageAdapter) -> None:
        self._observations = ObservationStore(storage)

    async def store_chunks(self, job_id: str, chunks: list[VlmChunkResponse]) -> None:
        """Replace a complete caption list; a concurrent ingestion claim is busy."""
        await self._observations.store_chunks(job_id, chunks)

    async def get_chunks(self, job_id: str) -> list[VlmChunkResponse]:
        """Retrieve complete detached responses, preserving source clocks/order."""
        return await self._observations.get_chunks(job_id)

    async def __aenter__(self) -> AdapterChunkStore:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Do nothing: the injected adapter remains caller-owned."""


class FileChunkStore(AdapterChunkStore):
    """Convenience caption store owning a FileStorageAdapter.

    Use ``async with FileChunkStore(root=...)`` to close its workers. The default
    root is ~/.vss, used only when this store is explicitly constructed. Root and
    safety limits have the same meanings as FileStorageAdapter. Existing source
    video bytes and temporary clips are never copied into this store.
    """

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        max_snapshot_bytes: int = 64 * 1024 * 1024,
        max_records: int = 100_000,
        max_in_flight: int = 4,
        lock_wait_timeout_s: float = 30.0,
    ) -> None:
        self._owned_adapter = FileStorageAdapter(
            root=root,
            max_snapshot_bytes=max_snapshot_bytes,
            max_records=max_records,
            max_in_flight=max_in_flight,
            lock_wait_timeout_s=lock_wait_timeout_s,
        )
        super().__init__(self._owned_adapter)

    async def aclose(self) -> None:
        """Drain operations and close the adapter constructed by this facade."""
        await self._owned_adapter.aclose()
