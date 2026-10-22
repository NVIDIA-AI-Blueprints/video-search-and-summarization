# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions for operation-lock fairness and pathological JSON depth."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock instrumentation")
async def test_same_key_lock_retry_does_not_block_other_keys_at_worker_limit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    from vss.storage import (
        FileStorageAdapter,
        StorageConflictError,
    )

    holder = FileStorageAdapter(root=tmp_path, max_in_flight=1)
    contender = FileStorageAdapter(root=tmp_path, max_in_flight=1)
    await holder.write("custom", "same", [{"value": "old"}], expected_revision=None)

    entered_fsync, release_fsync = threading.Event(), threading.Event()
    contended_lock = threading.Event()
    holder_inode: list[tuple[int, int]] = []
    real_fsync, real_flock = os.fsync, fcntl.flock

    def blocked_fsync(fd: int) -> None:
        import stat

        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        if stat.S_ISREG(info.st_mode) and not holder_inode:
            holder_inode.append(identity)
        if stat.S_ISREG(info.st_mode) and identity == holder_inode[0]:
            entered_fsync.set()
            if not release_fsync.wait(3):
                raise TimeoutError("test did not release holder write")
        real_fsync(fd)

    def observed_flock(fd: int, flags: int) -> None:
        try:
            real_flock(fd, flags)
        except BlockingIOError:
            if flags & fcntl.LOCK_NB:
                contended_lock.set()
            raise

    monkeypatch.setattr(os, "fsync", blocked_fsync)
    monkeypatch.setattr(fcntl, "flock", observed_flock)
    holder_write = asyncio.create_task(
        holder.write("custom", "same", [{"value": "holder"}], expected_revision=1)
    )
    await asyncio.wait_for(asyncio.to_thread(entered_fsync.wait), timeout=2)
    same_key = asyncio.create_task(
        contender.write("custom", "same", [{"value": "contender"}], expected_revision=1)
    )
    await asyncio.wait_for(asyncio.to_thread(contended_lock.wait), timeout=2)
    try:
        unrelated = await asyncio.wait_for(
            contender.write(
                "custom", "unrelated", [{"ok": True}], expected_revision=None
            ),
            timeout=0.4,
        )
        assert unrelated.revision == 1
    finally:
        release_fsync.set()
        await asyncio.gather(holder_write, return_exceptions=True)
        await asyncio.gather(same_key, return_exceptions=True)
        await asyncio.gather(holder.aclose(), contender.aclose())
    with pytest.raises(StorageConflictError):
        same_key.result()


async def test_deep_nested_input_raises_typed_configuration_error(
    tmp_path: Path,
) -> None:
    from vss.storage import FileStorageAdapter, StorageConfigurationError

    value: object = "leaf"
    for _ in range(1_200):
        value = {"nested": value}
    store = FileStorageAdapter(root=tmp_path)
    async with store:
        with pytest.raises(StorageConfigurationError):
            await store.write(
                "custom", "deep", [{"value": value}], expected_revision=None
            )


async def test_well_framed_deep_file_raises_typed_corruption_error(
    tmp_path: Path,
) -> None:
    from vss.storage import FileStorageAdapter, StorageCorruptError

    key = "deep"
    store = FileStorageAdapter(root=tmp_path)
    async with store:
        await store.write("custom", key, [{"ok": True}], expected_revision=None)
        path = (
            tmp_path / "custom" / (hashlib.sha256(key.encode()).hexdigest() + ".jsonl")
        )
        header = {
            "format_version": "1.0",
            "schema_version": "1.0",
            "namespace": "custom",
            "key": key,
            "revision": 1,
            "record_count": 1,
        }
        prefix = (
            json.dumps(header, separators=(",", ":")).encode()
            + b"\n"
            + b'{"nested":' * 1_200
            + b'"leaf"'
            + b"}" * 1_200
            + b"\n"
        )
        footer = json.dumps(
            {"sha256": hashlib.sha256(prefix).hexdigest()}, separators=(",", ":")
        ).encode()
        path.write_bytes(prefix + footer + b"\n")
        with pytest.raises(StorageCorruptError):
            await store.read("custom", key)


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock instrumentation")
async def test_close_waits_for_read_retrying_a_contended_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    from vss.storage import FileStorageAdapter

    holder = FileStorageAdapter(root=tmp_path)
    reader = FileStorageAdapter(root=tmp_path)
    await holder.write("custom", "same", [{"value": "old"}], expected_revision=None)
    entered_fsync, release_fsync = threading.Event(), threading.Event()
    contended_lock = threading.Event()
    real_fsync, real_flock = os.fsync, fcntl.flock

    def blocked_fsync(fd: int) -> None:
        import stat

        if stat.S_ISREG(os.fstat(fd).st_mode):
            entered_fsync.set()
            if not release_fsync.wait(3):
                raise TimeoutError("test did not release holder write")
        real_fsync(fd)

    def observed_flock(fd: int, flags: int) -> None:
        try:
            real_flock(fd, flags)
        except BlockingIOError:
            if flags & fcntl.LOCK_NB:
                contended_lock.set()
            raise

    monkeypatch.setattr(os, "fsync", blocked_fsync)
    monkeypatch.setattr(fcntl, "flock", observed_flock)
    update = asyncio.create_task(
        holder.write("custom", "same", [{"value": "new"}], expected_revision=1)
    )
    await asyncio.wait_for(asyncio.to_thread(entered_fsync.wait), timeout=2)
    reading = asyncio.create_task(reader.read("custom", "same"))
    await asyncio.wait_for(asyncio.to_thread(contended_lock.wait), timeout=2)
    closing = asyncio.create_task(reader.aclose())
    await asyncio.sleep(0.03)
    assert not closing.done()
    release_fsync.set()
    updated, snapshot = await asyncio.gather(update, reading)
    await closing
    assert updated.revision == snapshot.revision == 2
    await holder.aclose()
