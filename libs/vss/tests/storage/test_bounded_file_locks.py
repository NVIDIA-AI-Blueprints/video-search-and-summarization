# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reusable file locks preserve exact ownership across processes and crashes."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import sys
import threading
from contextlib import AsyncExitStack, contextmanager
from pathlib import Path

import pytest

from vss.storage import (
    FileStorageAdapter,
    StorageBusyError,
    StorageConfigurationError,
    StorageConflictError,
    StorageCorruptError,
    StorageError,
    StorageLimitError,
)


def slots(root: Path) -> list[Path]:
    return sorted((root / ".locks").glob("slot-*.lock"))


def mode(root: Path) -> dict[str, str]:
    return json.loads((root / ".locks" / "mode.json").read_bytes())


@pytest.mark.parametrize("namespace", ["jobs", "slot-camera"])
async def test_unmarked_per_key_locks_are_rejected_without_conversion(
    tmp_path, namespace
):
    directory = tmp_path / ".locks" / namespace
    directory.mkdir(parents=True)
    old = directory / "old.claim.lock"
    old.write_bytes(b"retained")
    with pytest.raises(StorageConfigurationError, match="fresh storage root"):
        async with FileStorageAdapter(root=tmp_path) as store:
            async with store.exclusive("jobs", "new"):
                pytest.fail("unsupported per-key root was opened")
    assert old.read_bytes() == b"retained"
    assert not (tmp_path / ".locks" / "mode.json").exists()
    assert not slots(tmp_path)


@contextmanager
def held_path_lock(path):
    """Hold an OS lock independently of the slot assignment protocol."""
    from vss.storage.file import _lock_fd, _unlock_close

    fd = FileStorageAdapter._open_lock(path)
    try:
        _lock_fd(fd)
    except BaseException:
        os.close(fd)
        raise
    try:
        yield
    finally:
        _unlock_close(fd)


async def child(script: str, *args: str) -> asyncio.subprocess.Process:
    import vss.storage

    # Use the same source package as the parent, even with isolated Python.
    source = str(Path(vss.storage.__file__).resolve().parents[2])
    bootstrap = f"import sys; sys.path.insert(0, {source!r})\n"
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        bootstrap + script,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def line(process: asyncio.subprocess.Process) -> str:
    assert process.stdout is not None
    result = await asyncio.wait_for(process.stdout.readline(), timeout=10)
    if not result:
        assert process.stderr is not None
        error = await asyncio.wait_for(process.stderr.read(), timeout=3)
        pytest.fail(f"child failed: {error.decode(errors='replace')}")
    return result.decode().strip()


