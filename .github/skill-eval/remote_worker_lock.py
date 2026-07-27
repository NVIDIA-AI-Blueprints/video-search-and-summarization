# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-runner lease for a shared skills-eval worker.

The lock itself lives on the worker so coordinators on different self-hosted
runner machines observe the same reservation.  Transport is deliberately
injected: callers can use ``brev exec`` for managed workers or direct SSH for
registered external nodes without duplicating the lock protocol.

Keep ``REMOTE_LOCK_DIR`` stable.  Older NemoClaw jobs already coordinate
through that path and parse the compatibility messages emitted by the shell
commands below.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple, Protocol

REMOTE_LOCK_DIR = "/tmp/skill-eval/locks/nemoclaw-worker.lockdir"
REMOTE_LOCK_GUARD = "/tmp/skill-eval/locks/worker-lock.guard"
_MARKER_PREFIX = "SKILL_EVAL_REMOTE_LOCK_"


class CommandResultLike(Protocol):
    """Minimal result contract returned by a remote command executor."""

    returncode: int
    stdout: str | None
    stderr: str | None


RemoteExecutor = Callable[[str, int], CommandResultLike]
OwnerInactiveChecker = Callable[[str], bool]


class RemoteLockHeartbeat(NamedTuple):
    stop_event: threading.Event
    lost_event: threading.Event
    thread: threading.Thread


@dataclass
class RemoteWorkerLease:
    """One exact-owner remote lock plus its coordinator-side heartbeat."""

    label: str
    owner: str
    run_remote: RemoteExecutor = field(repr=False)
    heartbeat: RemoteLockHeartbeat = field(repr=False)
    _release_guard: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _released: bool = field(default=False, init=False, repr=False)
    _release_result: bool = field(default=False, init=False, repr=False)

    @property
    def lost_event(self) -> threading.Event:
        """Set when ownership is lost or cannot be confirmed safely."""
        return self.heartbeat.lost_event

    def release(self) -> bool:
        """Stop the heartbeat, then remove only this lease's exact owner."""
        with self._release_guard:
            if self._released:
                return self._release_result
            if not _stop_remote_worker_lock_heartbeat(self.heartbeat):
                print(
                    "[skill-eval-lock] WARN: leaving remote lock in place "
                    f"because the heartbeat did not stop on {self.label}",
                    flush=True,
                )
                return False
            cleared = clear_remote_worker_lock(
                self.run_remote,
                self.label,
                self.owner,
            )
            if cleared:
                self._release_result = True
                self._released = True
            return cleared


def _safe_slug(value: str) -> str:
    return (
        "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")
        or "scenario"
    )


def build_remote_lock_owner(owner_context: str | None = None) -> str:
    """Build the v2 owner identity understood by current NemoClaw jobs."""
    context = (
        owner_context
        or os.environ.get("SKILL_EVAL_LOCK_OWNER_CONTEXT")
        or os.environ.get("NEMOCLAW_LOCK_OWNER_CONTEXT")
        or os.environ.get("GITHUB_JOB")
        or "skill-eval"
    )
    return "__".join(
        _safe_slug(part)
        for part in (
            "v2",
            os.environ.get("GITHUB_RUN_ID", "local"),
            os.environ.get("GITHUB_RUN_ATTEMPT", "0"),
            context,
            str(os.getpid()),
            str(int(time.time())),
            uuid.uuid4().hex,
        )
    )


def _command_output(result: CommandResultLike) -> str:
    return (result.stdout or "") + (result.stderr or "")


def _command_tail(result: CommandResultLike, limit: int = 500) -> str:
    return _command_output(result)[-limit:].strip()


def _owner_marker(action: str, owner: str) -> str:
    """Return an exact, owner-bound remote protocol marker."""
    return f"{_MARKER_PREFIX}{action}:{owner}"


def _has_exact_stdout_line(result: CommandResultLike, expected: str) -> bool:
    """Accept a protocol marker only when the remote stdout emitted it alone."""
    return expected in (result.stdout or "").splitlines()


