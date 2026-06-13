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

    async def test_brev_instance_can_be_found_by_id(self):
        async def fake_run_brev(*args, **kw):
            return brev_env.ExecResult(
                stdout='[{"id":"instance-123","name":"vss-eval-rtx-1g-2"}]',
                stderr=None,
                return_code=0,
            )

        original = brev_env._run_brev
        brev_env._run_brev = fake_run_brev
        try:
            result = await brev_env._find_brev_instance("instance-123")
            self.assertIsNotNone(result)
            self.assertEqual(result["name"], "vss-eval-rtx-1g-2")
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

    def test_larger_gpu_partition_satisfies_smaller_requirement(self):
        inst = {"name": "test", "gpu": "RTX PRO SERVER 6000", "instance_type": "rtx-2g"}

        async def fake_catalog_count(instance_type):
            return 2

        original = brev_env._get_instance_gpu_count_from_catalog
        brev_env._get_instance_gpu_count_from_catalog = fake_catalog_count
        try:
            asyncio.run(
                brev_env._check_instance_matches(
                    inst,
                    {"gpu_type": "RTX PRO 6000", "gpu_count": 1},
                )
            )
        finally:
            brev_env._get_instance_gpu_count_from_catalog = original

    def test_smaller_gpu_partition_fails_larger_requirement(self):
        inst = {"name": "test", "gpu": "RTX PRO SERVER 6000", "instance_type": "rtx-1g"}

        async def fake_catalog_count(instance_type):
            return 1

        original = brev_env._get_instance_gpu_count_from_catalog
        brev_env._get_instance_gpu_count_from_catalog = fake_catalog_count
        try:
            with self.assertRaisesRegex(RuntimeError, "want at least 2"):
                asyncio.run(
                    brev_env._check_instance_matches(
                        inst,
                        {"gpu_type": "RTX PRO 6000", "gpu_count": 2},
                    )
                )
        finally:
            brev_env._get_instance_gpu_count_from_catalog = original


