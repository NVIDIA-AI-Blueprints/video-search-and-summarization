# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Configurable bounded lock waits preserve ownership, CAS and cancellation."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


@asynccontextmanager
async def _held_operation_lock(adapter, namespace, key):
    """Hold the selected protocol's actual key lock during fault injection."""
    from vss.storage.file import _unlock_close

    handle = await adapter._run(adapter._acquire_key, namespace, key, "operation")
    try:
        yield
    finally:
        await adapter._run(_unlock_close, handle[0])


@pytest.mark.parametrize(
    "value", [0, -1, True, "5", None, float("inf"), float("nan"), 10**400]
)
@pytest.mark.parametrize("entry", ["config", "adapter", "caption_store"])
def test_lock_timeout_rejects_invalid_values(tmp_path, value, entry):
    from vss.storage import FileStorageAdapter, StorageConfig
    from vss.summarization import FileChunkStore

    with pytest.raises(ValidationError):
        if entry == "config":
            StorageConfig(backend="file", root=tmp_path, lock_wait_timeout_s=value)
        elif entry == "adapter":
            FileStorageAdapter(root=tmp_path, lock_wait_timeout_s=value)
        else:
            FileChunkStore(root=tmp_path, lock_wait_timeout_s=value)


async def test_lock_timeout_roundtrip_factory_and_caption_store(tmp_path):
    from vss.storage import FileStorageAdapter, StorageConfig, create_storage
    from vss.summarization import FileChunkStore

    assert StorageConfig(backend="file").lock_wait_timeout_s == 30.0
    async with FileStorageAdapter(root=tmp_path) as default:
        assert default.lock_wait_timeout_s == 30.0
    config = StorageConfig(backend="file", root=tmp_path, lock_wait_timeout_s=12.5)
    assert StorageConfig.model_validate_json(config.model_dump_json()) == config
    async with create_storage(config) as storage:
        assert storage.lock_wait_timeout_s == 12.5
    async with FileChunkStore(root=tmp_path, lock_wait_timeout_s=7) as store:
        assert store._owned_adapter.lock_wait_timeout_s == 7.0


@pytest.mark.parametrize("backend", ["memory", "elasticsearch"])
def test_custom_lock_wait_is_file_only(backend):
    from vss.storage import ElasticsearchConfig, StorageConfig

    options = {"backend": backend}
    if backend == "elasticsearch":
        options["elasticsearch"] = ElasticsearchConfig(url="https://es.test")
    config = StorageConfig(**options)
    assert StorageConfig.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError):
        StorageConfig(**options, lock_wait_timeout_s=1.0)


@pytest.mark.parametrize("value", ["0", "-1", "", "nan", "inf", "true"])
def test_environment_lock_timeout_rejects_invalid_values(monkeypatch, value):
    from vss.ingestion.persistence import environment_storage_config
    from vss.storage import StorageConfigurationError

    monkeypatch.setenv("VSS_STORAGE_BACKEND", "file")
    monkeypatch.delenv("VSS_STORAGE_ROOT", raising=False)
    monkeypatch.setenv("VSS_STORAGE_LOCK_WAIT_TIMEOUT_S", value)
    with pytest.raises(StorageConfigurationError):
        environment_storage_config()


def test_environment_lock_timeout_selects_file_setting(monkeypatch):
    from vss.ingestion.persistence import environment_storage_config

    monkeypatch.setenv("VSS_STORAGE_BACKEND", "file")
    monkeypatch.delenv("VSS_STORAGE_ROOT", raising=False)
    monkeypatch.setenv("VSS_STORAGE_LOCK_WAIT_TIMEOUT_S", "45.5")
    assert environment_storage_config().lock_wait_timeout_s == 45.5


