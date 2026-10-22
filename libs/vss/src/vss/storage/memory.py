# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Transient in-process storage adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from weakref import WeakValueDictionary

from .base import (
    StorageAdapter,
    detach_records,
    validate_expected_revision,
    validate_identity,
)
from .errors import StorageBusyError, StorageConflictError, StorageNotFoundError
from .models import StorageSnapshot


class InMemoryStorageAdapter(StorageAdapter):
    """Store detached snapshots in memory; contents disappear on close/process exit."""

    @property
    def persistent(self) -> bool:
        """Memory snapshots are transient and do not survive process exit."""
        return False

    def __init__(
        self,
        *,
        max_snapshot_bytes: int = 64 * 1024 * 1024,
        max_records: int = 100_000,
        max_in_flight: int = 4,
    ) -> None:
        """Create a transient adapter with explicit positive safety limits."""
        from .models import StorageConfig

        config = StorageConfig(
            backend="memory",
            max_snapshot_bytes=max_snapshot_bytes,
            max_records=max_records,
            max_in_flight=max_in_flight,
        )
        self.max_snapshot_bytes = config.max_snapshot_bytes
        self.max_records = config.max_records
        self._records: dict[tuple[str, str], StorageSnapshot] = {}
        self._map_lock = threading.RLock()
        self._operation_locks: WeakValueDictionary[tuple[str, str], threading.Lock] = (
            WeakValueDictionary()
        )
        self._claim_locks: WeakValueDictionary[tuple[str, str], threading.Lock] = (
            WeakValueDictionary()
        )
        self._init_runtime(config.max_in_flight)

    def _key_lock(
        self,
        mapping: WeakValueDictionary[tuple[str, str], threading.Lock],
        key: tuple[str, str],
    ) -> threading.Lock:
        """Share a lock while owners/waiters hold it; forget idle keys."""
        with self._map_lock:
            lock = mapping.get(key)
            if lock is None:
                lock = threading.Lock()
                mapping[key] = lock
            return lock

    async def read(self, namespace: str, key: str) -> StorageSnapshot:
        """Return a copy of one committed snapshot."""
        namespace, key = validate_identity(namespace, key)

        def operation() -> StorageSnapshot:
            identity = (namespace, key)
            lock = self._key_lock(self._operation_locks, identity)
            with lock:
                with self._map_lock:
                    snapshot = self._records.get(identity)
                    if snapshot is None:
                        raise StorageNotFoundError(key)
                    return StorageSnapshot.model_validate(snapshot.model_dump())

        return await self._run(operation)

    async def write(
        self,
        namespace: str,
        key: str,
        records: Sequence[dict[str, Any]],
        *,
        expected_revision: int | None,
    ) -> StorageSnapshot:
        """Create or compare-and-swap a complete detached snapshot."""
        namespace, key = validate_identity(namespace, key)
        expected_revision = validate_expected_revision(expected_revision)

        def operation() -> StorageSnapshot:
            detached = detach_records(records, self.max_records)
            payload = self._encoded_payload(namespace, key, detached, 1)
            if len(payload) > self.max_snapshot_bytes:
                from .errors import StorageLimitError

                raise StorageLimitError("snapshot exceeds configured byte limit")
            identity = (namespace, key)
            lock = self._key_lock(self._operation_locks, identity)
            with lock:
                with self._map_lock:
                    current = self._records.get(identity)
                    if expected_revision is None:
                        if current is not None:
                            raise StorageConflictError("snapshot already exists")
                        revision = 1
                    else:
                        if current is None or current.revision != expected_revision:
                            raise StorageConflictError("snapshot revision changed")
                        revision = current.revision + 1
                    actual = self._encoded_payload(namespace, key, detached, revision)
                    if len(actual) > self.max_snapshot_bytes:
                        from .errors import StorageLimitError

                        raise StorageLimitError(
                            "snapshot exceeds configured byte limit"
                        )
                    result = StorageSnapshot(revision=revision, records=detached)
                    self._records[identity] = result
                    return StorageSnapshot.model_validate(result.model_dump())

        return await self._run(operation)

    @staticmethod
    def _encoded_payload(
        namespace: str,
        key: str,
        records: tuple[dict[str, Any], ...],
        revision: int,
    ) -> bytes:
        """Measure records using the exact file adapter snapshot framing."""
        header = {
            "format_version": "1.0",
            "schema_version": "1.0",
            "namespace": namespace,
            "key": key,
            "revision": revision,
            "record_count": len(records),
        }
        prefix = b"".join(
            json.dumps(
                item, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
            + b"\n"
            for item in (header, *records)
        )
        footer = {"sha256": hashlib.sha256(prefix).hexdigest()}
        return prefix + json.dumps(footer, separators=(",", ":")).encode() + b"\n"

    @asynccontextmanager
    async def exclusive(self, namespace: str, key: str) -> AsyncIterator[None]:
        """Acquire a process-local nonblocking claim independent of snapshot I/O."""
        namespace, key = validate_identity(namespace, key)
        identity = (namespace, key)
        self._claim_started()
        acquired = False
        try:
            lock = self._key_lock(self._claim_locks, identity)
            acquired_holder: list[bool] = []

            def acquire() -> bool:
                acquired_holder.append(lock.acquire(blocking=False))
                return acquired_holder[-1]

            try:
                acquired = await self._run(acquire)
            except asyncio.CancelledError:
                if acquired_holder and acquired_holder[-1]:
                    lock.release()
                raise
            if not acquired:
                raise StorageBusyError(f"claim is already held for {namespace}/{key}")
            try:
                yield
            finally:
                lock.release()
        finally:
            self._claim_finished()

    async def aclose(self) -> None:
        """Settle active operations and close this transient adapter."""
        try:
            await self._close_runtime()
        finally:
            if self._state == "closed":
                with self._map_lock:
                    self._records.clear()
                    self._operation_locks.clear()
                    self._claim_locks.clear()
