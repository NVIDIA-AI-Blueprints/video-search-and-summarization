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

"""Permanent reusable OS lock slots, coordinated by a permanent registry.

All openers must participate. Busy identities live after Windows lock byte zero;
free slots may contain torn metadata after a crash and are simply reused.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from .base import validate_identity
from .errors import (
    StorageBusyError,
    StorageConfigurationError,
    StorageCorruptError,
    StorageError,
)
from .file import FileStorageAdapter, _lock_fd, _RegistryBusyError, _unlock_close

_METADATA_LIMIT = 32 * 1024


def _validate_layout(adapter: FileStorageAdapter, directory: Path) -> None:
    """Reject incompatible locks, including beside a valid slots marker."""
    has_marker = (directory / "mode.json").exists()
    for path in directory.iterdir():
        if path.name in {"registry.lock", "mode.json", ".mode-pending"}:
            adapter._ensure_regular_or_missing(path)
        elif re.fullmatch(r"slot-[0-9]{8,}\.lock", path.name):
            adapter._ensure_regular_or_missing(path)
            if not has_marker:
                raise StorageCorruptError("slots exist without a lock protocol marker")
        elif path.name.startswith("slot-") and not stat.S_ISDIR(path.lstat().st_mode):
            raise StorageCorruptError("invalid lock slot filename")
        else:
            raise StorageConfigurationError(
                "Unsupported pre-release lock layout; use a fresh storage root"
            )


def _ensure_protocol(adapter: FileStorageAdapter, directory: Path) -> None:
    _validate_layout(adapter, directory)
    marker = directory / "mode.json"
    adapter._ensure_regular_or_missing(marker)
    if marker.exists():
        with marker.open("rb") as stream:
            raw = stream.read(1025)
        try:
            record = json.loads(raw)
            if (
                len(raw) > 1024
                or type(record) is not dict
                or set(record) != {"schema_version", "mode"}
                or record["schema_version"] != "1.0"
                or type(record["mode"]) is not str
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise StorageCorruptError("invalid lock protocol marker") from None
        if record["mode"] != "slots":
            raise StorageConfigurationError(
                "Unsupported lock protocol; use a fresh storage root"
            )
    else:
        # A fixed pending path also bounds debris from interrupted initialization.
        pending = directory / ".mode-pending"
        adapter._ensure_regular_or_missing(pending)
        flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(pending, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                payload = json.dumps(
                    {"schema_version": "1.0", "mode": "slots"}
                ).encode()
                if stream.write(payload) != len(payload):
                    raise StorageError("short lock protocol marker write")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, marker)
            for path in (directory, adapter.root, adapter.root.parent):
                adapter._fsync_directory(path)
        finally:
            try:
                pending.unlink()
            except FileNotFoundError:
                pass


def _busy_identity(fd: int) -> dict[str, str]:
    os.lseek(fd, 1, os.SEEK_SET)
    raw = os.read(fd, _METADATA_LIMIT + 1)
    try:
        identity = json.loads(raw)
        if (
            len(raw) > _METADATA_LIMIT
            or type(identity) is not dict
            or set(identity) != {"schema_version", "namespace", "key", "purpose"}
            or identity["schema_version"] != "1.0"
            or identity["purpose"] not in {"operation", "claim"}
        ):
            raise ValueError
        validate_identity(identity["namespace"], identity["key"])
    except (ValueError, TypeError, RecursionError, StorageConfigurationError):
        raise StorageCorruptError("busy lock slot has invalid identity") from None
    return identity


def _slots(
    adapter: FileStorageAdapter, directory: Path, identity: dict[str, str]
) -> tuple[int, Path]:
    paths = sorted(directory.glob("slot-*.lock"))
    indexes = []
    for path in paths:
        match = re.fullmatch(r"slot-([0-9]{8,})\.lock", path.name)
        if match is None:
            raise StorageCorruptError("invalid lock slot filename")
        indexes.append(int(match[1]))
    free: tuple[int, Path] | None = None
    try:
        # A free early slot cannot hide a matching owner in a later slot.
        for path in paths:
            fd = adapter._open_lock(path)
            try:
                _lock_fd(fd)
            except StorageBusyError:
                try:
                    busy = _busy_identity(fd)
                finally:
                    os.close(fd)
                if busy == identity:
                    raise StorageBusyError("storage key is busy") from None
            except BaseException:
                os.close(fd)
                raise
            else:
                if free is None:
                    free = (fd, path)
                else:
                    _unlock_close(fd)
        if free is None:
            path = directory / f"slot-{max(indexes, default=-1) + 1:08d}.lock"
            fd = adapter._open_lock(path)
            try:
                _lock_fd(fd)
            except BaseException:
                os.close(fd)
                raise
            free = (fd, path)
        fd = free[0]
        raw = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()
        os.lseek(fd, 1, os.SEEK_SET)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise StorageError("lock identity write did not progress")
            view = view[written:]
        os.ftruncate(fd, len(raw) + 1)
        result, free = free, None
        return result
    finally:
        if free is not None:
            _unlock_close(free[0])


def acquire_key(
    adapter: FileStorageAdapter, namespace: str, key: str, purpose: str
) -> tuple[int, Path]:
    """Return a held descriptor; caller must unlock/close on every exit path."""
    adapter._assert_safe_root(may_be_missing=True)
    directory = adapter.root / ".locks"
    try:
        guard_fd = adapter._open_lock(directory / "registry.lock")
        try:
            _lock_fd(guard_fd)
        except StorageBusyError:
            os.close(guard_fd)
            raise _RegistryBusyError("storage lock registry is busy") from None
        except BaseException:
            os.close(guard_fd)
            raise
        handle = None
        try:
            _ensure_protocol(adapter, directory)
            handle = _slots(
                adapter,
                directory,
                {
                    "schema_version": "1.0",
                    "namespace": namespace,
                    "key": key,
                    "purpose": purpose,
                },
            )
        finally:
            try:
                _unlock_close(guard_fd)
            except BaseException:
                if handle is not None:
                    _unlock_close(handle[0])
                raise
        assert handle is not None
        return handle
    except OSError:
        raise StorageError("storage lock coordination failed") from None
