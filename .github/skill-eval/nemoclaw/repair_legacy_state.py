#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Quarantine one provably stale CI-only NemoClaw onboarding session.

NemoClaw v0.0.97 segregates non-default gateway state before command dispatch.
An older shared ``onboard-session.json`` can claim the default gateway while
the same ``demo`` sandbox's registry row already claims the skill-eval gateway.
The upstream migration correctly refuses that contradictory state before
``nemoclaw onboard --fresh`` gets a chance to discard the stale session.

This helper repairs only that exact CI boundary.  It never edits the registry,
never follows links, and never removes a session.  Instead it atomically moves
the owner-only shared session into an owner-only, worker-local quarantine under
``~/.nemoclaw``.  The quarantine is deliberately outside the skill-eval
artifact safelist and must never be uploaded.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import IO, Mapping, Sequence

DEFAULT_GATEWAY_PORT = 8080
SKILL_EVAL_GATEWAY_PORT = 19080
SKILL_EVAL_SANDBOX = "demo"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_STATE_ENTRIES = 1024
TRUE_VALUES = frozenset({"1", "true", "yes"})
QUARANTINE_DIR_NAME = ".skill-eval-session-quarantine"
MIGRATION_LOCK_NAME = ".gateway-state-migration.lock"
MIGRATION_INTENT_NAME = ".gateway-state-migration"


class LegacyStateRepairError(RuntimeError):
    """The stale session could not be proven safe to quarantine."""

    def __init__(self, code: str):
        if not re.fullmatch(r"[a-z0-9_]+", code):
            raise ValueError("legacy-state repair errors require fixed reason codes")
        self.code = code
        super().__init__(code)


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUE_VALUES


def _path_lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _require_real_owned_directory(
    path: Path,
    *,
    uid: int,
    owner_only: bool,
) -> os.stat_result:
    info = _path_lstat(path)
    if info is None:
        raise LegacyStateRepairError("state_directory_missing")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise LegacyStateRepairError("state_directory_unsafe")
    if info.st_uid != uid:
        raise LegacyStateRepairError("state_owner_mismatch")
    if owner_only and info.st_mode & 0o077:
        raise LegacyStateRepairError("state_permissions_unsafe")
    return info


def _open_owned_json(
    path: Path,
    *,
    uid: int,
) -> tuple[IO[bytes], os.stat_result, object]:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0:
        info = _path_lstat(path)
        if info is not None and stat.S_ISLNK(info.st_mode):
            raise LegacyStateRepairError("state_file_unsafe")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise LegacyStateRepairError("state_file_unsafe") from exc

    handle = os.fdopen(fd, "rb")
    try:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise LegacyStateRepairError("state_file_unsafe")
        if info.st_uid != uid:
            raise LegacyStateRepairError("state_owner_mismatch")
        if info.st_mode & 0o077:
            raise LegacyStateRepairError("state_permissions_unsafe")
        if info.st_size > MAX_JSON_BYTES:
            raise LegacyStateRepairError("state_file_too_large")
        try:
            value = json.load(handle)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LegacyStateRepairError("state_json_invalid") from exc
        return handle, info, value
    except BaseException:
        handle.close()
        raise


def _gateway_port_from_name(value: object) -> int:
    if value == "nemoclaw":
        return DEFAULT_GATEWAY_PORT
    if not isinstance(value, str):
        raise LegacyStateRepairError("gateway_identity_invalid")
    match = re.fullmatch(r"nemoclaw-([1-9][0-9]{0,4})", value)
    if not match:
        raise LegacyStateRepairError("gateway_identity_invalid")
    port = int(match.group(1))
    if port > 65535 or port == DEFAULT_GATEWAY_PORT:
        raise LegacyStateRepairError("gateway_identity_invalid")
    return port


def _registry_row_port(row: object, sandbox_name: str) -> int:
    if not isinstance(row, dict) or row.get("name") != sandbox_name:
        raise LegacyStateRepairError("registry_row_invalid")

    has_port = row.get("gatewayPort") is not None
    has_name = row.get("gatewayName") is not None
    raw_port = row.get("gatewayPort")
    if has_port and (
        isinstance(raw_port, bool)
        or not isinstance(raw_port, int)
        or raw_port < 1
        or raw_port > 65535
    ):
        raise LegacyStateRepairError("gateway_identity_invalid")
    named_port = (
        _gateway_port_from_name(row.get("gatewayName")) if has_name else None
    )
    if has_port and named_port is not None and raw_port != named_port:
        raise LegacyStateRepairError("gateway_identity_invalid")
    if has_port:
        return int(raw_port)
    if named_port is not None:
        return named_port
    return DEFAULT_GATEWAY_PORT


def _session_has_recovery_state(session: Mapping[str, object]) -> bool:
    if session.get("stationExpressIntent") is not None:
        return True
    if session.get("stationExpressReceiptRetirement") is not None:
        return True

    machine = session.get("machine")
    if isinstance(machine, dict) and machine.get("recoveryReceipt") is not None:
        return True

    checkpoint = session.get("checkpoint")
    return isinstance(checkpoint, dict) and checkpoint.get("sandboxRecreate") is not None


