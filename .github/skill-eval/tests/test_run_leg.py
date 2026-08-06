#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for run_leg.py.

Run:
    python3 .github/skill-eval/tests/test_run_leg.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "run_leg", Path(__file__).resolve().parents[1] / "run_leg.py"
)
run_leg = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_leg
_SPEC.loader.exec_module(run_leg)


class DiscoverInvocations(unittest.TestCase):
    def test_discover_single_step_invocation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task_dir = root / "alerts_cv" / "rtxpro6000bw"
            task_dir.mkdir(parents=True)
            (task_dir / "task.toml").write_text("step_count = 1\n")

            invocations = run_leg.discover_invocations(root)

        self.assertEqual(len(invocations), 1)
        self.assertEqual(invocations[0].harbor_root.name, "alerts_cv")
        self.assertEqual(invocations[0].include_task_name, "rtxpro6000bw")
        self.assertIsNone(invocations[0].step_index)

    def test_discover_multi_step_invocations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            platform_dir = root / "foo" / "l40s"
            for step in (1, 2):
                step_dir = platform_dir / f"step-{step}"
                step_dir.mkdir(parents=True)
                (step_dir / "task.toml").write_text("step_count = 2\n")

            invocations = run_leg.discover_invocations(root)

        self.assertEqual(len(invocations), 2)
        self.assertEqual([i.include_task_name for i in invocations], ["step-1", "step-2"])
        self.assertTrue(all(i.harbor_root.name == "l40s" for i in invocations))
        self.assertEqual([i.step_index for i in invocations], [1, 2])
        self.assertEqual([i.step_count for i in invocations], [2, 2])

    def test_discover_multi_chain_invocations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for mode in ("remote-all", "standalone"):
                platform_dir = root / "spec" / f"l40s-{mode}"
                for step in (1, 2):
                    step_dir = platform_dir / f"step-{step}"
                    step_dir.mkdir(parents=True)
                    (step_dir / "task.toml").write_text("step_count = 2\n")

            invocations = run_leg.discover_invocations(root)

        self.assertEqual(len(invocations), 4)
        self.assertEqual(
            [(i.chain_key, i.include_task_name) for i in invocations],
            [
                ("spec_l40s-remote-all", "step-1"),
                ("spec_l40s-remote-all", "step-2"),
                ("spec_l40s-standalone", "step-1"),
                ("spec_l40s-standalone", "step-2"),
            ],
        )


class HarborCommand(unittest.TestCase):
    def test_build_command_uses_env_and_v1_suffix(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/alerts_cv"),
            include_task_name="rtxpro6000bw",
            chain_key="alerts_cv_rtxpro6000bw",
        )

        cmd = run_leg.build_harbor_command(
            invocation,
            Path("/tmp/results"),
            "aws/anthropic/bedrock-claude-opus-4-6",
            "https://inference-api.nvidia.com/v1",
        )

        self.assertEqual(run_leg.SKILL_EVAL_PYTHON_VERSION, (3, 12))
        self.assertEqual(run_leg.HARBOR_REQUIREMENT, "harbor==0.20.0")
        self.assertEqual(
            cmd[:7],
            [
                "uvx",
                "--python",
                run_leg.sys.executable,
                "--from",
                run_leg.HARBOR_REQUIREMENT,
                "harbor",
                "run",
            ],
        )
        self.assertIn("--include-task-name", cmd)
        self.assertEqual(cmd[cmd.index("--include-task-name") + 1], "rtxpro6000bw")
        self.assertEqual(cmd[cmd.index("-a") + 1], "claude-code")
        self.assertEqual(cmd[cmd.index("--model") + 1], "aws/anthropic/bedrock-claude-opus-4-6")
        self.assertEqual(cmd[cmd.index("--ak") + 1], "api_base=https://inference-api.nvidia.com/v1")
        self.assertEqual(cmd[cmd.index("-o") + 1], "/tmp/results")
        self.assertEqual(
            cmd[cmd.index("--environment-build-timeout-multiplier") + 1],
            str(run_leg.HARBOR_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER),
        )
        self.assertEqual(
            cmd[cmd.index("--agent-timeout-multiplier") + 1],
            str(run_leg.HARBOR_AGENT_TIMEOUT_MULTIPLIER),
        )
        self.assertEqual(
            cmd[cmd.index("--verifier-timeout-multiplier") + 1],
            str(run_leg.HARBOR_VERIFIER_TIMEOUT_MULTIPLIER),
        )

    def test_build_command_codex_agent(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/alerts_cv"),
            include_task_name="rtxpro6000bw",
            chain_key="alerts_cv_rtxpro6000bw",
        )

        cmd = run_leg.build_harbor_command(
            invocation,
            Path("/tmp/results"),
            "openai/openai/gpt-5-codex",
            "https://inference-api.nvidia.com/v1",
            "codex",
        )

        # codex runs through the NvCodex subclass (keeps the full model id);
        # endpoint via --ak api_base, key from the env (not on the CLI).
        self.assertEqual(cmd[cmd.index("-a") + 1], "agents.nv_codex:NvCodex")
        self.assertEqual(cmd[cmd.index("--model") + 1], "openai/openai/gpt-5-codex")
        self.assertEqual(cmd[cmd.index("--ak") + 1], "api_base=https://inference-api.nvidia.com/v1")
        # The key must never be passed on the command line.
        self.assertFalse(any("OPENAI_API_KEY" in part for part in cmd))
        self.assertNotIn("CLAUDE_CODE_DISABLE_THINKING=1", cmd)

    def test_build_command_rejects_unknown_agent(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/alerts_cv"),
            include_task_name="rtxpro6000bw",
            chain_key="alerts_cv_rtxpro6000bw",
        )
        with self.assertRaises(ValueError):
            run_leg.build_harbor_command(
                invocation, Path("/tmp/results"), "m", "https://x/v1", "Codex"
            )


