#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the first-trial gate in BrevEnvironment.start().

Proves the fix for the lvs_profile_summarize step-2 failure (PR #1227):
the destructive box-prep — docker reset, host-data purge, AND the repo
sync whose `git clean -fdx` deletes `deploy/docker/data-dir/` (the LVS/VIOS
bind-mount host source) — must run ONLY on a spec's first trial
(single-step, or `step-1`), and must be SKIPPED on `step-2+` so it can't
delete the live bind mounts out from under step-1's still-running
containers.

These tests need no Brev box: every box-side coroutine is stubbed and we
assert purely on which ones start() awaited for each task-dir shape.

Run:
    python3 -m pytest .github/skill-eval/envs/tests/test_step_gate.py -v
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# --- Stub the harbor.environments.base import so brev_env is importable. ---
_base = types.ModuleType("harbor.environments.base")


class _BaseEnvironment:
    def __init__(self, *a, **kw):
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


def _async_ok(*_a, **_kw):
    """Stand-in for a successful brev exec (smoke test, dir setup, etc.)."""
    return brev_env._ExecResult(stdout="harbor-ready", stderr="", return_code=0) \
        if hasattr(brev_env, "_ExecResult") else _ExecResult(
            stdout="harbor-ready", return_code=0)


class StepGateTest(unittest.IsolatedAsyncioTestCase):
    async def _record_start_commands(self, task_dir_name: str) -> list[str]:
        tmp = Path(tempfile.mkdtemp())
        env_dir = tmp / task_dir_name / "environment"
        env_dir.mkdir(parents=True)
        env = brev_env.BrevEnvironment()
        env.environment_dir = env_dir
        env._instance_name = "vss-eval-test"
        commands: list[str] = []

        async def record_exec(_instance, command, **_kwargs):
            commands.append(command)
            return _async_ok()

        with mock.patch.object(env, "_resolve_instance_name", return_value="vss-eval-test"), \
             mock.patch.object(env, "_reset_docker_runtime", new=mock.AsyncMock()), \
             mock.patch.object(env, "_purge_host_data_dirs", new=mock.AsyncMock()), \
             mock.patch.object(env, "_sync_repo_to_pr_head", new=mock.AsyncMock()), \
             mock.patch.object(brev_env, "_find_brev_instance",
                               new=mock.AsyncMock(return_value={"name": "vss-eval-test"})), \
             mock.patch.object(brev_env, "_check_instance_matches",
                               new=mock.AsyncMock(return_value=None)), \
             mock.patch.object(brev_env, "_check_live_resources",
                               new=mock.AsyncMock(return_value=None)), \
             mock.patch.object(brev_env, "_run_brev_exec",
                               new=mock.AsyncMock(side_effect=record_exec)):
            await env.start(force_build=False)

        return commands

    async def _run_start_for(self, task_dir_name: str):
        """Drive start() for a task dir named `task_dir_name`, with every
        box-side coroutine stubbed, and return the set of gated helpers that
        were awaited."""
        tmp = Path(tempfile.mkdtemp())
        env_dir = tmp / task_dir_name / "environment"
        env_dir.mkdir(parents=True)

        env = brev_env.BrevEnvironment()
        env.environment_dir = env_dir
        env._instance_name = "vss-eval-test"

        reset = mock.AsyncMock()
        purge = mock.AsyncMock()
        sync = mock.AsyncMock()

        with mock.patch.object(env, "_resolve_instance_name", return_value="vss-eval-test"), \
             mock.patch.object(env, "_reset_docker_runtime", new=reset), \
             mock.patch.object(env, "_purge_host_data_dirs", new=purge), \
             mock.patch.object(env, "_sync_repo_to_pr_head", new=sync), \
             mock.patch.object(brev_env, "_find_brev_instance",
                               new=mock.AsyncMock(return_value={"name": "vss-eval-test"})), \
             mock.patch.object(brev_env, "_check_instance_matches",
                               new=mock.AsyncMock(return_value=None)), \
             mock.patch.object(brev_env, "_check_live_resources",
                               new=mock.AsyncMock(return_value=None)), \
             mock.patch.object(brev_env, "_run_brev_exec",
                               new=mock.AsyncMock(side_effect=_async_ok)):
            await env.start(force_build=False)

        calls: set[str] = set()
        if reset.await_count:
            calls.add("reset")
        if purge.await_count:
            calls.add("purge")
        if sync.await_count:
            calls.add("sync")
        return calls

    async def test_step1_runs_all_prep(self):
        calls = await self._run_start_for("step-1")
        self.assertEqual(calls, {"reset", "purge", "sync"},
                         "step-1 must reset, purge, and sync (clean slate)")

    async def test_single_step_runs_all_prep(self):
        # Single-step spec: task dir is the platform, not `step-N`.
        calls = await self._run_start_for("rtxpro6000bw")
        self.assertEqual(calls, {"reset", "purge", "sync"},
                         "single-step spec must reset, purge, and sync")

    async def test_step2_skips_all_prep(self):
        calls = await self._run_start_for("step-2")
        self.assertNotIn("sync", calls,
                         "step-2 must NOT re-sync: git clean would delete the "
                         "live deploy/docker/data-dir bind mounts")
        self.assertEqual(calls, set(),
                         "step-2 must skip reset, purge, AND sync")

    async def test_step10_skips_all_prep(self):
        # Guard the string compare: `step-10` must not be mistaken for step-1.
        calls = await self._run_start_for("step-10")
        self.assertEqual(calls, set(), "step-10 must skip all destructive prep")

    async def test_every_task_clears_staged_skills_before_upload(self):
        commands = await self._record_start_commands("step-3")

        self.assertTrue(
            any("rm -rf /logs/artifacts /logs/verifier /skills" in cmd
                for cmd in commands),
            "every warm-worker task must remove stale staged skills",
        )

    async def test_step1_resets_openclaw_workspace_and_attestations_together(self):
        commands = await self._record_start_commands("step-1")
        combined = "\n".join(commands)

        self.assertIn(
            'sudo rm -rf "$HOME/.openclaw/workspace" '
            '"$HOME/.openclaw/workspace-attestations"',
            combined,
        )
        self.assertIn('mkdir -p "$HOME/.openclaw/workspace"', combined)

    async def test_later_steps_preserve_openclaw_workspace_and_attestations(self):
        commands = await self._record_start_commands("step-2")
        combined = "\n".join(commands)

        self.assertNotIn("workspace-attestations", combined)
        self.assertNotIn('rm -rf "$HOME/.openclaw/workspace"', combined)

if __name__ == "__main__":
    unittest.main()
