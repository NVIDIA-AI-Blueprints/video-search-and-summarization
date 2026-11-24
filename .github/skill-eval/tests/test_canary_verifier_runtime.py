# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared worker verifier runtime contracts."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SKILL_EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_EVAL))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


run_leg = _load("canary_run_leg", SKILL_EVAL / "run_leg.py")
alerts = _load(
    "canary_alerts_adapter", SKILL_EVAL / "adapters/vss-manage-alerts/generate.py"
)
judge = _load("shared_judge", SKILL_EVAL / "verifiers/generic_judge.py")


class SharedVerifierRuntime(unittest.TestCase):
    def test_runtime_preflight_follows_shared_judge_in_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp)
            self.assertFalse(run_leg.needs_judge_verifier_runtime(dataset))
            tests = dataset / "some-future-skill" / "tests"
            tests.mkdir(parents=True)
            (tests / "generic_judge.py").touch()
            self.assertTrue(run_leg.needs_judge_verifier_runtime(dataset))

    def test_regular_alerts_no_longer_uses_system_pip(self):
        with mock.patch.dict(
            os.environ,
            {
                "SKILLS_EVAL_CLEAN_WORKER_AFTER_LEG": "false",
                "SKILLS_EVAL_OPERATIONAL_HARNESS": "claude-code",
            },
        ):
            script = alerts._test_sh(1, "cv_mode_gate.json")
        self.assertNotIn("pip install", script)
        self.assertIn('python3 "$TEST_DIR/generic_judge.py"', script)
        self.assertNotIn("verifier-py312-sdk-0.2.128", script)

    def test_canary_uses_pinned_worker_python_regardless_of_cleanup(self):
        for cleanup in ("true", "false"):
            with self.subTest(cleanup=cleanup), mock.patch.dict(
                os.environ,
                {
                    "SKILLS_EVAL_CLEAN_WORKER_AFTER_LEG": cleanup,
                    "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
                },
            ):
                script = alerts._test_sh(1, "cv_mode_gate.json")
                self.assertIn("set -euo pipefail", script)
                self.assertIn(
                    "$HOME/.cache/vss-skill-eval/verifier-py312-sdk-0.2.128/bin/python",
                    script,
                )
                self.assertIn('"$VERIFIER_PYTHON" "$TEST_DIR/generic_judge.py"', script)
                self.assertNotIn("pip install", script)

    def test_worker_preflight_checks_version_and_installs_only_if_missing(self):
        completed = subprocess.CompletedProcess([], 0, "judge verifier runtime ready", "")
        with mock.patch.object(
            run_leg.subprocess, "run", return_value=completed
        ) as remote:
            run_leg.prepare_judge_verifier_runtime("vss-eval-l40s-2g-1")
        command = remote.call_args.args[0]
        self.assertEqual(command[-3], "vss-eval-l40s-2g-1")
        self.assertIn("if ! check_runtime; then", command[-2])
        self.assertIn("uv venv --python 3.12 --clear", command[-2])
        self.assertIn("claude-agent-sdk==0.2.128", command[-2])
        self.assertIn('importlib.metadata.version("claude-agent-sdk")', command[-2])

    def test_worker_preflight_fails_before_harbor_on_missing_uv(self):
        completed = subprocess.CompletedProcess([], 1, "", "uv missing on worker")
        with mock.patch.object(run_leg.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "uv missing on worker"):
                run_leg.prepare_judge_verifier_runtime("vss-eval-l40s-2g-1")

    def test_shared_judge_reexecs_into_prepared_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            python = Path(tmp) / ".cache/vss-skill-eval/verifier-py312-sdk-0.2.128/bin/python"
            python.parent.mkdir(parents=True)
            python.touch()
            with mock.patch.object(judge.Path, "home", return_value=Path(tmp)), \
                 mock.patch.object(judge.os, "execv") as execv:
                judge._use_isolated_runtime()
            execv.assert_called_once()
            self.assertEqual(execv.call_args.args[0], str(python))
            self.assertEqual(execv.call_args.args[1][0], str(python))

    def test_shared_judge_stays_on_local_python_without_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(judge.Path, "home", return_value=Path(tmp)), \
                 mock.patch.object(judge.os, "execv") as execv:
                judge._use_isolated_runtime()
            execv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
