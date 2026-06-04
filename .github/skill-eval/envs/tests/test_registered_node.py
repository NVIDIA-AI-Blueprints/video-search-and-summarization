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
import sys
import tempfile
import types
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
