#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for timeout cancellation and Claude log recovery."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
import uuid

# Stub Harbor so the environment provider can be tested without installing it.
_base = types.ModuleType("harbor.environments.base")


class _BaseEnvironment:
    def __init__(self, *args, **kwargs):
        pass


class _ExecResult:
    def __init__(self, stdout=None, stderr=None, return_code=0):
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code


_base.BaseEnvironment = _BaseEnvironment
_base.ExecResult = _ExecResult
sys.modules.setdefault("harbor", types.ModuleType("harbor"))
sys.modules.setdefault("harbor.environments", types.ModuleType("harbor.environments"))
sys.modules["harbor.environments.base"] = _base

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import envs.brev_env as brev_env  # noqa: E402


class _BlockingProcess:
    """Small asyncio subprocess stand-in that blocks until cancelled once."""

    def __init__(self):
        self.pid = 4242
        self.returncode = None
        self.communicate_calls = 0

    async def communicate(self, input=None):
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            await asyncio.Event().wait()
        self.returncode = -9
        return b"", b""

    async def wait(self):
        self.returncode = -9
        return self.returncode


class SubprocessCancellationTest(unittest.IsolatedAsyncioTestCase):
    def _agent_marker_from_command(self, command):
        match = re.search(
            rf"{brev_env.REMOTE_AGENT_RUN_ENV}="
            rf"({brev_env.REMOTE_AGENT_RUN_PREFIX}[0-9a-f]{{32}})",
            command,
        )
        self.assertIsNotNone(match)
        return match.group(1)

    async def test_cancellation_kills_and_reaps_process_group(self):
        proc = _BlockingProcess()
        with mock.patch.object(brev_env, "_kill_proc_group") as kill_group:
            task = asyncio.create_task(
                brev_env._communicate_with_cancellation_cleanup(
                    proc,
                    input_data=b"\n",
                    timeout=60,
                )
            )
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        kill_group.assert_called_once_with(proc)
        self.assertEqual(proc.communicate_calls, 2)
        self.assertEqual(proc.returncode, -9)

    async def test_group_kill_still_runs_after_leader_exits(self):
        proc = _BlockingProcess()
        proc.returncode = 0
        with mock.patch.object(brev_env.os, "killpg") as kill_group:
            brev_env._kill_proc_group(proc)
        kill_group.assert_called_once_with(proc.pid, signal.SIGKILL)

    async def test_interrupted_claude_exec_reaps_remote_agent_before_returning(self):
        env = brev_env.BrevEnvironment()
        env._instance_name = "vss-eval-test"
        interrupted = asyncio.CancelledError()
        with mock.patch.object(
            brev_env,
            "_run_brev_exec",
            new=mock.AsyncMock(
                side_effect=[
                    interrupted,
                    brev_env.ExecResult(stdout="reaped", return_code=0),
                ]
            ),
        ) as run:
            with self.assertRaises(asyncio.CancelledError):
                await env.exec(
                    "claude --verbose --output-format=stream-json --print"
                )

        self.assertEqual(run.await_count, 2)
        marker = self._agent_marker_from_command(run.await_args_list[0].args[1])
        self.assertIn(
            f"{brev_env.REMOTE_AGENT_RUN_ENV}={marker}",
            run.await_args_list[1].args[1],
        )

    async def test_nonzero_claude_exec_reaps_remote_agent_before_verifier(self):
        env = brev_env.BrevEnvironment()
        env._instance_name = "vss-eval-test"
        with mock.patch.object(
            brev_env,
            "_run_brev_exec",
            new=mock.AsyncMock(
                side_effect=[
                    brev_env.ExecResult(stderr="killed", return_code=143),
                    brev_env.ExecResult(stdout="reaped", return_code=0),
                ]
            ),
        ) as run:
            result = await env.exec(
                "claude --verbose --output-format=stream-json --print"
            )

        self.assertEqual(result.return_code, 143)
        self.assertEqual(run.await_count, 2)
        marker = self._agent_marker_from_command(run.await_args_list[0].args[1])
        self.assertIn(
            f"{brev_env.REMOTE_AGENT_RUN_ENV}={marker}",
            run.await_args_list[1].args[1],
        )

    async def test_nonzero_codex_exec_is_marked_and_reaped(self):
        env = brev_env.BrevEnvironment()
        env._instance_name = "vss-eval-test"
        with mock.patch.object(
            brev_env,
            "_run_brev_exec",
            new=mock.AsyncMock(
                side_effect=[
                    brev_env.ExecResult(stderr="killed", return_code=143),
                    brev_env.ExecResult(stdout="reaped", return_code=0),
                ]
            ),
        ) as run:
            result = await env.exec(
                "codex exec --dangerously-bypass-approvals-and-sandbox --json"
            )

        self.assertEqual(result.return_code, 143)
        self.assertEqual(run.await_count, 2)
        marker = self._agent_marker_from_command(run.await_args_list[0].args[1])
        self.assertIn(
            f"{brev_env.REMOTE_AGENT_RUN_ENV}={marker}",
            run.await_args_list[1].args[1],
        )
        self.assertIn("codex exe[c]", run.await_args_list[1].args[1])

    async def test_successful_agent_fails_closed_when_reap_fails(self):
        env = brev_env.BrevEnvironment()
        env._instance_name = "vss-eval-test"
        with (
            mock.patch.object(
                brev_env,
                "_run_brev_exec",
                new=mock.AsyncMock(
                    side_effect=[
                        brev_env.ExecResult(stdout="done", return_code=0),
                        brev_env.ExecResult(stderr="reap failed", return_code=1),
                    ]
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "remote agent reap failed"),
        ):
            await env.exec(
                "claude --verbose --output-format=stream-json --print"
            )

    async def test_cancellation_during_post_agent_reap_retries_cleanup(self):
        env = brev_env.BrevEnvironment()
        env._instance_name = "vss-eval-test"
        with mock.patch.object(
            brev_env,
            "_run_brev_exec",
            new=mock.AsyncMock(
                side_effect=[
                    brev_env.ExecResult(stdout="done", return_code=0),
                    asyncio.CancelledError(),
                    brev_env.ExecResult(stdout="reaped", return_code=0),
                ]
            ),
        ) as run:
            with self.assertRaises(asyncio.CancelledError):
                await env.exec(
                    "claude --verbose --output-format=stream-json --print"
                )

        self.assertEqual(run.await_count, 3)
        marker = self._agent_marker_from_command(run.await_args_list[0].args[1])
        for reap_call in run.await_args_list[1:]:
            self.assertIn(
                f"{brev_env.REMOTE_AGENT_RUN_ENV}={marker}",
                reap_call.args[1],
            )


class AgentLogRecoveryTest(unittest.IsolatedAsyncioTestCase):
    def _environment(self):
        env = brev_env.BrevEnvironment()
        env._instance_name = "vss-eval-test"
        return env

    async def test_empty_agent_download_recovers_raw_log(self):
        env = self._environment()
        env._download_dir_once = mock.AsyncMock(return_value=None)
        env._download_claude_log_fallback = mock.AsyncMock(return_value=True)
        with tempfile.TemporaryDirectory() as tmp:
            await env.download_dir("/logs/agent", tmp)
        env._download_claude_log_fallback.assert_awaited_once()

    async def test_fallback_error_after_primary_success_is_best_effort(self):
        env = self._environment()
        env._download_dir_once = mock.AsyncMock(return_value=None)
        env._download_claude_log_fallback = mock.AsyncMock(
            side_effect=RuntimeError("fallback transport failed")
        )
        with tempfile.TemporaryDirectory() as tmp:
            await env.download_dir("/logs/agent", tmp)
        env._download_claude_log_fallback.assert_awaited_once()

    async def test_session_jsonl_skips_raw_log_fallback(self):
        env = self._environment()
        env._download_claude_log_fallback = mock.AsyncMock(return_value=True)

        async def write_session(_source, target):
            path = Path(target) / "sessions" / "projects" / "project"
            path.mkdir(parents=True)
            (path / "session.jsonl").write_text("{}\n")

        env._download_dir_once = mock.AsyncMock(side_effect=write_session)
        with tempfile.TemporaryDirectory() as tmp:
            await env.download_dir("/logs/agent", tmp)
        env._download_claude_log_fallback.assert_not_awaited()

    async def test_failed_agent_download_uses_raw_log_fallback(self):
        env = self._environment()
        original = RuntimeError("session transfer failed")
        env._download_dir_once = mock.AsyncMock(side_effect=original)
        env._download_claude_log_fallback = mock.AsyncMock(return_value=True)

        with mock.patch.object(brev_env, "BREV_DOWNLOAD_RETRIES", 1):
            with tempfile.TemporaryDirectory() as tmp:
                await env.download_dir("/logs/agent", tmp)
        env._download_claude_log_fallback.assert_awaited_once()

    async def test_failed_fallback_preserves_original_download_error(self):
        env = self._environment()
        original = RuntimeError("session transfer failed")
        env._download_dir_once = mock.AsyncMock(side_effect=original)
        env._download_claude_log_fallback = mock.AsyncMock(return_value=False)

        with mock.patch.object(brev_env, "BREV_DOWNLOAD_RETRIES", 1):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(RuntimeError, "session transfer failed"):
                    await env.download_dir("/logs/agent", tmp)

    async def test_fallback_decodes_marker_bounded_payload(self):
        env = self._environment()
        payload = b'{"type":"assistant","message":"last event"}\n'

        async def fake_exec(_instance, command, timeout):
            marker_match = re.search(
                r"(__HARBOR_CLAUDE_FALLBACK_[0-9a-f]+__)START",
                command,
            )
            self.assertIsNotNone(marker_match)
            marker = marker_match.group(1)
            encoded = base64.b64encode(payload).decode()
            return brev_env.ExecResult(
                stdout=f"brev noise\n{marker}START\n{encoded}\n{marker}END\n",
                stderr=None,
                return_code=0,
            )

        with mock.patch.object(brev_env, "_run_brev_exec", new=fake_exec):
            with tempfile.TemporaryDirectory() as tmp:
                recovered = await env._download_claude_log_fallback("/logs/agent", tmp)
                self.assertTrue(recovered)
                self.assertEqual((Path(tmp) / "claude-code.txt").read_bytes(), payload)

    def test_staged_base64_parts_decode_after_async_transport_cleanup(self):
        payload = b"trajectory payload" * 100
        encoded = base64.b64encode(payload).decode()
        parts = [encoded[:37], encoded[37:103], encoded[103:]]
        self.assertEqual(brev_env._decode_base64_parts(parts), payload)


class RemoteAgentReapTest(unittest.TestCase):
    _TREE_SCRIPT = r"""
import os
from pathlib import Path
import sys
import time

pid = os.fork()
if pid == 0:
    os.setsid()
    Path(sys.argv[1]).write_text(str(os.getpid()))
    while True:
        time.sleep(1)
while not Path(sys.argv[1]).exists():
    time.sleep(0.01)
while True:
    time.sleep(1)
"""

    @staticmethod
    def _running(pid):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return False
        closing_paren = stat.rfind(")")
        return stat[closing_paren + 2 :].split()[0] != "Z"

    def test_reaper_retains_self_excluding_legacy_agent_patterns(self):
        command = brev_env._stray_agent_reap_command()
        subprocess.run(["bash", "-n", "-c", command], check=True)
        self.assertIn("stream-jso[n]", command)
        self.assertIn("codex exe[c]", command)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires /proc")
    def test_exact_and_startup_reapers_kill_detached_marked_groups_only(self):
        for generic in (False, True):
            with self.subTest(generic=generic), tempfile.TemporaryDirectory() as td:
                child_file = Path(td) / "child.pid"
                shim_dir = Path(td) / "bin"
                shim_dir.mkdir()
                # Production also finds pre-marker agents by argv. Disable
                # that broad migration fallback in this live CI-host probe so
                # it can never signal an unrelated job on a shared runner.
                pgrep_shim = shim_dir / "pgrep"
                pgrep_shim.write_text("#!/bin/sh\nexit 1\n")
                pgrep_shim.chmod(0o755)
                unique = uuid.uuid4().hex
                marker_env = f"HARBOR_SKILL_EVAL_AGENT_TEST_{unique.upper()}"
                marker_prefix = f"skill-eval-test-{unique}-"
                marker = marker_prefix + ("b" if generic else "a") * 32
                env = os.environ.copy()
                env[marker_env] = marker
                agent = subprocess.Popen(
                    [sys.executable, "-c", self._TREE_SCRIPT, str(child_file)],
                    env=env,
                    start_new_session=True,
                )
                unmarked = subprocess.Popen(["sleep", "30"], start_new_session=True)
                child_pid = None
                try:
                    deadline = time.monotonic() + 2
                    while not child_file.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(child_file.exists())
                    child_pid = int(child_file.read_text())

                    with (
                        mock.patch.object(
                            brev_env,
                            "REMOTE_AGENT_RUN_ENV",
                            marker_env,
                        ),
                        mock.patch.object(
                            brev_env,
                            "REMOTE_AGENT_RUN_PREFIX",
                            marker_prefix,
                        ),
                    ):
                        command = brev_env._stray_agent_reap_command(
                            None if generic else marker
                        )
                    reaper_env = os.environ.copy()
                    reaper_env["PATH"] = (
                        f"{shim_dir}:{reaper_env.get('PATH', '')}"
                    )
                    subprocess.run(
                        ["bash", "-c", command],
                        check=True,
                        capture_output=True,
                        env=reaper_env,
                        text=True,
                        timeout=5,
                    )
                    agent.wait(timeout=2)
                    deadline = time.monotonic() + 2
                    while self._running(child_pid) and time.monotonic() < deadline:
                        time.sleep(0.02)

                    self.assertFalse(self._running(child_pid))
                    self.assertIsNone(unmarked.poll())
                finally:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(agent.pid, signal.SIGKILL)
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(unmarked.pid, signal.SIGKILL)
                    if child_pid is not None:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(child_pid, signal.SIGKILL)
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        agent.wait(timeout=1)
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        unmarked.wait(timeout=1)


class PriorAgentOutputIsolationTest(unittest.TestCase):
    def test_archive_command_preserves_every_mappable_root_output(self):
        command = brev_env._prior_agent_output_archive_command()
        subprocess.run(["bash", "-n", "-c", command], check=True)
        for output in (
            "claude-code.txt",
            "trajectory.json",
            "trajectory.jsonl",
            "agent.log",
        ):
            self.assertIn(output, command)
        self.assertNotIn('rm -f -- "$ROOT/$name"', command)
        self.assertIn(
            'mv "$ROOT/$name" "$ARCHIVE/root-output/"',
            command,
        )
        self.assertIn('mv "$PROJ"/* "$ARCHIVE/sessions/"', command)
        self.assertIn("-mtime +7", command)

    def test_transfer_wall_budget_includes_active_and_reap_windows(self):
        self.assertEqual(brev_env.BREV_TRANSFER_ACTIVE_TIMEOUT_SEC, 600)
        self.assertEqual(brev_env.BREV_TRANSFER_CANCELLATION_GRACE_SEC, 30)
        self.assertEqual(brev_env.BREV_TRANSFER_TOTAL_TIMEOUT_SEC, 630)
        self.assertEqual(brev_env.BREV_DOWNLOAD_PRIMARY_TIMEOUT_SEC, 480)
        self.assertEqual(brev_env.BREV_LOG_FALLBACK_TIMEOUT_SEC, 120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
