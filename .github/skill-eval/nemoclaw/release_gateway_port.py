#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Release one stale, identity-verified NemoClaw host gateway listener.

This is a recovery path for eval workers whose previous NemoClaw package is
too incomplete to import its normal ``releaseManagedGatewayPort`` helper.  It
deliberately mirrors that helper's destructive boundary: only PIDs discovered
as listeners on the requested port are considered, and every listener must
have an exact managed-gateway process identity before any signal is sent.
PID files, process-name sweeps, and privileged signals are never used.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Callable, NamedTuple, Sequence

DIRECT_GATEWAY_NAMES = frozenset({"openshell-gateway", "openclaw-gateway"})
COMPAT_RUNTIME_NAMES = frozenset({"docker"})
COMPAT_GATEWAY_TOKEN = "/opt/nemoclaw/openshell-gateway"
SYSTEM_LSOF_CANDIDATES = (
    Path("/usr/bin/lsof"),
    Path("/usr/sbin/lsof"),
    Path("/bin/lsof"),
    Path("/sbin/lsof"),
)


class GatewayReleaseError(RuntimeError):
    """The scoped gateway release could not be proven safe and complete."""


class ProcessIdentity(NamedTuple):
    pid: int
    start_time: int
    argv: tuple[str, ...]
    executable: str


def _resolve_system_lsof() -> Path:
    for candidate in SYSTEM_LSOF_CANDIDATES:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise GatewayReleaseError(
        "lsof is unavailable in the trusted system paths; refusing to scan listeners"
    )