def _acquire_command(owner: str) -> str:
    return f"""set -eu
lock_root=/tmp/skill-eval/locks
lock_dir="$lock_root/nemoclaw-worker.lockdir"
guard="$lock_root/worker-lock.guard"
owner={shlex.quote(owner)}
now=$(date +%s)
mkdir -p "$lock_root"
exec 9>"$guard"
flock -x 9
if mkdir "$lock_dir" 2>/dev/null; then
  cleanup_incomplete_lock() {{
    if [ ! -s "$lock_dir/owner" ]; then
      rm -rf "$lock_dir"
    fi
  }}
  trap cleanup_incomplete_lock EXIT HUP INT TERM
  printf '%s\\n' "$owner" > "$lock_dir/owner"
  printf '%s\\n' "$now" > "$lock_dir/created"
  trap - EXIT HUP INT TERM
  printf 'SKILL_EVAL_REMOTE_LOCK_ACQUIRED:%s\\n' "$owner"
  exit 0
fi
created=$(cat "$lock_dir/created" 2>/dev/null || stat -c %Y "$lock_dir" 2>/dev/null || printf '%s\\n' "$now")
age=$((now - created))
locked_owner=$(cat "$lock_dir/owner" 2>/dev/null || echo unknown)
printf 'SKILL_EVAL_REMOTE_LOCK_BUSY:%s\\n' "$locked_owner"
echo "NemoClaw worker is locked by $locked_owner age=${{age}}s"
exit 1
"""


def remote_lock_owner_from_output(output: str) -> str | None:
    """Parse an exact standalone busy marker produced by the remote command."""
    prefix = f"{_MARKER_PREFIX}BUSY:"
    for line in output.splitlines():
        if not line.startswith(prefix):
            continue
        owner = line.removeprefix(prefix)
        if owner and not re.search(r"\s", owner):
            return owner
    return None


def _run_acquire_attempt(
    run_remote: RemoteExecutor,
    label: str,
    owner: str,
) -> tuple[bool, str | None, str]:
    """Attempt once, retrying only an uncertain response-loss outcome."""

    def invoke() -> tuple[CommandResultLike | None, str]:
        try:
            result = run_remote(_acquire_command(owner), 60)
        except Exception as exc:  # noqa: BLE001 - transport adapters vary.
            print(
                f"[skill-eval-lock] remote lock check failed on {label}: {exc!r}",
                flush=True,
            )
            return None, ""
        tail = _command_tail(result)
        return result, tail

    result, tail = invoke()
    acquired_marker = _owner_marker("ACQUIRED", owner)
    if (
        result is not None
        and result.returncode == 0
        and _has_exact_stdout_line(result, acquired_marker)
    ):
        return True, None, tail
    locked_owner = remote_lock_owner_from_output(tail)
    if locked_owner == owner:
        print(
            f"[skill-eval-lock] reconciled remote lock acquisition on {label}",
            flush=True,
        )
        return True, owner, tail
    if locked_owner:
        return False, locked_owner, tail

    # The first command may have created the directory before its transport
    # response was lost.  Repeating the same mkdir protocol is safe: it either
    # acquires a still-free lock or reports the exact UUID owner just written.
    result, retry_tail = invoke()
    tail = retry_tail or tail
    if (
        result is not None
        and result.returncode == 0
        and _has_exact_stdout_line(result, acquired_marker)
    ):
        return True, None, tail
    locked_owner = remote_lock_owner_from_output(tail)
    if locked_owner == owner:
        print(
            f"[skill-eval-lock] reconciled remote lock acquisition on {label}",
            flush=True,
        )
        return True, owner, tail
    if not locked_owner:
        print(
            "[skill-eval-lock] cleaning up an unconfirmed exact-owner "
            f"acquisition on {label}",
            flush=True,
        )
        clear_remote_worker_lock(run_remote, label, owner)
    return False, locked_owner, tail


