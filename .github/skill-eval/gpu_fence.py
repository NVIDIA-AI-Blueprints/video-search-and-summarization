# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""GPU-worker lease fencing daemon and command wrapper.

The coordinator owns the PostgreSQL lease.  This module runs on the selected
GPU worker, validates the exact lease capability, and supervises every Harbor
command under a generation-scoped process group.  If validation fails, the
database becomes unavailable past the conservative local deadline, or a newer
generation arrives, stale process groups and containers are terminated before
new work is admitted.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import pwd
import secrets
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

DEFAULT_SOCKET_PATH = Path("/run/vss-gpu-fence/control.sock")
DEFAULT_STATE_PATH = Path("/var/lib/vss-gpu-fence/high-water.json")
DEFAULT_POLL_SEC = 5
DEFAULT_SHUTDOWN_MARGIN_SEC = 30
DEFAULT_TERMINATION_GRACE_SEC = 5
MAX_REQUEST_BYTES = 64 * 1024

LOGGER = logging.getLogger("vss-gpu-fence")
VERSION = "1"

VALIDATE_SQL = """
SELECT valid, remaining_seconds
FROM public.validate_gpu_lease(%s::text, %s::uuid, %s::bigint)
"""


class FenceError(RuntimeError):
    """A worker fence operation could not be completed safely."""


class FenceRejectedError(FenceError):
    """The requested generation is stale, invalid, or no longer safe."""


@dataclasses.dataclass(frozen=True)
class LeaseValidation:
    valid: bool
    remaining_seconds: float