def _state_has_station_receipt(state_root: Path) -> bool:
    try:
        entries = os.scandir(state_root)
    except OSError as exc:
        raise LegacyStateRepairError("state_inspection_failed") from exc
    with entries:
        for index, entry in enumerate(entries, start=1):
            if index > MAX_STATE_ENTRIES:
                raise LegacyStateRepairError("state_directory_too_large")
            if entry.name == "station-express-resume" or entry.name.startswith(
                "station-express-resume.retiring-"
            ):
                return True
    return False


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags | nofollow)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _acquire_migration_lock(state_root: Path, *, uid: int) -> tuple[Path, int]:
    lock_path = state_root / MIGRATION_LOCK_NAME
    try:
        os.mkdir(lock_path, 0o700)
    except FileExistsError as exc:
        raise LegacyStateRepairError("migration_in_progress") from exc
    except OSError as exc:
        raise LegacyStateRepairError("repair_lock_failed") from exc

    try:
        lock_info = _require_real_owned_directory(
            lock_path,
            uid=uid,
            owner_only=True,
        )
        owner_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        owner_fd = os.open(lock_path / "owner", owner_flags | nofollow, 0o600)
        try:
            os.write(owner_fd, str(os.getpid()).encode("ascii"))
            os.fsync(owner_fd)
        finally:
            os.close(owner_fd)
        _fsync_directory(lock_path)
        _fsync_directory(state_root)
        return lock_path, lock_info.st_ino
    except BaseException:
        try:
            (lock_path / "owner").unlink(missing_ok=True)
            lock_path.rmdir()
        except OSError:
            pass
        raise


def _release_migration_lock(
    state_root: Path,
    lock_path: Path,
    expected_inode: int,
    *,
    uid: int,
) -> None:
    info = _path_lstat(lock_path)
    if (
        info is None
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != uid
        or info.st_ino != expected_inode
    ):
        raise LegacyStateRepairError("repair_lock_changed")
    owner = lock_path / "owner"
    owner_info = _path_lstat(owner)
    if (
        owner_info is None
        or stat.S_ISLNK(owner_info.st_mode)
        or not stat.S_ISREG(owner_info.st_mode)
        or owner_info.st_uid != uid
        or owner_info.st_mode & 0o077
    ):
        raise LegacyStateRepairError("repair_lock_changed")
    owner.unlink()
    lock_path.rmdir()
    _fsync_directory(state_root)


def _ensure_quarantine_directory(state_root: Path, *, uid: int) -> Path:
    quarantine = state_root / QUARANTINE_DIR_NAME
    try:
        os.mkdir(quarantine, 0o700)
        _fsync_directory(state_root)
    except FileExistsError:
        pass
    except OSError as exc:
        raise LegacyStateRepairError("quarantine_create_failed") from exc
    _require_real_owned_directory(quarantine, uid=uid, owner_only=True)
    return quarantine