class PhaseBudgets(unittest.TestCase):
    def test_default_backstop_exceeds_all_phases_and_recovery_headroom(self):
        self.assertEqual(run_leg.HARBOR_ENVIRONMENT_BUILD_BUDGET_SEC, 1800)
        self.assertEqual(run_leg.HARBOR_AGENT_SETUP_BUDGET_SEC, 360)
        self.assertEqual(run_leg.HARBOR_AGENT_BUDGET_SEC, 3600)
        self.assertEqual(run_leg.HARBOR_VERIFIER_BUDGET_SEC, 1800)
        self.assertEqual(run_leg.HARBOR_PHASE_BUDGET_SEC, 7560)
        self.assertEqual(run_leg.HARBOR_TRANSFER_OPERATION_BUDGET_SEC, 630)
        self.assertEqual(run_leg.HARBOR_RECOVERY_TRANSFER_OPERATION_COUNT, 4)
        self.assertEqual(run_leg.HARBOR_CLEANUP_RECOVERY_HEADROOM_SEC, 2520)
        self.assertEqual(
            run_leg.MIN_HARBOR_BACKSTOP_SEC,
            run_leg.HARBOR_PHASE_BUDGET_SEC
            + run_leg.HARBOR_CLEANUP_RECOVERY_HEADROOM_SEC,
        )
        self.assertEqual(run_leg.MIN_HARBOR_BACKSTOP_SEC, 10080)
        self.assertEqual(run_leg.DEFAULT_HARBOR_TIMEOUT_SEC, 12000)
        self.assertEqual(run_leg.HARBOR_SIGINT_GRACE_SEC, 1380)
        self.assertEqual(run_leg.HARBOR_SHUTDOWN_GRACE_SEC, 1420)
        self.assertEqual(
            run_leg.invocation_reserve_sec(run_leg.DEFAULT_HARBOR_TIMEOUT_SEC),
            13480,
        )
        self.assertGreater(
            run_leg.DEFAULT_HARBOR_TIMEOUT_SEC,
            run_leg.MIN_HARBOR_BACKSTOP_SEC,
        )
        self.assertGreater(
            run_leg.MIN_BREV_EXEC_TIMEOUT_SEC,
            run_leg.HARBOR_AGENT_BUDGET_SEC,
        )

    def test_timeout_validation_rejects_boundary_and_accepts_default(self):
        with self.assertRaisesRegex(ValueError, "cleanup/recovery"):
            run_leg.validate_harbor_timeout_sec(
                run_leg.MIN_HARBOR_BACKSTOP_SEC
            )

        self.assertEqual(
            run_leg.validate_harbor_timeout_sec(
                run_leg.DEFAULT_HARBOR_TIMEOUT_SEC
            ),
            run_leg.DEFAULT_HARBOR_TIMEOUT_SEC,
        )

    def test_parse_args_uses_validated_default_and_rejects_short_override(self):
        required = [
            "--dataset-root", "/tmp/data",
            "--results-root", "/tmp/results",
        ]
        args = run_leg.parse_args(required)
        self.assertEqual(
            args.harbor_timeout_sec, run_leg.DEFAULT_HARBOR_TIMEOUT_SEC
        )

        with mock.patch.object(run_leg.sys, "stderr"):
            with self.assertRaises(SystemExit) as raised:
                run_leg.parse_args(
                    required
                    + [
                        "--harbor-timeout-sec",
                        str(run_leg.MIN_HARBOR_BACKSTOP_SEC),
                    ]
                )
        self.assertEqual(raised.exception.code, 2)

    def test_parse_args_preserves_valid_timeout_env_overrides(self):
        required = [
            "--dataset-root", "/tmp/data",
            "--results-root", "/tmp/results",
        ]
        with mock.patch.dict(
            run_leg.os.environ,
            {
                "SKILL_EVAL_LOCK_TIMEOUT_SEC": "123",
                "SKILL_EVAL_HARBOR_TIMEOUT_SEC": "13000",
            },
            clear=False,
        ):
            args = run_leg.parse_args(required)

        self.assertEqual(args.lock_timeout_sec, 123)
        self.assertEqual(args.harbor_timeout_sec, 13000)

    def test_agent_deadline_is_inherited_and_expired_values_fail_closed(self):
        with (
            mock.patch.dict(
                run_leg.os.environ,
                {run_leg.WORK_DEADLINE_ENV: "12345.5"},
                clear=True,
            ),
            mock.patch.object(run_leg.time, "monotonic", return_value=10000.0),
        ):
            self.assertEqual(run_leg.resolve_work_deadline(), 12345.5)

        with (
            mock.patch.dict(
                run_leg.os.environ,
                {run_leg.WORK_DEADLINE_ENV: "9999"},
                clear=True,
            ),
            mock.patch.object(run_leg.time, "monotonic", return_value=10000.0),
            self.assertRaises(run_leg.LegDeadlineError),
        ):
            run_leg.resolve_work_deadline()

    def test_sdk_deadline_fallback_reserves_agent_verdict_window(self):
        with (
            mock.patch.dict(
                run_leg.os.environ,
                {run_leg.SDK_DEADLINE_ENV: "15000"},
                clear=True,
            ),
            mock.patch.object(run_leg.time, "monotonic", return_value=10000.0),
        ):
            self.assertEqual(
                run_leg.resolve_work_deadline(),
                15000 - run_leg.AGENT_VERDICT_RESERVE_SEC,
            )


class BrevAuthenticationFailures(unittest.TestCase):
    AUTH_TRACE = (
        "github.com/brevdev/brev-cli/pkg/auth.Auth.PromptForLogin\n"
        "/go/src/github.com/brevdev/brev-cli/pkg/auth/auth.go:247\n"
        ": [error]\n"
        "github.com/brevdev/brev-cli/pkg/auth.shouldLogin\n"
        ": EOF\nEOF\n"
    )

    def test_managed_inventory_fails_fast_on_headless_login_eof(self):
        result = subprocess.CompletedProcess(
            ["brev", "ls", "--json"],
            1,
            "",
            self.AUTH_TRACE,
        )
        with (
            mock.patch.object(
                run_leg.subprocess,
                "run",
                return_value=result,
            ) as run,
            mock.patch.object(run_leg.time, "sleep") as sleep,
            self.assertRaisesRegex(
                run_leg.BrevAuthenticationError,
                "PromptForLogin reached EOF",
            ),
        ):
            run_leg._list_brev_instances()

        run.assert_called_once()
        sleep.assert_not_called()

    def test_registered_inventory_fails_fast_on_headless_login_eof(self):
        result = subprocess.CompletedProcess(
            ["brev", "ls", "nodes", "--json"],
            1,
            "",
            self.AUTH_TRACE,
        )
        with (
            mock.patch.object(
                run_leg.subprocess,
                "run",
                return_value=result,
            ) as run,
            mock.patch.object(run_leg.time, "sleep") as sleep,
            self.assertRaisesRegex(
                run_leg.BrevAuthenticationError,
                "PromptForLogin reached EOF",
            ),
        ):
            run_leg._list_registered_nodes()

        run.assert_called_once()
        sleep.assert_not_called()

    def test_generic_transport_eof_is_not_classified_as_auth(self):
        reason = run_leg._brev_auth_failure_reason(
            "",
            "rpc error: error reading from server: EOF",
            "brev exec worker",
        )

        self.assertIsNone(reason)


class HarborEnvironment(unittest.TestCase):
    def test_brev_exec_timeout_outlives_harbor_agent_budget(self):
        with mock.patch.dict(
            run_leg.os.environ, {"BREV_EXEC_TIMEOUT": "60"}, clear=True
        ):
            env = run_leg.harbor_env("vss-eval-box")

        self.assertEqual(env["BREV_INSTANCE"], "vss-eval-box")
        self.assertEqual(
            int(env["BREV_EXEC_TIMEOUT"]), run_leg.MIN_BREV_EXEC_TIMEOUT_SEC
        )
        self.assertGreater(
            int(env["BREV_EXEC_TIMEOUT"]), run_leg.HARBOR_AGENT_BUDGET_SEC
        )
        self.assertEqual(
            int(env["BREV_TRANSFER_TOTAL_TIMEOUT_SEC"]),
            run_leg.HARBOR_TRANSFER_OPERATION_BUDGET_SEC,
        )

    def test_brev_exec_timeout_preserves_a_larger_operator_cap(self):
        configured = run_leg.MIN_BREV_EXEC_TIMEOUT_SEC + 123
        with mock.patch.dict(
            run_leg.os.environ,
            {"BREV_EXEC_TIMEOUT": str(configured)},
            clear=True,
        ):
            env = run_leg.harbor_env("vss-eval-box")

        self.assertEqual(int(env["BREV_EXEC_TIMEOUT"]), configured)

    def test_transfer_timeout_overrides_a_larger_inherited_cap(self):
        with mock.patch.dict(
            run_leg.os.environ,
            {"BREV_TRANSFER_TOTAL_TIMEOUT_SEC": "9999"},
            clear=True,
        ):
            env = run_leg.harbor_env("vss-eval-box")

        self.assertEqual(
            int(env["BREV_TRANSFER_TOTAL_TIMEOUT_SEC"]),
            run_leg.HARBOR_TRANSFER_OPERATION_BUDGET_SEC,
        )


