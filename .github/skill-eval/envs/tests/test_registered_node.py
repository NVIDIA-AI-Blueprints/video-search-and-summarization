#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for registered-node SSH fallback in brev_env.py.

These tests don't need an actual Brev instance — they monkeypatch the
module-level `_registered_nodes_cache` and stub asyncio subprocess calls.

Run manually:
    python3 -m pytest .github/skill-eval/envs/tests/test_registered_node.py -v
Or directly:
    python3 .github/skill-eval/envs/tests/test_registered_node.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# Stub the harbor.environments.base import so brev_env is importable.
_base = types.ModuleType("harbor.environments.base")

class _BaseEnvironment:
    def __init__(self, *a, **kw): pass

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

ENVS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENVS_DIR))

import brev_env  # noqa: E402


class LocalNimCredentialDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_ngc_key_is_sent_on_stdin_without_worker_key_file(self):
        env = brev_env.BrevEnvironment()
        env._instance_name = "SPARK"
        owner = "a" * 24
        plan = {"owner": owner, "token": "leg-token", "routes": []}
        uploads = []
        calls = []

        async def upload(source, target):
            uploads.append(target)

        async def execute(instance, command, timeout=0, input_data=None):
            calls.append((command, input_data))
            return brev_env.ExecResult(return_code=0)

        with mock.patch.dict(os.environ, {
            "SKILL_EVAL_LOCAL_NIM_PLAN": json.dumps(plan),
            "NGC_API_KEY": "private-registry-key",
        }), mock.patch.object(env, "upload_file", side_effect=upload), \
             mock.patch.object(brev_env, "_run_brev_exec", side_effect=execute):
            await env._start_local_nims()

        self.assertEqual(len(uploads), 2)
        self.assertFalse(any(path.endswith(".key") for path in uploads))
        self.assertEqual(calls[0][1], b"private-registry-key\n")
        self.assertNotIn("private-registry-key", calls[0][0])


class RtspSampleUrlResolution(unittest.TestCase):
    def test_uses_public_default_when_unset(self):
        with mock.patch.dict(os.environ, {"RTSP_SAMPLE_URL": ""}):
            self.assertEqual(
                brev_env._resolve_rtsp_sample_url(),
                "rtsp://global.stg.ga.launchpad.nvidia.com:11333/camera03",
            )

    def test_preserves_operator_override(self):
        custom_url = "rtsp://stream.example.test:8554/eval"
        with mock.patch.dict(os.environ, {"RTSP_SAMPLE_URL": custom_url}):
            self.assertEqual(brev_env._resolve_rtsp_sample_url(), custom_url)


class RegisteredNodeDetection(unittest.TestCase):

    def setUp(self):
        # Force cache population from a fake node list.
        brev_env._registered_nodes_cache = {
            "spark": {"name": "SPARK", "status": "Connected", "external_node_id": "extnode-x"},
            "h100-vlm": {"name": "H100-VLM", "status": "Connected", "external_node_id": "extnode-y"},
        }

    def tearDown(self):
        brev_env._registered_nodes_cache = None

    def test_is_registered_node_case_insensitive(self):
        self.assertTrue(asyncio.run(brev_env._is_registered_node("SPARK")))
        self.assertTrue(asyncio.run(brev_env._is_registered_node("spark")))
        self.assertTrue(asyncio.run(brev_env._is_registered_node("Spark")))
        self.assertTrue(asyncio.run(brev_env._is_registered_node("H100-VLM")))
        self.assertTrue(asyncio.run(brev_env._is_registered_node("h100-vlm")))

    def test_is_not_registered(self):
        self.assertFalse(asyncio.run(brev_env._is_registered_node("vss-eval-rtx")))
        self.assertFalse(asyncio.run(brev_env._is_registered_node("unknown")))
        self.assertFalse(asyncio.run(brev_env._is_registered_node("")))

    def test_ssh_alias(self):
        self.assertEqual(brev_env._ssh_alias_for("SPARK"), "spark")
        self.assertEqual(brev_env._ssh_alias_for("H100-VLM"), "h100-vlm")
        self.assertEqual(brev_env._ssh_alias_for("spark"), "spark")


