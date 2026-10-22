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

import asyncio

import pytest

from vss.ingestion import (
    IngestionJobState,
    IngestionQueue,
    IngestionQueueFullError,
)
from vss.ingestion.queue import get_default_ingestion_queue


async def test_queue_runs_jobs_in_fifo_order() -> None:
    queue = IngestionQueue(max_concurrency=1, max_pending=3)
    started: list[int] = []

    async def work(value: int) -> int:
        started.append(value)
        await asyncio.sleep(0)
        return value

    jobs = [queue.submit(lambda value=value: work(value)) for value in range(3)]

    assert queue.running_count == 1
    assert queue.pending_count == 2
    assert await asyncio.gather(*jobs) == [0, 1, 2]
    assert started == [0, 1, 2]
    assert all(job.state is IngestionJobState.SUCCEEDED for job in jobs)


async def test_queue_limits_concurrency_to_four_jobs() -> None:
    queue = IngestionQueue(max_concurrency=4, max_pending=100)
    release = asyncio.Event()
    four_started = asyncio.Event()
    active = 0
    maximum_active = 0

    async def work(value: int) -> int:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        if active == 4:
            four_started.set()
        try:
            await release.wait()
            return value
        finally:
            active -= 1

    jobs = [queue.submit(lambda value=value: work(value)) for value in range(8)]
    await four_started.wait()

    assert queue.running_count == 4
    assert queue.pending_count == 4
    assert maximum_active == 4

    release.set()
    assert await asyncio.gather(*jobs) == list(range(8))
    assert maximum_active == 4


async def test_queue_rejects_work_when_capacity_is_exhausted() -> None:
    queue = IngestionQueue(max_concurrency=1, max_pending=1)
    release = asyncio.Event()

    async def work() -> None:
        await release.wait()

    running = queue.submit(work)
    pending = queue.submit(work)

    with pytest.raises(IngestionQueueFullError, match="ingestion queue is full"):
        queue.submit(work)

    assert pending.cancel()
    assert running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(0)


async def test_queue_cancels_pending_and_running_jobs() -> None:
    queue = IngestionQueue(max_concurrency=1, max_pending=2)
    started = asyncio.Event()
    release = asyncio.Event()

    async def work() -> str:
        started.set()
        await release.wait()
        return "done"

    running = queue.submit(work)
    pending = queue.submit(work)
    await started.wait()

    assert pending.cancel()
    assert pending.state is IngestionJobState.CANCELLED
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert running.cancel()
    assert running.state is IngestionJobState.CANCELLED
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(0)
    assert queue.running_count == 0
    assert queue.pending_count == 0


async def test_queue_propagates_job_failures() -> None:
    queue = IngestionQueue()

    async def fail() -> None:
        raise RuntimeError("backend failed")

    job = queue.submit(fail)

    with pytest.raises(RuntimeError, match="backend failed"):
        await job
    assert job.state is IngestionJobState.FAILED
    assert isinstance(job.exception(), RuntimeError)


async def test_cancelling_awaiter_cancels_running_job() -> None:
    queue = IngestionQueue()
    started = asyncio.Event()
    blocked = asyncio.Event()

    async def work() -> None:
        started.set()
        await blocked.wait()

    job = queue.submit(work)
    waiter = asyncio.ensure_future(job)
    await started.wait()
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter
    await asyncio.sleep(0)
    assert job.state is IngestionJobState.CANCELLED
    assert queue.running_count == 0


async def test_default_queue_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VSS_INGESTION_MAX_CONCURRENCY", "7")
    monkeypatch.setenv("VSS_INGESTION_QUEUE_SIZE", "23")

    queue = get_default_ingestion_queue()

    assert queue.max_concurrency == 7
    assert queue.max_pending == 23
