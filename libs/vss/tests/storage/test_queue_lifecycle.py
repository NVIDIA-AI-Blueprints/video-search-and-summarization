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

"""Regression for cancellation before a pending job's future callback runs."""

from __future__ import annotations

import asyncio

import pytest

from vss.ingestion import IngestionJobState, IngestionQueue


async def test_late_completion_callback_cannot_close_replayed_job():
    queue = IngestionQueue(max_concurrency=1)
    first = queue.submit(lambda: asyncio.sleep(0, result=1))
    assert await first == 1
    await first.wait_closed()
    release = asyncio.Event()

    async def replay_work():
        await release.wait()
        return 2

    replay = queue.submit(replay_work, job_id=first.id)
    # Deterministically deliver the old completion after the ID is reused.
    queue._finish_entry(first)
    assert not replay._closed.done()
    release.set()
    assert await asyncio.wait_for(replay, 1) == 2
    await asyncio.wait_for(replay.wait_closed(), 1)
    assert queue.pending_count == queue.running_count == 0


async def test_cancelled_pending_waiter_is_closed_when_drain_beats_future_callback():
    queue = IngestionQueue(max_concurrency=1)
    release = asyncio.Event()
    waiter: asyncio.Task[int] | None = None
    invoked = False

    async def head_work():
        await release.wait()
        assert waiter is not None
        # Cancellation propagates immediately to the pending result Future,
        # while its callback cannot run until this head coroutine returns.
        waiter.cancel()

    async def pending_work():
        nonlocal invoked
        invoked = True
        return 42

    head = queue.submit(head_work)
    pending = queue.submit(pending_work)

    async def await_pending():
        return await pending

    waiter = asyncio.create_task(await_pending())
    await asyncio.sleep(0)
    release.set()
    await head
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await asyncio.wait_for(pending.wait_closed(), timeout=0.5)
    assert pending.cancelled()
    assert pending.state is IngestionJobState.CANCELLED
    assert not invoked
    assert queue.pending_count == queue.running_count == 0
    replacement = queue.submit(lambda: asyncio.sleep(0, result=7), job_id=pending.id)
    assert not pending.cancel()
    assert await replacement == 7
    await replacement.wait_closed()