class RunCommand(unittest.TestCase):
    COMMAND = ["uvx", "harbor", "run"]
    ENV = {"BREV_INSTANCE": "vss-eval-box"}

    @staticmethod
    def _expired(timeout):
        return run_leg.subprocess.TimeoutExpired(RunCommand.COMMAND, timeout)

    def test_normal_exit_returns_child_status_without_signaling(self):
        proc = mock.Mock(pid=4321)
        proc.wait.return_value = 7
        with (
            mock.patch.object(run_leg.subprocess, "Popen", return_value=proc),
            mock.patch.object(run_leg.os, "killpg") as killpg,
        ):
            rc = run_leg.run_command(self.COMMAND, self.ENV, timeout_sec=42)

        self.assertEqual(rc, 7)
        proc.wait.assert_called_once_with(timeout=42)
        killpg.assert_not_called()

    def test_signal_exit_is_normalized_and_reaps_remaining_tree(self):
        proc = mock.Mock(pid=4321)
        proc.wait.return_value = -run_leg.signal.SIGTERM
        with (
            mock.patch.object(run_leg.subprocess, "Popen", return_value=proc),
            mock.patch.object(
                run_leg, "_cancel_process_tree", return_value=True
            ) as cancel_tree,
        ):
            rc = run_leg.run_command(self.COMMAND, self.ENV, timeout_sec=42)

        self.assertEqual(rc, 128 + run_leg.signal.SIGTERM)
        cancel_tree.assert_called_once_with(proc, 4321, mock.ANY)

    def test_timeout_uses_sigint_first_and_keeps_timeout_outcome(self):
        proc = mock.Mock(pid=4321)
        proc.wait.side_effect = [self._expired(42)]
        with (
            mock.patch.object(run_leg.subprocess, "Popen", return_value=proc),
            mock.patch.object(run_leg.os, "killpg") as killpg,
            mock.patch.object(
                run_leg, "_wait_for_process_group_exit", return_value=True
            ) as wait_group,
        ):
            rc = run_leg.run_command(self.COMMAND, self.ENV, timeout_sec=42)

        self.assertEqual(rc, 124)
        proc.wait.assert_called_once_with(timeout=42)
        wait_group.assert_called_once_with(
            proc,
            4321,
            run_leg.HARBOR_SIGINT_GRACE_SEC,
            mock.ANY,
        )
        killpg.assert_called_once_with(4321, run_leg.signal.SIGINT)

    def test_timeout_escalates_from_sigint_to_sigterm(self):
        proc = mock.Mock(pid=4321)
        proc.wait.side_effect = [self._expired(42)]
        with (
            mock.patch.object(run_leg.subprocess, "Popen", return_value=proc),
            mock.patch.object(run_leg.os, "killpg") as killpg,
            mock.patch.object(
                run_leg,
                "_wait_for_process_group_exit",
                side_effect=[False, True],
            ) as wait_group,
        ):
            rc = run_leg.run_command(self.COMMAND, self.ENV, timeout_sec=42)

        self.assertEqual(rc, 124)
        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(4321, run_leg.signal.SIGINT),
                mock.call(4321, run_leg.signal.SIGTERM),
            ],
        )
        self.assertEqual(
            wait_group.call_args_list,
            [
                mock.call(
                    proc,
                    4321,
                    run_leg.HARBOR_SIGINT_GRACE_SEC,
                    mock.ANY,
                ),
                mock.call(
                    proc,
                    4321,
                    run_leg.HARBOR_SIGTERM_GRACE_SEC,
                    mock.ANY,
                ),
            ],
        )

    def test_timeout_escalates_through_sigkill_with_bounded_waits(self):
        proc = mock.Mock(pid=4321)
        proc.wait.side_effect = [self._expired(42)]
        with (
            mock.patch.object(run_leg.subprocess, "Popen", return_value=proc),
            mock.patch.object(run_leg.os, "killpg") as killpg,
            mock.patch.object(
                run_leg,
                "_wait_for_process_group_exit",
                side_effect=[False, False, False],
            ) as wait_group,
        ):
            rc = run_leg.run_command(self.COMMAND, self.ENV, timeout_sec=42)

        self.assertEqual(rc, 124)
        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(4321, run_leg.signal.SIGINT),
                mock.call(4321, run_leg.signal.SIGTERM),
                mock.call(4321, run_leg.signal.SIGKILL),
            ],
        )
        self.assertEqual(
            wait_group.call_args_list[-1],
            mock.call(
                proc,
                4321,
                run_leg.HARBOR_SIGKILL_GRACE_SEC,
                mock.ANY,
            ),
        )

    def test_external_sigterm_is_forwarded_and_preserves_signal_status(self):
        proc = mock.Mock(pid=4321)
        proc.wait.side_effect = run_leg._RunCommandInterrupted(
            run_leg.signal.SIGTERM
        )
        with (
            mock.patch.object(run_leg.subprocess, "Popen", return_value=proc),
            mock.patch.object(
                run_leg, "_cancel_process_tree", return_value=True
            ) as cancel_tree,
        ):
            rc = run_leg.run_command(self.COMMAND, self.ENV, timeout_sec=42)

        self.assertEqual(rc, 128 + run_leg.signal.SIGTERM)
        cancel_tree.assert_called_once_with(proc, 4321, mock.ANY)

    def test_signal_during_post_wait_group_scan_still_cleans_child_tree(self):
        proc = mock.Mock(pid=4321)
        proc.wait.return_value = 0
        with (
            mock.patch.object(run_leg.subprocess, "Popen", return_value=proc),
            mock.patch.object(
                run_leg,
                "_registered_transport_groups",
                side_effect=run_leg._RunCommandInterrupted(
                    run_leg.signal.SIGTERM
                ),
            ),
            mock.patch.object(
                run_leg, "_cancel_process_tree", return_value=True
            ) as cancel_tree,
        ):
            rc = run_leg.run_command(self.COMMAND, self.ENV, timeout_sec=42)

        self.assertEqual(rc, 128 + run_leg.signal.SIGTERM)
        cancel_tree.assert_called_once_with(proc, 4321, mock.ANY)

    def test_repeated_signal_during_timeout_teardown_does_not_skip_cleanup(self):
        proc = mock.Mock(pid=4321)
        proc.wait.side_effect = [self._expired(42)]

        def cancel_tree(_proc, _pgid, _registry):
            handler = run_leg.signal.getsignal(run_leg.signal.SIGTERM)
            self.assertEqual(handler, run_leg.signal.SIG_IGN)
            return True

        with (
            mock.patch.object(run_leg.subprocess, "Popen", return_value=proc),
            mock.patch.object(run_leg, "_cancel_process_tree", side_effect=cancel_tree),
        ):
            rc = run_leg.run_command(self.COMMAND, self.ENV, timeout_sec=42)

        self.assertEqual(rc, 124)


