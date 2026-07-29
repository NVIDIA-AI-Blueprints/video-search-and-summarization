# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Static contracts for direct GPU skill-eval workflow scheduling."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = (
    REPO_ROOT / ".github" / "workflows" / "skills-eval.yml",
    REPO_ROOT / ".github" / "workflows" / "skills-eval-daily.yml",
)
INSTALLER = REPO_ROOT / ".github" / "skill-eval" / "install-local-gpu-runner.sh"
JOB_HOOK = REPO_ROOT / ".github" / "skill-eval" / "local-gpu-job-started.sh"
SERVICE = REPO_ROOT / ".github" / "skill-eval" / "local-gpu-runner.service"
SUPERVISOR = (
    REPO_ROOT
    / ".github"
    / "skill-eval"
    / "local-gpu-runner-supervise.sh"
)
BREV_ENV = REPO_ROOT / ".github" / "skill-eval" / "envs" / "brev_env.py"


class GpuRunnerWorkflowContract(unittest.TestCase):
    def test_eval_jobs_consume_planned_hardware_labels(self):
        for workflow in WORKFLOWS:
            text = workflow.read_text()
            self.assertIn("runs-on: ${{ matrix.runner_labels }}", text)
            self.assertIn("runs-on: ubuntu-24.04", text)

    def test_coordinator_environment_path_is_runner_relative(self):
        for workflow in WORKFLOWS:
            text = workflow.read_text()
            self.assertIn(
                "${SKILL_EVAL_ENV_FILE:-$HOME/eval-coordinator/.env}",
                text,
            )
            self.assertNotIn(
                "source /home/ubuntu/eval-coordinator/.env",
                text,
            )

    def test_direct_runner_is_supervised_and_staged_until_drain(self):
        installer = INSTALLER.read_text()
        hook = JOB_HOOK.read_text()
        service = SERVICE.read_text()
        supervisor = SUPERVISOR.read_text()
        brev_env = BREV_ENV.read_text()

        self.assertIn(
            "systemctl enable --now vss-skill-eval-gpu-runner.service",
            installer,
        )
        self.assertIn("Restart=always", service)
        self.assertIn("ExecStart=/opt/actions-runner/supervise.sh", service)
        self.assertIn("while true", supervisor)
        self.assertIn("direct-gpu-runner.enabled", hook)
        self.assertIn("Driver/library version mismatch", hook)
        self.assertIn("nvidia-quarantine", hook)
        self.assertIn("DIRECT_GPU_RUNNER_MARKER", brev_env)


if __name__ == "__main__":
    unittest.main()
