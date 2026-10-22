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

"""Regression cases for storage worker admission and shutdown behavior."""

from __future__ import annotations

import asyncio
import importlib
import os
import stat
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest


@pytest.mark.parametrize("kind", ["config", "adapter"])
def test_file_backend_defaults_to_home_vss_without_writing_home(
    kind: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from vss.storage import FileStorageAdapter, StorageConfig, create_storage

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    if kind == "config":
        config = StorageConfig(backend="file")
        assert config.root == tmp_path / ".vss"
        store = create_storage(config)
    else:
        store = FileStorageAdapter(root=None)
        assert store.root == tmp_path / ".vss"
    assert not (tmp_path / ".vss").exists()
    asyncio.run(store.aclose())
    assert not (tmp_path / ".vss").exists()


def test_file_config_explicit_null_root_uses_home_vss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from vss.storage import StorageConfig

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    config = StorageConfig.model_validate({"backend": "file", "root": None})
    assert config.root == tmp_path / ".vss"


async def test_custom_adapter_uses_base_async_context_without_runtime_mixin() -> None:
    from vss.storage import StorageAdapter, StorageSnapshot

    class CustomAdapter(StorageAdapter):
        @property
        def persistent(self) -> bool:
            return False

        async def read(self, namespace: str, key: str) -> StorageSnapshot:
            return StorageSnapshot(revision=1, records=())

        async def write(
            self,
            namespace: str,
            key: str,
            records: tuple[dict[str, Any], ...],
            *,
            expected_revision: int | None,
        ) -> StorageSnapshot:
            return StorageSnapshot(revision=1, records=records)

        @asynccontextmanager
        async def exclusive(self, namespace: str, key: str) -> Any:
            yield

        async def aclose(self) -> None:
            self.closed = True

    adapter = CustomAdapter()
    async with adapter as entered:
        assert entered is adapter
    assert adapter.closed


@pytest.mark.parametrize("kind", ["memory", "file"])
async def test_json_detachment_and_encoding_run_off_event_loop(
    kind: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, worker_gate
) -> None:
    storage = importlib.import_module("vss.storage")
    module = importlib.import_module(
        "vss.storage.memory" if kind == "memory" else "vss.storage.file"
    )
    adapter = (
        storage.InMemoryStorageAdapter()
        if kind == "memory"
        else storage.FileStorageAdapter(root=tmp_path)
    )
    original = module.detach_records

    def blocked(records: Any, max_records: int) -> Any:
        worker_gate.block()
        return original(records, max_records)

    monkeypatch.setattr(module, "detach_records", blocked)
    async with adapter:
        write = asyncio.create_task(
            adapter.write(
                "custom", "job", [{"text": "x" * 100_000}], expected_revision=None
            )
        )
        try:
            await worker_gate.wait_until_blocked(write)
            worker_gate.release.set()
            result = await asyncio.wait_for(write, 10)
            assert result.records == ({"text": "x" * 100_000},)
        finally:
            worker_gate.release.set()
            await asyncio.gather(write, return_exceptions=True)


async def test_cancelled_close_still_drains_and_shuts_down_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vss.storage import FileStorageAdapter

    store = FileStorageAdapter(root=tmp_path)
    await store.write("custom", "job", [{"value": "old"}], expected_revision=None)
    entered, release = threading.Event(), threading.Event()
    real_fsync = os.fsync

    def blocked_fsync(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test did not release file commit")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", blocked_fsync)
    write = asyncio.create_task(
        store.write("custom", "job", [{"value": "new"}], expected_revision=1)
    )
    await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=2)
    close = asyncio.create_task(store.aclose())
    await asyncio.sleep(0.02)
    close.cancel()
    await asyncio.sleep(0.01)
    close.cancel()
    release.set()
    await asyncio.gather(write, return_exceptions=True)
    outcome = await asyncio.gather(close, return_exceptions=True)
    assert isinstance(outcome[0], asyncio.CancelledError)
    assert store._state == "closed"
    assert store._executor._shutdown


async def test_repeated_cancel_while_claim_release_waits_for_admission_does_not_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vss.storage import FileStorageAdapter

    store = FileStorageAdapter(root=tmp_path, max_in_flight=1)
    observer = FileStorageAdapter(root=tmp_path, max_in_flight=1)
    await store.write("custom", "writer", [{"value": "old"}], expected_revision=None)
    claimed = asyncio.Event()

    async def owner() -> None:
        async with store.exclusive("jobs", "claim"):
            claimed.set()
            await asyncio.Event().wait()

    owner_task = asyncio.create_task(owner())
    await asyncio.wait_for(claimed.wait(), timeout=2)
    entered_fsync, release_fsync = threading.Event(), threading.Event()
    real_fsync = os.fsync

    def blocked_fsync(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            entered_fsync.set()
            if not release_fsync.wait(3):
                raise TimeoutError("test did not release unrelated writer")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", blocked_fsync)
    unrelated = asyncio.create_task(
        store.write("custom", "writer", [{"value": "new"}], expected_revision=1)
    )
    await asyncio.wait_for(asyncio.to_thread(entered_fsync.wait), timeout=2)
    owner_task.cancel()
    await asyncio.sleep(0.02)
    assert store._active_operations >= 2, "claim release did not wait for admission"
    owner_task.cancel()
    release_fsync.set()
    await asyncio.gather(unrelated, return_exceptions=True)
    await asyncio.gather(owner_task, return_exceptions=True)

    async with observer.exclusive("jobs", "claim"):
        pass
    await asyncio.gather(store.aclose(), observer.aclose())


async def test_short_snapshot_write_is_rejected_before_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vss.storage import FileStorageAdapter, StorageError, StorageNotFoundError

    store = FileStorageAdapter(root=tmp_path)
    real_fdopen = os.fdopen

    class ShortWriter:
        def __init__(self, stream: Any) -> None:
            self.stream = stream

        def __enter__(self) -> ShortWriter:
            return self

        def __exit__(self, *args: Any) -> Any:
            return self.stream.__exit__(*args)

        def write(self, data: bytes) -> int:
            return self.stream.write(data[:-1])

        def __getattr__(self, name: str) -> Any:
            return getattr(self.stream, name)

    def short_fdopen(fd: int, *args: Any, **kwargs: Any) -> ShortWriter:
        return ShortWriter(real_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr(os, "fdopen", short_fdopen)
    async with store:
        with pytest.raises(StorageError):
            await store.write("custom", "job", [{}], expected_revision=None)
        monkeypatch.setattr(os, "fdopen", real_fdopen)
        with pytest.raises(StorageNotFoundError):
            await store.read("custom", "job")
