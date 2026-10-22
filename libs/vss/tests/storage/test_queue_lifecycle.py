# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression for cancellation before a pending job's future callback runs."""

from __future__ import annotations

import asyncio

import pytest

from vss.ingestion import IngestionJobState, IngestionQueue


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