class ProcessGroupShutdown(unittest.TestCase):
    def test_leader_exit_is_not_enough_when_group_still_exists(self):
        proc = mock.Mock(pid=4321)
        proc.wait.return_value = 0
        with (
            mock.patch.object(run_leg, "_process_group_exists", return_value=True),
            mock.patch.object(run_leg.time, "monotonic", side_effect=[10.0, 10.0]),
        ):
            exited = run_leg._wait_for_process_group_exit(proc, 4321, 0)

        self.assertFalse(exited)
        proc.wait.assert_called_once_with(timeout=0)

    def test_wait_succeeds_only_after_group_probe_reports_gone(self):
        proc = mock.Mock(pid=4321)
        proc.wait.return_value = 0
        with mock.patch.object(
            run_leg, "_process_group_exists", return_value=False
        ) as group_exists:
            exited = run_leg._wait_for_process_group_exit(proc, 4321, 1)

        self.assertTrue(exited)
        group_exists.assert_called_once_with(4321)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires /proc")
    def test_registry_keeps_tracking_group_after_registered_leader_exits(self):
        leader = """
import os
import signal
import sys
import time

signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
pid = os.fork()
if pid == 0:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    time.sleep(30)
    os._exit(0)
print("ready", flush=True)
time.sleep(30)
"""
        with tempfile.TemporaryDirectory() as td:
            registry = Path(td) / "registry"
            registry.touch()
            env = run_leg.os.environ.copy()
            env[run_leg.TRANSPORT_PGID_REGISTRY_ENV] = str(registry)
            proc = run_leg.subprocess.Popen(
                [sys.executable, "-c", leader],
                stdout=run_leg.subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
            pgid = proc.pid
            try:
                self.assertEqual(proc.stdout.readline().strip(), "ready")
                start_ticks = run_leg._process_start_ticks(pgid)
                self.assertIsNotNone(start_ticks)
                registry.write_text(f"{pgid} {start_ticks}\n")
                run_leg.os.kill(proc.pid, run_leg.signal.SIGINT)
                proc.wait(timeout=2)
                self.assertIsNone(run_leg._process_start_ticks(pgid))
                self.assertEqual(
                    run_leg._registered_transport_groups(registry),
                    [pgid],
                )
            finally:
                with contextlib.suppress(ProcessLookupError):
                    run_leg.os.killpg(pgid, run_leg.signal.SIGKILL)
                with contextlib.suppress(run_leg.subprocess.TimeoutExpired):
                    proc.wait(timeout=2)
                if proc.stdout is not None:
                    proc.stdout.close()

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires POSIX groups")
    def test_real_group_survives_sigint_leader_exit_then_dies_on_sigterm(self):
        leader = """
import os
import signal
import sys
import time

signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
read_fd, write_fd = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(read_fd)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    os.write(write_fd, b"1")
    os.close(write_fd)
    time.sleep(30)
    os._exit(0)

os.close(write_fd)
os.read(read_fd, 1)
os.close(read_fd)
print("ready", flush=True)
time.sleep(30)
"""
        proc = run_leg.subprocess.Popen(
            [sys.executable, "-c", leader],
            stdout=run_leg.subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        pgid = proc.pid
        try:
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            self.assertFalse(
                run_leg._signal_process_group_and_wait(
                    proc, pgid, run_leg.signal.SIGINT, 0.2
                )
            )
            self.assertTrue(run_leg._process_group_exists(pgid))
            self.assertTrue(
                run_leg._signal_process_group_and_wait(
                    proc, pgid, run_leg.signal.SIGTERM, 2
                )
            )
            self.assertFalse(run_leg._process_group_exists(pgid))
        finally:
            try:
                run_leg.os.killpg(pgid, run_leg.signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except run_leg.subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()


class RunInvocations(unittest.TestCase):
    ENV = {
        "ANTHROPIC_MODEL": "aws/anthropic/bedrock-claude-opus-4-6",
        "ANTHROPIC_BASE_URL": "https://inference-api.nvidia.com/v1",
    }

    def test_timeout_stops_all_single_step_invocations(self):
        invocations = [
            run_leg.HarborInvocation(
                harbor_root=Path(f"/tmp/datasets/spec-{index}"),
                include_task_name=f"task-{index}",
                chain_key=f"spec-{index}",
            )
            for index in (1, 2)
        ]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with (
                mock.patch.dict(run_leg.os.environ, self.ENV, clear=True),
                mock.patch.object(run_leg, "harbor_env", return_value={}),
                mock.patch.object(
                    run_leg, "build_harbor_command", return_value=["harbor"]
                ),
                mock.patch.object(run_leg, "run_command", return_value=124) as run,
                mock.patch.object(run_leg, "publish_trace", return_value=None),
            ):
                rc = run_leg.run_invocations(
                    invocations,
                    "vss-eval-box",
                    root / "results",
                    root / "scratch",
                    "spec",
                    "RTXPRO6000BW",
                    run_leg.DEFAULT_HARBOR_TIMEOUT_SEC,
                )

        self.assertEqual(rc, 124)
        run.assert_called_once()

    def test_chain_timeout_writes_skip_markers_before_stopping(self):
        invocations = [
            run_leg.HarborInvocation(
                harbor_root=Path("/tmp/datasets/spec"),
                include_task_name=f"step-{index}",
                chain_key="spec_rtx",
                step_index=index,
                step_count=3,
            )
            for index in (1, 2, 3)
        ]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scratch = root / "scratch"
            with (
                mock.patch.dict(run_leg.os.environ, self.ENV, clear=True),
                mock.patch.object(run_leg, "harbor_env", return_value={}),
                mock.patch.object(
                    run_leg, "build_harbor_command", return_value=["harbor"]
                ),
                mock.patch.object(run_leg, "run_command", return_value=124) as run,
                mock.patch.object(run_leg, "publish_trace", return_value=None),
            ):
                rc = run_leg.run_invocations(
                    invocations,
                    "vss-eval-box",
                    root / "results",
                    scratch,
                    "search",
                    "RTXPRO6000BW",
                    run_leg.DEFAULT_HARBOR_TIMEOUT_SEC,
                )

            step2 = scratch / "skipped-search-RTXPRO6000BW-step-2.txt"
            step3 = scratch / "skipped-search-RTXPRO6000BW-step-3.txt"
            self.assertTrue(step2.is_file())
            self.assertTrue(step3.is_file())
            self.assertIn("reward=missing", step2.read_text())

        self.assertEqual(rc, 124)
        run.assert_called_once()

    def test_whole_leg_deadline_refuses_unfunded_step_and_marks_chain(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/spec"),
            include_task_name="step-1",
            chain_key="spec_rtx",
            step_index=1,
            step_count=3,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scratch = root / "scratch"
            with (
                mock.patch.dict(run_leg.os.environ, self.ENV, clear=True),
                mock.patch.object(run_leg, "harbor_env", return_value={}),
                mock.patch.object(run_leg, "run_command") as run,
                mock.patch.object(run_leg.time, "monotonic", return_value=100.0),
            ):
                rc = run_leg.run_invocations(
                    [invocation],
                    "vss-eval-box",
                    root / "results",
                    scratch,
                    "search",
                    "RTXPRO6000BW",
                    run_leg.DEFAULT_HARBOR_TIMEOUT_SEC,
                    100.0
                    + run_leg.invocation_reserve_sec(
                        run_leg.DEFAULT_HARBOR_TIMEOUT_SEC
                    )
                    - 1,
                )

            self.assertEqual(rc, 124)
            run.assert_not_called()
            for step in (1, 2, 3):
                marker = scratch / f"skipped-search-RTXPRO6000BW-step-{step}.txt"
                self.assertIn("whole-leg-deadline", marker.read_text())


class SkipMarkers(unittest.TestCase):
    def test_latest_reward_ignores_prior_chain_reward_when_since_is_set(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reward = root / "2026-06-04" / "step-1__old" / "verifier" / "reward.txt"
            reward.parent.mkdir(parents=True)
            reward.write_text("1.0\n")
            since = time.time() + 10

            self.assertIsNone(run_leg.latest_reward(root, "step-1", started_at=since))

    def test_write_skip_markers(self):
        with tempfile.TemporaryDirectory() as td:
            scratch = Path(td)
            run_leg.write_skip_markers(
                scratch,
                spec_stem="vios_ops",
                platform="L40S",
                failed_step=2,
                reward="0.2",
                step_count=4,
            )

            step3 = scratch / "skipped-vios_ops-L40S-step-3.txt"
            step4 = scratch / "skipped-vios_ops-L40S-step-4.txt"
            self.assertTrue(step3.exists())
            self.assertTrue(step4.exists())
            self.assertEqual(
                step3.read_text().strip(),
                "skipped (prior-step fail, step=2 reward=0.2)",
            )


class TraceUrls(unittest.TestCase):
    """Regression cover for the blank-Harbor-page bug.

    PR #1254 / run 30284131217 shipped seven trace links whose final
    segment was the `--include-task-name` filter (`step-7`) instead of
    Harbor's `task_name`. The viewer is a client-side SPA, so every one
    of them opened as an empty page instead of erroring.
    """

    # Shape mirrors a real trial's result.json.
    RESULT = {
        "task_name": "nvidia-vss/vss-generate-video-report-base-l40s-step-7",
        "trial_name": "step-7__E6dBECL",
        "source": "l40s",
        "agent_info": {
            "name": "claude-code",
            "model_info": {
                "name": "anthropic/bedrock-claude-opus-4-6",
                "provider": "aws",
            },
        },
    }
    JOB = (
        "vss-generate-video-report__base_profile_report__L40S"
        "__30284131217__2026-07-27__17-16-47"
    )

    def setUp(self):
        self._orig_env = os.environ.get("BREV_ENV_ID")
        os.environ["BREV_ENV_ID"] = "13xh5gpe7"

    def tearDown(self):
        if self._orig_env is None:
            os.environ.pop("BREV_ENV_ID", None)
        else:
            os.environ["BREV_ENV_ID"] = self._orig_env

    def _write_result(self, directory: Path, payload=None) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        result = directory / "result.json"
        result.write_text(json.dumps(payload if payload is not None else self.RESULT))
        return result

    def test_trace_url_matches_the_viewer_route(self):
        with tempfile.TemporaryDirectory() as td:
            result = self._write_result(Path(td) / "step-7__E6dBECL")
            url = run_leg.trace_url(result, self.JOB)

        self.assertEqual(
            url,
            "https://harbor-13xh5gpe7.brevlab.com/jobs/"
            + self.JOB
            + "/tasks/l40s/claude-code/aws"
            + "/anthropic%2Fbedrock-claude-opus-4-6"
            + "/nvidia-vss%2Fvss-generate-video-report-base-l40s-step-7",
        )

    def test_trace_url_never_ends_in_the_include_task_filter(self):
        """The exact regression: a bare `step-7` tail renders a blank page."""
        with tempfile.TemporaryDirectory() as td:
            result = self._write_result(Path(td) / "step-7__E6dBECL")
            url = run_leg.trace_url(result, self.JOB)

        self.assertFalse(url.endswith("/step-7"))
        self.assertTrue(url.endswith("-step-7"))
        # Slashes inside <model>/<task> must be segments, not path levels.
        self.assertEqual(url.count("%2F"), 2)

    def test_trace_url_none_on_incomplete_result(self):
        with tempfile.TemporaryDirectory() as td:
            partial = dict(self.RESULT)
            partial.pop("task_name")
            result = self._write_result(Path(td) / "step-7__X", partial)

            self.assertIsNone(run_leg.trace_url(result, self.JOB))
            self.assertIsNone(run_leg.trace_url(Path(td) / "missing.json", self.JOB))

    def test_publish_trace_flattens_into_viewer_and_records_url(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/base/l40s"),
            include_task_name="step-7",
            chain_key="base_l40s",
            step_index=7,
            step_count=8,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            results_root = root / "results" / "leg" / "30284131217"
            trial = results_root / "2026-07-27__17-16-47" / "step-7__E6dBECL"
            self._write_result(trial)
            viewer_root = root / "_viewer"
            orig_viewer = run_leg.VIEWER_ROOT
            run_leg.VIEWER_ROOT = viewer_root
            try:
                url = run_leg.publish_trace(
                    results_root, invocation, 0.0, "leg", "30284131217"
                )
            finally:
                run_leg.VIEWER_ROOT = orig_viewer

            job_dir = viewer_root / "leg__30284131217__2026-07-27__17-16-47"
            # Flattened: the trial sits at the job's top level, with no
            # intervening <date>/ level for the viewer to miss.
            self.assertTrue((job_dir / "step-7__E6dBECL" / "result.json").is_file())
            self.assertFalse((job_dir / "2026-07-27__17-16-47").exists())
            # Copy, not move — the workflow collector still tars results_root.
            self.assertTrue((trial / "result.json").is_file())

            row = (results_root / "trace-urls.tsv").read_text().strip().split("\t")

        self.assertEqual(row[0], "step-7")
        self.assertEqual(row[1], "step-7__E6dBECL")
        self.assertEqual(row[2], url)
        self.assertIn("/jobs/leg__30284131217__2026-07-27__17-16-47/tasks/", url)

    def test_publish_trace_returns_none_when_trial_produced_no_result(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/base/l40s"),
            include_task_name="step-1",
            chain_key="base_l40s",
            step_index=1,
            step_count=8,
        )
        with tempfile.TemporaryDirectory() as td:
            results_root = Path(td) / "results"
            results_root.mkdir(parents=True)

            self.assertIsNone(
                run_leg.publish_trace(results_root, invocation, 0.0, "leg", "1")
            )
            self.assertFalse((results_root / "trace-urls.tsv").exists())


class CachedManagedSSH(unittest.TestCase):
    AUTH_ERROR = run_leg.BrevAuthenticationError("expired")

    def setUp(self):
        self.original_transports = dict(run_leg._WORKER_TRANSPORTS)
        self.original_configs = dict(run_leg._WORKER_SSH_CONFIGS)

    def tearDown(self):
        run_leg._WORKER_TRANSPORTS.clear()
        run_leg._WORKER_TRANSPORTS.update(self.original_transports)
        run_leg._WORKER_SSH_CONFIGS.clear()
        run_leg._WORKER_SSH_CONFIGS.update(self.original_configs)

    @staticmethod
    def _write_config(root: Path, source: str, mode: int = 0o600) -> Path:
        brev_home = root / ".brev"
        brev_home.mkdir(mode=0o700)
        config = brev_home / "ssh_config"
        config.write_text(source, encoding="utf-8")
        config.chmod(mode)
        return config

    def test_cache_requires_explicit_opt_in_and_allowlist(self):
        with tempfile.TemporaryDirectory() as td:
            config = self._write_config(
                Path(td),
                "Host vss-eval-rtx-2g-2\n  HostName 192.0.2.10\n",
            )
            base_env = {
                "BREV_SSH_CONFIG": str(config),
                "BREV_DIRECT_SSH_POOL": "vss-eval-rtx-2g-2",
            }
            with mock.patch.dict(os.environ, base_env, clear=True):
                self.assertEqual(run_leg._cached_managed_ssh_candidates(), {})

            enabled_without_allowlist = {
                "BREV_SSH_CONFIG": str(config),
                "BREV_ALLOW_CACHED_SSH": "1",
            }
            with mock.patch.dict(
                os.environ, enabled_without_allowlist, clear=True
            ):
                self.assertEqual(run_leg._cached_managed_ssh_candidates(), {})

    def test_cache_intersects_only_exact_safe_host_aliases(self):
        with tempfile.TemporaryDirectory() as td:
            config = self._write_config(
                Path(td),
                "\n".join(
                    (
                        "Host VSS-EVAL-RTX-2G-2",
                        "  HostName 192.0.2.10",
                        "Host vss-eval-rtx-2g-5-host",
                        "Host unrelated-host",
                    )
                ),
            )
            env = {
                "BREV_ALLOW_CACHED_SSH": "true",
                "BREV_DIRECT_SSH_POOL": (
                    "vss-eval-rtx-2g-2,vss-eval-rtx-2g-3,"
                    "vss-eval-rtx-2g-5-host,unrelated-host"
                ),
                "BREV_SSH_CONFIG": str(config),
            }
            with mock.patch.dict(os.environ, env, clear=True):
                # One unsafe operator entry rejects the entire allowlist.
                self.assertEqual(run_leg._cached_managed_ssh_candidates(), {})

                os.environ["BREV_DIRECT_SSH_POOL"] = (
                    "vss-eval-rtx-2g-2,vss-eval-rtx-2g-3"
                )
                candidates = run_leg._cached_managed_ssh_candidates()

        self.assertEqual(candidates, {"vss-eval-rtx-2g-2": config})

    def test_cache_rejects_patterns_and_executable_proxy_stanzas(self):
        with tempfile.TemporaryDirectory() as td:
            config = self._write_config(
                Path(td),
                "\n".join(
                    (
                        "Host *",
                        "  ProxyCommand /tmp/untrusted-proxy %h %p",
                        "Host vss-eval-rtx-2g-2",
                        "  HostName 192.0.2.10",
                    )
                ),
            )
            env = {
                "BREV_ALLOW_CACHED_SSH": "1",
                "BREV_DIRECT_SSH_POOL": "vss-eval-rtx-2g-2",
                "BREV_SSH_CONFIG": str(config),
            }
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(run_leg._cached_managed_ssh_candidates(), {})

            safe_stanza = (
                "Host vss-eval-rtx-2g-2\n"
                "  HostName 192.0.2.10\n"
            )
            unsafe_stanza = (
                "Host vss-eval-rtx-2g-2\n"
                "  ProxyCommand /tmp/untrusted-proxy %h %p\n"
            )
            for source in (
                safe_stanza + unsafe_stanza,
                unsafe_stanza + safe_stanza,
            ):
                config.write_text(source, encoding="utf-8")
                with mock.patch.dict(os.environ, env, clear=True):
                    self.assertEqual(
                        run_leg._cached_managed_ssh_candidates(),
                        {},
                    )

            config.write_text(
                "Host vss-eval-rtx-2g-2\n"
                "  ProxyCommand /tmp/untrusted-proxy %h %p\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(run_leg._cached_managed_ssh_candidates(), {})

    def test_cache_rejects_writable_or_symlinked_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._write_config(
                root,
                "Host vss-eval-rtx-2g-2\n  HostName 192.0.2.10\n",
                mode=0o666,
            )
            env = {
                "BREV_ALLOW_CACHED_SSH": "1",
                "BREV_DIRECT_SSH_POOL": "vss-eval-rtx-2g-2",
                "BREV_SSH_CONFIG": str(config),
            }
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(run_leg._cached_managed_ssh_candidates(), {})

            config.chmod(0o600)
            link = root / "cached-config-link"
            link.symlink_to(config)
            env["BREV_SSH_CONFIG"] = str(link)
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(run_leg._cached_managed_ssh_candidates(), {})

    def test_auth_failure_merges_cached_alias_and_propagates_to_harbor(self):
        config = Path("/safe/operator/.brev/ssh_config")
        env = {
            "BREV_ALLOW_CACHED_SSH": "1",
            "BREV_DIRECT_SSH_POOL": "vss-eval-rtx-2g-2",
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(
                run_leg,
                "_cached_managed_ssh_candidates",
                return_value={"vss-eval-rtx-2g-2": config},
            ),
            mock.patch.object(
                run_leg,
                "_list_brev_instances",
                side_effect=self.AUTH_ERROR,
            ),
            mock.patch.object(
                run_leg,
                "_list_registered_nodes",
                side_effect=self.AUTH_ERROR,
            ),
        ):
            instances = run_leg._list_pool_instances("RTX PRO 6000")
            propagated = os.environ.get(
                run_leg.CACHED_MANAGED_SSH_ADMITTED_ENV
            )

        self.assertEqual([item["name"] for item in instances], [
            "vss-eval-rtx-2g-2"
        ])
        self.assertTrue(instances[0]["_registered"])
        self.assertTrue(instances[0]["_cached_managed_ssh"])
        self.assertEqual(propagated, "vss-eval-rtx-2g-2")
        self.assertEqual(
            run_leg._WORKER_TRANSPORTS["vss-eval-rtx-2g-2"], "ssh"
        )
        self.assertEqual(
            run_leg._WORKER_SSH_CONFIGS["vss-eval-rtx-2g-2"], str(config)
        )

    def test_empty_inventory_merges_only_allowlisted_cached_alias(self):
        config = Path("/safe/operator/.brev/ssh_config")
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                run_leg,
                "_cached_managed_ssh_candidates",
                return_value={"vss-eval-rtx-2g-3": config},
            ),
            mock.patch.object(run_leg, "_list_brev_instances", return_value=[]),
            mock.patch.object(run_leg, "_list_registered_nodes", return_value=[]),
        ):
            instances = run_leg._list_pool_instances("RTX PRO 6000")

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0]["name"], "vss-eval-rtx-2g-3")
        self.assertTrue(instances[0]["_cached_managed_ssh"])

    def test_healthy_api_inventory_does_not_merge_different_cached_worker(self):
        config = Path("/safe/operator/.brev/ssh_config")
        managed = {
            "name": "vss-eval-rtx-2g-2",
            "status": "RUNNING",
            "gpu": "RTX PRO 6000",
        }
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                run_leg,
                "_cached_managed_ssh_candidates",
                return_value={"vss-eval-rtx-2g-3": config},
            ) as cached,
            mock.patch.object(
                run_leg, "_list_brev_instances", return_value=[managed]
            ),
            mock.patch.object(run_leg, "_list_registered_nodes", return_value=[]),
        ):
            instances = run_leg._list_pool_instances("RTX PRO 6000")
            propagated = os.environ.get(
                run_leg.CACHED_MANAGED_SSH_ADMITTED_ENV
            )

        self.assertEqual(instances, [managed])
        cached.assert_not_called()
        self.assertEqual(
            run_leg._WORKER_TRANSPORTS["vss-eval-rtx-2g-2"], "brev"
        )
        self.assertIsNone(propagated)

    def test_cached_worker_remote_lock_uses_explicit_ssh_config(self):
        calls = []
        run_leg._WORKER_TRANSPORTS["vss-eval-rtx-2g-2"] = "ssh"
        run_leg._WORKER_SSH_CONFIGS["vss-eval-rtx-2g-2"] = (
            "/home/ubuntu/.brev/ssh_config"
        )

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "ok\n", "")

        with mock.patch.object(run_leg.subprocess, "run", side_effect=fake_run):
            result = run_leg._remote_lock_executor(
                "vss-eval-rtx-2g-2"
            )("echo ok", 60)

        self.assertEqual(result.returncode, 0)
        command, kwargs = calls[0]
        self.assertEqual(command[:4], [
            "ssh", "-F", "/home/ubuntu/.brev/ssh_config", "-T"
        ])
        self.assertEqual(command[-2:], ["vss-eval-rtx-2g-2", "echo ok"])
        self.assertEqual(kwargs["input"], "")


