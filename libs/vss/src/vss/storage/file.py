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

"""Private, checksummed JSONL snapshots on a caller-owned local filesystem."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import stat
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from .base import (
    StorageAdapter,
    detach_records,
    validate_expected_revision,
    validate_identity,
)
from .errors import (
    StorageBusyError,
    StorageConflictError,
    StorageCorruptError,
    StorageError,
    StorageIndeterminateError,
    StorageLimitError,
    StorageNotFoundError,
)
from .models import StorageConfig, StorageSnapshot


class _RegistryBusyError(StorageBusyError):
    """Registry contention, distinct from an already-owned job key."""


class FileStorageAdapter(StorageAdapter):
    """Persist snapshots as atomically replaced, checksummed JSONL files.

    The root must be a trusted, caller-owned local filesystem directory.
    Created paths use private permissions where supported. Fresh roots reuse
    stable lock slots. Unsupported pre-release lock layouts are rejected.
    All processes using slot mode must support its registry protocol.
    The default root is ``Path.home() / '.vss'``. Each snapshot is limited by
    ``max_snapshot_bytes`` and ``max_records``; filesystem work is admitted to a
    bounded executor controlled by ``max_in_flight``.
    """

    @property
    def persistent(self) -> bool:
        """File snapshots remain available after closing or restarting Python."""
        return True

    def __init__(
        self,
        *,
        root: Path | str | None = None,
        max_snapshot_bytes: int = 64 * 1024 * 1024,
        max_records: int = 100_000,
        max_in_flight: int = 4,
        lock_wait_timeout_s: float = 30.0,
    ) -> None:
        """Create a file adapter with validated limits.

        ``root=None`` selects ``Path.home() / '.vss'``. Directory creation is
        deferred until first use; created storage paths use private permissions
        where supported. Invalid limits raise Pydantic ``ValidationError``.
        ``lock_wait_timeout_s`` bounds retries for a contended operation lock;
        it does not interrupt work holding a lock. Ownership claims fail promptly.
        Reusable slots are the only lock protocol. Unsupported pre-release
        per-key lock roots are rejected; use a fresh root for new work.
        Snapshot bytes and the public CAS/claim contracts do not change.
        """
        config_values: dict[str, Any] = {
            "backend": "file",
            "max_snapshot_bytes": max_snapshot_bytes,
            "max_records": max_records,
            "max_in_flight": max_in_flight,
            "lock_wait_timeout_s": lock_wait_timeout_s,
        }
        if root is not None:
            config_values["root"] = root
        config = StorageConfig(**config_values)
        assert config.root is not None
        self.root = config.root.expanduser()
        if not self.root.is_absolute():
            self.root = Path(os.path.abspath(self.root))
        self.max_snapshot_bytes = config.max_snapshot_bytes
        self.max_records = config.max_records
        self.lock_wait_timeout_s = config.lock_wait_timeout_s
        self._init_runtime(config.max_in_flight)

    def _snapshot_path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / namespace / f"{digest}.jsonl"

    def _acquire_key(self, namespace: str, key: str, purpose: str) -> tuple[int, Path]:
        """Acquire protocol-aware ownership; caller must unlock and close."""
        from ._file_locks import acquire_key

        return acquire_key(self, namespace, key, purpose)

    @staticmethod
    def _lstat(path: Path) -> os.stat_result | None:
        try:
            return path.lstat()
        except FileNotFoundError:
            return None

    @classmethod
    def _ensure_directory(cls, path: Path) -> tuple[Path, ...]:
        """Create one path privately and return directories whose entries changed."""
        info = cls._lstat(path)
        if info is not None:
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise StorageError(f"unsafe storage directory: {path}")
            return ()
        parent = path.parent
        created_parents = (
            cls._ensure_directory(parent) if cls._lstat(parent) is None else ()
        )
        parent_info = cls._lstat(parent)
        if (
            parent_info is None
            or stat.S_ISLNK(parent_info.st_mode)
            or not stat.S_ISDIR(parent_info.st_mode)
        ):
            raise StorageError(f"unsafe storage parent directory: {parent}")
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            info = cls._lstat(path)
            if (
                info is None
                or stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
            ):
                raise StorageError(f"unsafe storage directory: {path}") from None
            return created_parents
        except OSError as exc:
            raise StorageError(f"could not create storage directory: {path}") from exc
        try:
            cls._fsync_directory(parent)
        except OSError as exc:
            # Before publication this remains a definite failure. The new
            # directory may be visible, but no snapshot commit was attempted.
            raise StorageError(
                f"could not flush storage directory entry: {path}"
            ) from exc
        return (*created_parents, parent)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(path, flags)
        try:
            try:
                os.fsync(fd)
            except OSError as exc:
                unsupported = {errno.EINVAL, errno.EBADF}
                if hasattr(errno, "ENOTSUP"):
                    unsupported.add(errno.ENOTSUP)
                if exc.errno not in unsupported:
                    raise
        finally:
            os.close(fd)

    @staticmethod
    def _ensure_regular_or_missing(path: Path) -> None:
        info = FileStorageAdapter._lstat(path)
        if info is None:
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise StorageError(f"unsafe storage file: {path}")

    @classmethod
    def _open_lock(cls, path: Path) -> int:
        cls._ensure_directory(path.parent)
        cls._ensure_regular_or_missing(path)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as exc:
            raise StorageError(f"could not open storage lock: {path}") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise StorageError(f"unsafe storage lock: {path}")
        except BaseException:
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _json_line(value: Any) -> bytes:
        return (
            json.dumps(
                value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
            + b"\n"
        )

    @classmethod
    def _encode(
        cls,
        namespace: str,
        key: str,
        revision: int,
        records: tuple[dict[str, Any], ...],
    ) -> bytes:
        header = {
            "format_version": "1.0",
            "schema_version": "1.0",
            "namespace": namespace,
            "key": key,
            "revision": revision,
            "record_count": len(records),
        }
        prefix = cls._json_line(header) + b"".join(
            cls._json_line(item) for item in records
        )
        return prefix + cls._json_line({"sha256": hashlib.sha256(prefix).hexdigest()})

    @staticmethod
    def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    @classmethod
    def _decode_json(cls, raw: bytes) -> Any:
        def invalid_constant(value: str) -> None:
            raise ValueError(f"nonfinite JSON number: {value}")

        try:
            return json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=cls._pairs_no_duplicates,
                parse_constant=invalid_constant,
            )
        except RecursionError as exc:
            raise ValueError("JSON nesting is too deep") from exc

    def _read_snapshot(self, namespace: str, key: str) -> StorageSnapshot:
        path = self._snapshot_path(namespace, key)
        self._ensure_directory(path.parent)
        self._ensure_regular_or_missing(path)
        try:
            info = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            raise StorageNotFoundError(key) from None
        except OSError as exc:
            raise StorageError("could not inspect storage snapshot") from exc
        if not stat.S_ISREG(info.st_mode):
            raise StorageError(f"unsafe storage file: {path}")
        if info.st_size > self.max_snapshot_bytes:
            raise StorageLimitError("stored snapshot exceeds configured byte limit")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as stream:
                data = stream.read(self.max_snapshot_bytes + 1)
        except FileNotFoundError:
            raise StorageNotFoundError(key) from None
        except OSError as exc:
            raise StorageError("could not read storage snapshot") from exc
        if len(data) > self.max_snapshot_bytes:
            raise StorageLimitError("stored snapshot exceeds configured byte limit")
        try:
            if not data.endswith(b"\n"):
                raise ValueError("snapshot is missing final newline")
            lines = data.splitlines(keepends=True)
            if len(lines) < 2 or any(line == b"\n" for line in lines):
                raise ValueError("snapshot has blank or missing lines")
            parsed = [self._decode_json(line[:-1]) for line in lines]
            header, footer = parsed[0], parsed[-1]
            if type(header) is not dict or type(footer) is not dict:
                raise ValueError("header and footer must be objects")
            if set(header) != {
                "format_version",
                "schema_version",
                "namespace",
                "key",
                "revision",
                "record_count",
            }:
                raise ValueError("invalid header fields")
            if header["format_version"] != "1.0" or header["schema_version"] != "1.0":
                raise ValueError("unsupported storage version")
            if header["namespace"] != namespace or header["key"] != key:
                raise ValueError("snapshot identity mismatch")
            revision, count = header["revision"], header["record_count"]
            if (
                type(revision) is not int
                or revision <= 0
                or type(count) is not int
                or count < 0
            ):
                raise ValueError("invalid revision or record count")
            records = parsed[1:-1]
            if count != len(records) or count > self.max_records:
                if count > self.max_records:
                    raise StorageLimitError(
                        "stored record count exceeds configured limit"
                    )
                raise ValueError("record count mismatch")
            if any(type(record) is not dict for record in records):
                raise ValueError("records must be JSON objects")
            if set(footer) != {"sha256"} or type(footer["sha256"]) is not str:
                raise ValueError("invalid checksum footer")
            prefix = b"".join(lines[:-1])
            if hashlib.sha256(prefix).hexdigest() != footer["sha256"]:
                raise ValueError("snapshot checksum mismatch")
            return StorageSnapshot(
                schema_version=header["schema_version"],
                revision=revision,
                records=tuple(records),
            )
        except StorageLimitError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise StorageCorruptError(
                "stored snapshot is corrupt or unsupported"
            ) from exc

    def _write_snapshot(
        self,
        namespace: str,
        key: str,
        records: tuple[dict[str, Any], ...],
        expected_revision: int | None,
    ) -> StorageSnapshot:
        path = self._snapshot_path(namespace, key)
        created_dirs = list(self._ensure_directory(path.parent))
        self._ensure_regular_or_missing(path)
        current: StorageSnapshot | None
        try:
            current = self._read_snapshot(namespace, key)
        except StorageNotFoundError:
            current = None
        if expected_revision is None:
            if current is not None:
                raise StorageConflictError("snapshot already exists")
            revision = 1
        else:
            if current is None or current.revision != expected_revision:
                raise StorageConflictError("snapshot revision changed")
            revision = current.revision + 1
        data = self._encode(namespace, key, revision, records)
        if len(data) > self.max_snapshot_bytes:
            raise StorageLimitError("snapshot exceeds configured byte limit")

        fd = -1
        temp_path: Path | None = None
        replaced = False
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            temp_path = Path(temp_name)
            os.fchmod(fd, 0o600) if hasattr(os, "fchmod") else None
            with os.fdopen(fd, "wb") as stream:
                fd = -1
                written = stream.write(data)
                if written != len(data):
                    raise OSError(errno.EIO, "short write while publishing snapshot")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
            replaced = True
            # Replacement flushes the destination directory. A newly created
            # namespace/root also changes each ancestor directory entry.
            dirs = [path.parent, *created_dirs]
            if self.root in created_dirs:
                dirs.append(self.root.parent)
            seen: set[Path] = set()
            for directory in dirs:
                if directory not in seen:
                    seen.add(directory)
                    self._fsync_directory(directory)
        except OSError as exc:
            if replaced:
                raise StorageIndeterminateError(
                    "snapshot replacement is visible but durability is uncertain"
                ) from exc
            raise StorageError("snapshot commit failed before replacement") from exc
        finally:
            if fd >= 0:
                os.close(fd)
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
        return StorageSnapshot(revision=revision, records=records)

    async def _operation_with_lock[T](
        self,
        namespace: str,
        key: str,
        operation: Callable[[], T],
        *,
        _admitted: bool = False,
    ) -> T:
        """Retry nonblocking OS locks without occupying workers during backoff."""
        if not _admitted:
            self._operation_started()
        deadline = time.monotonic() + self.lock_wait_timeout_s
        try:
            while True:

                def attempt() -> T:
                    handle = self._acquire_key(namespace, key, "operation")
                    try:
                        result = operation()
                    except BaseException:
                        try:
                            _release_key(handle[0])
                        except StorageError:
                            pass  # Preserve the operation's primary failure.
                        raise
                    _release_key(handle[0])
                    return result

                try:
                    return await self._run(attempt, _admitted=True)
                except StorageBusyError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise StorageBusyError("storage key remained busy") from None
                    await asyncio.sleep(min(0.01, remaining))
        finally:
            if not _admitted:
                self._operation_finished()

    async def read(self, namespace: str, key: str) -> StorageSnapshot:
        """Read one complete, checksum-verified snapshot under its key lock."""
        namespace, key = validate_identity(namespace, key)

        return await self._operation_with_lock(
            namespace, key, lambda: self._read_snapshot(namespace, key)
        )

    async def write(
        self,
        namespace: str,
        key: str,
        records: Sequence[dict[str, Any]],
        *,
        expected_revision: int | None,
    ) -> StorageSnapshot:
        """Atomically replace one complete snapshot after a revision check."""
        namespace, key = validate_identity(namespace, key)
        expected_revision = validate_expected_revision(expected_revision)
        self._operation_started()
        try:
            detached = await self._run(
                detach_records, records, self.max_records, _admitted=True
            )
            return await self._operation_with_lock(
                namespace,
                key,
                lambda: self._write_snapshot(
                    namespace, key, detached, expected_revision
                ),
                _admitted=True,
            )
        finally:
            self._operation_finished()

    def _assert_safe_root(self, *, may_be_missing: bool) -> None:
        info = self._lstat(self.root)
        if info is None:
            if may_be_missing:
                return
            raise StorageError("storage root does not exist")
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise StorageError("storage root must be a real directory, not a symlink")

    @asynccontextmanager
    async def exclusive(self, namespace: str, key: str) -> AsyncIterator[None]:
        """Hold a cross-process nonblocking claim separate from snapshot locks."""
        namespace, key = validate_identity(namespace, key)
        self._claim_started()

        def acquire_handle() -> tuple[int, Any]:
            return self._acquire_key(namespace, key, "claim")

        handle: tuple[int, Any] | None = None
        try:
            holder: list[tuple[int, Any]] = []

            def do_acquire() -> bool:
                try:
                    holder.append(acquire_handle())
                    return True
                except _RegistryBusyError:
                    raise
                except StorageBusyError:
                    return False

            try:
                deadline = time.monotonic() + self.lock_wait_timeout_s
                while True:
                    try:
                        got = await self._run(do_acquire)
                        break
                    except _RegistryBusyError:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise StorageBusyError(
                                "storage registry remained busy"
                            ) from None
                        await asyncio.sleep(min(0.01, remaining))
            except asyncio.CancelledError:
                # _run waits for its worker. If it acquired during cancellation,
                # the holder communicates that descriptor for cleanup.
                if holder:
                    try:
                        _release_key(holder.pop()[0])
                    except StorageError:
                        pass  # Cancellation remains the primary failure.
                raise
            if not got:
                raise StorageBusyError(f"claim is already held for {namespace}/{key}")
            handle = holder.pop()
            body_failed = False
            try:
                yield
            except BaseException:
                body_failed = True
                raise
            finally:
                fd = handle[0]
                release_task = asyncio.create_task(self._run(_release_key, fd))
                cancelled = False
                try:
                    # Retain cancellation even if the settled worker failed.
                    while not release_task.done():
                        try:
                            await asyncio.shield(release_task)
                        except asyncio.CancelledError:
                            cancelled = True
                        except StorageError:
                            break
                    try:
                        release_task.result()
                    except StorageError:
                        if cancelled and not body_failed:
                            raise asyncio.CancelledError from None
                        if not body_failed:
                            raise
                    if cancelled and not body_failed:
                        raise asyncio.CancelledError
                finally:
                    # The worker has settled, including on release failure.
                    handle = None
        finally:
            if handle is not None:
                _release_key(handle[0])
            self._claim_finished()

    async def aclose(self) -> None:
        """Wait for admitted operations and close this adapter."""
        await self._close_runtime()


def _lock_fd(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        contention = isinstance(exc, BlockingIOError) or exc.errno in (
            errno.EACCES,
            errno.EAGAIN,
        )
        if contention:
            raise StorageBusyError("storage key is busy") from None
        raise StorageError("storage file locking failed") from exc


def _unlock_close(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _release_key(fd: int) -> None:
    """Close ownership with the public storage error contract."""
    try:
        _unlock_close(fd)
    except OSError as exc:
        raise StorageError("storage lock release failed") from exc