def _listening_pids(port: int, lsof_path: Path) -> tuple[int, ...]:
    try:
        result = subprocess.run(
            [
                str(lsof_path),
                "-nP",
                "-t",
                f"-iTCP:{port}",
                "-sTCP:LISTEN",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GatewayReleaseError(
            f"lsof failed while scanning port {port}: {exc}"
        ) from exc
    if result.returncode not in (0, 1):
        detail = (result.stderr or "").strip() or f"status {result.returncode}"
        raise GatewayReleaseError(f"lsof failed while scanning port {port}: {detail}")

    pids: set[int] = set()
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line.isascii() or not line.isdecimal():
            raise GatewayReleaseError(
                f"lsof returned an invalid listener PID for port {port}: {line!r}"
            )
        pid = int(line)
        if pid <= 0:
            raise GatewayReleaseError(
                f"lsof returned an invalid listener PID for port {port}: {pid}"
            )
        pids.add(pid)
    if result.returncode == 0 and not pids:
        raise GatewayReleaseError(
            f"lsof reported listeners on port {port} without any valid PIDs"
        )
    return tuple(sorted(pids))


def _read_process_identity(
    pid: int, proc_root: Path = Path("/proc")
) -> ProcessIdentity:
    try:
        raw_argv = (proc_root / str(pid) / "cmdline").read_bytes()
        stat = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
        executable = os.readlink(proc_root / str(pid) / "exe")
    except OSError as exc:
        raise GatewayReleaseError(
            f"listener PID {pid} disappeared while its identity was inspected"
        ) from exc

    argv_bytes = raw_argv.rstrip(b"\0").split(b"\0") if raw_argv else []
    if not argv_bytes:
        raise GatewayReleaseError(f"listener PID {pid} has an empty command line")
    try:
        argv = tuple(value.decode("utf-8", errors="strict") for value in argv_bytes)
    except UnicodeDecodeError as exc:
        raise GatewayReleaseError(
            f"listener PID {pid} has a non-UTF-8 command line"
        ) from exc

    closing_paren = stat.rfind(")")
    fields_after_comm = stat[closing_paren + 1 :].split() if closing_paren >= 0 else []
    # /proc/<pid>/stat field 22 is process starttime.  fields_after_comm[0]
    # is field 3 (state), so starttime is index 19.
    if len(fields_after_comm) <= 19 or not fields_after_comm[19].isdecimal():
        raise GatewayReleaseError(
            f"listener PID {pid} has an invalid /proc stat record"
        )
    if fields_after_comm[0] == "Z":
        raise GatewayReleaseError(f"listener PID {pid} is already a zombie")
    return ProcessIdentity(
        pid=pid,
        start_time=int(fields_after_comm[19]),
        argv=argv,
        executable=executable,
    )


def _clean_process_token(value: str) -> str:
    return value.strip("\"'").removesuffix(" (deleted)")


def _is_managed_gateway(identity: ProcessIdentity) -> bool:
    argv = tuple(_clean_process_token(value) for value in identity.argv)
    argv0_name = Path(argv[0]).name
    executable_name = Path(_clean_process_token(identity.executable)).name
    if argv0_name == executable_name and argv0_name in DIRECT_GATEWAY_NAMES:
        return True
    return (
        argv0_name == executable_name
        and argv0_name in COMPAT_RUNTIME_NAMES
        and COMPAT_GATEWAY_TOKEN in argv[1:]
    )


def _validate_listener_batch(
    pids: Sequence[int],
    *,
    proc_root: Path = Path("/proc"),
) -> dict[int, ProcessIdentity]:
    identities: dict[int, ProcessIdentity] = {}
    for pid in pids:
        identity = _read_process_identity(pid, proc_root)
        if not _is_managed_gateway(identity):
            argv0 = _clean_process_token(identity.argv[0])
            raise GatewayReleaseError(
                f"refusing to signal non-gateway listener PID {pid} ({argv0})"
            )
        identities[pid] = identity
    return identities


def _revalidate_identity(
    expected: ProcessIdentity,
    *,
    proc_root: Path = Path("/proc"),
) -> bool:
    try:
        current = _read_process_identity(expected.pid, proc_root)
    except GatewayReleaseError:
        return False
    if current.start_time != expected.start_time:
        raise GatewayReleaseError(
            f"listener PID {expected.pid} was reused before it could be signaled"
        )
    if (
        current.argv != expected.argv
        or current.executable != expected.executable
        or not _is_managed_gateway(current)
    ):
        raise GatewayReleaseError(
            f"listener PID {expected.pid} changed identity before it could be signaled"
        )
    return True


def _open_pidfds(
    identities: dict[int, ProcessIdentity],
    *,
    proc_root: Path = Path("/proc"),
) -> dict[int, int]:
    if not callable(getattr(os, "pidfd_open", None)) or not callable(
        getattr(signal, "pidfd_send_signal", None)
    ):
        raise GatewayReleaseError(
            "this Python/Linux runtime lacks pidfd signaling; "
            "refusing PID-based signals"
        )

    pidfds: dict[int, int] = {}
    try:
        for identity in identities.values():
            try:
                pidfds[identity.pid] = os.pidfd_open(identity.pid, 0)
            except OSError as exc:
                raise GatewayReleaseError(
                    f"could not open a pidfd for listener PID {identity.pid}: {exc}"
                ) from exc
        # A pidfd pins the process object. Revalidation proves each descriptor
        # was opened before the originally observed PID exited or was reused.
        for identity in identities.values():
            if not _revalidate_identity(identity, proc_root=proc_root):
                raise GatewayReleaseError(
                    f"listener PID {identity.pid} exited while its pidfd was opened"
                )
    except BaseException:
        for pidfd in pidfds.values():
            os.close(pidfd)
        raise
    return pidfds


def _pidfd_signal(pidfd: int, sig: int, pid: int) -> bool:
    try:
        signal.pidfd_send_signal(pidfd, sig, None, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise GatewayReleaseError(
            f"could not send signal {sig} through pidfd for gateway PID {pid}: {exc}"
        ) from exc
    return True


def _port_is_free(port: int) -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def release_gateway_port(
    port: int,
    *,
    lsof_path: Path | None = None,
    proc_root: Path = Path("/proc"),
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, ...]:
    """Release ``port`` only when every exact listener is a managed gateway."""
    if port < 1024 or port > 65535:
        raise GatewayReleaseError(f"gateway port is outside 1024-65535: {port}")
    trusted_lsof = lsof_path or _resolve_system_lsof()

    initial_pids = _listening_pids(port, trusted_lsof)
    if not initial_pids:
        if _port_is_free(port):
            return ()
        raise GatewayReleaseError(
            f"port {port} is busy but lsof found no listener; refusing to guess"
        )

    # Validate the entire set before signaling the first PID.  Then rescan and
    # revalidate start times to close both listener-set and PID-reuse races.
    identities = _validate_listener_batch(initial_pids, proc_root=proc_root)
    rescanned = _listening_pids(port, trusted_lsof)
    if rescanned != initial_pids:
        raise GatewayReleaseError(
            f"listener set on port {port} changed during validation"
        )
    for identity in identities.values():
        if not _revalidate_identity(identity, proc_root=proc_root):
            raise GatewayReleaseError(
                f"listener PID {identity.pid} exited during validation; retry safely"
            )

    pidfds = _open_pidfds(identities, proc_root=proc_root)
    try:
        survivors = dict(identities)
        # Revalidate the entire batch after every pidfd is open and before the
        # first signal. Each individual process is then checked once more at
        # its exact signal boundary.
        for identity in survivors.values():
            if not _revalidate_identity(identity, proc_root=proc_root):
                raise GatewayReleaseError(
                    f"listener PID {identity.pid} exited before SIGTERM; retry safely"
                )
        for identity in tuple(survivors.values()):
            if not _revalidate_identity(identity, proc_root=proc_root):
                raise GatewayReleaseError(
                    f"listener PID {identity.pid} changed before SIGTERM"
                )
            if not _pidfd_signal(
                pidfds[identity.pid],
                signal.SIGTERM,
                identity.pid,
            ):
                survivors.pop(identity.pid)

        deadline = time.monotonic() + 1.0
        while survivors and time.monotonic() < deadline:
            survivors = {
                pid: identity
                for pid, identity in survivors.items()
                if _revalidate_identity(identity, proc_root=proc_root)
            }
            if survivors:
                sleep(0.05)

        if survivors:
            # Before escalation, prove that no new or unexpected listener
            # appeared, then revalidate every original process through /proc.
            # Held pidfds make the subsequent signals immune to PID reuse.
            current_listeners = _listening_pids(port, trusted_lsof)
            if any(pid not in survivors for pid in current_listeners):
                raise GatewayReleaseError(
                    f"listener set on port {port} changed before SIGKILL"
                )
            for identity in survivors.values():
                if not _revalidate_identity(identity, proc_root=proc_root):
                    raise GatewayReleaseError(
                        f"listener PID {identity.pid} exited before SIGKILL; "
                        "retry safely"
                    )
            for identity in tuple(survivors.values()):
                if not _revalidate_identity(identity, proc_root=proc_root):
                    raise GatewayReleaseError(
                        f"listener PID {identity.pid} changed before SIGKILL"
                    )
                if not _pidfd_signal(
                    pidfds[identity.pid],
                    signal.SIGKILL,
                    identity.pid,
                ):
                    survivors.pop(identity.pid)

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            remaining = _listening_pids(port, trusted_lsof)
            if not remaining and _port_is_free(port):
                return initial_pids
            sleep(0.05)
        remaining = _listening_pids(port, trusted_lsof)
        raise GatewayReleaseError(
            f"gateway port {port} is still busy after scoped release; "
            f"remaining listener PIDs: {list(remaining)}"
        )
    finally:
        for pidfd in pidfds.values():
            os.close(pidfd)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        stopped = release_gateway_port(args.port)
    except GatewayReleaseError as exc:
        parser.exit(1, f"Cannot safely release the stale OpenShell gateway: {exc}\n")
    if stopped:
        print(
            f"Released NemoClaw gateway port {args.port} "
            f"(stopped host process {', '.join(map(str, stopped))})."
        )
    else:
        print(f"NemoClaw gateway port {args.port} was already free.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
