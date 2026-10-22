# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""S02–S05: actual filesystem publication, contention, faults and process death.

Only public adapters and documented JSONL framing are inspected. Fault injection
uses OS calls rather than private storage helpers. Process termination establishes
process-crash recovery, never a claim about power-loss durability.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import importlib
import json
import os
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture
def api() -> ModuleType:
    return importlib.import_module("vss.storage")


def snapshot_path(root: Path, namespace: str = "custom", key: str = "job") -> Path:
    return (
        root / namespace / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".jsonl")
    )


def framed(lines: list[bytes]) -> bytes:
    """Recompute the documented checksum to exercise semantic corruption too."""
    prefix = b"".join(line.rstrip(b"\n") + b"\n" for line in lines)
    footer = json.dumps({"sha256": hashlib.sha256(prefix).hexdigest()}).encode()
    return prefix + footer + b"\n"


@pytest.mark.parametrize("number", [b"1e999", b"-1e999"])
async def test_valid_checksum_nonfinite_number_raises_corruption(api, tmp_path, number):
    async with api.FileStorageAdapter(root=tmp_path) as store:
        await store.write("custom", "job", [{"number": 1}], expected_revision=None)
        path = snapshot_path(tmp_path)
        header = path.read_bytes().splitlines()[0]
        path.write_bytes(framed([header, b'{"number":' + number + b"}"]))
        with pytest.raises(api.StorageCorruptError):
            await store.read("custom", "job")


async def wait_thread_event(event: threading.Event) -> None:
    async with asyncio.timeout(3):
        while not event.is_set():
            await asyncio.sleep(0.005)


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    project_root = Path(__file__).resolve().parents[2]
    source_roots = (
        project_root / "src",
        project_root / "packages/ingestion/src",
        project_root / "packages/summarization/src",
    )
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (*(str(path) for path in source_roots), env.get("PYTHONPATH")))
    )
    return env


async def start_child(script: str, *args: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env(),
    )


async def child_line(process: asyncio.subprocess.Process) -> str:
    assert process.stdout is not None
    line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
    if not line:
        assert process.stderr is not None
        error = await asyncio.wait_for(process.stderr.read(), timeout=3)
        pytest.fail(
            f"child exited before expected output: {error.decode(errors='replace')}"
        )
    return line.decode().strip()