class FindBrevInstanceFallback(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        brev_env._registered_nodes_cache = {
            "spark": {"name": "SPARK", "status": "Connected"},
        }

    async def asyncTearDown(self):
        brev_env._registered_nodes_cache = None

    async def test_registered_node_returns_synthetic_entry(self):
        """If `brev ls` has no match but `brev ls nodes` does, return a
        synthetic dict with _registered=True."""
        async def fake_run_brev(*args, **kw):
            # brev ls --json returns empty cloud list
            return brev_env.ExecResult(stdout="[]", stderr=None, return_code=0)

        original = brev_env._run_brev
        brev_env._run_brev = fake_run_brev
        try:
            result = await brev_env._find_brev_instance("SPARK")
            self.assertIsNotNone(result)
            self.assertTrue(result.get("_registered"))
            self.assertEqual(result["name"], "SPARK")
            self.assertEqual(result["type"], "registered")
        finally:
            brev_env._run_brev = original

    async def test_unknown_instance_returns_none(self):
        async def fake_run_brev(*args, **kw):
            return brev_env.ExecResult(stdout="[]", stderr=None, return_code=0)

        original = brev_env._run_brev
        brev_env._run_brev = fake_run_brev
        try:
            result = await brev_env._find_brev_instance("does-not-exist")
            self.assertIsNone(result)
        finally:
            brev_env._run_brev = original


class CheckInstanceMatchesForRegistered(unittest.TestCase):

    def test_registered_instance_bypasses_gpu_name_check(self):
        """Registered nodes often have empty `gpu` field — shouldn't fail."""
        inst = {"name": "SPARK", "_registered": True, "gpu": ""}
        # Should not raise
        asyncio.run(brev_env._check_instance_matches(inst, {"gpu_type": "GB10"}))

    def test_brev_managed_still_checks_gpu(self):
        """Non-registered instances still enforce GPU-name match."""
        inst = {"name": "test", "gpu": "L40S", "instance_type": "test-l40s"}

        async def fake_catalog_count(instance_type):
            return 1

        original = brev_env._get_instance_gpu_count_from_catalog
        brev_env._get_instance_gpu_count_from_catalog = fake_catalog_count
        try:
            with self.assertRaises(RuntimeError):
                asyncio.run(brev_env._check_instance_matches(inst, {"gpu_type": "H100"}))
        finally:
            brev_env._get_instance_gpu_count_from_catalog = original


class UploadDirTarballCopy(unittest.IsolatedAsyncioTestCase):

    async def test_upload_dir_copies_tarball_and_extracts_with_short_command(self):
        exec_calls = []
        copy_calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            exec_calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="", stderr=None, return_code=0)

        async def fake_run_brev_copy(src, dst, timeout=brev_env.BREV_COPY_TIMEOUT):
            copy_calls.append((src, dst, timeout))
            self.assertTrue(Path(src).is_file())
            return brev_env.ExecResult(stdout="", stderr=None, return_code=0)

        original_exec = brev_env._run_brev_exec
        original_copy = brev_env._run_brev_copy
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env._run_brev_copy = fake_run_brev_copy
        try:
            with tempfile.TemporaryDirectory() as td:
                src_dir = Path(td) / "skills"
                src_dir.mkdir()
                (src_dir / "SKILL.md").write_text("test skill\n")

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                await env.upload_dir(src_dir, "/skills")
        finally:
            brev_env._run_brev_exec = original_exec
            brev_env._run_brev_copy = original_copy

        self.assertEqual(len(copy_calls), 1)
        copied_src, copied_dst, _ = copy_calls[0]
        self.assertTrue(copied_src.endswith(".tar.gz"))
        self.assertFalse(Path(copied_src).exists())
        self.assertRegex(
            copied_dst,
            r"^vss-eval-test:/tmp/skill-eval/uploads/[0-9a-f]+/archive\.tar\.gz$",
        )

        commands = [call[1] for call in exec_calls]
        self.assertEqual(len(commands), 2)
        self.assertIn("mkdir -p /tmp/skill-eval/uploads/", commands[0])
        extract_cmd = commands[1]
        self.assertIn("tar -xzf", extract_cmd)
        self.assertIn("-C /skills", extract_cmd)
        self.assertIn("rm -f /tmp/skill-eval/uploads/", extract_cmd)
        self.assertIn("rmdir /tmp/skill-eval/uploads/", extract_cmd)
        self.assertLess(max(len(command) for command in commands), 1000)
        self.assertNotIn("base64", "\n".join(commands))
        self.assertNotIn("echo '", "\n".join(commands))

    async def test_upload_dir_raises_when_tarball_copy_fails(self):
        exec_calls = []
        copy_calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            exec_calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="", stderr=None, return_code=0)

        async def fake_run_brev_copy(src, dst, timeout=brev_env.BREV_COPY_TIMEOUT):
            copy_calls.append((src, dst, timeout))
            return brev_env.ExecResult(stdout="", stderr="copy failed", return_code=1)

        original_exec = brev_env._run_brev_exec
        original_copy = brev_env._run_brev_copy
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env._run_brev_copy = fake_run_brev_copy
        try:
            with tempfile.TemporaryDirectory() as td:
                src_dir = Path(td) / "skills"
                src_dir.mkdir()
                (src_dir / "SKILL.md").write_text("test skill\n")

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                with self.assertRaisesRegex(RuntimeError, "copy failed"):
                    await env.upload_dir(src_dir, "/skills")
        finally:
            brev_env._run_brev_exec = original_exec
            brev_env._run_brev_copy = original_copy

        self.assertEqual(len(copy_calls), 1)
        copied_src, _, _ = copy_calls[0]
        self.assertFalse(Path(copied_src).exists())
        self.assertEqual(len(exec_calls), 1)
        self.assertIn("mkdir -p /tmp/skill-eval/uploads/", exec_calls[0][1])


