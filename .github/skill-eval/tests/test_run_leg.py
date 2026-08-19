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
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

# run_leg imports its sibling `leg_timing`, and spec_from_file_location does
# not put the loaded file's directory on sys.path the way running it as a
# script does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_SPEC = importlib.util.spec_from_file_location(
    "run_leg", Path(__file__).resolve().parents[1] / "run_leg.py"
)
run_leg = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_leg
_SPEC.loader.exec_module(run_leg)

import leg_timing  # noqa: E402 - must follow the sys.path insert above


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
            run_leg.CLAUDE_AGENT_SDK_REQUIREMENT, "claude-agent-sdk==0.2.128"
        )
        self.assertEqual(
            cmd[:9],
            [
                "uvx",
                "--python",
                run_leg.sys.executable,
                "--from",
                run_leg.HARBOR_REQUIREMENT,
                "--with",
                run_leg.CLAUDE_AGENT_SDK_REQUIREMENT,
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

    def test_build_command_nemoclaw_reuses_standard_dispatch(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/base"),
            include_task_name="rtxpro6000bw",
            chain_key="base_rtxpro6000bw",
        )

        cmd = run_leg.build_harbor_command(
            invocation,
            Path("/tmp/results"),
            "aws/anthropic/bedrock-claude-opus-4-6",
            "https://inference-api.nvidia.com/v1",
            "nemoclaw",
        )

        self.assertEqual(cmd[cmd.index("-a") + 1], "agents.nemoclaw:NemoClaw")
        self.assertEqual(
            cmd[cmd.index("--environment-import-path") + 1],
            "envs.nemoclaw_brev_env:NemoClawBrevEnvironment",
        )
        self.assertEqual(
            cmd[cmd.index("--environment-build-timeout-multiplier") + 1],
            str(run_leg.NEMOCLAW_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER),
        )
        self.assertNotIn("--ak", cmd)
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
        self.assertEqual(
            run_leg.NEMOCLAW_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER,
            10.0,
        )
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
            lambda _skill=None, _spec_stem=None: self.FLEET
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
            ["vss-eval-rtx-2g-VM1b"],
        )

    def test_4090_pool_is_limited_to_approved_skills(self):
        env = {
            "BREV_REGISTERED_POOL": "vss-eval-rtx-2g-VM1b",
            "BREV_RTX4090_POOL": (
                "vss-eval-geforce-rtx4090-vm1,"
                "vss-eval-geforce-rtx4090-vm2"
            ),
        }
        with mock.patch.dict(run_leg.os.environ, env, clear=True):
            approved = run_leg._registered_pool_allowlist(
                "vss-ask-video", "base_profile_video_understanding"
            )
            unapproved = run_leg._registered_pool_allowlist(
                "vss-deploy-profile", "search"
            )

        self.assertEqual(
            approved,
            {
                "vss-eval-rtx-2g-vm1b",
                "vss-eval-geforce-rtx4090-vm1",
                "vss-eval-geforce-rtx4090-vm2",
            },
        )
        self.assertEqual(unapproved, {"vss-eval-rtx-2g-vm1b"})

    def test_4090_test_capabilities_fail_closed(self):
        self.assertTrue(run_leg._rtx4090_supports(
            "vss-deploy-profile", "alerts_cv"
        ))
        self.assertTrue(run_leg._rtx4090_supports(
            "vss-manage-alerts", "subscriptions_lifecycle"
        ))
        self.assertFalse(run_leg._rtx4090_supports(
            "vss-deploy-profile", "search"
        ))
        self.assertFalse(run_leg._rtx4090_supports(
            "vss-deploy-profile", "warehouse"
        ))
        self.assertFalse(run_leg._rtx4090_supports(
            "vss-deploy-dense-captioning", "alerts_profile_api"
        ))
        self.assertFalse(run_leg._rtx4090_supports(
            "vss-deploy-detection-tracking-3d", "deploy"
        ))
        self.assertFalse(run_leg._rtx4090_supports("vss-ask-video", None))

    def test_4090_capability_route_bypasses_rtx_pro_type_only_for_skill(self):
        fleet = [{
            "name": "vss-eval-geforce-rtx4090-vm1",
            "status": "RUNNING",
            "gpu": "GEFORCE RTX 4090",
            "_registered": True,
            "_rtx4090_capability_routed": True,
        }]
        run_leg._list_pool_instances = (
            lambda _skill=None, _spec_stem=None: fleet
        )
        requirements = {"gpu_type": "RTX PRO 6000", "gpu_count": 1}

        approved = run_leg.pool_candidates({
            **requirements,
            "skill": "vss-ask-video",
        }, "base_profile_video_understanding")
        unapproved = run_leg.pool_candidates({
            **requirements,
            "skill": "vss-deploy-dense-captioning",
        }, "alerts_profile_api")

        self.assertEqual(approved, ["vss-eval-geforce-rtx4090-vm1"])
        self.assertEqual(unapproved, [])

    def test_underprovisioned_registered_node_is_filtered(self):
        fleet = [
            {"name": "vss-eval-geforce-rtx4090-vm1", "status": "RUNNING",
             "gpu": "GEFORCE RTX 4090", "_registered": True,
             "_rtx4090_capability_routed": True},
            {"name": "vss-eval-rtx-2g-VM1b", "status": "RUNNING",
             "gpu": "RTX PRO 6000", "_registered": True},
        ]
        run_leg._list_pool_instances = (
            lambda _skill=None, _spec_stem=None: fleet
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
            try:
                with run_leg.hold_pool_lock(
                    lambda: ["box-a", "box-b"], lock_dir, timeout_sec=5
                ) as chosen:
                    self.assertEqual(chosen, "box-b")
            finally:
                held.close()

    def test_times_out_when_all_held(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp)
            held = (lock_dir / "box-a.lock").open("a+")
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                start = time.monotonic()
                with self.assertRaises(run_leg.LockTimeoutError):
                    with run_leg.hold_pool_lock(
                        lambda: ["box-a"], lock_dir, timeout_sec=0
                    ):
                        pass
                self.assertLess(time.monotonic() - start, 5)
            finally:
                held.close()

    def test_lock_released_on_exit(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp)
            with run_leg.hold_pool_lock(lambda: ["box-a"], lock_dir, 5) as chosen:
                self.assertEqual(chosen, "box-a")
            probe = (lock_dir / "box-a.lock").open("a+")
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                probe.close()


class TheHeartbeatNamesTheRightPhase(unittest.TestCase):
    """The lock wait is the longest gap a leg can have, and it was the one
    phase the heartbeat could not name: `record_phase` was called directly, so
    the recorded interval said `lock-wait` while every tick during it said
    `startup`."""

    def test_the_lock_wait_is_labelled_while_it_is_being_waited_on(self):
        seen: list[str] = []

        @contextlib.contextmanager
        def fake_lock(*_args, **_kwargs):
            seen.append(leg_timing._CURRENT_PHASE)
            yield "vss-eval-box-1"

        with mock.patch.object(run_leg, "hold_pool_lock", fake_lock):
            with self.assertRaises(SystemExit):
                self._run_main_far_enough()
        self.assertEqual(
            seen, ["lock-wait"],
            "the heartbeat would report 'startup' for the whole wait",
        )

    def test_the_label_is_restored_when_the_lock_raises(self):
        """A stuck `lock-wait` misreports every later tick for the rest of the
        leg, which is worse than the missing label it replaced."""
        @contextlib.contextmanager
        def exploding_lock(*_args, **_kwargs):
            raise OSError("bad lock dir")
            yield  # pragma: no cover

        before = leg_timing._CURRENT_PHASE
        with mock.patch.object(run_leg, "hold_pool_lock", exploding_lock):
            with contextlib.suppress(BaseException):
                self._run_main_far_enough()
        self.assertEqual(leg_timing._CURRENT_PHASE, before)

    def _run_main_far_enough(self):
        """Drive main() to the lock and no further."""
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "dataset" / "platform" / "step-1"
            dataset.mkdir(parents=True)
            (dataset / "task.toml").write_text("step_count = 1\n", encoding="utf-8")
            with mock.patch.object(
                run_leg, "SKILL_EVAL_PYTHON_VERSION", sys.version_info[:2]
            ), mock.patch.object(
                run_leg, "run_invocations", side_effect=SystemExit(0)
            ), mock.patch.object(leg_timing, "start_heartbeat",
                                 return_value=(mock.Mock(), mock.Mock())):
                run_leg.main([
                    "--dataset-root", str(Path(tmp) / "dataset"),
                    "--results-root", str(Path(tmp) / "results"),
                    "--scratch", str(Path(tmp) / "scratch"),
                    "--spec-stem", "spec",
                    "--platform", "L40S",
                ])


class InstrumentationNeverChangesTheVerdict(unittest.TestCase):
    """The one property the whole feature rests on, pinned through main()."""

    def _argv(self, tmp: str) -> list[str]:
        return [
            "--dataset-root", str(Path(tmp) / "dataset"),
            "--results-root", str(Path(tmp) / "results"),
            "--scratch", str(Path(tmp) / "scratch"),
            "--spec-stem", "spec",
            "--platform", "L40S",
        ]

    def setUp(self):
        # main() gates on the interpreter; that gate is not what these pin, and
        # the suite should pass under whichever python a reviewer has to hand.
        patcher = mock.patch.object(
            run_leg, "SKILL_EVAL_PYTHON_VERSION", sys.version_info[:2]
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self._saved_phases = list(leg_timing._PHASES)
        leg_timing._PHASES.clear()
        self.addCleanup(lambda: leg_timing._PHASES.__setitem__(slice(None), self._saved_phases))

    def _dataset(self, tmp: str) -> None:
        task_dir = Path(tmp) / "dataset" / "chain" / "l40s"
        task_dir.mkdir(parents=True)
        (task_dir / "task.toml").write_text("step_count = 1\n")

    @contextlib.contextmanager
    def _held_lock(self):
        with mock.patch.object(run_leg, "hold_pool_lock") as lock:
            lock.return_value.__enter__.return_value = "box-a"
            lock.return_value.__exit__.return_value = False
            yield lock

    def test_exit_code_survives_a_failing_phase_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._dataset(tmp)
            with self._held_lock(), \
                 mock.patch.object(run_leg, "run_invocations", return_value=42), \
                 mock.patch.object(leg_timing, "write_phase_timings", side_effect=RuntimeError("boom")
                 ):
                self.assertEqual(run_leg.main(self._argv(tmp)), 42)

    def test_instrumentation_logging_swallows_a_broken_pipe(self):
        # BrokenPipeError on a closed stdout is the realistic version of this.
        # Scoped to the instrumentation path on purpose: run_leg's pre-existing
        # FATAL prints are unprotected too, but that predates this change and
        # widening the assertion here would quietly claim otherwise.
        with mock.patch("builtins.print", side_effect=BrokenPipeError("closed")):
            leg_timing.leg_log("must not raise")
            with leg_timing.phase("harbor:step-1"):
                pass
            leg_timing.write_phase_timings(Path("/nonexistent"))

        self.assertEqual(leg_timing._PHASES[-1]["phase"], "harbor:step-1")

    def test_a_lock_failure_that_is_not_a_timeout_still_records_the_wait(self):
        # An invalid instance name raises ValueError inside hold_pool_lock.
        # Without a phase, the artifact cannot distinguish "died selecting a
        # box after 40 minutes" from "never waited at all".
        with tempfile.TemporaryDirectory() as tmp:
            self._dataset(tmp)
            with mock.patch.object(
                run_leg, "hold_pool_lock", side_effect=ValueError("invalid name")
            ):
                self.assertEqual(run_leg.main(self._argv(tmp)), 1)

        self.assertEqual(
            [e["phase"] for e in leg_timing._PHASES], ["lock-wait-failed"]
        )

    def test_a_dead_heartbeat_thread_does_not_fail_the_leg(self):
        """The name used to contradict the body: it asserted RuntimeError
        escaped, inside a class asserting the opposite invariant, so scanning
        the names said the property held while the test proved it did not.

        A runner at its thread limit is the case. Losing the heartbeat costs
        visibility; raising costs the leg, and the leg is worth more."""
        with tempfile.TemporaryDirectory() as tmp:
            self._dataset(tmp)
            with self._held_lock(), \
                 mock.patch.object(run_leg, "run_invocations", return_value=0), \
                 mock.patch.object(
                     run_leg.threading.Thread,
                     "start",
                     side_effect=RuntimeError("no threads"),
                 ):
                self.assertEqual(run_leg.main(self._argv(tmp)), 0)

    def test_sigterm_unwinds_so_a_cancelled_leg_still_writes_its_timings(self):
        """Python turns only SIGINT into an exception. SIGTERM keeps SIG_DFL
        and kills the interpreter without unwinding, so main()'s finally never
        runs and a cancelled leg produced no artifact at all. `skills-eval.yml`
        sets cancel-in-progress, so this is the normal way a leg ends.

        In a subprocess on purpose. Delivering a real SIGTERM in-process means
        that if the handler is ever removed, the signal takes the whole test
        runner down instead of failing this one test, and a suite that dies is
        harder to read than a suite that reports.
        """
        driver = """
import importlib.util, os, signal, sys
from contextlib import contextmanager
# run_leg imports its sibling leg_timing, and loading by path does not put the
# file's directory on sys.path the way running it as a script does.
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))
spec = importlib.util.spec_from_file_location("run_leg", sys.argv[1])
run_leg = importlib.util.module_from_spec(spec)
sys.modules["run_leg"] = run_leg
spec.loader.exec_module(run_leg)

@contextmanager
def lock_then_sigterm(*a, **k):
    os.kill(os.getpid(), signal.SIGTERM)
    yield "box"

run_leg.hold_pool_lock = lock_then_sigterm
run_leg.SKILL_EVAL_PYTHON_VERSION = sys.version_info[:2]
run_leg.main(sys.argv[2:])
"""
        with tempfile.TemporaryDirectory() as tmp:
            self._dataset(tmp)
            script = Path(tmp) / "driver.py"
            script.write_text(driver, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(script),
                 str(Path(__file__).resolve().parents[1] / "run_leg.py"),
                 *self._argv(tmp)],
                capture_output=True, text=True, timeout=60,
            )
            written = Path(tmp) / "results" / leg_timing.PHASE_TIMINGS_NAME
            self.assertTrue(
                written.exists(),
                f"SIGTERM killed the leg before it recorded anything "
                f"(rc={completed.returncode}): {completed.stderr[-400:]}",
            )
            self.assertEqual(
                [e["phase"] for e in json.loads(written.read_text())["phases"]],
                ["lock-wait-failed"],
            )
        # 128+SIGTERM, the conventional code, reached by unwinding rather than
        # by the default terminating action.
        self.assertEqual(completed.returncode, 128 + signal.SIGTERM)

    def test_the_artifact_is_replaced_atomically(self):
        """This write runs in main()'s finally, which is where a second signal
        during a cancellation lands. A partial write leaves truncated JSON at
        the real path, and a reader cannot tell that from a leg that recorded
        nothing, so it is worse than no file."""
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results"
            results.mkdir()
            destination = results / leg_timing.PHASE_TIMINGS_NAME
            destination.write_text('{"phases": ["previous"]}\n', encoding="utf-8")
            with mock.patch.object(leg_timing, "_PHASES",
                [{"phase": "lock-wait", "start_s": 0.0, "end_s": 1.0, "seconds": 1.0}],
            ), mock.patch.object(
                run_leg.os, "replace", side_effect=OSError("interrupted")
            ):
                leg_timing.write_phase_timings(results)
            # The previous artifact survives intact rather than being truncated.
            self.assertEqual(
                json.loads(destination.read_text())["phases"], ["previous"]
            )
            self.assertEqual(
                list(results.glob("*.partial")), [],
                "a half-written sibling was left to be collected as an artifact",
            )

    def test_a_cancelled_lock_wait_is_still_recorded(self):
        """`skills-eval.yml` sets cancel-in-progress, so a push cancels
        in-flight legs and the cancellation lands as KeyboardInterrupt. Under
        `except Exception` the wait was dropped from the artifact for exactly
        the legs that had spent longest in it."""
        @contextlib.contextmanager
        def cancelled_lock(*_args, **_kwargs):
            raise KeyboardInterrupt
            yield  # pragma: no cover

        with tempfile.TemporaryDirectory() as tmp:
            self._dataset(tmp)
            with mock.patch.object(run_leg, "hold_pool_lock", cancelled_lock), \
                 mock.patch.object(leg_timing, "_PHASES", []) as phases:
                with contextlib.suppress(KeyboardInterrupt):
                    run_leg.main(self._argv(tmp))
        self.assertEqual(
            [entry["phase"] for entry in phases], ["lock-wait-failed"],
            "a cancelled lock wait left no interval in the artifact",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