def test_environment_lock_timeout_requires_explicit_backend(monkeypatch):
    from vss.ingestion.persistence import environment_storage_config
    from vss.storage import StorageConfigurationError

    monkeypatch.delenv("VSS_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("VSS_STORAGE_ROOT", raising=False)
    monkeypatch.setenv("VSS_STORAGE_LOCK_WAIT_TIMEOUT_S", "10")
    with pytest.raises(StorageConfigurationError):
        environment_storage_config()


async def test_default_lock_wait_survives_slow_write_and_rechecks_revision(
    tmp_path, monkeypatch
):
    import vss.storage.file as file_module
    from vss.storage import FileStorageAdapter, StorageConflictError

    holder = FileStorageAdapter(root=tmp_path, max_in_flight=1)
    reader = FileStorageAdapter(root=tmp_path, max_in_flight=1)
    writer = FileStorageAdapter(root=tmp_path, max_in_flight=1)
    await holder.write("data", "job", [{"v": "old"}], expected_revision=None)
    entered, release = threading.Event(), threading.Event()
    reader_contended, writer_contended = threading.Event(), threading.Event()
    original_encode = holder._encode
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(
        file_module, "time", SimpleNamespace(monotonic=lambda: clock.value)
    )

    def blocked_encode(*args):
        entered.set()
        if not release.wait(3):
            raise TimeoutError("test did not release holder")
        return original_encode(*args)

    def observed_lock(adapter, event):
        original_acquire = adapter._acquire_key

        def acquire(*args):
            from vss.storage import StorageBusyError

            try:
                return original_acquire(*args)
            except StorageBusyError:
                event.set()
                raise

        return acquire

    monkeypatch.setattr(holder, "_encode", blocked_encode)
    monkeypatch.setattr(reader, "_acquire_key", observed_lock(reader, reader_contended))
    monkeypatch.setattr(writer, "_acquire_key", observed_lock(writer, writer_contended))
    tasks = [
        asyncio.create_task(
            holder.write("data", "job", [{"v": "new"}], expected_revision=1)
        )
    ]
    try:
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        tasks.extend(
            [
                asyncio.create_task(reader.read("data", "job")),
                asyncio.create_task(
                    writer.write("data", "job", [{"v": "stale"}], expected_revision=1)
                ),
            ]
        )
        async with asyncio.timeout(2):
            while not (reader_contended.is_set() and writer_contended.is_set()):
                await asyncio.sleep(0.005)
        # Advance only the adapter's retry clock, leaving asyncio's clock intact.
        # The real OS key lock remains held beyond the original five-second budget.
        clock.value += 8.0
        await asyncio.sleep(0.05)
        assert not tasks[1].done() and not tasks[2].done()
    finally:
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(holder.aclose(), reader.aclose(), writer.aclose())
    assert results[0].revision == results[1].revision == 2
    assert results[1].records == ({"v": "new"},)
    assert isinstance(results[2], StorageConflictError)


async def test_short_lock_deadline_expires_without_changing_snapshot(tmp_path):
    from vss.storage import FileStorageAdapter, StorageBusyError

    async with FileStorageAdapter(root=tmp_path) as holder:
        original = await holder.write(
            "data", "job", [{"v": "old"}], expected_revision=None
        )
        async with FileStorageAdapter(
            root=tmp_path, lock_wait_timeout_s=0.03
        ) as waiter:
            async with _held_operation_lock(holder, "data", "job"):
                with pytest.raises(StorageBusyError):
                    await asyncio.wait_for(
                        waiter.write(
                            "data", "job", [{"v": "new"}], expected_revision=1
                        ),
                        timeout=1,
                    )
            assert await waiter.read("data", "job") == original
            assert (
                await waiter.write("data", "job", [{"v": "retry"}], expected_revision=1)
            ).revision == 2


async def test_cancel_long_lock_wait_preserves_claim_and_reuses_single_worker(
    tmp_path, monkeypatch
):
    from vss.storage import FileStorageAdapter, StorageBusyError

    async with (
        FileStorageAdapter(root=tmp_path) as holder,
        FileStorageAdapter(
            root=tmp_path, lock_wait_timeout_s=60, max_in_flight=1
        ) as waiter,
        FileStorageAdapter(root=tmp_path) as observer,
    ):
        original = await holder.write(
            "data", "job", [{"v": "old"}], expected_revision=None
        )
        contended = asyncio.Event()
        loop = asyncio.get_running_loop()
        blocked_identity = ("data", "job", "operation")
        original_lock = waiter._acquire_key

        def observed_lock(*args):
            try:
                return original_lock(*args)
            except StorageBusyError:
                if args == blocked_identity:
                    loop.call_soon_threadsafe(contended.set)
                raise

        monkeypatch.setattr(waiter, "_acquire_key", observed_lock)
        async with waiter.exclusive("jobs", "job"):
            async with _held_operation_lock(holder, "data", "job"):
                waiting = asyncio.create_task(
                    waiter.write(
                        "data", "job", [{"v": "canceled"}], expected_revision=1
                    )
                )
                try:
                    await asyncio.wait_for(contended.wait(), timeout=2)
                    assert not waiting.done()
                    assert (
                        await asyncio.wait_for(
                            waiter.write("data", "other", [], expected_revision=None),
                            timeout=1,
                        )
                    ).revision == 1
                    waiting.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(waiting, timeout=1)
                    with pytest.raises(StorageBusyError):
                        async with observer.exclusive("jobs", "job"):
                            pytest.fail(
                                "claim released by canceling one storage operation"
                            )
                finally:
                    waiting.cancel()
                    await asyncio.gather(waiting, return_exceptions=True)
            assert await waiter.read("data", "job") == original
        async with observer.exclusive("jobs", "job"):
            pass
