# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end-ish check that VSS is deployed by the ENVIRONMENT, before the agent turn.

Drives `NemoClawBrevEnvironment._predeploy_vss` against a real dataset produced
by the vss-ask-video adapter, so a drift between adapter output and environment
expectation fails here rather than on a GPU box 40 minutes into a leg.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# Stub harbor so brev_env is importable (same shim the sibling tests use).
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

SKILL_EVAL_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = SKILL_EVAL_DIR.parents[1]
sys.path.insert(0, str(SKILL_EVAL_DIR))

from envs import nemoclaw_brev_env  # noqa: E402


def _generate_ask_video_dataset(out: Path, spec_stem: str) -> Path:
    """Run the real adapter; return the step-1 task dir."""
    subprocess.run(
        [
            sys.executable,
            str(SKILL_EVAL_DIR / "adapters/vss-ask-video/generate.py"),
            "--output-dir", str(out),
            "--skill-dir", str(REPO_ROOT / "skills/vss-ask-video"),
            "--deploy-skill-dir", str(REPO_ROOT / "skills/vss-deploy-profile"),
            "--video-io-skill-dir",
            str(REPO_ROOT / "skills/vss-manage-video-io-storage"),
            "--spec",
            str(REPO_ROOT / f"skills/vss-ask-video/evals/{spec_stem}.json"),
        ],
        check=True,
        capture_output=True,
        cwd=str(REPO_ROOT),
    )
    step1 = next(p for p in sorted(out.rglob("task.toml")) if p.parent.name == "step-1")
    return step1.parent


class _Env(nemoclaw_brev_env.NemoClawBrevEnvironment):
    """Minimal stand-in: only the bits `_predeploy_vss` touches."""

    def __init__(self, task_dir: Path):
        self.environment_dir = task_dir / "environment"
        self._instance_name = "vss-eval-l40s-test"


class PredeployHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.base_task = _generate_ask_video_dataset(
            root / "base", "base_profile_video_understanding"
        )
        cls.direct_task = _generate_ask_video_dataset(
            root / "direct", "direct_vlm_video_understanding"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _run(self, task_dir: Path, return_code: int = 0):
        env = _Env(task_dir)
        calls: list[tuple[str, str]] = []

        async def fake_exec(instance, command, timeout=None):
            calls.append((instance, command))
            return _ExecResult(return_code=return_code, stdout="", stderr="boom")

        # asyncio.run(), not get_event_loop(): sibling suites close the loop,
        # so a shared one makes this file pass alone and fail in the full run.
        with mock.patch.object(nemoclaw_brev_env, "_run_brev_exec", fake_exec):
            if return_code == 0:
                asyncio.run(env._predeploy_vss())
            else:
                with self.assertRaisesRegex(RuntimeError, "VSS pre-deploy failed"):
                    asyncio.run(env._predeploy_vss())
        return calls

    def test_adapter_metadata_drives_a_deploy_before_the_agent_turn(self) -> None:
        calls = self._run(self.base_task)
        self.assertEqual(len(calls), 1, "expected exactly one pre-deploy exec")
        instance, command = calls[0]
        self.assertEqual(instance, "vss-eval-l40s-test")
        self.assertIn("predeploy.py", command)
        self.assertIn("--profile base", command)

    def test_predeploy_runs_for_both_ask_video_specs(self) -> None:
        for task in (self.base_task, self.direct_task):
            with self.subTest(task=task.parts[-3]):
                self.assertEqual(len(self._run(task)), 1)

    def test_command_pins_repo_dir_and_does_not_use_dev_profile_sh(self) -> None:
        _, command = self._run(self.base_task)[0]
        # predeploy.py derives VSS_REPO_DIR from HOME, which the command
        # reassigns to the nemoclaw home -- so it must be pinned explicitly.
        self.assertIn('export VSS_REPO_DIR="$repo"', command)
        # The documented NemoClaw path is the orchestrator MCP, not the script.
        self.assertNotIn("dev-profile.sh", command)

    def test_predeploy_runs_on_python_312_not_the_box_python3(self) -> None:
        """orchestrator_mcp_helper.py does `from enum import StrEnum` (3.11+).

        The Brev boxes ship python3 == 3.10, so invoking predeploy.py with bare
        `python3` ImportErrors before a single MCP call. Run 33587758108 failed
        both legs on exactly this. Must match `_setup_command`'s interpreter.
        """
        _, command = self._run(self.base_task)[0]
        self.assertIn("--python 3.12", command)
        self.assertNotIn("python3 .github/skill-eval/nemoclaw/predeploy.py", command)

    def test_helper_still_requires_311_so_the_pin_stays_load_bearing(self) -> None:
        helper = (
            REPO_ROOT / "deploy/docker/scripts/orchestrator_mcp_helper.py"
        ).read_text()
        self.assertIn(
            "from enum import StrEnum",
            helper,
            "if the helper drops StrEnum this pin can be revisited",
        )

    def test_failed_predeploy_fails_the_leg(self) -> None:
        # A silent pre-deploy failure would surface as unexplained check
        # failures in every later step.
        self._run(self.base_task, return_code=1)

    def test_skipped_when_adapter_emits_no_profile(self) -> None:
        """Opt-in contract: no `profile` in [metadata] means no pre-deploy.

        This is what keeps vss-deploy-* / vss-setup-* correct.
        """
        with tempfile.TemporaryDirectory() as td:
            task = Path(td) / "step-1"
            (task / "environment").mkdir(parents=True)
            (task / "task.toml").write_text(
                '[metadata]\nskill = "vss-deploy-profile"\nplatform = "L40S"\n'
            )
            self.assertEqual(self._run(task), [])

    def test_no_generated_instruction_tells_the_agent_to_deploy(self) -> None:
        """The whole point: deployment is not triggered by an expects[] step."""
        for task in (self.base_task, self.direct_task):
            for instruction in sorted(task.parent.parent.rglob("instruction.md")):
                text = instruction.read_text()
                with self.subTest(instruction=str(instruction)):
                    self.assertNotIn("/vss-deploy-profile -p", text)
                    self.assertIn("ALREADY DEPLOYED", text)


if __name__ == "__main__":
    unittest.main()