class VersionCompareSanity(unittest.TestCase):
    """Extra coverage for _version_lt beyond the generate.py tests."""

    def test_driver_version_ordering(self):
        self.assertTrue(brev_env._version_lt("570.195.03", "580.95"))
        self.assertTrue(brev_env._version_lt("565.57.01", "580.95"))
        self.assertFalse(brev_env._version_lt("580.105.08", "580.95"))
        self.assertFalse(brev_env._version_lt("580.95", "580.95"))


class ClaudeTaskScratchCleanup(unittest.TestCase):
    def test_cleanup_command_targets_current_user_task_dirs(self):
        cmd = brev_env._claude_task_scratch_cleanup_command()

        self.assertIn('BASE="/tmp/claude-${UID_NUM}"', cmd)
        self.assertIn("-name tasks", cmd)
        self.assertIn("-exec rm -rf {} +", cmd)
        self.assertIn("[claude-task-scratch]", cmd)
        self.assertNotIn("sudo rm -rf /tmp/claude-", cmd)
        # The rm step must not swallow stderr — a real cleanup failure has to
        # surface its error to the caller, not raise an empty-tail RuntimeError.
        self.assertNotIn("rm -rf {} + 2>/dev/null", cmd)


class PreparationRetries(unittest.IsolatedAsyncioTestCase):
    async def test_repo_sync_recovers_after_transport_timeout(self):
        env = object.__new__(brev_env.BrevEnvironment)
        env._instance_name = "vss-eval-l40s"
        failure = brev_env.ExecResult(stderr="Command timed out", return_code=124)
        success = brev_env.ExecResult(stdout="synced repo", return_code=0)
        with mock.patch.object(brev_env, "_run_brev_exec", new=mock.AsyncMock(side_effect=[success, failure, success])) as execute, \
             mock.patch.object(brev_env, "_transport_backoff", new=mock.AsyncMock()) as pause:
            await env._sync_repo_to_pr_head()
        self.assertEqual(execute.await_count, 3)
        self.assertEqual(execute.await_args_list[1], execute.await_args_list[2])
        pause.assert_awaited_once_with(0)

    async def test_failed_preflight_stops_before_repo_sync(self):
        env = object.__new__(brev_env.BrevEnvironment)
        env._instance_name = "vss-eval-l40s"
        failure = brev_env.ExecResult(stderr="Connection timed out", return_code=124)
        with mock.patch.object(brev_env, "_run_brev_exec", new=mock.AsyncMock(return_value=failure)) as execute, \
             mock.patch.object(brev_env, "_transport_backoff", new=mock.AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "connectivity preflight failed"):
                await env._sync_repo_to_pr_head()
        self.assertEqual(execute.await_count, 3)
        self.assertTrue(all("git" not in call.args[1] for call in execute.await_args_list))

    async def test_transport_failure_is_bounded(self):
        failure = brev_env.ExecResult(stderr="context deadline exceeded", return_code=1)
        with mock.patch.object(brev_env, "_run_brev_exec", new=mock.AsyncMock(return_value=failure)) as execute, \
             mock.patch.object(brev_env, "_transport_backoff", new=mock.AsyncMock()) as pause:
            result = await brev_env._run_brev_exec_retry("worker", "git fetch", timeout=300)
        self.assertIs(result, failure)
        self.assertEqual(execute.await_count, 3)
        self.assertEqual(pause.await_count, 2)

    async def test_authentication_and_command_errors_are_not_retried(self):
        for message in ("Permission denied", "authentication failed", "fatal: bad revision"):
            failure = brev_env.ExecResult(stderr=message, return_code=1)
            with mock.patch.object(brev_env, "_run_brev_exec", new=mock.AsyncMock(return_value=failure)) as execute, \
                 mock.patch.object(brev_env, "_transport_backoff", new=mock.AsyncMock()) as pause:
                self.assertIs(await brev_env._run_brev_exec_retry("worker", "git fetch", 300), failure)
                execute.assert_awaited_once()
                pause.assert_not_awaited()

    async def test_transfer_recovers_from_rate_limit(self):
        failure = brev_env.ExecResult(stderr="Too many requests", return_code=1)
        success = brev_env.ExecResult(return_code=0)
        with mock.patch.object(brev_env, "_run_brev_copy_once", new=mock.AsyncMock(side_effect=[failure, success])) as transfer, \
             mock.patch.object(brev_env, "_transport_backoff", new=mock.AsyncMock()) as pause:
            self.assertIs(await brev_env._run_brev_copy("src", "worker:dst"), success)
        self.assertEqual(transfer.await_count, 2)
        pause.assert_awaited_once_with(0)

    async def test_cancellation_does_not_trigger_retry(self):
        with mock.patch.object(brev_env, "_run_brev_exec", new=mock.AsyncMock(side_effect=asyncio.CancelledError)), \
             mock.patch.object(brev_env, "_transport_backoff", new=mock.AsyncMock()) as pause:
            with self.assertRaises(asyncio.CancelledError):
                await brev_env._run_brev_exec_retry("worker", "git fetch", 300)
            pause.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)


