# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Static contracts for the skill-eval Python runtime."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_VERSION = "3.12"
SDK_REQUIREMENT = "claude-agent-sdk==0.2.128"


def test_pr_and_daily_workflows_pin_every_python_job() -> None:
    python_job_counts = {
        ".github/workflows/skills-eval.yml": 2,
        ".github/workflows/skills-eval-daily.yml": 2,
    }
    for relative_path, python_job_count in python_job_counts.items():
        workflow = (REPO_ROOT / relative_path).read_text()
        assert f'SKILL_EVAL_PYTHON_VERSION: "{PYTHON_VERSION}"' in workflow
        assert workflow.count("name: Set up skill-eval Python") == python_job_count
        assert (
            workflow.count("python-version: ${{ env.SKILL_EVAL_PYTHON_VERSION }}")
            == python_job_count
        )
        assert workflow.count("name: Prepare isolated agent runtime") == 1
        assert SDK_REQUIREMENT in workflow
        assert 'export PATH=' in workflow
        assert "$skill_eval_venv_dir/bin" in workflow
        if relative_path.endswith("skills-eval.yml"):
            assert 'export PATH="/usr/local/bin:$skill_eval_venv_dir/bin:$PATH"' in workflow
            assert "Assert OpenShell GPU runtime" in workflow
            assert "uv tool run" in workflow


def test_ci_executes_harness_contracts_on_production_python() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text()
    assert f'SKILL_EVAL_EXPECTED_PYTHON_VERSION: "{PYTHON_VERSION}"' in workflow
    assert f'uvx --python {PYTHON_VERSION} --from "pytest==9.1.1" pytest' in workflow