class NemoClawBrevCommands(unittest.IsolatedAsyncioTestCase):

    async def test_start_wipes_stale_artifacts_without_deleting_trial_inputs(self):
        calls = []

        async def fake_find_brev_instance(name):
            return {"name": name, "gpu": "RTX PRO SERVER 6000", "instance_type": "rtx-1g"}

        async def fake_check_instance_matches(instance, requirements):
            return None

        async def fake_check_live_resources(instance, requirements):
            return None

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            if "echo harbor-ready" in command:
                return brev_env.ExecResult(stdout="harbor-ready\n", stderr=None, return_code=0)
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original_find = brev_env._find_brev_instance
        original_match = brev_env._check_instance_matches
        original_live = brev_env._check_live_resources
        original_exec = brev_env._run_brev_exec
        original_default = brev_env.DEFAULT_INSTANCE
        brev_env._find_brev_instance = fake_find_brev_instance
        brev_env._check_instance_matches = fake_check_instance_matches
        brev_env._check_live_resources = fake_check_live_resources
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env.DEFAULT_INSTANCE = "vss-eval-test"
        try:
            with tempfile.TemporaryDirectory() as td:
                task_dir = Path(td) / "task"
                env_dir = task_dir / "environment"
                env_dir.mkdir(parents=True)
                (task_dir / "task.toml").write_text(
                    "[metadata]\nrunner = \"nemoclaw\"\n",
                    encoding="utf-8",
                )

                async def noop(self, *args, **kwargs):
                    return None

                env = brev_env.BrevEnvironment()
                env.environment_dir = env_dir
                env._reset_docker_runtime = types.MethodType(noop, env)
                env._sync_repo_to_pr_head = types.MethodType(noop, env)
                env._ensure_nemoclaw_ready = types.MethodType(noop, env)
                await env.start(force_build=False)
        finally:
            brev_env._find_brev_instance = original_find
            brev_env._check_instance_matches = original_match
            brev_env._check_live_resources = original_live
            brev_env._run_brev_exec = original_exec
            brev_env.DEFAULT_INSTANCE = original_default

        reset_commands = [command for _, command, _ in calls if "sudo rm -rf" in command]
        self.assertEqual(len(reset_commands), 1)
        reset = reset_commands[0]
        self.assertIn("/logs/artifacts", reset)
        self.assertIn("/logs/verifier", reset)
        self.assertNotIn("rm -rf /tests", reset)
        self.assertNotIn("rm -rf /solution", reset)
        self.assertNotIn("rm -rf /skills", reset)
        self.assertIn("mkdir -p /logs/agent /logs/verifier /logs/artifacts /tests /solution /skills", reset)
        self.assertLess(reset.index("sudo rm -rf"), reset.index("sudo mkdir -p"))

    async def test_upload_dir_replaces_trial_input_dirs(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        async def fake_run_brev_copy(src, dst, timeout=brev_env.BREV_COPY_TIMEOUT):
            calls.append(("copy", f"{src}->{dst}", timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original_exec = brev_env._run_brev_exec
        original_copy = brev_env._run_brev_copy
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env._run_brev_copy = fake_run_brev_copy
        try:
            with tempfile.TemporaryDirectory() as td:
                source = Path(td) / "tests"
                source.mkdir()
                (source / "nemoclaw_prompt.md").write_text("fresh prompt\n", encoding="utf-8")

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                await env.upload_dir(source, "/tests")
        finally:
            brev_env._run_brev_exec = original_exec
            brev_env._run_brev_copy = original_copy

        extract_commands = [
            command for _, command, _ in calls
            if isinstance(command, str) and "tar -xzf" in command
        ]
        self.assertEqual(len(extract_commands), 1)
        command = extract_commands[0]
        self.assertIn("sudo rm -rf /tests", command)
        self.assertLess(command.index("sudo rm -rf /tests"), command.index("tar -xzf"))

    async def test_upload_dir_does_not_replace_non_trial_dirs(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        async def fake_run_brev_copy(src, dst, timeout=brev_env.BREV_COPY_TIMEOUT):
            calls.append(("copy", f"{src}->{dst}", timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original_exec = brev_env._run_brev_exec
        original_copy = brev_env._run_brev_copy
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env._run_brev_copy = fake_run_brev_copy
        try:
            with tempfile.TemporaryDirectory() as td:
                source = Path(td) / "payload"
                source.mkdir()
                (source / "file.txt").write_text("data\n", encoding="utf-8")

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                await env.upload_dir(source, "/tmp/payload")
        finally:
            brev_env._run_brev_exec = original_exec
            brev_env._run_brev_copy = original_copy

        extract_commands = [
            command for _, command, _ in calls
            if isinstance(command, str) and "tar -xzf" in command
        ]
        self.assertEqual(len(extract_commands), 1)
        self.assertNotIn("sudo rm -rf /tmp/payload", extract_commands[0])

    async def test_nemoclaw_setup_sources_profile_without_nounset(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        try:
            env = brev_env.BrevEnvironment()
            env._instance_name = "vss-eval-test"
            await env._ensure_nemoclaw_ready({"required_mcp_tools": ["vss_orchestrator__docker_up"]})
        finally:
            brev_env._run_brev_exec = original

        self.assertEqual(len(calls), 1)
        command = calls[0][1]
        self.assertIn("set -eo pipefail\nset +u\nsource ~/.profile", command)
        self.assertIn("source ~/.profile 2>/dev/null || true\nset -u\nexport PATH", command)
        self.assertNotIn("set -euo pipefail\nsource ~/.profile", command)
        self.assertIn("--required-tools vss_orchestrator__docker_up", command)
        self.assertIn("apt-get install -y -qq libcairo2-dev pkg-config", command)
        self.assertIn("NEMOCLAW_PRESTAGE_ALERTS_MODELS", command)
        self.assertIn("models/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx", command)
        self.assertIn("models/rtdetr-its/model_epoch_035.fp16.onnx", command)

    async def test_nemoclaw_launcher_bypasses_outer_claude(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        previous_fast = os.environ.pop("NEMOCLAW_FAST_READINESS_MODE", None)
        try:
            env = brev_env.BrevEnvironment()
            env._instance_name = "vss-eval-test"
            env._task_metadata = {
                "runner": "nemoclaw",
                "deployment_profile": "base",
                "expected_skill": "vss-ask-video",
            }
            await env.exec(
                "claude --print 'run python3 .github/skill-eval/nemoclaw/headless_runner.py'",
                timeout_sec=123,
            )
        finally:
            brev_env._run_brev_exec = original
            if previous_fast is not None:
                os.environ["NEMOCLAW_FAST_READINESS_MODE"] = previous_fast

        self.assertEqual(len(calls), 1)
        command = calls[0][1]
        self.assertIn("NemoClaw direct Harbor launcher", command)
        self.assertIn("python3 .github/skill-eval/nemoclaw/headless_runner.py", command)
        self.assertIn("--launch-mode cli", command)
        self.assertNotIn("--wait-profile base", command)
        self.assertIn("--expected-skill vss-ask-video", command)
        self.assertIn("--prompt-file /tests/nemoclaw_prompt.md", command)
        self.assertNotIn("claude --print", command)

    async def test_nemoclaw_launcher_can_opt_into_fast_readiness_mode(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        previous_fast = os.environ.get("NEMOCLAW_FAST_READINESS_MODE")
        brev_env._run_brev_exec = fake_run_brev_exec
        os.environ["NEMOCLAW_FAST_READINESS_MODE"] = "1"
        try:
            env = brev_env.BrevEnvironment()
            env._instance_name = "vss-eval-test"
            env._task_metadata = {
                "runner": "nemoclaw",
                "deployment_profile": "base",
                "expected_skill": "vss-ask-video",
            }
            await env.exec(
                "claude --print 'run python3 .github/skill-eval/nemoclaw/headless_runner.py'",
                timeout_sec=123,
            )
        finally:
            brev_env._run_brev_exec = original
            if previous_fast is None:
                os.environ.pop("NEMOCLAW_FAST_READINESS_MODE", None)
            else:
                os.environ["NEMOCLAW_FAST_READINESS_MODE"] = previous_fast

        self.assertIn("--wait-profile base", calls[0][1])

    async def test_nemoclaw_launcher_embeds_current_prompt_when_task_dir_exists(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        try:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(tmp) / "rtxpro6000bw"
                tests_dir = task_dir / "tests"
                env_dir = task_dir / "environment"
                tests_dir.mkdir(parents=True)
                env_dir.mkdir()
                (tests_dir / "nemoclaw_prompt.md").write_text(
                    "Use /vss-deploy-profile for this exact trial.\n",
                    encoding="utf-8",
                )

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                env.environment_dir = env_dir
                env._task_metadata = {
                    "runner": "nemoclaw",
                    "expected_skill": "vss-deploy-profile",
                }
                await env.exec(
                    "claude --print 'run python3 .github/skill-eval/nemoclaw/headless_runner.py'",
                    timeout_sec=123,
                )
        finally:
            brev_env._run_brev_exec = original

        self.assertEqual(len(calls), 1)
        command = calls[0][1]
        self.assertIn("/tmp/skill-eval/nemoclaw/current_prompt.md", command)
        self.assertIn("base64 -d > /tmp/skill-eval/nemoclaw/current_prompt.md", command)
        self.assertIn("--expected-skill vss-deploy-profile", command)
        self.assertNotIn("--prompt-file /tests/nemoclaw_prompt.md", command)

    async def test_nemoclaw_launcher_bypasses_outer_claude_without_metadata(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        try:
            env = brev_env.BrevEnvironment()
            env._instance_name = "vss-eval-test"
            env._task_metadata = {}
            await env.exec(
                "claude --print 'run python3 .github/skill-eval/nemoclaw/headless_runner.py'",
                timeout_sec=123,
            )
        finally:
            brev_env._run_brev_exec = original

        self.assertEqual(len(calls), 1)
        command = calls[0][1]
        self.assertIn("NemoClaw direct Harbor launcher", command)
        self.assertIn("python3 .github/skill-eval/nemoclaw/headless_runner.py", command)
        self.assertNotIn("claude --print", command)

    async def test_nemoclaw_intercept_only_matches_launcher(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        try:
            env = brev_env.BrevEnvironment()
            env._instance_name = "vss-eval-test"
            env._task_metadata = {"runner": "nemoclaw"}
            await env.exec("echo no launcher here", timeout_sec=123)
        finally:
            brev_env._run_brev_exec = original

        self.assertEqual(len(calls), 1)
        self.assertIn("echo no launcher here", calls[0][1])

    async def test_repo_sync_injects_pr_head_from_coordinator_env(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="synced repo to abc1234\n", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        old_head = os.environ.get("PR_HEAD_SHA")
        old_repo = os.environ.get("PR_REPO")
        brev_env._run_brev_exec = fake_run_brev_exec
        os.environ["PR_HEAD_SHA"] = "abc1234"
        os.environ["PR_REPO"] = "NVIDIA-AI-Blueprints/video-search-and-summarization"
        try:
            env = brev_env.BrevEnvironment()
            env._instance_name = "vss-eval-test"
            await env._sync_repo_to_pr_head()
        finally:
            brev_env._run_brev_exec = original
            if old_head is None:
                os.environ.pop("PR_HEAD_SHA", None)
            else:
                os.environ["PR_HEAD_SHA"] = old_head
            if old_repo is None:
                os.environ.pop("PR_REPO", None)
            else:
                os.environ["PR_REPO"] = old_repo

        self.assertEqual(len(calls), 1)
        command = calls[0][1]
        self.assertIn("PR_HEAD_SHA=abc1234", command)
        self.assertIn("PR_REPO=NVIDIA-AI-Blueprints/video-search-and-summarization", command)
        self.assertNotIn('PR_HEAD_SHA="${PR_HEAD_SHA:-}"', command)
        self.assertIn('"$REPO/deployments" "$REPO/deploy/docker/data-dir"', command)
        self.assertIn("sudo rm -rf \"$stale_path\"", command)
        self.assertIn("git clean failed; repairing checkout ownership", command)
        self.assertIn("-path \"$REPO/data\" -prune", command)


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

    async def test_upload_dir_retries_transient_brev_copy_failure(self):
        exec_calls = []
        copy_calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            exec_calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="", stderr=None, return_code=0)

        async def fake_run_brev_copy(src, dst, timeout=brev_env.BREV_COPY_TIMEOUT):
            copy_calls.append((src, dst, timeout))
            if len(copy_calls) == 1:
                return brev_env.ExecResult(
                    stdout="",
                    stderr=(
                        "waiting for instance to be ready... "
                        "rpc error: code = Unavailable desc = error reading from server: EOF"
                    ),
                    return_code=1,
                )
            return brev_env.ExecResult(stdout="", stderr=None, return_code=0)

        original_exec = brev_env._run_brev_exec
        original_copy = brev_env._run_brev_copy
        original_backoff = brev_env.BREV_UPLOAD_BACKOFF_SEC
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env._run_brev_copy = fake_run_brev_copy
        brev_env.BREV_UPLOAD_BACKOFF_SEC = 0
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
            brev_env.BREV_UPLOAD_BACKOFF_SEC = original_backoff

        self.assertEqual(len(copy_calls), 2)
        commands = [call[1] for call in exec_calls]
        self.assertEqual(sum("mkdir -p /tmp/skill-eval/uploads/" in command for command in commands), 2)
        self.assertEqual(sum("tar -xzf" in command for command in commands), 1)


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
