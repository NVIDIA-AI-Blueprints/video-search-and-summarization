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

    async def test_successful_deferred_agent_is_not_reaped_immediately(self):
        env = brev_env.BrevEnvironment()
        env._instance_name = "vss-eval-test"
        marker = f"{brev_env.REMOTE_AGENT_RUN_PREFIX}{'a' * 32}"
        with (
            mock.patch.dict(
                brev_env.os.environ,
                {
                    brev_env.AGENT_RUN_MARKER_OVERRIDE_ENV: marker,
                    brev_env.DEFER_AGENT_REAP_ENV: "1",
                },
                clear=False,
            ),
            mock.patch.object(
                brev_env,
                "_run_brev_exec",
                new=mock.AsyncMock(
                    return_value=brev_env.ExecResult(stdout="done", return_code=0)
                ),
            ) as run,
        ):
            result = await env.exec(
                "claude --verbose --output-format=stream-json --print"
            )

        self.assertEqual(result.return_code, 0)
        run.assert_awaited_once()
        self.assertIn(
            f"{brev_env.REMOTE_AGENT_RUN_ENV}={marker}",
            run.await_args.args[1],
        )

    async def test_failed_deferred_agent_is_still_reaped_immediately(self):
        env = brev_env.BrevEnvironment()
        env._instance_name = "vss-eval-test"
        marker = f"{brev_env.REMOTE_AGENT_RUN_PREFIX}{'b' * 32}"
        with (
            mock.patch.dict(
                brev_env.os.environ,
                {
                    brev_env.AGENT_RUN_MARKER_OVERRIDE_ENV: marker,
                    brev_env.DEFER_AGENT_REAP_ENV: "1",
                },
                clear=False,
            ),
            mock.patch.object(
                brev_env,
                "_run_brev_exec",
                new=mock.AsyncMock(
                    side_effect=[
                        brev_env.ExecResult(stderr="failed", return_code=1),
                        brev_env.ExecResult(stdout="reaped", return_code=0),
                    ]
                ),
            ) as run,
        ):
            result = await env.exec(
                "claude --verbose --output-format=stream-json --print"
            )

        self.assertEqual(result.return_code, 1)
        self.assertEqual(run.await_count, 2)
        self.assertIn(
            f"{brev_env.REMOTE_AGENT_RUN_ENV}={marker}",
            run.await_args_list[1].args[1],
        )

    async def test_fixture_staging_runs_before_setup_verifier_and_fails_closed(self):
        for staging_rc in (0, 1):
            env = brev_env.BrevEnvironment()
            env._instance_name = "vss-eval-test"
            outputs = [brev_env.ExecResult(return_code=0), brev_env.ExecResult(return_code=staging_rc)]
            if staging_rc:
                outputs.append(brev_env.ExecResult(return_code=0))
            with mock.patch.dict(os.environ, {
                brev_env.DEFER_AGENT_REAP_ENV: "1",
                "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
                "SKILL_EVAL_NEMOCLAW_FIXTURES": '["warehouse_safety_0001.mp4"]',
                "NEMOCLAW_SANDBOX_NAME": "se-current",
            }), mock.patch.object(brev_env, "_run_brev_exec", new=mock.AsyncMock(side_effect=outputs)) as run:
                result = await env.exec("codex exec --json")
            self.assertEqual(result.return_code, staging_rc)
            self.assertIn("stage_fixtures.py", run.await_args_list[1].args[1])
            marker = self._agent_marker_from_command(run.await_args_list[0].args[1])
            self.assertIn(f"{brev_env.REMOTE_AGENT_RUN_ENV}={marker}", run.await_args_list[1].args[1])
            self.assertIn("se-current", run.await_args_list[1].args[1])
            self.assertEqual(run.await_args_list[1].kwargs["timeout"], 375)
            self.assertEqual(run.await_count, 3 if staging_rc else 2)

    async def test_two_fixture_files_receive_sufficient_transfer_budget(self):
        env = brev_env.BrevEnvironment()
        env._instance_name = "vss-eval-test"
        with mock.patch.dict(os.environ, {
            brev_env.DEFER_AGENT_REAP_ENV: "1",
            "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
            "SKILL_EVAL_NEMOCLAW_FIXTURES": '["warehouse_sample.mp4", "sample-warehouse-ladder.mp4"]',
            "NEMOCLAW_SANDBOX_NAME": "se-current",
        }), mock.patch.object(brev_env, "_run_brev_exec", new=mock.AsyncMock(return_value=brev_env.ExecResult(return_code=0))) as run:
            result = await env.exec("codex exec --json")
        self.assertEqual(result.return_code, 0)
        self.assertEqual(run.await_args_list[1].kwargs["timeout"], 675)

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
    # Publish the pid atomically. write_text() creates a zero-length file
    # before the bytes land, so a waiter that gates on exists() can read ''.
    tmp = Path(sys.argv[1] + ".tmp")
    tmp.write_text(str(os.getpid()))
    os.replace(tmp, sys.argv[1])
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
            "codex.txt",
            "openclaw.txt",
            "openclaw.session.jsonl",
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
        self.assertIn("-mtime +7", command)

    def test_session_archive_handles_empty_hidden_and_mixed_entries(self):
        for case in ("missing", "empty", "hidden", "mixed"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "agent"
                root.mkdir()
                sessions = root / "sessions"
                archive = Path(tmp) / "archive"
                entries = {}
                if case != "missing":
                    sessions.mkdir()
                if case in ("hidden", "mixed"):
                    entries[".session marker"] = "hidden marker"
                    entries[".state/nested.jsonl"] = "hidden session"
                    (sessions / ".dangling").symlink_to("missing-target")
                if case == "mixed":
                    entries["-session\nwith space.jsonl"] = "visible session"
                    entries["2026/10/07/rollout.jsonl"] = "nested session"
                for name, content in entries.items():
                    path = sessions / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content)
                command = brev_env._prior_agent_output_archive_command()
                command = command.replace("/logs/agent", str(root)).replace(
                    "$HOME/.claude-archive", str(archive)
                )
                subprocess.run(["sh", "-c", command], check=True, capture_output=True)
                if sessions.exists():
                    self.assertEqual(list(sessions.iterdir()), [])
                if entries:
                    saved = list(archive.glob("*/sessions"))
                    self.assertEqual(len(saved), 1)
                    for name, content in entries.items():
                        self.assertEqual((saved[0] / name).read_text(), content)
                    self.assertTrue((saved[0] / ".dangling").is_symlink())
                    self.assertEqual(os.readlink(saved[0] / ".dangling"), "missing-target")
                else:
                    self.assertFalse(archive.exists())
                # A second trial with no new session files must be harmless.
                subprocess.run(["sh", "-c", command], check=True, capture_output=True)

    def test_session_archive_still_fails_on_real_move_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            sessions = root / "sessions"
            sessions.mkdir(parents=True)
            marker = sessions / ".session"
            marker.write_text("prior evidence")
            shim = Path(tmp) / "bin"
            shim.mkdir()
            (shim / "mv").write_text("#!/bin/sh\necho 'archive move refused' >&2\nexit 5\n")
            (shim / "mv").chmod(0o755)
            command = brev_env._prior_agent_output_archive_command()
            command = command.replace("/logs/agent", str(root)).replace(
                "$HOME/.claude-archive", str(Path(tmp) / "archive")
            )
            env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}")
            result = subprocess.run(["sh", "-c", command], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("archive move refused", result.stderr)
            self.assertEqual(marker.read_text(), "prior evidence")

    def test_mixed_harness_outputs_are_archived_before_failed_next_trial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            root.mkdir()
            outputs = {
                "codex.txt": "previous coding setup",
                "openclaw.txt": "previous operational envelope",
                "openclaw.session.jsonl": "previous operational session",
                "trajectory.json": "previous trajectory",
                "agent.log": "previous failure",
            }
            for name, content in outputs.items():
                (root / name).write_text(content)
            command = brev_env._prior_agent_output_archive_command()
            command = command.replace("/logs/agent", str(root)).replace(
                "$HOME/.claude-archive", str(Path(tmp) / "archive")
            )
            subprocess.run(["bash", "-c", command], check=True, capture_output=True)
            self.assertEqual(list(root.iterdir()), [])
            for name, content in outputs.items():
                copies = list((Path(tmp) / "archive").glob(f"*/root-output/{name}"))
                self.assertEqual(len(copies), 1)
                self.assertEqual(copies[0].read_text(), content)

    def test_codex_sessions_are_archived_even_when_next_launch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            sessions = root / "sessions"
            for name in ("2026/10/06/rollout-old.jsonl", "projects/project/old.jsonl"):
                path = sessions / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("previous successful setup")
            (root / "trajectory.json").write_text("previous trajectory")
            command = brev_env._prior_agent_output_archive_command()
            command = command.replace("/logs/agent", str(root)).replace(
                "$HOME/.claude-archive", str(Path(tmp) / "archive")
            )
            subprocess.run(["bash", "-c", command], check=True, capture_output=True)
            # A failed next launch produces no sessions: neither mapper can
            # discover the previous trial's deployment or token counts.
            self.assertEqual(list(sessions.rglob("*.jsonl")), [])
            self.assertFalse((root / "trajectory.json").exists())
            self.assertEqual(len(list((Path(tmp) / "archive").rglob("*.jsonl"))), 2)

    def test_transfer_wall_budget_includes_active_and_reap_windows(self):
        self.assertEqual(brev_env.BREV_TRANSFER_ACTIVE_TIMEOUT_SEC, 600)
        self.assertEqual(brev_env.BREV_TRANSFER_CANCELLATION_GRACE_SEC, 30)
        self.assertEqual(brev_env.BREV_TRANSFER_TOTAL_TIMEOUT_SEC, 630)
        self.assertEqual(brev_env.BREV_DOWNLOAD_PRIMARY_TIMEOUT_SEC, 480)
        self.assertEqual(brev_env.BREV_LOG_FALLBACK_TIMEOUT_SEC, 120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
