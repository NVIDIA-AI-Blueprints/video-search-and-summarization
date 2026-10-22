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

"""Portable synchronization for tests of blocking storage worker operations."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any

import pytest


class WorkerGate:
    """Prove worker ownership and loop progress without timer tick assumptions."""

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.loop_thread = threading.get_ident()
        self.entered = asyncio.Event()
        self.release = threading.Event()

    def block(self) -> None:
        """Signal from a distinct worker, then wait for the event loop to release."""
        assert threading.get_ident() != self.loop_thread, (
            "blocking storage work ran on the event-loop thread"
        )
        self.loop.call_soon_threadsafe(self.entered.set)
        if not self.release.wait(10):
            raise TimeoutError("event loop did not release the blocked worker")

    async def wait_until_blocked(self, operation: asyncio.Task[Any]) -> None:
        """Return only while the worker is blocked and this loop can still run."""
        notification = asyncio.create_task(self.entered.wait())
        try:
            done, _ = await asyncio.wait(
                (notification, operation),
                timeout=10,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if notification not in done:
                if operation in done:
                    await operation  # Surface the original failure immediately.
                pytest.fail("storage operation did not reach the blocked worker")
            assert not operation.done(), "operation finished before worker release"
            assert not self.release.is_set()
            assert threading.get_ident() == self.loop_thread
        finally:
            notification.cancel()
            await asyncio.gather(notification, return_exceptions=True)


@pytest.fixture
async def worker_gate() -> AsyncIterator[WorkerGate]:
    """Always unblock a test worker, including assertion/error cleanup paths."""
    gate = WorkerGate()
    try:
        yield gate
    finally:
        gate.release.set()