class LocalNimStartupOrder(unittest.IsolatedAsyncioTestCase):
    async def test_spark_starts_nim_after_reset_without_capacity_checks(self):
        events = []

        async def record_reset():
            events.append("reset")

        async def record_nim():
            events.append("nim")

        async def execute(instance, command, **kwargs):
            return brev_env.ExecResult(
                stdout="aarch64" if command == "uname -m" else "harbor-ready",
                return_code=0,
            )

        with tempfile.TemporaryDirectory() as directory:
            env = brev_env.BrevEnvironment()
            env.environment_dir = Path(directory) / "step-1" / "environment"
            env.environment_dir.mkdir(parents=True)
            with (
                mock.patch.dict(os.environ, {
                    "SKILLS_EVAL_SPARK_RUNNER": "true",
                    "SKILL_EVAL_LOCAL_NIM_PLAN": "{}",
                    "SKILL_EVAL_PRESERVE_DEPLOYMENT": "0",
                }),
                mock.patch.object(env, "_read_task_metadata", return_value={}),
                mock.patch.object(env, "_resolve_instance_name", return_value="Spark-ba-WiFi"),
                mock.patch.object(brev_env, "_find_brev_instance", new=mock.AsyncMock(return_value={"_registered": True})),
                mock.patch.object(brev_env, "_check_instance_matches", new=mock.AsyncMock()) as matches,
                mock.patch.object(brev_env, "_check_live_resources", new=mock.AsyncMock()) as resources,
                mock.patch.object(brev_env, "_run_brev_exec", side_effect=execute),
                mock.patch.object(env, "_reset_docker_runtime", side_effect=record_reset),
                mock.patch.object(env, "_purge_host_data_dirs", new=mock.AsyncMock()),
                mock.patch.object(env, "_probe_bind_mount", new=mock.AsyncMock()),
                mock.patch.object(env, "_sync_repo_to_pr_head", new=mock.AsyncMock()),
                mock.patch.object(env, "_start_local_nims", side_effect=record_nim),
            ):
                await env.start(False)
                matches.assert_not_called()
                resources.assert_not_called()
                self.assertEqual(events, ["reset", "nim"])
                self.assertTrue(env._started)


if __name__ == "__main__":
    unittest.main()
