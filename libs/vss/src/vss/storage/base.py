# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Common adapter contract and bounded asynchronous worker lifecycle."""

from __future__ import annotations

import asyncio
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
from typing import Any, TypeVar

from .errors import (
    StorageBusyError,
    StorageClosedError,
    StorageConfigurationError,
)
from .models import StorageSnapshot

T = TypeVar("T")


class StorageAdapter(ABC):
    """Asynchronous compare-and-swap storage contract.

    Implementations own their resources. Callers close injected adapters only
    when they own them; ``persistent`` truthfully indicates restart durability.
    """

    @property
    @abstractmethod
    def persistent(self) -> bool:
        """Whether committed snapshots survive adapter and process shutdown."""

    @abstractmethod
    async def read(self, namespace: str, key: str) -> StorageSnapshot:
        """Return a detached snapshot.

        Raises ``StorageConfigurationError`` for invalid identities,
        ``StorageNotFoundError`` for a missing key, ``StorageCorruptError`` for
        invalid persisted data, ``StorageLimitError`` for configured bounds, and
        ``StorageClosedError`` once shutdown starts.
        """

    @abstractmethod
    async def write(
        self,
        namespace: str,
        key: str,
        records: Sequence[dict[str, Any]],
        *,
        expected_revision: int | None,
    ) -> StorageSnapshot:
        """Atomically create or compare-and-swap a complete snapshot.

        ``expected_revision=None`` creates only when the key is missing; a
        positive revision replaces only that exact committed revision. Invalid
        JSON or identities raise ``StorageConfigurationError``; configured size
        or record bounds raise ``StorageLimitError``; stale state raises
        ``StorageConflictError``. Persisted damage and I/O failures raise the
        corresponding typed storage errors, including
        ``StorageIndeterminateError`` after a visible but uncertain commit.
        """

    @abstractmethod
    def exclusive(self, namespace: str, key: str) -> AbstractAsyncContextManager[None]:
        """Claim one namespace/key without waiting.

        A competing claim raises ``StorageBusyError`` immediately. The claim is
        independent of snapshot operation locks and lasts until context exit.
        """

    @abstractmethod
    async def aclose(self) -> None:
        """Reject new work, settle admitted work and release owned resources.

        Close is idempotent. If an exclusive claim is active,
        ``StorageBusyError`` leaves the adapter open so the owner can finish.
        Cancellation is re-raised only after admitted operations and shutdown
        have settled.
        """

    async def __aenter__(self) -> StorageAdapter:
        """Return this adapter for use as an async context manager."""
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Close this adapter when its context exits."""
        await self.aclose()

    def _init_runtime(self, max_in_flight: int) -> None:
        self._max_in_flight = max_in_flight
        self._executor = ThreadPoolExecutor(
            max_workers=max_in_flight, thread_name_prefix="vss-storage"
        )
        self._admission = asyncio.Semaphore(max_in_flight)
        self._runtime_lock = threading.RLock()
        self._state = "open"
        self._active_operations = 0
        self._active_claims = 0
        self._drained = asyncio.Event()
        self._drained.set()
        self._closed_event = asyncio.Event()

    def _ensure_open(self) -> None:
        with self._runtime_lock:
            if self._state != "open":
                raise StorageClosedError("storage adapter is closing or closed")

    def _operation_started(self) -> None:
        """Keep a logical async operation alive across worker-free waits."""
        with self._runtime_lock:
            if self._state != "open":
                raise StorageClosedError("storage adapter is closing or closed")
            self._active_operations += 1
            self._drained.clear()

    def _operation_finished(self) -> None:
        with self._runtime_lock:
            self._active_operations -= 1
            if self._active_operations == 0:
                self._drained.set()

    async def _run(
        self, function: Callable[..., T], *args: Any, _admitted: bool = False
    ) -> T:
        """Run a blocking operation with bounded admission and settled cancel."""
        if _admitted:
            with self._runtime_lock:
                if self._state == "closed":
                    raise StorageClosedError("storage adapter is closed")
        else:
            self._operation_started()
        acquired = False
        try:
            await self._admission.acquire()
            acquired = True
            with self._runtime_lock:
                if self._state == "closed":
                    raise StorageClosedError("storage adapter is closed")
            loop = asyncio.get_running_loop()
            task = loop.run_in_executor(self._executor, function, *args)
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Native filesystem work cannot safely be abandoned. Repeated
                # cancellation requests are delayed until the commit settles.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        break
                if task.done() and not task.cancelled():
                    try:
                        task.exception()
                    except BaseException:
                        pass
                raise
        finally:
            if acquired:
                self._admission.release()
            if not _admitted:
                self._operation_finished()

    async def _close_runtime(self) -> None:
        initiate = False
        with self._runtime_lock:
            if self._state == "closed":
                return
            if self._state == "open":
                if self._active_claims:
                    raise StorageBusyError(
                        "cannot close while an exclusive claim is active"
                    )
                self._state = "closing"
                initiate = True
        cancelled = False
        try:
            if initiate:
                drain_task = asyncio.create_task(self._drained.wait())
                cancelled |= await _wait_settled(drain_task)
                shutdown_task = asyncio.create_task(
                    asyncio.to_thread(
                        self._executor.shutdown, wait=True, cancel_futures=False
                    )
                )
                cancelled |= await _wait_settled(shutdown_task)
                with self._runtime_lock:
                    self._state = "closed"
            else:
                closed_task = asyncio.create_task(self._closed_event.wait())
                cancelled |= await _wait_settled(closed_task)
        finally:
            if initiate:
                with self._runtime_lock:
                    self._state = "closed"
                    self._closed_event.set()
        if cancelled:
            raise asyncio.CancelledError

    def _claim_started(self) -> None:
        with self._runtime_lock:
            if self._state != "open":
                raise StorageClosedError("storage adapter is closing or closed")
            self._active_claims += 1

    def _claim_finished(self) -> None:
        with self._runtime_lock:
            self._active_claims -= 1


def validate_identity(namespace: Any, key: Any) -> tuple[str, str]:
    """Validate the portable namespace and bounded opaque key contract."""
    import re

    if (
        type(namespace) is not str
        or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", namespace) is None
    ):
        raise StorageConfigurationError("namespace must be a lowercase token")
    if type(key) is not str or not key:
        raise StorageConfigurationError("key must be a nonempty string")
    try:
        encoded = key.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise StorageConfigurationError("key must be valid UTF-8") from exc
    if len(encoded) > 4096:
        raise StorageConfigurationError("key exceeds 4096 UTF-8 bytes")
    return namespace, key


async def _wait_settled[T](task: asyncio.Task[T]) -> bool:
    """Await a cleanup task despite repeated cancellation and report it."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    return cancelled