class PoolCandidates(unittest.TestCase):
    FLEET = [
        {"name": "vss-eval-rtx-1g-2", "status": "RUNNING",
         "gpu": "RTX PRO Server 6000", "instance_type": "g7e.4xlarge"},
        {"name": "vss-eval-rtx-1g-3", "status": "STOPPED",
         "gpu": "RTX PRO Server 6000", "instance_type": "g7e.4xlarge"},
        {"name": "vss-eval-rtx-2g-2", "status": "RUNNING",
         "gpu": "RTX PRO Server 6000", "instance_type": "g7e.12xlarge"},
        {"name": "vss-eval-l40s", "status": "RUNNING",
         "gpu": "L40S", "instance_type": "massedcompute_L40Sx2"},
        # gpu flake: catalog refresh returns "-" but instance_type carries it
        {"name": "vss-eval-l40s-2", "status": "RUNNING",
         "gpu": "-", "instance_type": "massedcompute_L40Sx2"},
        {"name": "vss-eval-rtx-2g-VM1b", "status": "RUNNING",
         "gpu": "RTX PRO 6000",
         "instance_type": "registered-external-node", "_registered": True},
        {"name": "not-a-pool-box", "status": "RUNNING",
         "gpu": "RTX PRO Server 6000", "instance_type": "g7e.4xlarge"},
    ]

    def setUp(self):
        self._orig = run_leg._list_pool_instances
        run_leg._list_pool_instances = (
            lambda _required_gpu_type=None: self.FLEET
        )

    def tearDown(self):
        run_leg._list_pool_instances = self._orig

    def test_filters_running_pool_and_gpu_type(self):
        names = run_leg.pool_candidates(
            {"gpu_type": "RTX PRO 6000", "gpu_count": 1})
        self.assertEqual(
            names,
            [
                "vss-eval-rtx-2g-VM1b",
                "vss-eval-rtx-1g-2",
                "vss-eval-rtx-2g-2",
            ],
        )

    def test_exact_count_hint_sorts_first(self):
        names = run_leg.pool_candidates(
            {"gpu_type": "RTX PRO 6000", "gpu_count": 2})
        self.assertEqual(names[0], "vss-eval-rtx-2g-VM1b")

    def test_gpu_flake_accepted_via_instance_type(self):
        names = run_leg.pool_candidates({"gpu_type": "L40S", "gpu_count": 1})
        self.assertEqual(names, ["vss-eval-l40s", "vss-eval-l40s-2"])

    def test_gpu_count_zero_accepts_any_running_pool_box(self):
        names = run_leg.pool_candidates({"gpu_count": 0})
        self.assertEqual(len(names), 5)
        self.assertNotIn("not-a-pool-box", names)
        self.assertNotIn("vss-eval-rtx-1g-3", names)

    def test_registered_gpu_hint_fails_closed_for_unknown_pool(self):
        self.assertEqual(
            run_leg._registered_gpu_hint("vss-eval-rtx-2g-VM1b"),
            "RTX PRO 6000",
        )
        self.assertEqual(
            run_leg._registered_gpu_hint(
                "vss-eval-geforce-rtx4090-vm1"
            ),
            "GEFORCE RTX 4090",
        )
        self.assertEqual(run_leg._registered_gpu_hint("vss-eval-mystery"), "")

    def test_pool_snapshot_merges_and_normalizes_registered_nodes(self):
        orig_managed = run_leg._list_brev_instances
        orig_registered = run_leg._list_registered_nodes
        try:
            run_leg._list_brev_instances = lambda: [
                {"name": "vss-eval-rtx-2g", "status": "RUNNING"}
            ]
            run_leg._list_registered_nodes = lambda: [
                {"name": "vss-eval-rtx-2g-VM1b", "status": "Connected"},
                # A duplicate must not be added twice.
                {"name": "vss-eval-rtx-2g", "status": "Connected"},
            ]

            with mock.patch.dict(
                run_leg.os.environ,
                {"BREV_REGISTERED_POOL": "vss-eval-rtx-2g-VM1b"},
            ):
                instances = self._orig()
        finally:
            run_leg._list_brev_instances = orig_managed
            run_leg._list_registered_nodes = orig_registered

        self.assertEqual(len(instances), 2)
        registered = instances[1]
        self.assertEqual(registered["status"], "RUNNING")
        self.assertEqual(registered["gpu"], "RTX PRO 6000")
        self.assertTrue(registered["_registered"])

    def test_registered_pool_requires_explicit_allowlist(self):
        orig_managed = run_leg._list_brev_instances
        orig_registered = run_leg._list_registered_nodes
        try:
            run_leg._list_brev_instances = lambda: []
            run_leg._list_registered_nodes = lambda: [
                {"name": "vss-eval-rtx-2g-VM1b", "status": "Connected"},
                {"name": "vss-eval-rtx-2g-skybridge", "status": "Connected"},
            ]
            with mock.patch.dict(
                run_leg.os.environ,
                {
                    "BREV_REGISTERED_POOL":
                        "vss-eval-rtx-2g-VM1b, vss-eval-rtx-2g-VM2b"
                },
            ):
                instances = self._orig()
        finally:
            run_leg._list_brev_instances = orig_managed
            run_leg._list_registered_nodes = orig_registered

        self.assertEqual(
            [instance["name"] for instance in instances],
            ["vss-eval-rtx-2g-VM1b", "vss-eval-rtx-2g-vm2b"],
        )
        self.assertNotIn(
            "vss-eval-rtx-2g-skybridge",
            [instance["name"] for instance in instances],
        )
        self.assertTrue(instances[1]["_inventory_fallback"])

    def test_empty_api_inventory_uses_registered_ssh_allowlist(self):
        with (
            mock.patch.dict(
                run_leg.os.environ,
                {"BREV_REGISTERED_POOL": "vss-eval-rtx-2g-VM1b"},
                clear=True,
            ),
            mock.patch.object(
                run_leg, "_list_brev_instances", return_value=[]
            ),
            mock.patch.object(
                run_leg, "_list_registered_nodes", return_value=[]
            ),
        ):
            instances = self._orig("RTX PRO 6000")

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0]["name"], "vss-eval-rtx-2g-vm1b")
        self.assertTrue(instances[0]["_registered"])
        self.assertTrue(instances[0]["_inventory_fallback"])

    def test_expired_brev_auth_falls_back_to_registered_ssh_allowlist(self):
        auth_error = run_leg.BrevAuthenticationError("expired")
        with (
            mock.patch.dict(
                run_leg.os.environ,
                {
                    "BREV_REGISTERED_POOL": (
                        "vss-eval-rtx-2g-VM1b,vss-eval-rtx-2g-VM2b"
                    )
                },
                clear=True,
            ),
            mock.patch.object(
                run_leg, "_list_brev_instances", side_effect=auth_error
            ),
            mock.patch.object(
                run_leg, "_list_registered_nodes", side_effect=auth_error
            ),
        ):
            instances = self._orig("RTX PRO 6000")

        self.assertEqual(
            [instance["name"] for instance in instances],
            ["vss-eval-rtx-2g-vm1b", "vss-eval-rtx-2g-vm2b"],
        )
        self.assertTrue(all(instance["_registered"] for instance in instances))
        self.assertTrue(
            all(instance["_inventory_fallback"] for instance in instances)
        )
        self.assertTrue(all(instance["status"] == "RUNNING" for instance in instances))

    def test_expired_brev_auth_without_allowlist_still_fails_closed(self):
        auth_error = run_leg.BrevAuthenticationError("expired")
        with (
            mock.patch.dict(run_leg.os.environ, {}, clear=True),
            mock.patch.object(
                run_leg, "_list_brev_instances", side_effect=auth_error
            ),
        ):
            with self.assertRaisesRegex(
                run_leg.BrevAuthenticationError, "expired"
            ):
                self._orig("RTX PRO 6000")

    def test_allowlisted_worker_uses_ssh_without_brev_inventory(self):
        run_leg._WORKER_TRANSPORTS.clear()
        with (
            mock.patch.dict(
                run_leg.os.environ,
                {"BREV_REGISTERED_POOL": "vss-eval-rtx-2g-VM1b"},
                clear=True,
            ),
            mock.patch.object(run_leg, "_list_brev_instances") as managed,
            mock.patch.object(run_leg, "_list_registered_nodes") as registered,
        ):
            self.assertTrue(run_leg._worker_uses_ssh("VSS-EVAL-RTX-2G-VM1B"))

        managed.assert_not_called()
        registered.assert_not_called()

    def test_allowlisted_4090_worker_uses_ssh_without_brev_inventory(self):
        run_leg._WORKER_TRANSPORTS.clear()
        with (
            mock.patch.dict(
                run_leg.os.environ,
                {"BREV_RTX4090_POOL": "vss-eval-geforce-rtx4090-vm1"},
                clear=True,
            ),
            mock.patch.object(run_leg, "_list_brev_instances") as managed,
            mock.patch.object(run_leg, "_list_registered_nodes") as registered,
        ):
            self.assertTrue(
                run_leg._worker_uses_ssh("vss-eval-geforce-rtx4090-vm1")
            )

        managed.assert_not_called()
        registered.assert_not_called()

    def test_4090_pool_is_gated_by_explicit_gpu_type(self):
        env = {
            "BREV_REGISTERED_POOL": "vss-eval-rtx-2g-VM1b",
            "BREV_RTX4090_POOL": (
                "vss-eval-geforce-rtx4090-vm1,"
                "vss-eval-geforce-rtx4090-vm2"
            ),
        }
        with mock.patch.dict(run_leg.os.environ, env, clear=True):
            rtx_pro = run_leg._registered_pool_allowlist("RTX PRO 6000")
            rtx4090 = run_leg._registered_pool_allowlist(
                "GEFORCE RTX 4090"
            )

        self.assertEqual(rtx_pro, {"vss-eval-rtx-2g-vm1b"})
        self.assertEqual(
            rtx4090,
            {
                "vss-eval-rtx-2g-vm1b",
                "vss-eval-geforce-rtx4090-vm1",
                "vss-eval-geforce-rtx4090-vm2",
            },
        )

    def test_registered_4090_discovery_requires_explicit_gpu_type(self):
        orig_managed = run_leg._list_brev_instances
        orig_registered = run_leg._list_registered_nodes
        try:
            run_leg._list_pool_instances = self._orig
            run_leg._list_brev_instances = lambda: []
            run_leg._list_registered_nodes = lambda: [
                {
                    "name": "vss-eval-geforce-rtx4090-vm1",
                    "status": "Connected",
                },
            ]
            with mock.patch.dict(
                run_leg.os.environ,
                {
                    "BREV_RTX4090_POOL":
                        "vss-eval-geforce-rtx4090-vm1",
                },
                clear=True,
            ):
                rtx_pro = run_leg.pool_candidates({
                    "gpu_type": "RTX PRO 6000",
                    "gpu_count": 1,
                })
                rtx4090 = run_leg.pool_candidates({
                    "gpu_type": "GEFORCE RTX 4090",
                    "gpu_count": 1,
                })
        finally:
            run_leg._list_brev_instances = orig_managed
            run_leg._list_registered_nodes = orig_registered

        self.assertEqual(rtx_pro, [])
        self.assertEqual(rtx4090, ["vss-eval-geforce-rtx4090-vm1"])

    def test_4090_selection_requires_explicit_gpu_type(self):
        fleet = [{
            "name": "vss-eval-geforce-rtx4090-vm1",
            "status": "RUNNING",
            "gpu": "GEFORCE RTX 4090",
            "_registered": True,
        }]
        run_leg._list_pool_instances = (
            lambda _required_gpu_type=None: fleet
        )
        requirements = {"gpu_type": "RTX PRO 6000", "gpu_count": 1}

        legacy_route = run_leg.pool_candidates({
            **requirements,
            "skill": "vss-ask-video",
        }, "base_profile_video_understanding")
        explicit_route = run_leg.pool_candidates({
            "gpu_type": "GEFORCE RTX 4090",
            "gpu_count": 1,
            "skill": "vss-ask-video",
        }, "base_profile_video_understanding")

        self.assertEqual(legacy_route, [])
        self.assertEqual(explicit_route, ["vss-eval-geforce-rtx4090-vm1"])

    def test_underprovisioned_registered_node_is_filtered(self):
        fleet = [
            {"name": "vss-eval-geforce-rtx4090-vm1", "status": "RUNNING",
             "gpu": "GEFORCE RTX 4090", "_registered": True},
            {"name": "vss-eval-rtx-2g-VM1b", "status": "RUNNING",
             "gpu": "RTX PRO 6000", "_registered": True},
        ]
        run_leg._list_pool_instances = (
            lambda _required_gpu_type=None: fleet
        )

        names = run_leg.pool_candidates({
            "skill": "vss-ask-video",
            "gpu_type": "RTX PRO 6000",
            "gpu_count": 2,
        }, "base_profile_video_understanding")

        self.assertEqual(names, ["vss-eval-rtx-2g-VM1b"])