def try_acquire_remote_worker_lock(
    run_remote: RemoteExecutor,
    label: str,
    *,
    owner_context: str | None = None,
    owner_inactive: OwnerInactiveChecker | None = None,
) -> RemoteWorkerLease | None:
    """Try to acquire and heartbeat the shared worker lock.

    ``run_remote`` receives ``(shell_command, timeout_seconds)``.  It must
    return an object with ``returncode``, ``stdout``, and ``stderr`` fields.
    Remote transport failures fail closed and return ``None``.
    """
    owner = build_remote_lock_owner(owner_context)
    acquired, locked_owner, tail = _run_acquire_attempt(
        run_remote,
        label,
        owner,
    )
    checker = owner_inactive or remote_lock_owner_is_inactive
    if not acquired and locked_owner and locked_owner != owner:
        try:
            inactive = checker(locked_owner)
        except Exception as exc:  # noqa: BLE001 - inability to prove is active.
            print(
                "[skill-eval-lock] could not verify remote lock owner "
                f"{locked_owner} on {label}: {exc!r}",
                flush=True,
            )
            inactive = False
        if inactive:
            print(
                "[skill-eval-lock] removing remote lock from inactive run: "
                f"{locked_owner}",
                flush=True,
            )
            if clear_remote_worker_lock(run_remote, label, locked_owner):
                acquired, locked_owner, tail = _run_acquire_attempt(
                    run_remote,
                    label,
                    owner,
                )

    if not acquired:
        if tail:
            print(
                f"[skill-eval-lock] {label} remote lock unavailable: {tail}",
                flush=True,
            )
        return None

    try:
        heartbeat = _start_remote_worker_lock_heartbeat(
            run_remote,
            label,
            owner,
        )
    except Exception:
        clear_remote_worker_lock(run_remote, label, owner)
        raise
    return RemoteWorkerLease(label, owner, run_remote, heartbeat)


def clear_remote_worker_lock(
    run_remote: RemoteExecutor,
    label: str,
    owner: str,
) -> bool:
    """Remove the lock only when its current owner is exactly ``owner``."""
    command = f"""set -eu
lock_root=/tmp/skill-eval/locks
lock_dir="$lock_root/nemoclaw-worker.lockdir"
guard="$lock_root/worker-lock.guard"
expected={shlex.quote(owner)}
mkdir -p "$lock_root"
exec 9>"$guard"
flock -x 9
actual=$(cat "$lock_dir/owner" 2>/dev/null || true)
if [ -d "$lock_dir" ] && [ "$actual" = "$expected" ]; then
  rm -rf "$lock_dir"
  printf 'SKILL_EVAL_REMOTE_LOCK_CLEARED:%s\\n' "$expected"
  echo "removed NemoClaw worker lock owned by $expected"
  exit 0
fi
echo "NemoClaw worker lock owner changed to ${{actual:-none}}; not removing"
exit 1
"""
    try:
        result = run_remote(command, 60)
    except Exception as exc:  # noqa: BLE001 - transport adapters vary.
        print(
            f"[skill-eval-lock] remote lock cleanup failed on {label}: {exc!r}",
            flush=True,
        )
        return False
    tail = _command_tail(result)
    cleared_marker = _owner_marker("CLEARED", owner)
    if result.returncode != 0 or not _has_exact_stdout_line(result, cleared_marker):
        if tail:
            print(
                f"[skill-eval-lock] remote lock cleanup skipped on {label}: {tail}",
                flush=True,
            )
        return False
    if tail:
        print(f"[skill-eval-lock] {label}: {tail}", flush=True)
    return True