def detach_records(records: Any, max_records: int) -> tuple[dict[str, Any], ...]:
    """Validate finite JSON objects and return a deeply detached tuple."""
    import json
    import math

    if not isinstance(records, (list, tuple)):
        raise StorageConfigurationError("records must be a list or tuple")
    if len(records) > max_records:
        from .errors import StorageLimitError

        raise StorageLimitError("record count exceeds configured limit")

    def validate(value: Any, seen: set[int], depth: int = 0) -> None:
        if depth > 256:
            raise StorageConfigurationError("JSON nesting exceeds 256 levels")
        if value is None or type(value) in (str, bool, int):
            return
        if type(value) is float:
            if not math.isfinite(value):
                raise StorageConfigurationError("JSON numbers must be finite")
            return
        if type(value) in (list, dict):
            ident = id(value)
            if ident in seen:
                raise StorageConfigurationError("cyclic JSON value")
            seen.add(ident)
            try:
                if type(value) is dict:
                    for key, item in value.items():
                        if type(key) is not str:
                            raise StorageConfigurationError(
                                "JSON object keys must be strings"
                            )
                        validate(item, seen, depth + 1)
                else:
                    for item in value:
                        validate(item, seen, depth + 1)
            finally:
                seen.remove(ident)
            return
        raise StorageConfigurationError("value is not finite JSON data")

    detached: list[dict[str, Any]] = []
    for record in records:
        if type(record) is not dict:
            raise StorageConfigurationError("every record must be a JSON object")
        validate(record, set())
        try:
            encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
            clone = json.loads(encoded)
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise StorageConfigurationError("record is not valid JSON") from exc
        detached.append(clone)
    return tuple(deepcopy(detached))


def validate_expected_revision(revision: Any) -> int | None:
    """Reject bools and coercions for the create/CAS revision argument."""
    if revision is None:
        return None
    if type(revision) is not int or revision <= 0:
        raise StorageConfigurationError(
            "expected_revision must be None or a positive integer"
        )
    return revision
