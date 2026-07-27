# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the transport-neutral remote worker lease."""

from __future__ import annotations

import importlib.util
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "remote_worker_lock",
    Path(__file__).resolve().parents[1] / "remote_worker_lock.py",
)
remote_lock = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = remote_lock
_SPEC.loader.exec_module(remote_lock)


class Result:
    def __init__(
        self,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _owner_from_acquire_command(command: str) -> str:
    line = next(line for line in command.splitlines() if line.startswith("owner="))
    return shlex.split(line.removeprefix("owner="))[0]


class OwnerIdentityTests(unittest.TestCase):
    def test_v2_owner_prefers_shared_matrix_context(self):
        env = {
            "GITHUB_RUN_ID": "30277029589",
            "GITHUB_RUN_ATTEMPT": "3",
            "GITHUB_JOB": "eval",
            "NEMOCLAW_LOCK_OWNER_CONTEXT": "nemoclaw-row",
            "SKILL_EVAL_LOCK_OWNER_CONTEXT": "Ask Video · RTXPRO6000BW",
        }
        with mock.patch.dict(remote_lock.os.environ, env, clear=True):
            owner = remote_lock.build_remote_lock_owner()

        parts = owner.split("__")
        self.assertEqual(len(parts), 7)
        self.assertEqual(
            parts[:4],
            [
                "v2",
                "30277029589",
                "3",
                "ask-video---rtxpro6000bw",
            ],
        )
        self.assertTrue(parts[4].isdigit())
        self.assertTrue(parts[5].isdigit())
        self.assertRegex(parts[6], r"^[0-9a-f]{32}$")

    def test_owner_parsers_reject_non_v2_job_identity(self):
        self.assertEqual(
            remote_lock.github_run_id_from_lock_owner("12345__legacy"),
            "12345",
        )
        self.assertIsNone(
            remote_lock.github_job_identity_from_lock_owner("12345__legacy")
        )
        self.assertIsNone(
            remote_lock.github_job_identity_from_lock_owner(
                "v2__123__1__job__pid__time"
            )
        )


class AcquireReleaseTests(unittest.TestCase):
    def test_busy_active_owner_fails_closed(self):
        other = "v2__111__1__other-job__1__2__abc"
        calls: list[int] = []

        def execute(_command: str, timeout: int) -> Result:
            calls.append(timeout)
            return Result(
                1,
                f"NemoClaw worker is locked by {other} age=10s\n",
            )

        lease = remote_lock.try_acquire_remote_worker_lock(
            execute,
            "worker-a",
            owner_inactive=lambda owner: False,
        )

        self.assertIsNone(lease)
        self.assertEqual(calls, [60])

    def test_response_loss_reconciles_only_the_exact_generated_owner(self):
        attempts = 0
        release_calls = 0
        commands: list[str] = []

        def execute(command: str, timeout: int) -> Result:
            nonlocal attempts, release_calls
            commands.append(command)
            if "if mkdir" in command:
                attempts += 1
                owner = _owner_from_acquire_command(command)
                if attempts == 1:
                    raise subprocess.TimeoutExpired("remote", timeout)
                return Result(
                    1,
                    f"NemoClaw worker is locked by {owner} age=0s\n",
                )
            release_calls += 1
            return Result(0, "removed exact owner\n")

        lease = remote_lock.try_acquire_remote_worker_lock(
            execute,
            "worker-a",
            owner_context="matrix-row",
        )
        self.assertIsNotNone(lease)
        assert lease is not None

        self.assertEqual(attempts, 2)
        self.assertIn('exec 9>"$guard"', commands[0])
        self.assertIn("flock -x 9", commands[0])
        self.assertTrue(lease.release())
        self.assertEqual(release_calls, 1)

    def test_inactive_owner_is_cleared_then_acquisition_retried(self):
        old_owner = "v2__111__1__old-job__1__2__abc"
        acquisitions = 0
        clears: list[str] = []
        checked: list[str] = []

        def execute(command: str, _timeout: int) -> Result:
            nonlocal acquisitions
            if "if mkdir" in command:
                acquisitions += 1
                if acquisitions == 1:
                    return Result(
                        1,
                        f"NemoClaw worker is locked by {old_owner} age=20s\n",
                    )
                return Result(0)
            expected_line = next(
                line for line in command.splitlines() if line.startswith("expected=")
            )
            clears.append(shlex.split(expected_line.removeprefix("expected="))[0])
            return Result(0, "removed exact owner\n")

        def inactive(owner: str) -> bool:
            checked.append(owner)
            return True

        lease = remote_lock.try_acquire_remote_worker_lock(
            execute,
            "worker-b",
            owner_inactive=inactive,
        )
        self.assertIsNotNone(lease)
        assert lease is not None
        lease.release()

        self.assertEqual(checked, [old_owner])
        self.assertEqual(acquisitions, 2)
        self.assertEqual(clears[0], old_owner)
        self.assertEqual(clears[1], lease.owner)

    def test_changed_owner_is_never_deleted(self):
        seen: list[tuple[str, int]] = []

        def execute(command: str, timeout: int) -> Result:
            seen.append((command, timeout))
            return Result(
                1,
                "NemoClaw worker lock owner changed to another; not removing\n",
            )

        cleared = remote_lock.clear_remote_worker_lock(
            execute,
            "worker-c",
            "expected-owner",
        )

        self.assertFalse(cleared)
        self.assertEqual(seen[0][1], 60)
        self.assertIn('lock_dir="$lock_root/nemoclaw-worker.lockdir"', seen[0][0])
        self.assertIn('exec 9>"$guard"', seen[0][0])
        self.assertIn("flock -x 9", seen[0][0])
        self.assertIn('actual=$(cat "$lock_dir/owner"', seen[0][0])
        self.assertIn('[ "$actual" = "$expected" ]', seen[0][0])

    def test_double_response_loss_attempts_exact_owner_cleanup(self):
        commands: list[str] = []

        def execute(command: str, timeout: int) -> Result:
            commands.append(command)
            if "if mkdir" in command:
                raise subprocess.TimeoutExpired("remote", timeout)
            return Result(
                1,
                "NemoClaw worker lock owner changed to none; not removing\n",
            )

        lease = remote_lock.try_acquire_remote_worker_lock(
            execute,
            "worker-c",
            owner_context="matrix-row",
        )

        self.assertIsNone(lease)
        self.assertEqual(len(commands), 3)
        owner = _owner_from_acquire_command(commands[0])
        self.assertIn(f"expected={shlex.quote(owner)}", commands[2])

    def test_timeout_then_transport_error_cleans_unconfirmed_owner(self):
        commands: list[str] = []
        attempts = 0

        def execute(command: str, timeout: int) -> Result:
            nonlocal attempts
            commands.append(command)
            if "if mkdir" in command:
                attempts += 1
                if attempts == 1:
                    raise subprocess.TimeoutExpired("remote", timeout)
                return Result(255, "", "ssh: connection reset\n")
            return Result(
                1,
                "NemoClaw worker lock owner changed to none; not removing\n",
            )

        lease = remote_lock.try_acquire_remote_worker_lock(
            execute,
            "worker-c",
            owner_context="matrix-row",
        )

        self.assertIsNone(lease)
        self.assertEqual(len(commands), 3)
        owner = _owner_from_acquire_command(commands[0])
        self.assertIn(f"expected={shlex.quote(owner)}", commands[2])
        acquire = commands[0]
        self.assertIn('exec 9>"$guard"', acquire)
        self.assertIn("flock -x 9", acquire)
        self.assertLess(acquire.index("flock -x 9"), acquire.index('mkdir "$lock_dir"'))

    def test_heartbeat_start_failure_clears_acquired_exact_owner(self):
        commands: list[str] = []

        def execute(command: str, _timeout: int) -> Result:
            commands.append(command)
            return Result(0, "removed exact owner\n")

        with (
            mock.patch.object(
                remote_lock,
                "_start_remote_worker_lock_heartbeat",
                side_effect=RuntimeError("thread unavailable"),
            ),
            self.assertRaisesRegex(RuntimeError, "thread unavailable"),
        ):
            remote_lock.try_acquire_remote_worker_lock(
                execute,
                "worker-d",
            )

        self.assertEqual(len(commands), 2)
        owner = _owner_from_acquire_command(commands[0])
        self.assertIn(f"expected={shlex.quote(owner)}", commands[1])

    def test_release_stops_heartbeat_before_exact_delete(self):
        holder: dict[str, remote_lock.RemoteWorkerLease] = {}
        heartbeat_alive_at_delete: list[bool] = []

        def execute(command: str, _timeout: int) -> Result:
            if "if mkdir" in command:
                return Result(0)
            heartbeat_alive_at_delete.append(
                holder["lease"].heartbeat.thread.is_alive()
            )
            return Result(0, "removed exact owner\n")

        lease = remote_lock.try_acquire_remote_worker_lock(
            execute,
            "worker-e",
        )
        self.assertIsNotNone(lease)
        assert lease is not None
        holder["lease"] = lease

        self.assertTrue(lease.release())
        self.assertEqual(heartbeat_alive_at_delete, [False])
        # Release is idempotent and never issues a second delete.
        self.assertTrue(lease.release())
        self.assertEqual(heartbeat_alive_at_delete, [False])

    def test_release_can_retry_after_transient_cleanup_failure(self):
        deletes = 0

        def execute(command: str, _timeout: int) -> Result:
            nonlocal deletes
            if "if mkdir" in command:
                return Result(0)
            deletes += 1
            if deletes == 1:
                raise OSError("temporary transport failure")
            return Result(0, "removed exact owner\n")

        lease = remote_lock.try_acquire_remote_worker_lock(
            execute,
            "worker-e",
        )
        self.assertIsNotNone(lease)
        assert lease is not None

        self.assertFalse(lease.release())
        self.assertTrue(lease.release())
        self.assertEqual(deletes, 2)


class RefreshHeartbeatTests(unittest.TestCase):
    def test_refresh_is_atomic_and_exact_owner_only(self):
        commands: list[str] = []

        def execute(command: str, timeout: int) -> Result:
            commands.append(command)
            self.assertEqual(timeout, 30)
            return Result(
                0,
                "refreshed NemoClaw worker lock owned by exact-owner\n",
            )

        status = remote_lock.refresh_remote_worker_lock(
            execute,
            "worker-f",
            "exact-owner",
        )

        self.assertEqual(status, "refreshed")
        command = commands[0]
        self.assertIn("stat -Lc '%d:%i'", command)
        self.assertIn('exec 9>"$guard"', command)
        self.assertIn("flock -x 9", command)
        self.assertIn('mktemp "$lock_dir/.created.', command)
        self.assertIn('mv -f "$tmp" "$lock_dir/created"', command)
        self.assertNotIn('mkdir "$lock_dir"', command)
        self.assertNotIn('rm -rf "$lock_dir"', command)

    def test_refresh_distinguishes_owner_loss_from_transport_unknown(self):
        not_owner = remote_lock.refresh_remote_worker_lock(
            lambda _command, _timeout: Result(
                3,
                "NemoClaw worker lock is not owned by expected\n",
            ),
            "worker-g",
            "expected",
        )
        unknown = remote_lock.refresh_remote_worker_lock(
            lambda _command, _timeout: (_ for _ in ()).throw(OSError("network")),
            "worker-g",
            "expected",
        )

        self.assertEqual(not_owner, "not_owner")
        self.assertEqual(unknown, "unknown")

    def test_owner_loss_sets_lost_event(self):
        with (
            mock.patch.object(
                remote_lock,
                "_heartbeat_settings",
                return_value=(0.01, 0.03),
            ),
            mock.patch.object(
                remote_lock,
                "refresh_remote_worker_lock",
                return_value="not_owner",
            ),
        ):
            heartbeat = remote_lock._start_remote_worker_lock_heartbeat(
                lambda _command, _timeout: Result(0),
                "worker-h",
                "owner",
            )
            self.assertTrue(heartbeat.lost_event.wait(0.5))
            self.assertTrue(remote_lock._stop_remote_worker_lock_heartbeat(heartbeat))

    def test_unconfirmed_heartbeat_fails_closed_after_bounded_silence(self):
        refreshes = 0

        def unknown(*_args) -> str:
            nonlocal refreshes
            refreshes += 1
            return "unknown"

        with (
            mock.patch.object(
                remote_lock,
                "_heartbeat_settings",
                return_value=(0.01, 0.025),
            ),
            mock.patch.object(
                remote_lock,
                "refresh_remote_worker_lock",
                side_effect=unknown,
            ),
        ):
            heartbeat = remote_lock._start_remote_worker_lock_heartbeat(
                lambda _command, _timeout: Result(0),
                "worker-i",
                "owner",
            )
            self.assertTrue(heartbeat.lost_event.wait(0.5))
            self.assertTrue(remote_lock._stop_remote_worker_lock_heartbeat(heartbeat))

        self.assertGreaterEqual(refreshes, 3)

    def test_heartbeat_configuration_is_bounded_and_has_legacy_fallbacks(self):
        with mock.patch.dict(
            remote_lock.os.environ,
            {
                "SKILL_EVAL_REMOTE_LOCK_HEARTBEAT_SEC": "9999",
                "SKILL_EVAL_REMOTE_LOCK_HEARTBEAT_MAX_SILENCE_SEC": "9999",
            },
            clear=True,
        ):
            self.assertEqual(remote_lock._heartbeat_settings(), (240.0, 660.0))

        with mock.patch.dict(
            remote_lock.os.environ,
            {
                "NEMOCLAW_REMOTE_LOCK_HEARTBEAT_SEC": "bad",
                "NEMOCLAW_REMOTE_LOCK_HEARTBEAT_MAX_SILENCE_SEC": "bad",
            },
            clear=True,
        ):
            self.assertEqual(remote_lock._heartbeat_settings(), (180.0, 660.0))

        with mock.patch.dict(
            remote_lock.os.environ,
            {
                "SKILL_EVAL_REMOTE_LOCK_HEARTBEAT_SEC": "-1",
                "SKILL_EVAL_REMOTE_LOCK_HEARTBEAT_MAX_SILENCE_SEC": "-1",
            },
            clear=True,
        ):
            self.assertEqual(remote_lock._heartbeat_settings(), (30.0, 60.0))


class GitHubOwnerStatusTests(unittest.TestCase):
    def test_same_run_completed_matrix_job_is_inactive(self):
        owner = "v2__123__2__ask-video__44__55__abc"
        with (
            mock.patch.dict(
                remote_lock.os.environ,
                {"GITHUB_RUN_ID": "123"},
                clear=True,
            ),
            mock.patch.object(
                remote_lock,
                "_github_job_status",
                return_value="completed",
            ) as job_status,
        ):
            self.assertTrue(remote_lock.remote_lock_owner_is_inactive(owner))

        job_status.assert_called_once_with("123", "2", "ask-video")

    def test_same_run_unknown_job_is_never_evicted(self):
        owner = "v2__123__2__ask-video__44__55__abc"
        with (
            mock.patch.dict(
                remote_lock.os.environ,
                {"GITHUB_RUN_ID": "123"},
                clear=True,
            ),
            mock.patch.object(
                remote_lock,
                "_github_job_status",
                return_value=None,
            ),
            mock.patch.object(remote_lock, "_github_run_status") as run_status,
        ):
            self.assertFalse(remote_lock.remote_lock_owner_is_inactive(owner))

        run_status.assert_not_called()

    def test_other_run_requires_a_proven_terminal_status(self):
        owner = "v2__456__1__job__44__55__abc"
        with (
            mock.patch.dict(
                remote_lock.os.environ,
                {"GITHUB_RUN_ID": "123"},
                clear=True,
            ),
            mock.patch.object(
                remote_lock,
                "_github_job_status",
                return_value=None,
            ),
            mock.patch.object(
                remote_lock,
                "_github_run_status",
                side_effect=["in_progress", "completed", None],
            ),
        ):
            self.assertFalse(remote_lock.remote_lock_owner_is_inactive(owner))
            self.assertTrue(remote_lock.remote_lock_owner_is_inactive(owner))
            self.assertFalse(remote_lock.remote_lock_owner_is_inactive(owner))

    def test_unknown_github_status_is_never_treated_as_terminal(self):
        owner = "v2__456__1__job__44__55__abc"
        with (
            mock.patch.dict(
                remote_lock.os.environ,
                {"GITHUB_RUN_ID": "123"},
                clear=True,
            ),
            mock.patch.object(
                remote_lock,
                "_github_job_status",
                return_value="mystery-state",
            ),
        ):
            self.assertFalse(remote_lock.remote_lock_owner_is_inactive(owner))

    def test_github_job_match_is_exact_unique_and_unpaginated(self):
        payload = {
            "total_count": 2,
            "jobs": [
                {"name": "NemoClaw / Ask Video", "status": "completed"},
                {"name": "Other", "status": "in_progress"},
            ]
        }
        with (
            mock.patch.dict(
                remote_lock.os.environ,
                {
                    "GITHUB_REPOSITORY": "org/repo",
                    "GH_TOKEN": "token",
                },
                clear=True,
            ),
            mock.patch.object(
                remote_lock,
                "_run_local",
                return_value=Result(0, remote_lock.json.dumps(payload)),
            ),
        ):
            self.assertEqual(
                remote_lock._github_job_status(
                    "123",
                    "1",
                    "nemoclaw---ask-video",
                ),
                "completed",
            )
            self.assertIsNone(
                remote_lock._github_job_status("123", "1", "ask-video"),
            )

        payload["total_count"] = 101
        with (
            mock.patch.dict(
                remote_lock.os.environ,
                {
                    "GITHUB_REPOSITORY": "org/repo",
                    "GH_TOKEN": "token",
                },
                clear=True,
            ),
            mock.patch.object(
                remote_lock,
                "_run_local",
                return_value=Result(0, remote_lock.json.dumps(payload)),
            ),
        ):
            self.assertIsNone(
                remote_lock._github_job_status("123", "1", "missing"),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
