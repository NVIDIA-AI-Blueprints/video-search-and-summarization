# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Canary-only worker verifier runtime contracts."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
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


class CanaryVerifierRuntime(unittest.TestCase):
    def test_regular_alerts_verifier_is_unchanged(self):
        with mock.patch.dict(
            os.environ, {"SKILLS_EVAL_CLEAN_WORKER_AFTER_LEG": "false"}
        ):
            script = alerts._test_sh(1, "cv_mode_gate.json")
        self.assertIn("python3 -m pip install --quiet 'anthropic>=0.40.0'", script)
        self.assertIn('python3 "$TEST_DIR/generic_judge.py"', script)
        self.assertNotIn("verifier-py312-sdk-0.2.128", script)

    def test_canary_uses_pinned_worker_python_without_silent_install(self):
        with mock.patch.dict(
            os.environ, {"SKILLS_EVAL_CLEAN_WORKER_AFTER_LEG": "true"}
        ):
            script = alerts._test_sh(1, "cv_mode_gate.json")
        self.assertIn("set -euo pipefail", script)
        self.assertIn(
            "$HOME/.cache/vss-skill-eval/verifier-py312-sdk-0.2.128/bin/python", script
        )
        self.assertIn('"$VERIFIER_PYTHON" "$TEST_DIR/generic_judge.py"', script)
        self.assertNotIn("pip install", script)

    def test_worker_preflight_checks_version_and_installs_only_if_missing(self):
        completed = subprocess.CompletedProcess(
            [], 0, "canary verifier runtime ready", ""
        )
        with mock.patch.object(
            run_leg.subprocess, "run", return_value=completed
        ) as remote:
            run_leg.prepare_canary_verifier_runtime("vss-eval-l40s-2g-1")
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
                run_leg.prepare_canary_verifier_runtime("vss-eval-l40s-2g-1")


if __name__ == "__main__":
    unittest.main()
