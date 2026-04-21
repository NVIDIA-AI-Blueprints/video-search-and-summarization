#!/usr/bin/env python3
"""Unit tests for registered-node SSH fallback in brev_env.py.

These tests don't need an actual Brev instance — they monkeypatch the
module-level `_registered_nodes_cache` and stub asyncio subprocess calls.

Run manually:
    python3 -m pytest tools/eval/harbor/envs/tests/test_registered_node.py -v
Or directly:
    python3 tools/eval/harbor/envs/tests/test_registered_node.py
"""
from __future__ import annotations

import asyncio
import sys
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
        brev_env._check_instance_matches(inst, {"gpu_type": "GB10"})

    def test_brev_managed_still_checks_gpu(self):
        """Non-registered instances still enforce GPU-name match."""
        inst = {"name": "test", "gpu": "L40S"}
        with self.assertRaises(RuntimeError):
            brev_env._check_instance_matches(inst, {"gpu_type": "H100"})


class VersionCompareSanity(unittest.TestCase):
    """Extra coverage for _version_lt beyond the generate.py tests."""

    def test_driver_version_ordering(self):
        self.assertTrue(brev_env._version_lt("570.195.03", "580.95"))
        self.assertTrue(brev_env._version_lt("565.57.01", "580.95"))
        self.assertFalse(brev_env._version_lt("580.105.08", "580.95"))
        self.assertFalse(brev_env._version_lt("580.95", "580.95"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