def refresh_remote_worker_lock(
    run_remote: RemoteExecutor,
    label: str,
    owner: str,
) -> str:
    """Atomically refresh an exact-owner lease without creating/removing it."""
    command = f"""set -eu
lock_root=/tmp/skill-eval/locks
lock_dir="$lock_root/nemoclaw-worker.lockdir"
guard="$lock_root/worker-lock.guard"
expected={shlex.quote(owner)}
not_owner() {{
  printf 'SKILL_EVAL_REMOTE_LOCK_NOT_OWNER:%s\\n' "$expected"
  echo "NemoClaw worker lock is not owned by $expected"
  exit 3
}}
mkdir -p "$lock_root"
exec 9>"$guard"
flock -x 9
[ -d "$lock_dir" ] || not_owner
actual=$(cat "$lock_dir/owner" 2>/dev/null || true)
[ "$actual" = "$expected" ] || not_owner
before=$(stat -Lc '%d:%i' "$lock_dir" 2>/dev/null) || not_owner
tmp=$(mktemp "$lock_dir/.created.XXXXXX") || exit 4
trap 'rm -f "$tmp"' EXIT HUP INT TERM
printf '%s\\n' "$(date +%s)" > "$tmp"
after=$(stat -Lc '%d:%i' "$lock_dir" 2>/dev/null) || not_owner
actual=$(cat "$lock_dir/owner" 2>/dev/null || true)
[ "$before" = "$after" ] && [ "$actual" = "$expected" ] || not_owner
mv -f "$tmp" "$lock_dir/created"
trap - EXIT HUP INT TERM
printf 'SKILL_EVAL_REMOTE_LOCK_REFRESHED:%s\\n' "$expected"
echo "refreshed NemoClaw worker lock owned by $expected"
"""
    try:
        result = run_remote(command, _heartbeat_command_timeout())
    except Exception:  # noqa: BLE001 - heartbeat converts failures to unknown.
        return "unknown"
    if result.returncode == 0 and _has_exact_stdout_line(
        result,
        _owner_marker("REFRESHED", owner),
    ):
        return "refreshed"
    if result.returncode == 3 and _has_exact_stdout_line(
        result,
        _owner_marker("NOT_OWNER", owner),
    ):
        return "not_owner"
    return "unknown"


def _first_env_int(names: tuple[str, ...], default: int) -> int:
    for name in names:
        raw = os.environ.get(name)
        if raw is None:
            continue
        try:
            return int(raw)
        except ValueError:
            return default
    return default


def _heartbeat_settings() -> tuple[float, float]:
    configured_interval = _first_env_int(
        (
            "SKILL_EVAL_REMOTE_LOCK_HEARTBEAT_SEC",
            "NEMOCLAW_REMOTE_LOCK_HEARTBEAT_SEC",
        ),
        180,
    )
    interval_s = min(240, max(30, configured_interval))
    configured_max_silence = _first_env_int(
        (
            "SKILL_EVAL_REMOTE_LOCK_HEARTBEAT_MAX_SILENCE_SEC",
            "NEMOCLAW_REMOTE_LOCK_HEARTBEAT_MAX_SILENCE_SEC",
        ),
        660,
    )
    max_silence_s = min(
        660,
        max(interval_s * 2, configured_max_silence),
    )
    return float(interval_s), float(max_silence_s)


def _heartbeat_command_timeout() -> int:
    configured = _first_env_int(
        (
            "SKILL_EVAL_REMOTE_LOCK_HEARTBEAT_TIMEOUT_SEC",
            "NEMOCLAW_REMOTE_LOCK_HEARTBEAT_TIMEOUT_SEC",
        ),
        30,
    )
    return min(30, max(5, configured))


def _start_remote_worker_lock_heartbeat(
    run_remote: RemoteExecutor,
    label: str,
    owner: str,
) -> RemoteLockHeartbeat:
    stop_event = threading.Event()
    lost_event = threading.Event()
    interval_s, max_silence_s = _heartbeat_settings()

    def heartbeat_loop() -> None:
        last_success = time.monotonic()
        while not stop_event.wait(interval_s):
            try:
                status = refresh_remote_worker_lock(
                    run_remote,
                    label,
                    owner,
                )
            except Exception as exc:  # noqa: BLE001 - fail closed.
                print(
                    "[skill-eval-lock] WARN: remote worker lock heartbeat "
                    f"raised on {label}: {exc!r}",
                    flush=True,
                )
                status = "unknown"
            if stop_event.is_set():
                return
            if status == "refreshed":
                last_success = time.monotonic()
                continue
            if status == "not_owner":
                print(
                    f"[skill-eval-lock] ERROR: lost remote worker lock on {label}",
                    flush=True,
                )
                lost_event.set()
                return
            silence = time.monotonic() - last_success
            print(
                "[skill-eval-lock] WARN: remote worker lock heartbeat "
                f"unconfirmed on {label} ({int(silence)}s since success)",
                flush=True,
            )
            if silence >= max_silence_s:
                print(
                    "[skill-eval-lock] ERROR: remote worker lock heartbeat "
                    f"exceeded the {int(max_silence_s)}s safety window on "
                    f"{label}",
                    flush=True,
                )
                lost_event.set()
                return

    thread = threading.Thread(
        target=heartbeat_loop,
        name=f"skill-eval-lock-heartbeat-{_safe_slug(label)}",
        daemon=True,
    )
    thread.start()
    return RemoteLockHeartbeat(stop_event, lost_event, thread)