async def reap(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await asyncio.wait_for(process.communicate(), timeout=5)


async def test_documented_jsonl_framing_and_hashed_opaque_key(
    api: ModuleType, tmp_path: Path
) -> None:
    root = tmp_path / "store"
    key = "../../outside/摄像头"
    records = [{"text": "摄像头"}, {"number": 2}]
    async with api.FileStorageAdapter(root=root) as store:
        await store.write("custom", key, records, expected_revision=None)
    path = snapshot_path(root, key=key)
    lines = path.read_bytes().splitlines(keepends=True)
    header = json.loads(lines[0])
    assert header == {
        "format_version": "1.0",
        "schema_version": "1.0",
        "namespace": "custom",
        "key": key,
        "revision": 1,
        "record_count": 2,
    }
    assert [json.loads(line) for line in lines[1:-1]] == records
    assert json.loads(lines[-1]) == {
        "sha256": hashlib.sha256(b"".join(lines[:-1])).hexdigest()
    }
    assert not (tmp_path / "outside").exists()


async def test_file_instances_share_cas_and_claims(
    api: ModuleType, tmp_path: Path
) -> None:
    async with (
        api.FileStorageAdapter(root=tmp_path) as one,
        api.FileStorageAdapter(root=tmp_path) as two,
    ):
        await one.write("custom", "job", [{"winner": 0}], expected_revision=None)
        outcomes = await asyncio.gather(
            one.write("custom", "job", [{"winner": 1}], expected_revision=1),
            two.write("custom", "job", [{"winner": 2}], expected_revision=1),
            return_exceptions=True,
        )
        assert sum(isinstance(value, api.StorageSnapshot) for value in outcomes) == 1
        assert (
            sum(isinstance(value, api.StorageConflictError) for value in outcomes) == 1
        )
        assert await one.read("custom", "job") == await two.read("custom", "job")
        async with one.exclusive("jobs", "job"):
            with pytest.raises(api.StorageBusyError):
                async with two.exclusive("jobs", "job"):
                    pytest.fail("second adapter acquired an active job")
            async with two.exclusive("jobs", "different"):
                pass
        async with two.exclusive("jobs", "job"):
            pass


async def test_readers_observe_complete_snapshots_during_replacement(
    api: ModuleType, tmp_path: Path
) -> None:
    async with (
        api.FileStorageAdapter(root=tmp_path) as writer,
        api.FileStorageAdapter(root=tmp_path) as reader,
    ):
        await writer.write(
            "custom", "job", [{"generation": 0}] * 40, expected_revision=None
        )

        async def publish() -> None:
            for generation in range(1, 15):
                await writer.write(
                    "custom",
                    "job",
                    [{"generation": generation}] * 40,
                    expected_revision=generation,
                )

        async def observe() -> None:
            for _ in range(35):
                result = await reader.read("custom", "job")
                assert len(result.records) == 40
                assert {record["generation"] for record in result.records} == {
                    result.revision - 1
                }

        # This checks snapshot integrity across 14 durable replacements and 35
        # reads, not a throughput SLA. Shared runners may spend over 10 seconds
        # on filesystem I/O and registry retries. Lock deadlines have separate
        # tests; retain a bounded watchdog for hangs here.
        await asyncio.wait_for(asyncio.gather(publish(), observe()), timeout=60)
        final = await reader.read("custom", "job")
        assert final.revision == 15
        assert final.records == ({"generation": 14},) * 40


@pytest.mark.parametrize(
    "damage",
    [
        "truncated_footer",
        "missing_footer",
        "byte_corruption",
        "extra_line",
        "blank_line",
        "bad_utf8",
        "duplicate_header_key",
        "wrong_count",
        "wrong_key",
        "wrong_namespace",
        "future_format",
        "future_schema",
        "nonfinite",
        "nonobject_record",
    ],
)
async def test_corruption_is_never_salvaged_or_overwritten(
    api: ModuleType, tmp_path: Path, damage: str
) -> None:
    async with api.FileStorageAdapter(root=tmp_path) as store:
        await store.write(
            "custom", "job", [{"value": "original"}], expected_revision=None
        )
        path = snapshot_path(tmp_path)
        original = path.read_bytes()
        lines = original.splitlines(keepends=True)
        body = lines[:-1]
        header = json.loads(body[0])
        if damage == "truncated_footer":
            corrupt = original[:-12]
        elif damage == "missing_footer":
            corrupt = b"".join(body)
        elif damage == "byte_corruption":
            corrupt = original.replace(b"original", b"modified")
        elif damage == "extra_line":
            corrupt = original + b"{}\n"
        elif damage == "blank_line":
            corrupt = framed([body[0], b"", *body[1:]])
        elif damage == "bad_utf8":
            corrupt = framed([body[0], b'{"value":"\xff"}'])
        elif damage == "duplicate_header_key":
            duplicate = body[0].rstrip().removesuffix(b"}") + b',"revision":1}'
            corrupt = framed([duplicate, *body[1:]])
        elif damage == "nonfinite":
            corrupt = framed([body[0], b'{"value":NaN}'])
        elif damage == "nonobject_record":
            corrupt = framed([body[0], b"null"])
        else:
            field, value = {
                "wrong_count": ("record_count", 2),
                "wrong_key": ("key", "another-job"),
                "wrong_namespace": ("namespace", "another"),
                "future_format": ("format_version", "99.0"),
                "future_schema": ("schema_version", "99.0"),
            }[damage]
            header[field] = value
            corrupt = framed([json.dumps(header).encode(), *body[1:]])
        path.write_bytes(corrupt)
        with pytest.raises(api.StorageCorruptError):
            await store.read("custom", "job")
        with pytest.raises(api.StorageCorruptError):
            await store.write(
                "custom", "job", [{"replacement": True}], expected_revision=1
            )
        assert path.read_bytes() == corrupt


async def test_oversized_existing_file_fails_without_rewrite(
    api: ModuleType, tmp_path: Path
) -> None:
    async with api.FileStorageAdapter(root=tmp_path) as writer:
        await writer.write(
            "custom", "job", [{"large": "x" * 8192}], expected_revision=None
        )
    path = snapshot_path(tmp_path)
    original = path.read_bytes()
    async with api.FileStorageAdapter(root=tmp_path, max_snapshot_bytes=1024) as reader:
        with pytest.raises(api.StorageLimitError):
            await reader.read("custom", "job")
        with pytest.raises(api.StorageLimitError):
            await reader.write("custom", "job", [{}], expected_revision=1)
    assert path.read_bytes() == original


async def test_record_limit_applies_when_reading_existing_files(
    api: ModuleType, tmp_path: Path
) -> None:
    async with api.FileStorageAdapter(root=tmp_path) as writer:
        await writer.write("custom", "job", [{}, {}, {}], expected_revision=None)
    async with api.FileStorageAdapter(root=tmp_path, max_records=2) as reader:
        with pytest.raises(api.StorageLimitError):
            await reader.read("custom", "job")


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits")
async def test_private_created_paths_and_existing_root_mode_preserved(
    api: ModuleType, tmp_path: Path
) -> None:
    created_root = tmp_path / "new"
    async with api.FileStorageAdapter(root=created_root) as store:
        await store.write("custom", "job", [{}], expected_revision=None)
        async with store.exclusive("jobs", "job"):
            pass
    for path in [created_root, *created_root.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    existing = tmp_path / "existing"
    existing.mkdir(mode=0o755)
    existing.chmod(0o755)
    async with api.FileStorageAdapter(root=existing) as store:
        await store.write("custom", "job", [{}], expected_revision=None)
    assert stat.S_IMODE(existing.stat().st_mode) == 0o755


@pytest.mark.parametrize("kind", ["root", "namespace", "data"])
async def test_symlinks_are_rejected_without_modifying_target(
    api: ModuleType, tmp_path: Path, kind: str
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    marker = target / "keep"
    marker.write_bytes(b"caller-owned")
    root = tmp_path / "store"
    if kind == "root":
        root.symlink_to(target, target_is_directory=True)
    else:
        root.mkdir()
        if kind == "namespace":
            (root / "custom").symlink_to(target, target_is_directory=True)
        else:
            (root / "custom").mkdir()
            snapshot_path(root).symlink_to(marker)
    with pytest.raises(api.StorageError):
        async with api.FileStorageAdapter(root=root) as store:
            await store.write("custom", "job", [{}], expected_revision=None)
    assert marker.read_bytes() == b"caller-owned"
    assert sorted(path.name for path in target.iterdir()) == ["keep"]


async def test_stable_lock_files_are_not_unlinked_and_symlink_locks_rejected(
    api: ModuleType, tmp_path: Path
) -> None:
    root = tmp_path / "store"
    async with api.FileStorageAdapter(root=root) as store:
        async with store.exclusive("jobs", "job"):
            held = {
                path: path.stat().st_ino for path in root.rglob("*") if path.is_file()
            }
            assert held, "cross-process claim needs a persistent filesystem lock"
        for path, inode in held.items():
            assert path.exists()
            assert path.stat().st_ino == inode
        async with store.exclusive("jobs", "job"):
            assert all(path.stat().st_ino == inode for path, inode in held.items())
    target = tmp_path / "caller-owned"
    target.write_bytes(b"untouched")
    for path in held:
        path.unlink()
        path.symlink_to(target)
    with pytest.raises(api.StorageError):
        async with (
            api.FileStorageAdapter(root=root) as fresh,
            fresh.exclusive("jobs", "job"),
        ):
            pytest.fail("symlinked lock entry accepted")
    assert target.read_bytes() == b"untouched"


@pytest.mark.parametrize("fault", ["file_fsync", "replace"])
async def test_failure_before_replace_preserves_old_bytes_and_unrelated_temp(
    api: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    async with api.FileStorageAdapter(root=tmp_path) as store:
        await store.write("custom", "job", [{"value": "old"}], expected_revision=None)
        path = snapshot_path(tmp_path)
        old = path.read_bytes()
        unrelated = path.with_name("caller-owned-unrelated.tmp")
        unrelated.write_bytes(b"keep")
        injected = threading.Event()
        real_fsync, real_replace = os.fsync, os.replace

        def fail_fsync(fd: int) -> None:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                injected.set()
                raise OSError(errno.ENOSPC, "injected disk full")
            real_fsync(fd)

        def fail_replace(*args: Any, **kwargs: Any) -> None:
            injected.set()
            raise OSError(errno.EIO, "injected replacement failure")

        with monkeypatch.context() as patch:
            patch.setattr(
                os, "fsync", fail_fsync if fault == "file_fsync" else real_fsync
            )
            patch.setattr(
                os, "replace", fail_replace if fault == "replace" else real_replace
            )
            with pytest.raises(api.StorageError):
                await store.write(
                    "custom", "job", [{"value": "new"}], expected_revision=1
                )
        assert injected.is_set()
        assert path.read_bytes() == old
        assert unrelated.read_bytes() == b"keep"
        assert (await store.read("custom", "job")).revision == 1
        await store.write(
            "custom", "job", [{"value": "recovered"}], expected_revision=1
        )


@pytest.mark.skipif(os.name != "posix", reason="directory fsync gate on POSIX")
async def test_directory_fsync_failure_after_replace_is_indeterminate(
    api: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with api.FileStorageAdapter(root=tmp_path) as store:
        await store.write("custom", "job", [{"value": "old"}], expected_revision=None)
        real_replace, real_fsync = os.replace, os.fsync
        replaced = threading.Event()
        failed_flush = threading.Event()

        def observe_replace(*args: Any, **kwargs: Any) -> None:
            real_replace(*args, **kwargs)
            replaced.set()

        def fail_directory_flush(fd: int) -> None:
            if replaced.is_set() and stat.S_ISDIR(os.fstat(fd).st_mode):
                failed_flush.set()
                raise OSError(errno.EIO, "directory flush failed")
            real_fsync(fd)

        with monkeypatch.context() as patch:
            patch.setattr(os, "replace", observe_replace)
            patch.setattr(os, "fsync", fail_directory_flush)
            with pytest.raises(api.StorageIndeterminateError):
                await store.write(
                    "custom", "job", [{"value": "new"}], expected_revision=1
                )
        assert failed_flush.is_set()
        visible = await store.read("custom", "job")
        assert visible.revision == 2
        assert visible.records == ({"value": "new"},)
        with pytest.raises(api.StorageConflictError):
            await store.write(
                "custom", "job", [{"value": "stale retry"}], expected_revision=1
            )
        durable = await store.write(
            "custom", "job", visible.records, expected_revision=visible.revision
        )
        assert durable.revision == 3


@pytest.mark.skipif(os.name != "posix", reason="directory fsync gate on POSIX")
async def test_first_publication_flushes_new_directory_entries_in_parents(
    api: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "new-root"
    seen: set[tuple[int, int]] = set()
    real_fsync = os.fsync

    def observe(fd: int) -> None:
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            seen.add((info.st_dev, info.st_ino))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", observe)
    async with api.FileStorageAdapter(root=root) as store:
        await store.write("custom", "job", [{}], expected_revision=None)
    for directory in (tmp_path, root, root / "custom"):
        info = directory.stat()
        assert (info.st_dev, info.st_ino) in seen, (
            f"new directory entry not flushed in {directory}"
        )


@pytest.mark.parametrize("phase", ["file_fsync", "after_replace"])
async def test_cancelled_commit_settles_before_claim_release_and_keeps_loop_responsive(
    api: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    async with (
        api.FileStorageAdapter(root=tmp_path) as store,
        api.FileStorageAdapter(root=tmp_path) as other,
    ):
        await store.write("custom", "job", [{"value": "old"}], expected_revision=None)
        entered, release = threading.Event(), threading.Event()
        real_fsync, real_replace = os.fsync, os.replace
        loop_thread = threading.get_ident()
        native_threads: list[int] = []

        def block() -> None:
            native_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(6):
                raise TimeoutError("test did not release blocked filesystem call")

        def fsync(fd: int) -> None:
            if phase == "file_fsync" and stat.S_ISREG(os.fstat(fd).st_mode):
                block()
            real_fsync(fd)

        def replace(*args: Any, **kwargs: Any) -> None:
            real_replace(*args, **kwargs)
            if phase == "after_replace":
                block()

        async def owner() -> None:
            async with store.exclusive("jobs", "job"):
                await store.write(
                    "custom", "job", [{"value": "new"}], expected_revision=1
                )

        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", fsync)
            patch.setattr(os, "replace", replace)
            task = asyncio.create_task(owner())
            try:
                await wait_thread_event(entered)
                assert native_threads and loop_thread not in native_threads
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0.01)
                    assert not task.done(), (
                        "cancellation returned before native commit settled"
                    )
                with pytest.raises(api.StorageBusyError):
                    async with other.exclusive("jobs", "job"):
                        pytest.fail("claim released while write still in flight")
            finally:
                release.set()
                await asyncio.wait_for(
                    asyncio.gather(task, return_exceptions=True), timeout=3
                )
            assert task.cancelled()
        async with other.exclusive("jobs", "job"):
            current = await other.read("custom", "job")
        assert current.records in (({"value": "old"},), ({"value": "new"},))
        if phase == "after_replace":
            assert current.records == ({"value": "new"},)


async def test_bounded_admission_does_not_submit_cancelled_waiters_to_executor(
    api: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = api.FileStorageAdapter(root=tmp_path, max_in_flight=2)
    await store.write("custom", "initial", [{}], expected_revision=None)
    release = threading.Event()
    lock = threading.Lock()
    started = 0
    outstanding = 0
    max_outstanding = 0
    real_fsync, real_submit = os.fsync, ThreadPoolExecutor.submit
    real_write_snapshot = store._write_snapshot
    started_keys: list[str] = []

    def observe_write(namespace, key, *args):
        with lock:
            started_keys.append(key)
        return real_write_snapshot(namespace, key, *args)

    def fsync(fd: int) -> None:
        nonlocal started
        if stat.S_ISREG(os.fstat(fd).st_mode):
            with lock:
                started += 1
            if not release.wait(6):
                raise TimeoutError("test did not release blocked fsync")
        real_fsync(fd)

    def submit(executor: ThreadPoolExecutor, *args: Any, **kwargs: Any) -> Any:
        nonlocal outstanding, max_outstanding
        with lock:
            outstanding += 1
            max_outstanding = max(max_outstanding, outstanding)

        def completed(_: Any) -> None:
            nonlocal outstanding
            with lock:
                outstanding -= 1

        future = real_submit(executor, *args, **kwargs)
        future.add_done_callback(completed)
        return future

    tasks: list[asyncio.Task[Any]] = []
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", fsync)
            patch.setattr(ThreadPoolExecutor, "submit", submit)
            patch.setattr(store, "_write_snapshot", observe_write)
            tasks = [
                asyncio.create_task(
                    store.write("custom", f"job-{n}", [{}], expected_revision=None)
                )
                for n in range(15)
            ]
            async with asyncio.timeout(3):
                while started < 2:
                    await asyncio.sleep(0.005)
            await asyncio.sleep(0.03)
            assert started == 2
            assert max_outstanding <= 2, (
                "unadmitted operations filled an executor queue"
            )
            # Registry retries can let another key overtake an earlier task.
            # Cancel queued work rather than assuming submission-order admission.
            running = {int(key.removeprefix("job-")) for key in started_keys}
            queued = [task for index, task in enumerate(tasks) if index not in running]
            for task in queued:
                task.cancel()
            await asyncio.gather(*queued, return_exceptions=True)
            release.set()
            await asyncio.wait_for(
                asyncio.gather(*(tasks[n] for n in running)), timeout=3
            )
        for n in set(range(15)) - running:
            with pytest.raises(api.StorageNotFoundError):
                await store.read("custom", f"job-{n}")
        await store.write("custom", "after", [{}], expected_revision=None)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await store.aclose()


async def test_close_waits_for_admitted_write_without_claim(
    api: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = api.FileStorageAdapter(root=tmp_path)
    await store.write("custom", "job", [{}], expected_revision=None)
    entered, release = threading.Event(), threading.Event()
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            entered.set()
            if not release.wait(6):
                raise TimeoutError("test did not release blocked fsync")
        real_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fsync)
        write = asyncio.create_task(
            store.write("custom", "job", [{"new": True}], expected_revision=1)
        )
        close: asyncio.Task[Any] | None = None
        try:
            await wait_thread_event(entered)
            close = asyncio.create_task(store.aclose())
            await asyncio.sleep(0.03)
            assert not close.done()
            with pytest.raises(api.StorageClosedError):
                await store.read("custom", "job")
        finally:
            release.set()
            await asyncio.wait_for(
                asyncio.gather(write, *([close] if close else [])), timeout=3
            )
    await store.aclose()
    async with api.FileStorageAdapter(root=tmp_path) as fresh:
        assert (await fresh.read("custom", "job")).records == ({"new": True},)


CAS_CHILD = r"""
import asyncio, sys
from vss.storage import FileStorageAdapter, StorageConflictError
async def main():
    async with FileStorageAdapter(root=sys.argv[1]) as store:
        print("ready", flush=True)
        await asyncio.to_thread(sys.stdin.readline)
        try:
            result = await store.write(
                "custom", "job", [{"writer": sys.argv[2]}], expected_revision=1
            )
        except StorageConflictError:
            print("conflict", flush=True)
        else:
            print("won:" + str(result.revision), flush=True)
asyncio.run(main())
"""


async def test_multiprocess_cas_has_one_winner(api: ModuleType, tmp_path: Path) -> None:
    async with api.FileStorageAdapter(root=tmp_path) as store:
        await store.write(
            "custom", "job", [{"writer": "parent"}], expected_revision=None
        )
        children: list[asyncio.subprocess.Process] = []
        try:
            children = [
                await start_child(CAS_CHILD, str(tmp_path), str(n)) for n in range(2)
            ]
            assert await asyncio.gather(*(child_line(p) for p in children)) == [
                "ready",
                "ready",
            ]
            for child in children:
                assert child.stdin is not None
                child.stdin.write(b"go\n")
                await child.stdin.drain()
            results = await asyncio.gather(*(child_line(p) for p in children))
            assert sorted(results) == ["conflict", "won:2"]
            assert await asyncio.gather(
                *(asyncio.wait_for(p.wait(), timeout=5) for p in children)
            ) == [0, 0]
            result = await store.read("custom", "job")
            assert result.revision == 2
            assert result.records[0]["writer"] in ("0", "1")
        finally:
            await asyncio.gather(*(reap(p) for p in children))


CRASH_CHILD = r"""
import asyncio, os, stat, sys, threading
from vss.storage import FileStorageAdapter
phase = sys.argv[2]
replace, fsync = os.replace, os.fsync
def stop():
    print("blocked", flush=True)
    threading.Event().wait()
def hooked_replace(*args, **kwargs):
    if phase == "before_replace": stop()
    replace(*args, **kwargs)
    if phase == "after_replace": stop()
def hooked_fsync(fd):
    fsync(fd)
    if phase == "after_file_fsync" and stat.S_ISREG(os.fstat(fd).st_mode): stop()
os.replace, os.fsync = hooked_replace, hooked_fsync
async def main():
    async with FileStorageAdapter(root=sys.argv[1]) as store:
        async with store.exclusive("jobs", "job"):
            await store.write("custom", "job", [{"value": "new"}], expected_revision=1)
asyncio.run(main())
"""


@pytest.mark.parametrize(
    "phase", ["after_file_fsync", "before_replace", "after_replace"]
)
async def test_process_death_preserves_complete_snapshot_and_releases_claim(
    api: ModuleType, tmp_path: Path, phase: str
) -> None:
    async with api.FileStorageAdapter(root=tmp_path) as setup:
        await setup.write("custom", "job", [{"value": "old"}], expected_revision=None)
    orphan = tmp_path / "custom" / "caller-owned-orphan.tmp"
    orphan.write_bytes(b"not a committed snapshot")
    child = await start_child(CRASH_CHILD, str(tmp_path), phase)
    try:
        assert await child_line(child) == "blocked"
        async with api.FileStorageAdapter(root=tmp_path) as observer:
            with pytest.raises(api.StorageBusyError):
                async with observer.exclusive("jobs", "job"):
                    pytest.fail("parent acquired child's active claim")
        await reap(child)
        async with api.FileStorageAdapter(root=tmp_path) as fresh:
            async with fresh.exclusive("jobs", "job"):
                current = await fresh.read("custom", "job")
                expected = "new" if phase == "after_replace" else "old"
                assert current.records == ({"value": expected},)
                await fresh.write(
                    "custom",
                    "job",
                    [{"value": "resumed"}],
                    expected_revision=current.revision,
                )
        assert orphan.read_bytes() == b"not a committed snapshot"
    finally:
        await reap(child)