class HoldPoolLock(unittest.TestCase):
    def test_claims_first_free_candidate(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp)
            # Hold the preferred box's lock as if another leg owns it.
            held = (lock_dir / "box-a.lock").open("a+")
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            remote_lease = mock.Mock(lost_event=threading.Event())
            try:
                with (
                    mock.patch.object(
                        run_leg,
                        "_try_acquire_remote_worker_lease",
                        return_value=remote_lease,
                    ),
                    run_leg.hold_pool_lock(
                        lambda: ["box-a", "box-b"], lock_dir, timeout_sec=5
                    ) as worker,
                ):
                    self.assertEqual(worker.instance, "box-b")
            finally:
                held.close()
            remote_lease.release.assert_called_once()

    def test_times_out_when_all_held(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp)
            held = (lock_dir / "box-a.lock").open("a+")
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                start = time.monotonic()
                with (
                    self.assertRaises(run_leg.LockTimeoutError),
                    run_leg.hold_pool_lock(
                        lambda: ["box-a"],
                        lock_dir,
                        timeout_sec=0,
                    ),
                ):
                    pass
                self.assertLess(time.monotonic() - start, 5)
            finally:
                held.close()

    def test_lock_released_on_exit(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp)
            remote_lease = mock.Mock(lost_event=threading.Event())
            with (
                mock.patch.object(
                    run_leg,
                    "_try_acquire_remote_worker_lease",
                    return_value=remote_lease,
                ),
                run_leg.hold_pool_lock(
                    lambda: ["box-a"],
                    lock_dir,
                    5,
                ) as worker,
            ):
                self.assertEqual(worker.instance, "box-a")
            probe = (lock_dir / "box-a.lock").open("a+")
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                probe.close()
            remote_lease.release.assert_called_once()

    def test_remote_busy_candidate_releases_local_lock_and_uses_next(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp)
            remote_lease = mock.Mock(lost_event=threading.Event())
            attempts: list[str] = []

            def acquire(instance: str):
                attempts.append(instance)
                return None if instance == "box-a" else remote_lease

            with (
                mock.patch.object(
                    run_leg,
                    "_try_acquire_remote_worker_lease",
                    side_effect=acquire,
                ),
                run_leg.hold_pool_lock(
                    lambda: ["box-a", "box-b"],
                    lock_dir,
                    5,
                ) as worker,
            ):
                self.assertEqual(worker.instance, "box-b")
                probe = (lock_dir / "box-a.lock").open("a+")
                try:
                    fcntl.flock(
                        probe.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                finally:
                    probe.close()

        self.assertEqual(attempts, ["box-a", "box-b"])
        remote_lease.release.assert_called_once()


class RemoteWorkerLeaseIntegration(unittest.TestCase):
    def tearDown(self):
        run_leg._WORKER_TRANSPORTS.clear()

    def test_remote_executor_routes_managed_and_registered_workers(self):
        result = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            run_leg.subprocess,
            "run",
            return_value=result,
        ) as run:
            run_leg._WORKER_TRANSPORTS["managed-box"] = "brev"
            managed = run_leg._remote_lock_executor("managed-box")
            managed("echo managed", 17)

            run_leg._WORKER_TRANSPORTS["registered-box"] = "ssh"
            registered = run_leg._remote_lock_executor("Registered-Box")
            registered("echo registered", 19)

        managed_cmd = run.call_args_list[0].args[0]
        registered_cmd = run.call_args_list[1].args[0]
        self.assertEqual(managed_cmd[:3], ["brev", "exec", "managed-box"])
        self.assertEqual(registered_cmd[0], "ssh")
        self.assertIn("registered-box", registered_cmd)
        self.assertEqual(run.call_args_list[0].kwargs["timeout"], 17)
        self.assertEqual(run.call_args_list[1].kwargs["timeout"], 19)

    def test_run_command_lease_loss_wins_over_child_completion(self):
        lost_event = threading.Event()
        lost_event.set()

        rc = run_leg.run_command(
            [sys.executable, "-c", "pass"],
            os.environ.copy(),
            timeout_sec=10,
            abort_event=lost_event,
        )

        self.assertEqual(rc, 125)

    def test_multistep_chain_stops_immediately_after_lease_loss(self):
        invocations = [
            run_leg.HarborInvocation(
                harbor_root=Path("/tmp/profile"),
                include_task_name=f"step-{index}",
                chain_key="chain",
                step_index=index,
                step_count=2,
            )
            for index in (1, 2)
        ]
        with (
            mock.patch.dict(
                run_leg.os.environ,
                {
                    "ANTHROPIC_MODEL": "model",
                    "ANTHROPIC_BASE_URL": "https://example.test/v1",
                },
                clear=True,
            ),
            mock.patch.object(
                run_leg,
                "run_command",
                side_effect=[125, 0],
            ) as run_command,
        ):
            rc = run_leg.run_invocations(
                invocations,
                "worker",
                Path("/tmp/results"),
                Path("/tmp/scratch"),
                "spec",
                "RTXPRO6000BW",
                60,
            )

        self.assertEqual(rc, 125)
        run_command.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