def _quarantine_session(
    session_path: Path,
    session_handle: IO[bytes],
    expected_info: os.stat_result,
    quarantine: Path,
    *,
    uid: int,
) -> None:
    current = _path_lstat(session_path)
    if (
        current is None
        or stat.S_ISLNK(current.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or current.st_uid != uid
        or current.st_mode & 0o077
        or (current.st_dev, current.st_ino)
        != (expected_info.st_dev, expected_info.st_ino)
    ):
        raise LegacyStateRepairError("state_changed_during_repair")

    destination = quarantine / (
        "onboard-session.demo.default-to-19080."
        f"{time.time_ns()}.{os.getpid()}.json"
    )
    if _path_lstat(destination) is not None:
        raise LegacyStateRepairError("quarantine_collision")
    try:
        os.rename(session_path, destination)
        os.chmod(destination, 0o600, follow_symlinks=False)
        moved = os.fstat(session_handle.fileno())
        published = destination.lstat()
        if (
            stat.S_ISLNK(published.st_mode)
            or not stat.S_ISREG(published.st_mode)
            or (moved.st_dev, moved.st_ino) != (published.st_dev, published.st_ino)
        ):
            raise LegacyStateRepairError("quarantine_publish_failed")
        _fsync_directory(quarantine)
        _fsync_directory(session_path.parent)
    except LegacyStateRepairError:
        raise
    except OSError as exc:
        raise LegacyStateRepairError("quarantine_publish_failed") from exc


def repair_legacy_state(
    *,
    home: Path,
    environ: Mapping[str, str],
) -> str:
    """Repair the exact default-session/skill-eval-row conflict, or do nothing."""
    if not _is_true(environ.get("SKILL_EVAL_NEMOCLAW_CI")):
        return "disabled"
    if not _is_true(environ.get("NEMOCLAW_RECREATE_SANDBOX")):
        return "disabled"
    if (environ.get("NEMOCLAW_SANDBOX_NAME") or "").strip() != SKILL_EVAL_SANDBOX:
        return "not_applicable"
    try:
        gateway_port = int((environ.get("NEMOCLAW_GATEWAY_PORT") or "").strip())
    except ValueError:
        return "not_applicable"
    if gateway_port != SKILL_EVAL_GATEWAY_PORT:
        return "not_applicable"

    uid = os.getuid()
    if not home.is_absolute():
        raise LegacyStateRepairError("home_path_unsafe")
    _require_real_owned_directory(home, uid=uid, owner_only=False)
    state_root = home / ".nemoclaw"
    state_info = _path_lstat(state_root)
    if state_info is None:
        return "no_state"
    _require_real_owned_directory(state_root, uid=uid, owner_only=True)

    session_path = state_root / "onboard-session.json"
    session_info = _path_lstat(session_path)
    if session_info is None:
        return "no_session"
    if _path_lstat(state_root / "onboard.lock") is not None:
        raise LegacyStateRepairError("onboard_in_progress")
    if _path_lstat(state_root / MIGRATION_INTENT_NAME) is not None:
        raise LegacyStateRepairError("migration_in_progress")

    lock_path, lock_inode = _acquire_migration_lock(state_root, uid=uid)
    pending_error: BaseException | None = None
    try:
        if _path_lstat(state_root / "onboard.lock") is not None:
            raise LegacyStateRepairError("onboard_in_progress")
        if _path_lstat(state_root / MIGRATION_INTENT_NAME) is not None:
            raise LegacyStateRepairError("migration_in_progress")

        try:
            session_handle, opened_session_info, raw_session = _open_owned_json(
                session_path,
                uid=uid,
            )
        except FileNotFoundError:
            return "no_session"
        with session_handle:
            if not isinstance(raw_session, dict):
                raise LegacyStateRepairError("session_invalid")
            if raw_session.get("sandboxName") != SKILL_EVAL_SANDBOX:
                return "not_applicable"

            metadata = raw_session.get("metadata")
            if not isinstance(metadata, dict):
                raise LegacyStateRepairError("session_invalid")
            session_gateway_port = _gateway_port_from_name(
                metadata.get("gatewayName")
            )
            if session_gateway_port != DEFAULT_GATEWAY_PORT:
                return "not_applicable"

            registry_path = state_root / "sandboxes.json"
            try:
                registry_handle, _, raw_registry = _open_owned_json(
                    registry_path,
                    uid=uid,
                )
            except FileNotFoundError:
                return "not_applicable"
            with registry_handle:
                if not isinstance(raw_registry, dict):
                    raise LegacyStateRepairError("registry_invalid")
                sandboxes = raw_registry.get("sandboxes")
                if not isinstance(sandboxes, dict):
                    raise LegacyStateRepairError("registry_invalid")
                row = sandboxes.get(SKILL_EVAL_SANDBOX)
                row_gateway_port = _registry_row_port(
                    row,
                    SKILL_EVAL_SANDBOX,
                )
                if row_gateway_port != SKILL_EVAL_GATEWAY_PORT:
                    return "not_applicable"

            if _session_has_recovery_state(raw_session):
                raise LegacyStateRepairError("protected_recovery_state_present")
            if _state_has_station_receipt(state_root):
                raise LegacyStateRepairError("protected_recovery_state_present")
            gateways_root = state_root / "gateways"
            selected_root = gateways_root / str(SKILL_EVAL_GATEWAY_PORT)
            if _path_lstat(gateways_root) is not None:
                _require_real_owned_directory(
                    gateways_root,
                    uid=uid,
                    owner_only=True,
                )
            if _path_lstat(selected_root) is not None:
                _require_real_owned_directory(
                    selected_root,
                    uid=uid,
                    owner_only=True,
                )
                if _state_has_station_receipt(selected_root):
                    raise LegacyStateRepairError(
                        "protected_recovery_state_present"
                    )
            if _path_lstat(selected_root / "onboard.lock") is not None:
                raise LegacyStateRepairError("onboard_in_progress")
            if _path_lstat(selected_root / "onboard-session.json") is not None:
                raise LegacyStateRepairError("selected_session_present")

            quarantine = _ensure_quarantine_directory(state_root, uid=uid)
            _quarantine_session(
                session_path,
                session_handle,
                opened_session_info,
                quarantine,
                uid=uid,
            )
            return "quarantined"
    except BaseException as exc:
        pending_error = exc
        raise
    finally:
        try:
            _release_migration_lock(
                state_root,
                lock_path,
                lock_inode,
                uid=uid,
            )
        except BaseException:
            if pending_error is None:
                raise


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        raise SystemExit("repair_legacy_state.py accepts no arguments")
    raw_home = os.environ.get("HOME", "")
    try:
        result = repair_legacy_state(
            home=Path(raw_home),
            environ=os.environ,
        )
    except LegacyStateRepairError as exc:
        print(
            f"NemoClaw legacy-state repair refused: {exc.code}",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "NemoClaw legacy-state repair refused: state_inspection_failed",
            file=sys.stderr,
        )
        return 1

    if result == "quarantined":
        print(
            "NemoClaw legacy-state repair quarantined the stale demo session "
            "for gateway 19080."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