async def reap(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await asyncio.wait_for(process.communicate(), timeout=5)


async def test_fresh_root_persists_slots_protocol_across_restart(tmp_path):
    async with FileStorageAdapter(root=tmp_path) as store:
        async with store.exclusive("jobs", "first"):
            pass
    assert mode(tmp_path) == {"schema_version": "1.0", "mode": "slots"}
    retained = {p: p.stat().st_ino for p in (tmp_path / ".locks").iterdir()}
    async with FileStorageAdapter(root=tmp_path) as fresh:
        async with fresh.exclusive("jobs", "second"):
            pass
    assert {p: p.stat().st_ino for p in retained} == retained
    assert len(slots(tmp_path)) == 1


async def test_unique_jobs_reuse_concurrency_high_water_slots(tmp_path):
    async with FileStorageAdapter(root=tmp_path) as store:
        async with AsyncExitStack() as owners:
            for index in range(4):
                await owners.enter_async_context(store.exclusive("jobs", str(index)))
            await store.write("data", "initial", [], expected_revision=None)
            assert len(slots(tmp_path)) == 5
        original = {p: p.stat().st_ino for p in slots(tmp_path)}
        for index in range(200):
            key = f"unique-{index}"
            async with store.exclusive("jobs", key):
                await store.write("data", key, [{"i": index}], expected_revision=None)
                assert (await store.read("data", key)).revision == 1
        assert {p: p.stat().st_ino for p in slots(tmp_path)} == original
        assert not list((tmp_path / ".locks").glob("*/*.lock"))


async def test_free_early_slot_does_not_hide_matching_later_owner(tmp_path):
    async with (
        FileStorageAdapter(root=tmp_path) as owner,
        FileStorageAdapter(root=tmp_path) as peer,
        AsyncExitStack() as later,
    ):
        async with owner.exclusive("jobs", "early"):
            await later.enter_async_context(owner.exclusive("jobs", "later"))
        assert len(slots(tmp_path)) == 2
        with pytest.raises(StorageBusyError):
            async with peer.exclusive("jobs", "later"):
                pytest.fail("free slot bypassed an existing exact owner")
        async with peer.exclusive("jobs", "different"):
            assert len(slots(tmp_path)) == 2


async def test_namespaces_and_operation_purpose_have_independent_ownership(tmp_path):
    async with (
        FileStorageAdapter(root=tmp_path) as owner,
        FileStorageAdapter(root=tmp_path) as peer,
        owner.exclusive("jobs", "same"),
        peer.exclusive("other", "same"),
    ):
        snapshot = await peer.write("jobs", "same", [], expected_revision=None)
        assert snapshot.revision == 1
        with pytest.raises(StorageBusyError):
            async with peer.exclusive("jobs", "same"):
                pytest.fail("same namespace/key admitted twice")


async def test_live_per_key_root_is_rejected_without_bypassing_owner(tmp_path):
    digest = hashlib.sha256(b"held").hexdigest()
    path = tmp_path / ".locks" / "jobs" / f"{digest}.claim.lock"
    with held_path_lock(path):
        inode = path.stat().st_ino
        async with FileStorageAdapter(root=tmp_path) as store:
            with pytest.raises(StorageConfigurationError):
                async with store.exclusive("jobs", "held"):
                    pytest.fail("legacy owner was bypassed")
        assert path.stat().st_ino == inode
    assert not (tmp_path / ".locks" / "mode.json").exists()
    assert not slots(tmp_path)


@pytest.mark.parametrize("option", ["auto", "legacy", "slots"])
@pytest.mark.parametrize("entry", ["config", "adapter", "caption_store"])
def test_lock_mode_is_not_a_public_option(tmp_path, option, entry):
    from pydantic import ValidationError

    from vss.storage import StorageConfig
    from vss.summarization import FileChunkStore

    with pytest.raises((ValidationError, TypeError)):
        if entry == "config":
            StorageConfig(backend="file", root=tmp_path, lock_mode=option)
        elif entry == "adapter":
            FileStorageAdapter(root=tmp_path, lock_mode=option)
        else:
            FileChunkStore(root=tmp_path, lock_mode=option)


async def test_unsupported_protocol_marker_is_rejected_without_rewrite(tmp_path):
    directory = tmp_path / ".locks"
    directory.mkdir()
    marker = directory / "mode.json"
    original = b'{"schema_version":"1.0","mode":"legacy"}'
    marker.write_bytes(original)
    with pytest.raises(StorageConfigurationError, match="fresh storage root"):
        async with FileStorageAdapter(root=tmp_path) as fresh:
            async with fresh.exclusive("jobs", "new"):
                pytest.fail("unsupported protocol marker was replaced")
    assert marker.read_bytes() == original
    assert not slots(tmp_path)


async def test_slots_marker_cannot_hide_per_key_locks(tmp_path):
    directory = tmp_path / ".locks"
    directory.mkdir()
    marker = directory / "mode.json"
    original = b'{"schema_version":"1.0","mode":"slots"}'
    marker.write_bytes(original)
    old = directory / "jobs" / "old.claim.lock"
    old.parent.mkdir()
    old.write_bytes(b"retained")
    with pytest.raises(StorageConfigurationError, match="fresh storage root"):
        async with FileStorageAdapter(root=tmp_path) as store:
            async with store.exclusive("jobs", "old"):
                pytest.fail("marker hid an incompatible lock owner")
    assert marker.read_bytes() == original
    assert old.read_bytes() == b"retained"
    assert not slots(tmp_path)


@pytest.mark.parametrize("payload", [b"{", b'{"schema_version":"9","mode":"slots"}'])
async def test_invalid_mode_marker_fails_closed(tmp_path, payload):
    directory = tmp_path / ".locks"
    directory.mkdir()
    marker = directory / "mode.json"
    marker.write_bytes(payload)
    with pytest.raises(StorageError):
        async with FileStorageAdapter(root=tmp_path) as store:
            async with store.exclusive("jobs", "job"):
                pytest.fail("invalid mode marker was ignored")
    assert marker.read_bytes() == payload
    assert not slots(tmp_path)


async def test_busy_corrupt_identity_fails_closed_but_free_slot_recovers(tmp_path):
    async with (
        FileStorageAdapter(root=tmp_path) as owner,
        FileStorageAdapter(root=tmp_path) as peer,
    ):
        async with owner.exclusive("jobs", "held"):
            path = slots(tmp_path)[0]
            # Offset zero remains locked on Windows; only damage the metadata.
            with path.open("r+b", buffering=0) as stream:
                stream.seek(1)
                stream.write(b"{")
                stream.truncate()
            with pytest.raises(StorageError) as caught:
                async with peer.exclusive("jobs", "held"):
                    pytest.fail("corrupt live identity was treated as a free slot")
            assert not isinstance(caught.value, StorageBusyError)
            assert len(slots(tmp_path)) == 1
        async with peer.exclusive("jobs", "recovered"):
            assert len(slots(tmp_path)) == 1
            with path.open("rb", buffering=0) as stream:
                stream.seek(1)
                assert json.loads(stream.read())["key"] == "recovered"


@pytest.mark.parametrize("entry", ["registry.lock", "slot-00000000.lock"])
async def test_lock_symlinks_never_modify_target(tmp_path, entry):
    root = tmp_path / "store"
    async with FileStorageAdapter(root=root) as setup:
        async with setup.exclusive("jobs", "setup"):
            pass
    target = tmp_path / "caller-owned"
    target.write_bytes(b"unchanged")
    path = root / ".locks" / entry
    path.unlink()
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(StorageError):
        async with FileStorageAdapter(root=root) as store:
            async with store.exclusive("jobs", "job"):
                pytest.fail("symlink accepted")
    assert target.read_bytes() == b"unchanged"


async def test_maximum_escaped_key_is_readable_while_owned(tmp_path):
    key = "\0" * 4096
    async with (
        FileStorageAdapter(root=tmp_path) as owner,
        FileStorageAdapter(root=tmp_path) as peer,
        owner.exclusive("jobs", key),
    ):
        with pytest.raises(StorageBusyError):
            async with peer.exclusive("jobs", key):
                pytest.fail("maximum-length identity did not match")
        async with peer.exclusive("jobs", "different"):
            pass


async def test_registry_wait_is_cancellable_without_occupying_only_worker(tmp_path):
    async with FileStorageAdapter(root=tmp_path, max_in_flight=1) as store:
        async with store.exclusive("jobs", "setup"):
            pass

        async def claim():
            async with store.exclusive("jobs", "waiting"):
                pytest.fail("claim entered while registry was held")

        with held_path_lock(tmp_path / ".locks" / "registry.lock"):
            waiting = asyncio.create_task(claim())
            try:
                await asyncio.sleep(0.03)
                assert not waiting.done()
                assert (
                    await asyncio.wait_for(store._run(lambda: "available"), 1)
                    == "available"
                )
                waiting.cancel()
                await asyncio.sleep(0)
                waiting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(waiting, 1)
            finally:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
        async with store.exclusive("jobs", "waiting"):
            pass
        assert len(slots(tmp_path)) == 1


async def test_registry_wait_obeys_deadline_without_leaking_a_claim(tmp_path):
    async with FileStorageAdapter(root=tmp_path, lock_wait_timeout_s=0.03) as store:
        async with store.exclusive("jobs", "setup"):
            pass

        async def claim():
            async with store.exclusive("jobs", "waiting"):
                pytest.fail("claim entered while registry was held")

        with held_path_lock(tmp_path / ".locks" / "registry.lock"):
            with pytest.raises(StorageBusyError):
                await asyncio.wait_for(claim(), 1)
        async with store.exclusive("jobs", "waiting"):
            pass
        assert len(slots(tmp_path)) == 1


CLAIM_CHILD = r"""
import asyncio, sys
from vss.storage import FileStorageAdapter
async def main():
    async with FileStorageAdapter(root=sys.argv[1]) as store:
        async with store.exclusive("jobs", "held"):
            print("owned", flush=True)
            await asyncio.to_thread(sys.stdin.readline)
asyncio.run(main())
"""


async def test_process_death_releases_exact_claim_and_reuses_slot(tmp_path):
    process = await child(CLAIM_CHILD, str(tmp_path))
    try:
        assert await line(process) == "owned"
        async with FileStorageAdapter(root=tmp_path) as peer:
            with pytest.raises(StorageBusyError):
                async with peer.exclusive("jobs", "held"):
                    pytest.fail("child claim was bypassed")
            async with peer.exclusive("jobs", "other"):
                assert len(slots(tmp_path)) == 2
        retained = {p: p.stat().st_ino for p in slots(tmp_path)}
        await reap(process)
        async with FileStorageAdapter(root=tmp_path) as restarted:
            async with restarted.exclusive("jobs", "held"):
                pass
            async with restarted.exclusive("jobs", "new"):
                pass
        assert {p: p.stat().st_ino for p in slots(tmp_path)} == retained
    finally:
        await reap(process)


CAS_CHILD = r"""
import asyncio, sys
from vss.storage import FileStorageAdapter, StorageConflictError
async def main():
    async with FileStorageAdapter(root=sys.argv[1]) as store:
        print("ready", flush=True)
        await asyncio.to_thread(sys.stdin.readline)
        try:
            result = await store.write("data", "same", [], expected_revision=1)
        except StorageConflictError:
            print("conflict", flush=True)
        else:
            print("won:" + str(result.revision), flush=True)
asyncio.run(main())
"""


async def test_slot_protocol_multiprocess_cas_has_one_winner(tmp_path):
    async with FileStorageAdapter(root=tmp_path) as setup:
        await setup.write("data", "same", [], expected_revision=None)
    children = []
    try:
        for _ in range(2):
            children.append(await child(CAS_CHILD, str(tmp_path)))
        assert await asyncio.gather(*(line(p) for p in children)) == ["ready", "ready"]
        for process in children:
            assert process.stdin is not None
            process.stdin.write(b"go\n")
            await process.stdin.drain()
        assert sorted(await asyncio.gather(*(line(p) for p in children))) == [
            "conflict",
            "won:2",
        ]
        for process in children:
            assert await asyncio.wait_for(process.wait(), 5) == 0
        async with FileStorageAdapter(root=tmp_path) as fresh:
            assert (await fresh.read("data", "same")).revision == 2
        assert len(slots(tmp_path)) == 1
    finally:
        await asyncio.gather(*(reap(p) for p in children))


MARKER_CRASH_CHILD = r"""
import asyncio, os, sys
from pathlib import Path
from vss.storage import FileStorageAdapter
original = os.replace
def interrupted_replace(source, destination):
    if Path(destination).name == "mode.json":
        os._exit(17)
    return original(source, destination)
os.replace = interrupted_replace
async def main():
    async with FileStorageAdapter(root=sys.argv[1]) as store:
        async with store.exclusive("jobs", "job"):
            raise AssertionError("marker publication was not intercepted")
asyncio.run(main())
"""


async def test_repeated_marker_publication_crashes_keep_pending_files_bounded(tmp_path):
    for _ in range(3):
        process = await child(MARKER_CRASH_CHILD, str(tmp_path))
        try:
            assert await asyncio.wait_for(process.wait(), 5) == 17
        finally:
            await reap(process)
        assert not (tmp_path / ".locks" / "mode.json").exists()
        assert not slots(tmp_path)
    # One permanent registry and at most one pending mode publication.
    assert len(list((tmp_path / ".locks").iterdir())) <= 2
    async with FileStorageAdapter(root=tmp_path) as recovered:
        async with recovered.exclusive("jobs", "job"):
            pass
    assert mode(tmp_path)["mode"] == "slots"


async def test_deep_busy_slot_metadata_raises_typed_corruption(tmp_path):
    async with (
        FileStorageAdapter(root=tmp_path) as owner,
        FileStorageAdapter(root=tmp_path) as peer,
    ):
        async with owner.exclusive("jobs", "held"):
            path = slots(tmp_path)[0]
            # Within the metadata byte limit, beyond JSON decoder nesting limits.
            payload = b"[" * 15000 + b"0" + b"]" * 15000
            assert len(payload) < 32768
            with path.open("r+b", buffering=0) as stream:
                stream.seek(1)
                stream.write(payload)
                stream.truncate()
            with pytest.raises(StorageCorruptError):
                async with peer.exclusive("jobs", "held"):
                    pytest.fail("deep corrupt identity allowed another owner")
        async with peer.exclusive("jobs", "recovered"):
            assert len(slots(tmp_path)) == 1


def test_open_lock_closes_descriptor_when_fstat_fails(tmp_path, monkeypatch):
    target = tmp_path / "fault.lock"
    original_open, original_fstat = os.open, os.fstat
    opened = []

    def tracked_open(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if Path(path) == target:
            opened.append(fd)
        return fd

    def failed_fstat(fd):
        if opened and fd == opened[-1]:
            raise OSError(errno.EIO, "injected lock validation failure")
        return original_fstat(fd)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "open", tracked_open)
            patch.setattr(os, "fstat", failed_fstat)
            with pytest.raises((StorageError, OSError)):
                FileStorageAdapter._open_lock(target)
        assert len(opened) == 1
        with pytest.raises(OSError) as caught:
            original_fstat(opened[0])
        assert caught.value.errno == errno.EBADF
    finally:
        for fd in opened:
            try:
                original_fstat(fd)
            except OSError:
                pass
            else:
                os.close(fd)


@pytest.mark.parametrize("fault", ["write", "ftruncate"])
async def test_slot_metadata_io_failure_closes_descriptor_and_allows_reuse(
    tmp_path, monkeypatch, fault
):
    async with FileStorageAdapter(root=tmp_path) as store:
        async with store.exclusive("jobs", "setup"):
            pass
        path = slots(tmp_path)[0]
        inode = path.stat().st_ino
        original = getattr(os, fault)
        failed = []

        def injected(fd, *args):
            if os.fstat(fd).st_ino == inode:
                failed.append(fd)
                raise OSError(errno.EIO, "injected slot metadata failure")
            return original(fd, *args)

        with monkeypatch.context() as patch:
            patch.setattr(os, fault, injected)
            with pytest.raises(StorageError):
                async with store.exclusive("jobs", "failed"):
                    pytest.fail("failed metadata publication yielded ownership")
        assert len(failed) == 1
        with pytest.raises(OSError) as caught:
            os.fstat(failed[0])
        assert caught.value.errno == errno.EBADF
        async with store.exclusive("jobs", "failed"):
            assert slots(tmp_path) == [path]
            assert path.stat().st_ino == inode


async def test_registry_release_failure_closes_assigned_slot_and_allows_reuse(
    tmp_path, monkeypatch
):
    import vss.storage._file_locks as protocol

    async with FileStorageAdapter(root=tmp_path) as store:
        async with store.exclusive("jobs", "setup"):
            pass
        registry_inode = (tmp_path / ".locks" / "registry.lock").stat().st_ino
        original_release, original_slots = protocol._unlock_close, protocol._slots
        assigned = []

        def tracked_slots(*args):
            result = original_slots(*args)
            assigned.append(result[0])
            return result

        def failed_release(fd):
            is_registry = os.fstat(fd).st_ino == registry_inode
            original_release(fd)
            if is_registry:
                raise OSError(errno.EIO, "injected registry release failure")

        with monkeypatch.context() as patch:
            patch.setattr(protocol, "_slots", tracked_slots)
            patch.setattr(protocol, "_unlock_close", failed_release)
            with pytest.raises(StorageError):
                async with store.exclusive("jobs", "failed"):
                    pytest.fail("failed registry release yielded ownership")
        assert len(assigned) == 1
        with pytest.raises(OSError) as caught:
            os.fstat(assigned[0])
        assert caught.value.errno == errno.EBADF
        async with store.exclusive("jobs", "failed"):
            assert len(slots(tmp_path)) == 1


async def test_public_operation_release_failure_is_typed_and_key_reusable(
    tmp_path, monkeypatch
):
    import vss.storage.file as file_module

    async with FileStorageAdapter(root=tmp_path) as store:
        async with store.exclusive("jobs", "warm"):
            pass
        original = file_module._unlock_close

        def failed_release(fd):
            original(fd)
            raise OSError(errno.EIO, "injected public release failure")

        with monkeypatch.context() as patch:
            patch.setattr(file_module, "_unlock_close", failed_release)
            with pytest.raises(StorageError):
                await store.write("data", "job", [{"v": 1}], expected_revision=None)
        committed = await store.read("data", "job")
        assert committed.revision == 1
        assert committed.records == ({"v": 1},)
        assert (
            await store.write("data", "job", [{"v": 2}], expected_revision=1)
        ).revision == 2


async def test_operation_primary_error_survives_public_release_failure(
    tmp_path, monkeypatch
):
    import vss.storage.file as file_module

    async with FileStorageAdapter(root=tmp_path) as store:
        async with store.exclusive("jobs", "warm"):
            pass
        original = file_module._unlock_close
        primary = StorageLimitError("primary")

        def failed_release(fd):
            original(fd)
            raise OSError(errno.EIO, "injected public release failure")

        def failed_write(*args):
            raise primary

        with monkeypatch.context() as patch:
            patch.setattr(file_module, "_unlock_close", failed_release)
            patch.setattr(store, "_write_snapshot", failed_write)
            with pytest.raises(StorageLimitError) as caught:
                await store.write("data", "job", [], expected_revision=None)
            assert caught.value is primary
        assert (
            await store.write("data", "job", [], expected_revision=None)
        ).revision == 1


async def test_claim_primary_error_survives_public_release_failure(
    tmp_path, monkeypatch
):
    import vss.storage.file as file_module

    async with FileStorageAdapter(root=tmp_path) as store:
        async with store.exclusive("jobs", "warm"):
            pass
        original = file_module._unlock_close
        primary = StorageConflictError("primary")

        def failed_release(fd):
            original(fd)
            raise OSError(errno.EIO, "injected public release failure")

        with monkeypatch.context() as patch:
            patch.setattr(file_module, "_unlock_close", failed_release)
            with pytest.raises(StorageConflictError) as caught:
                async with store.exclusive("jobs", "job"):
                    raise primary
            assert caught.value is primary
        async with store.exclusive("jobs", "job"):
            assert len(slots(tmp_path)) == 1


async def test_cancellation_during_claim_release_survives_release_failure(
    tmp_path, monkeypatch
):
    import vss.storage.file as file_module

    async with FileStorageAdapter(root=tmp_path) as store:
        async with store.exclusive("jobs", "warm"):
            pass
        entered, finish = threading.Event(), threading.Event()
        original = file_module._unlock_close

        def failed_release(fd):
            original(fd)
            entered.set()
            if not finish.wait(3):
                raise TimeoutError("test did not resume claim release")
            raise OSError(errno.EIO, "injected public release failure")

        async def claimed():
            async with store.exclusive("jobs", "job"):
                pass

        with monkeypatch.context() as patch:
            patch.setattr(file_module, "_unlock_close", failed_release)
            task = asyncio.create_task(claimed())
            try:
                assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
                assert task.cancel()
                finish.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            finally:
                finish.set()
                await asyncio.gather(task, return_exceptions=True)
        async with store.exclusive("jobs", "job"):
            assert len(slots(tmp_path)) == 1