def _stop_remote_worker_lock_heartbeat(
    heartbeat: RemoteLockHeartbeat,
) -> bool:
    heartbeat.stop_event.set()
    heartbeat.thread.join(timeout=32)
    if heartbeat.thread.is_alive():
        print(
            "[skill-eval-lock] WARN: remote worker lock heartbeat did not stop",
            flush=True,
        )
        return False
    return True


def github_run_id_from_lock_owner(owner: str) -> str | None:
    match = re.match(r"^(?:v2__)?(\d+)__", owner)
    return match.group(1) if match else None


def github_job_identity_from_lock_owner(
    owner: str,
) -> tuple[str, str, str] | None:
    parts = owner.split("__")
    if (
        len(parts) != 7
        or parts[0] != "v2"
        or not parts[1].isdigit()
        or not parts[2].isdigit()
        or not parts[3]
    ):
        return None
    return parts[1], parts[2], parts[3]


def _run_local(
    cmd: list[str],
    *,
    timeout: int,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _github_job_status(
    run_id: str,
    run_attempt: str,
    job_context: str,
) -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return None
    try:
        result = _run_local(
            [
                "gh",
                "api",
                (
                    f"repos/{repo}/actions/runs/{run_id}/attempts/"
                    f"{run_attempt}/jobs?per_page=100"
                ),
            ],
            timeout=30,
            env={**os.environ, "GH_TOKEN": token},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        tail = _command_tail(result, 300)
        if tail:
            print(
                f"[skill-eval-lock] could not query GitHub jobs for run "
                f"{run_id} attempt {run_attempt}: {tail}",
                flush=True,
            )
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return None
    matches = [
        job
        for job in jobs
        if isinstance(job, dict)
        and _safe_slug(str(job.get("name") or "")) == job_context
    ]
    total_count = payload.get("total_count")
    if not isinstance(total_count, int) or total_count > len(jobs):
        return None
    if len(matches) != 1:
        return None
    status = str(matches[0].get("status") or "").strip()
    return status or None


def _github_run_status(run_id: str) -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return None
    try:
        result = _run_local(
            [
                "gh",
                "run",
                "view",
                run_id,
                "--repo",
                repo,
                "--json",
                "status",
                "--jq",
                ".status",
            ],
            timeout=30,
            env={**os.environ, "GH_TOKEN": token},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        tail = _command_tail(result, 300)
        if tail:
            print(
                f"[skill-eval-lock] could not query GitHub run {run_id}: {tail}",
                flush=True,
            )
        return None
    status = (result.stdout or "").strip()
    return status or None


def remote_lock_owner_is_inactive(owner: str) -> bool:
    """Return true only when GitHub proves the exact owner is terminal."""
    run_id = github_run_id_from_lock_owner(owner)
    if not run_id:
        return False
    job_identity = github_job_identity_from_lock_owner(owner)
    if job_identity:
        run_id, run_attempt, job_context = job_identity
        status = _github_job_status(run_id, run_attempt, job_context)
        if status is not None:
            return status == "completed"
    current_run_id = os.environ.get("GITHUB_RUN_ID", "")
    if run_id == current_run_id:
        return False
    status = _github_run_status(run_id)
    if status is None:
        return False
    return status == "completed"
