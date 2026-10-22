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

"""Bounded in-process queue for ingestion jobs."""

from __future__ import annotations

import asyncio
import os
import uuid
import weakref
from collections import deque
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from .exceptions import IngestionQueueFullError

DEFAULT_MAX_CONCURRENCY = 10
DEFAULT_QUEUE_SIZE = 100


class IngestionJobState(StrEnum):
    """Lifecycle state of an ingestion job."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestionJob[T](Awaitable[T]):
    """Awaitable handle for work submitted to an :class:`IngestionQueue`."""

    def __init__(
        self,
        job_id: UUID,
        future: asyncio.Future[T],
        queue: IngestionQueue,
    ) -> None:
        self._id = job_id
        self._future = future
        self._queue = queue
        self._state = IngestionJobState.PENDING
        self._task: asyncio.Task[None] | None = None
        self._closed: asyncio.Future[None] = queue._loop.create_future()

    @property
    def id(self) -> UUID:
        """Return the unique job identifier."""

        return self._id

    @property
    def state(self) -> IngestionJobState:
        """Return the current lifecycle state."""

        return self._state

    def done(self) -> bool:
        """Return whether the job reached a terminal state."""

        return self._future.done()

    def cancelled(self) -> bool:
        """Return whether the job was cancelled."""

        return self._future.cancelled()

    def cancel(self) -> bool:
        """Cancel pending or running work."""

        return self._queue.cancel(self)

    async def wait_closed(self) -> None:
        """Wait for worker cleanup, independently of result cancellation.

        ``cancel()`` and awaiting a cancelled job report cancellation immediately.
        Await this method before closing injected resources or restarting the
        same durable job ID. Cancelling a waiter does not cancel shared cleanup.
        """
        await asyncio.shield(self._closed)

    def result(self) -> T:
        """Return the completed result or raise its stored exception."""

        return self._future.result()

    def exception(self) -> BaseException | None:
        """Return the completed job exception."""

        return self._future.exception()

    def __await__(self) -> Generator[Any, None, T]:
        return self._future.__await__()


@dataclass(slots=True)
class _QueuedWork[T]:
    job: IngestionJob[T]
    factory: Callable[[], Awaitable[T]]


class IngestionQueue:
    """Bounded FIFO queue with limited concurrent execution."""

    def __init__(
        self,
        *,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_pending: int = DEFAULT_QUEUE_SIZE,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        self._loop = asyncio.get_running_loop()
        self._max_concurrency = max_concurrency
        self._max_pending = max_pending
        self._pending: deque[_QueuedWork[Any]] = deque()
        self._entries: dict[UUID, _QueuedWork[Any]] = {}
        self._running: dict[UUID, asyncio.Task[None]] = {}

    @property
    def max_concurrency(self) -> int:
        """Return the active-job limit."""

        return self._max_concurrency

    @property
    def max_pending(self) -> int:
        """Return the pending-job limit."""

        return self._max_pending

    @property
    def pending_count(self) -> int:
        """Return the number of jobs waiting to run."""

        return len(self._pending)

    @property
    def running_count(self) -> int:
        """Return the number of active jobs."""

        return len(self._running)

    def submit[T](
        self, factory: Callable[[], Awaitable[T]], *, job_id: UUID | None = None
    ) -> IngestionJob[T]:
        """Submit bounded work with an optional stable UUID.

        Duplicate active IDs and non-UUID explicit IDs raise ValueError. The ID
        can be reused after ``wait_closed()`` completes, for an explicit restart.
        """

        self._require_owner_loop()
        if (
            len(self._running) >= self._max_concurrency
            and len(self._pending) >= self._max_pending
        ):
            raise IngestionQueueFullError(
                "ingestion queue is full "
                f"({self._max_concurrency} running, {self._max_pending} pending)"
            )

        if job_id is not None and not isinstance(job_id, UUID):
            raise ValueError("job_id must be a UUID")
        selected_id = job_id if job_id is not None else uuid.uuid4()
        if selected_id in self._entries:
            raise ValueError("job_id is already pending or running")
        future = self._loop.create_future()
        job = IngestionJob(selected_id, future, self)
        entry = _QueuedWork(job=job, factory=factory)
        self._entries[job.id] = entry
        self._pending.append(entry)
        future.add_done_callback(
            lambda completed, submitted=job: self._future_completed(
                submitted, completed
            )
        )
        self._drain()
        return job

    def cancel(self, job: IngestionJob[Any]) -> bool:
        """Cancel a job owned by this queue."""

        self._require_owner_loop()
        entry = self._entries.get(job.id)
        if (
            entry is None
            or entry.job is not job
            or job.state is IngestionJobState.CANCELLED
            or (job.done() and not job.cancelled())
        ):
            return False

        job._state = IngestionJobState.CANCELLED
        if job._task is not None:
            job._task.cancel()
        else:
            try:
                self._pending.remove(entry)
            except ValueError:
                pass
            self._entries.pop(job.id, None)
            job._closed.set_result(None)
        job._future.cancel()
        self._drain()
        return True

    def _drain(self) -> None:
        while self._pending and len(self._running) < self._max_concurrency:
            entry = self._pending.popleft()
            if entry.job.cancelled():
                # An awaiting task can cancel this result Future before its
                # callback gets a turn. Complete pending-job cleanup here rather
                # than leaving wait_closed dependent on an entry we just removed.
                entry.job._state = IngestionJobState.CANCELLED
                self._entries.pop(entry.job.id, None)
                if not entry.job._closed.done():
                    entry.job._closed.set_result(None)
                continue
            entry.job._state = IngestionJobState.RUNNING
            task = self._loop.create_task(self._execute(entry))
            entry.job._task = task
            self._running[entry.job.id] = task
            # A task cancelled before its first instruction never enters its
            # coroutine's finally block; the callback still releases ownership.
            task.add_done_callback(
                lambda completed, submitted=entry.job: self._finished(submitted)
            )

    async def _execute[T](self, entry: _QueuedWork[T]) -> None:
        job = entry.job
        try:
            result = await entry.factory()
        except asyncio.CancelledError:
            job._state = IngestionJobState.CANCELLED
            if not job._future.done():
                job._future.cancel()
        except Exception as error:
            job._state = IngestionJobState.FAILED
            if not job._future.done():
                job._future.set_exception(error)
        else:
            job._state = IngestionJobState.SUCCEEDED
            if not job._future.done():
                job._future.set_result(result)
        finally:
            self._finished(job)

    def _finished(self, job: IngestionJob[Any]) -> None:
        entry = self._entries.get(job.id)
        if entry is not None and entry.job is job:
            job._task = None
            self._running.pop(job.id, None)
            self._entries.pop(job.id, None)
            if not job._closed.done():
                job._closed.set_result(None)
            self._drain()

    def _future_completed(
        self,
        job: IngestionJob[Any],
        future: asyncio.Future[Any],
    ) -> None:
        if future.cancelled() and job.state is not IngestionJobState.CANCELLED:
            self.cancel(job)

    def _require_owner_loop(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("ingestion queue cannot be used from another event loop")


_DEFAULT_QUEUES: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, weakref.ReferenceType[IngestionQueue]
] = weakref.WeakKeyDictionary()


def get_default_ingestion_queue() -> IngestionQueue:
    """Return the lazily created default queue for the running event loop."""

    loop = asyncio.get_running_loop()
    queue_reference = _DEFAULT_QUEUES.get(loop)
    queue = queue_reference() if queue_reference is not None else None
    if queue is None:
        queue = IngestionQueue(
            max_concurrency=_positive_env(
                "VSS_INGESTION_MAX_CONCURRENCY",
                DEFAULT_MAX_CONCURRENCY,
            ),
            max_pending=_positive_env(
                "VSS_INGESTION_QUEUE_SIZE",
                DEFAULT_QUEUE_SIZE,
            ),
        )
        _DEFAULT_QUEUES[loop] = weakref.ref(queue)
    return queue


def _positive_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if parsed < 1:
        raise ValueError(f"{name} must be at least 1")
    return parsed
