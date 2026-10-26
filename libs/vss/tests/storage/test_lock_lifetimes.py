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

"""Idle memory locks are released while active owners and waiters share locks."""

import asyncio
import gc
import weakref

import pytest


async def test_idle_memory_locks_do_not_accumulate_without_gc():
    from vss.storage import InMemoryStorageAdapter

    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        async with InMemoryStorageAdapter() as adapter:
            for index in range(100):
                await adapter.write("data", str(index), [], expected_revision=None)
                await adapter.read("data", str(index))
                async with adapter.exclusive("jobs", str(index)):
                    pass
            async with asyncio.timeout(1):
                while adapter._operation_locks or adapter._claim_locks:
                    await asyncio.sleep(0.005)
            assert len(adapter._records) == 100  # Stored data still belongs to callers.
    finally:
        if gc_was_enabled:
            gc.enable()


async def test_active_memory_claim_survives_other_idle_keys():
    from vss.storage import InMemoryStorageAdapter, StorageBusyError

    async with InMemoryStorageAdapter() as adapter:
        async with adapter.exclusive("jobs", "held"):
            reference = weakref.ref(adapter._claim_locks[("jobs", "held")])
            for index in range(100):
                async with adapter.exclusive("jobs", str(index)):
                    pass
            assert reference() is not None and reference().locked()
            assert ("jobs", "held") in adapter._claim_locks
            with pytest.raises(StorageBusyError):
                async with adapter.exclusive("jobs", "held"):
                    pytest.fail("active claim lost its shared lock")


async def test_memory_operation_waiters_keep_the_same_lock(monkeypatch):
    from vss.storage import InMemoryStorageAdapter, StorageConflictError

    async with InMemoryStorageAdapter(max_in_flight=4) as adapter:
        await adapter.write("data", "job", [], expected_revision=None)
        identity = ("data", "job")
        lock = adapter._key_lock(adapter._operation_locks, identity)
        original_key_lock = adapter._key_lock
        seen = []
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        def observed_key_lock(mapping, key):
            result = original_key_lock(mapping, key)
            if mapping is adapter._operation_locks and key == identity:
                seen.append(result)
                if len(seen) == 4:
                    loop.call_soon_threadsafe(entered.set)
            return result

        monkeypatch.setattr(adapter, "_key_lock", observed_key_lock)
        lock.acquire()
        tasks = [
            asyncio.create_task(
                adapter.write("data", "job", [{"v": index}], expected_revision=1)
            )
            for index in range(4)
        ]
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert all(item is lock for item in seen)
            assert all(not task.done() for task in tasks)
        finally:
            lock.release()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(isinstance(item, StorageConflictError) for item in results) == 3
        assert sum(getattr(item, "revision", None) == 2 for item in results) == 1