class PostgresLeaseValidator:
    """Validate one capability without exposing lease-table read access."""

    def __init__(
        self,
        database_url: str,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        if not database_url:
            raise ValueError("GPU_FENCE_DATABASE_URL must not be empty")
        self.database_url = database_url
        self._connect = connect or self._default_connect

    @staticmethod
    def _default_connect(database_url: str, **kwargs: Any):
        try:
            import psycopg
        except ImportError as exc:
            raise FenceError(
                "GPU fencing requires psycopg 3; install 'psycopg[binary]>=3.2,<4'"
            ) from exc
        return psycopg.connect(database_url, **kwargs)

    def validate(
        self,
        gpu_id: str,
        token: str,
        generation: int,
    ) -> LeaseValidation:
        try:
            parsed_token = uuid.UUID(token)
            with (
                self._connect(
                    self.database_url,
                    autocommit=True,
                    connect_timeout=5,
                    keepalives=1,
                    keepalives_idle=5,
                    keepalives_interval=5,
                    keepalives_count=2,
                    tcp_user_timeout=10000,
                    options="-c statement_timeout=5000 -c lock_timeout=5000",
                ) as conn,
                conn.cursor() as cursor,
            ):
                cursor.execute(VALIDATE_SQL, (gpu_id, parsed_token, generation))
                row = cursor.fetchone()
        except FenceError:
            raise
        except Exception as exc:
            raise FenceError(f"PostgreSQL lease validation failed: {exc}") from exc
        if row is None:
            raise FenceError("PostgreSQL lease validation returned no row")
        return LeaseValidation(bool(row[0]), float(row[1]))


@dataclasses.dataclass
class ActiveSession:
    token: str
    generation: int
    session_id: str
    deadline_monotonic: float
    process_groups: set[int] = dataclasses.field(default_factory=set)


class WorkerCleanup:
    """Terminate registered process groups and dedicated-worker containers."""

    def __init__(
        self,
        termination_grace_sec: int = DEFAULT_TERMINATION_GRACE_SEC,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.termination_grace_sec = termination_grace_sec
        self._run = run
        self._sleep = sleep

    @staticmethod
    def _signal_groups(process_groups: Sequence[int], sig: signal.Signals) -> None:
        for pgid in process_groups:
            if pgid <= 1:
                continue
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError):
                continue

    @staticmethod
    def _discover_fenced_process_groups() -> set[int]:
        groups: set[int] = set()
        for environ_path in Path("/proc").glob("[0-9]*/environ"):
            try:
                if b"VSS_GPU_FENCE_SESSION_ID=" not in environ_path.read_bytes():
                    continue
                pid = int(environ_path.parent.name)
                process_group = os.getpgid(pid)
                if process_group > 1:
                    groups.add(process_group)
            except (OSError, ProcessLookupError, ValueError):
                continue
        return groups

    @staticmethod
    def _group_exists(process_group: int) -> bool:
        # Linux keeps an exited-but-unreaped leader as a zombie. Zombies
        # cannot execute or mutate the worker and must not make cleanup look
        # failed while the SSH parent is still reaping them.
        proc_root = Path("/proc")
        if proc_root.is_dir():
            try:
                for stat_path in proc_root.glob("[0-9]*/stat"):
                    raw = stat_path.read_text()
                    fields = raw[raw.rfind(")") + 2 :].split()
                    if (
                        len(fields) > 2
                        and int(fields[2]) == process_group
                        and fields[0] != "Z"
                    ):
                        return True
                return False
            except (OSError, ValueError):
                pass
        try:
            os.killpg(process_group, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def __call__(self, process_groups: Sequence[int], reason: str) -> None:
        groups = sorted(set(process_groups) | self._discover_fenced_process_groups())
        LOGGER.warning(
            "fencing active GPU session: reason=%s process_groups=%s",
            reason,
            groups,
        )
        self._signal_groups(groups, signal.SIGTERM)
        deadline = time.monotonic() + self.termination_grace_sec
        alive = [pgid for pgid in groups if self._group_exists(pgid)]
        while alive and time.monotonic() < deadline:
            self._sleep(min(0.1, max(0, deadline - time.monotonic())))
            alive = [pgid for pgid in alive if self._group_exists(pgid)]
        if alive:
            self._signal_groups(alive, signal.SIGKILL)
            kill_deadline = time.monotonic() + 2
            while alive and time.monotonic() < kill_deadline:
                self._sleep(0.05)
                alive = [pgid for pgid in alive if self._group_exists(pgid)]
        if alive:
            raise FenceError(f"process groups survived fencing cleanup: {alive}")

        # Catch unregistered processes left before daemon startup or processes
        # that escaped their original command group.  The bracketed pattern
        # keeps pkill's own argv from matching.
        reaped = self._run(
            ["pkill", "-9", "-f", "claude --verbose --output-format=stream-jso[n]"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if reaped.returncode not in (0, 1):
            raise FenceError(f"stray-agent cleanup failed: rc={reaped.returncode}")

        # Pool workers are dedicated to one skill-eval lease.  Docker moves
        # detached containers into docker.service's cgroup, so process-group
        # termination alone cannot fence them.
        listed = self._run(
            ["docker", "ps", "-aq"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        container_ids = (listed.stdout or "").split()
        if listed.returncode != 0:
            raise FenceError(
                f"cannot enumerate worker containers: rc={listed.returncode}"
            )
        if container_ids:
            removed = self._run(
                ["docker", "rm", "-f", *container_ids],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            if removed.returncode != 0:
                raise FenceError(f"container cleanup failed: rc={removed.returncode}")
        verified = self._run(
            ["docker", "ps", "-aq"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if verified.returncode != 0 or (verified.stdout or "").split():
            raise FenceError("worker containers survived fencing cleanup")
        uploads = Path("/tmp/vss-gpu-fence/uploads")
        if uploads.exists():
            import shutil

            shutil.rmtree(uploads)


class FenceController:
    """Thread-safe generation state machine used by the worker daemon."""

    def __init__(
        self,
        gpu_id: str,
        validator: PostgresLeaseValidator,
        state_path: Path = DEFAULT_STATE_PATH,
        cleanup: Callable[[Sequence[int], str], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        shutdown_margin_sec: int = DEFAULT_SHUTDOWN_MARGIN_SEC,
    ) -> None:
        if not gpu_id:
            raise ValueError("GPU_FENCE_GPU_ID must not be empty")
        if shutdown_margin_sec < 10:
            raise ValueError("shutdown_margin_sec must be at least 10 seconds")
        self.gpu_id = gpu_id
        self.validator = validator
        self.state_path = state_path
        self.cleanup = cleanup or WorkerCleanup()
        self.monotonic = monotonic
        self.shutdown_margin_sec = shutdown_margin_sec
        self._lock = threading.RLock()
        self._validation_lock = threading.Lock()
        self._active: ActiveSession | None = None
        self._blocked_reason = ""
        self._boot_id = self._read_boot_id()
        self._state_load_error = ""
        try:
            self._high_water, self._pending_groups = self._load_state()
        except FenceError as exc:
            # Construction must not prevent startup cleanup. Keep admission
            # permanently blocked until an operator repairs/removes the
            # corrupt state after inspecting the worker.
            self._high_water = (1 << 63) - 1
            self._pending_groups = set()
            self._state_load_error = str(exc)
            self._blocked_reason = self._state_load_error

    @staticmethod
    def _read_boot_id() -> str:
        try:
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            return "unknown"

    def _load_state(self) -> tuple[int, set[int]]:
        try:
            payload = json.loads(self.state_path.read_text())
            generation = int(payload["generation"])
            if generation < 0:
                raise ValueError("negative generation")
            process_groups = {int(group) for group in payload.get("process_groups", [])}
            if any(group <= 1 for group in process_groups):
                raise ValueError("invalid process group")
            if payload.get("boot_id") != self._boot_id:
                process_groups.clear()
            return generation, process_groups
        except FileNotFoundError:
            return 0, set()
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise FenceError(
                f"invalid high-water state at {self.state_path}: {exc}"
            ) from exc

    def _save_state_locked(self) -> None:
        process_groups = set(self._pending_groups)
        if self._active is not None:
            process_groups.update(self._active.process_groups)
        payload = {
            "boot_id": self._boot_id,
            "generation": self._high_water,
            "process_groups": sorted(process_groups),
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, sort_keys=True) + "\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.state_path)
        except OSError as exc:
            raise FenceError(f"cannot persist GPU fence state: {exc}") from exc

    def _safe_deadline(
        self,
        validation: LeaseValidation,
        operation_started: float,
    ) -> float:
        usable = validation.remaining_seconds - self.shutdown_margin_sec
        deadline = operation_started + usable
        if not validation.valid or usable <= 0 or deadline <= self.monotonic():
            raise FenceRejectedError(
                "lease is invalid or too close to expiry for safe GPU execution"
            )
        return deadline

    @staticmethod
    def _canonical_token(token: str) -> str:
        try:
            return str(uuid.UUID(token))
        except (ValueError, AttributeError) as exc:
            raise FenceRejectedError("lease token is not a valid UUID") from exc

    def startup_cleanup(self) -> None:
        """Fail closed after daemon restart because prior groups are unknown."""
        with self._validation_lock, self._lock:
            if self._state_load_error:
                self.cleanup(
                    [],
                    f"invalid persisted fence state: {self._state_load_error}",
                )
                raise FenceError(
                    "persisted GPU fence state is invalid; stale work was "
                    "cleaned but operator repair is required"
                )
            self._blocked_reason = "GPU fence daemon startup cleanup"
            self._cleanup_pending_locked(self._blocked_reason)

    def claim(self, token: str, generation: int) -> ActiveSession:
        token = self._canonical_token(token)
        if generation < 1:
            raise FenceRejectedError("generation must be positive")
        with self._validation_lock:
            operation_started = self.monotonic()
            validation = self.validator.validate(self.gpu_id, token, generation)
            deadline = self._safe_deadline(validation, operation_started)

            with self._lock:
                if self._blocked_reason:
                    self._cleanup_pending_locked(
                        f"retry after blocked cleanup: {self._blocked_reason}"
                    )
                if generation < self._high_water:
                    raise FenceRejectedError(
                        f"generation {generation} is below local high-water "
                        f"{self._high_water}"
                    )
                if self._active is not None:
                    if generation == self._active.generation and secrets.compare_digest(
                        token, self._active.token
                    ):
                        self._active.deadline_monotonic = deadline
                        return dataclasses.replace(
                            self._active,
                            process_groups=set(self._active.process_groups),
                        )
                    if generation <= self._active.generation:
                        raise FenceRejectedError(
                            "generation does not supersede the active GPU session"
                        )
                    self._terminate_locked(
                        f"superseded by PostgreSQL generation {generation}"
                    )

                self._high_water = max(self._high_water, generation)
                self._active = ActiveSession(
                    token=token,
                    generation=generation,
                    session_id=secrets.token_urlsafe(24),
                    deadline_monotonic=deadline,
                )
                try:
                    self._save_state_locked()
                except FenceError:
                    try:
                        self._terminate_locked(
                            "failed to persist newly admitted GPU session"
                        )
                    except FenceError:
                        pass
                    raise
                return dataclasses.replace(self._active, process_groups=set())

    def register(self, session_id: str, process_group: int) -> None:
        if process_group <= 1:
            raise FenceRejectedError("invalid process group")
        try:
            actual_group = os.getpgid(process_group)
        except ProcessLookupError as exc:
            raise FenceRejectedError(
                "fenced command exited before registration"
            ) from exc
        if actual_group != process_group:
            raise FenceRejectedError(
                f"process {process_group} does not lead its process group"
            )
        with self._lock:
            if self._blocked_reason:
                raise FenceRejectedError(
                    f"GPU worker is blocked: {self._blocked_reason}"
                )
            if self._active is None or not secrets.compare_digest(
                session_id, self._active.session_id
            ):
                raise FenceRejectedError("GPU fence session is not active")
            if self.monotonic() >= self._active.deadline_monotonic:
                self._terminate_locked("local worker safety deadline reached")
                raise FenceRejectedError("GPU fence session safety deadline expired")
            self._active.process_groups.add(process_group)
            try:
                self._save_state_locked()
            except FenceError:
                try:
                    self._terminate_locked("failed to persist registered process group")
                except FenceError:
                    pass
                raise

    @staticmethod
    def _group_exists(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def _prune_process_groups_locked(self) -> None:
        if self._active is None:
            return
        previous = self._active.process_groups
        self._active.process_groups = {
            pgid for pgid in self._active.process_groups if self._group_exists(pgid)
        }
        if self._active.process_groups != previous:
            self._save_state_locked()

    def _cleanup_pending_locked(self, reason: str) -> None:
        try:
            self.cleanup(sorted(self._pending_groups), reason)
        except Exception as exc:
            self._blocked_reason = f"{reason}: {exc}"
            raise FenceError(
                f"GPU cleanup failed; worker remains blocked: {exc}"
            ) from exc
        self._pending_groups.clear()
        try:
            self._save_state_locked()
        except FenceError as exc:
            self._blocked_reason = f"{reason}: state persistence failed: {exc}"
            raise FenceError(
                "GPU work was cleaned, but admission remains blocked because "
                f"state persistence failed: {exc}"
            ) from exc
        self._blocked_reason = ""

    def _terminate_locked(self, reason: str) -> None:
        if self._active is not None:
            self._pending_groups.update(self._active.process_groups)
        self._active = None
        self._blocked_reason = reason
        try:
            self._save_state_locked()
        except FenceError:
            # Persistence is restart defense. It must never prevent the
            # immediate process/container cleanup that enforces expiry now.
            LOGGER.exception("could not persist pending cleanup; cleaning immediately")
        self._cleanup_pending_locked(reason)

    def enforce_deadline(self) -> bool:
        """Independent watchdog path; never waits for PostgreSQL I/O."""
        with self._lock:
            if self._active is None:
                return not self._blocked_reason
            if self.monotonic() < self._active.deadline_monotonic:
                return True
            try:
                self._terminate_locked("local worker safety deadline reached")
            except FenceError:
                LOGGER.exception("deadline cleanup failed; worker remains blocked")
            return False

    def poll_once(self) -> bool:
        """Validate the active lease; return False after fencing it."""
        with self._validation_lock:
            with self._lock:
                if self._active is None:
                    return not self._blocked_reason
                self._prune_process_groups_locked()
                token = self._active.token
                generation = self._active.generation
                session_id = self._active.session_id
                deadline = self._active.deadline_monotonic

            operation_started = self.monotonic()
            try:
                validation = self.validator.validate(self.gpu_id, token, generation)
                refreshed_deadline = self._safe_deadline(validation, operation_started)
            except FenceRejectedError as exc:
                with self._lock:
                    if (
                        self._active is not None
                        and self._active.session_id == session_id
                    ):
                        try:
                            self._terminate_locked(str(exc))
                        except FenceError:
                            LOGGER.exception(
                                "invalid-lease cleanup failed; worker blocked"
                            )
                return False
            except FenceError as exc:
                if self.monotonic() < deadline:
                    LOGGER.warning(
                        "lease validation unavailable; retaining session only until "
                        "local safety deadline: %s",
                        exc,
                    )
                    return True
                self.enforce_deadline()
                return False

            with self._lock:
                if self._active is not None and self._active.session_id == session_id:
                    self._active.deadline_monotonic = refreshed_deadline
            return True

    def shutdown(self) -> None:
        with self._lock:
            if self._active is not None:
                self._terminate_locked("GPU fence daemon shutdown")

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._prune_process_groups_locked()
            if self._active is None:
                return {
                    "version": VERSION,
                    "gpu_id": self.gpu_id,
                    "active": False,
                    "blocked": bool(self._blocked_reason),
                    "blocked_reason": self._blocked_reason,
                    "high_water_generation": self._high_water,
                }
            return {
                "version": VERSION,
                "gpu_id": self.gpu_id,
                "active": True,
                "generation": self._active.generation,
                "blocked": bool(self._blocked_reason),
                "deadline_in_seconds": max(
                    0, self._active.deadline_monotonic - self.monotonic()
                ),
                "process_group_count": len(self._active.process_groups),
                "high_water_generation": self._high_water,
            }


class FenceRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            response = {"ok": False, "error": "request too large"}
        else:
            try:
                request = json.loads(raw)
                response = self.server.dispatch(request)  # type: ignore[attr-defined]
            except (FenceError, KeyError, TypeError, ValueError) as exc:
                response = {"ok": False, "error": str(exc)}
            except Exception as exc:
                LOGGER.exception("unexpected fence request failure")
                response = {"ok": False, "error": f"internal fence error: {exc}"}
        self.wfile.write((json.dumps(response) + "\n").encode())


class FenceServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(
        self,
        socket_path: Path,
        controller: FenceController,
    ) -> None:
        self.socket_path = socket_path
        self.controller = controller
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        super().__init__(str(socket_path), FenceRequestHandler)
        os.chmod(socket_path, 0o600)

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request["operation"]
        if operation == "claim":
            if request.get("gpu_id") != self.controller.gpu_id:
                raise FenceRejectedError("claim targets a different GPU worker")
            session = self.controller.claim(
                str(request["token"]), int(request["generation"])
            )
            return {
                "ok": True,
                "session_id": session.session_id,
                "generation": session.generation,
            }
        if operation == "register":
            self.controller.register(
                str(request["session_id"]), int(request["process_group"])
            )
            return {"ok": True}
        if operation == "status":
            return {"ok": True, **self.controller.status()}
        raise FenceRejectedError(f"unsupported operation: {operation!r}")

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self.socket_path.unlink(missing_ok=True)


def request(socket_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(str(socket_path))
            client.sendall((json.dumps(payload) + "\n").encode())
            response = b""
            while not response.endswith(b"\n"):
                chunk = client.recv(4096)
                if not chunk:
                    break
                response += chunk
                if len(response) > MAX_REQUEST_BYTES:
                    raise FenceError("worker fence response too large")
    except OSError as exc:
        raise FenceError(f"cannot reach GPU fence daemon: {exc}") from exc
    try:
        decoded = json.loads(response)
    except json.JSONDecodeError as exc:
        raise FenceError("GPU fence daemon returned invalid JSON") from exc
    if not decoded.get("ok"):
        raise FenceRejectedError(decoded.get("error") or "GPU fence request rejected")
    return decoded


def run_daemon(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    validator = PostgresLeaseValidator(args.database_url)
    controller = FenceController(
        args.gpu_id,
        validator,
        state_path=args.state_path,
        shutdown_margin_sec=args.shutdown_margin_sec,
    )
    controller.startup_cleanup()
    probe = validator.validate(args.gpu_id, str(uuid.uuid4()), 0)
    if probe.valid:
        raise FenceError("database validation probe unexpectedly accepted")
    server = FenceServer(args.socket_path, controller)
    stop = threading.Event()

    def handle_signal(signum, _frame) -> None:
        LOGGER.info("received signal %s", signum)
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    server_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.5},
        name="gpu-fence-server",
        daemon=True,
    )
    server_thread.start()

    def poll_loop() -> None:
        while not stop.wait(args.poll_sec):
            try:
                controller.poll_once()
            except Exception:
                LOGGER.exception("lease polling failed")

    def deadline_loop() -> None:
        while not stop.wait(1):
            controller.enforce_deadline()

    poll_thread = threading.Thread(
        target=poll_loop,
        name="gpu-fence-postgres-poll",
        daemon=True,
    )
    deadline_thread = threading.Thread(
        target=deadline_loop,
        name="gpu-fence-deadline",
        daemon=True,
    )
    poll_thread.start()
    deadline_thread.start()
    try:
        stop.wait()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=10)
        poll_thread.join(timeout=1)
        deadline_thread.join(timeout=1)
        try:
            controller.shutdown()
        except FenceError:
            LOGGER.exception("shutdown cleanup failed; worker remains blocked")
            return 1
    return 0


def run_claim(args: argparse.Namespace) -> int:
    token_path = args.token_file
    expected_uid = int(os.environ.get("SUDO_UID", str(os.getuid())))
    metadata = token_path.lstat()
    if (
        token_path.parent != Path("/tmp")
        or not token_path.name.startswith(".vss-gpu-fence-claim-")
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not 1 <= metadata.st_size <= 128
    ):
        raise FenceError("claim token file has unsafe path, owner, or mode")
    descriptor = os.open(token_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        token = os.read(descriptor, 128).decode().strip()
    finally:
        os.close(descriptor)
        token_path.unlink(missing_ok=True)
    response = request(
        args.socket_path,
        {
            "operation": "claim",
            "gpu_id": args.gpu_id,
            "token": token,
            "generation": args.generation,
        },
    )
    print(f"VSS_GPU_FENCE_SESSION={response['session_id']}")
    return 0


def _drop_sudo_privileges() -> None:
    uid_text = os.environ.get("SUDO_UID")
    gid_text = os.environ.get("SUDO_GID")
    if not uid_text or not gid_text:
        return
    uid = int(uid_text)
    gid = int(gid_text)
    account = pwd.getpwuid(uid)
    os.initgroups(account.pw_name, gid)
    os.setgid(gid)
    os.setuid(uid)
    os.environ.update(
        HOME=account.pw_dir,
        USER=account.pw_name,
        LOGNAME=account.pw_name,
    )


def run_exec(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise FenceError("fenced exec requires a command after --")
    try:
        os.setpgid(0, 0)
    except PermissionError:
        # Already a session/process-group leader.
        pass
    process_group = os.getpgrp()
    if process_group != os.getpid():
        raise FenceError("could not establish a dedicated fenced process group")
    request(
        args.socket_path,
        {
            "operation": "register",
            "session_id": args.session_id,
            "process_group": process_group,
        },
    )
    os.environ["VSS_GPU_FENCE_SESSION_ID"] = args.session_id
    _drop_sudo_privileges()
    os.execvpe(command[0], command, os.environ.copy())
    return 127


def run_status(args: argparse.Namespace) -> int:
    print(
        json.dumps(request(args.socket_path, {"operation": "status"}), sort_keys=True)
    )
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    daemon = subparsers.add_parser("daemon")
    daemon.add_argument("--gpu-id", default=os.environ.get("GPU_FENCE_GPU_ID", ""))
    daemon.add_argument(
        "--database-url", default=os.environ.get("GPU_FENCE_DATABASE_URL", "")
    )
    daemon.add_argument("--socket-path", type=Path, default=DEFAULT_SOCKET_PATH)
    daemon.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    daemon.add_argument("--poll-sec", type=int, default=DEFAULT_POLL_SEC)
    daemon.add_argument(
        "--shutdown-margin-sec",
        type=int,
        default=DEFAULT_SHUTDOWN_MARGIN_SEC,
    )
    daemon.set_defaults(handler=run_daemon)

    claim = subparsers.add_parser("claim")
    claim.add_argument("--gpu-id", required=True)
    claim.add_argument("--token-file", required=True, type=Path)
    claim.add_argument("--generation", required=True, type=int)
    claim.add_argument("--socket-path", type=Path, default=DEFAULT_SOCKET_PATH)
    claim.set_defaults(handler=run_claim)

    execute = subparsers.add_parser("exec")
    execute.add_argument("--session-id", required=True)
    execute.add_argument("--socket-path", type=Path, default=DEFAULT_SOCKET_PATH)
    execute.add_argument("command", nargs=argparse.REMAINDER)
    execute.set_defaults(handler=run_exec)

    status = subparsers.add_parser("status")
    status.add_argument("--socket-path", type=Path, default=DEFAULT_SOCKET_PATH)
    status.set_defaults(handler=run_status)
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv or sys.argv[1:])
        return int(args.handler(args))
    except FenceError as exc:
        print(f"FENCED: {exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
