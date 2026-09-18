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
        # A guest ~/.eval_env is sourced with `set -a`, so its PATH can drop
        # /usr/bin and hide nvidia-smi from the live GPU probe. Appended,
        # never prepended: the per-leg venv still wins.
        assert (
            'export PATH="$skill_eval_venv_dir/bin:/usr/local/bin:'
            '${PATH:+$PATH:}/usr/bin:/bin"'
        ) in workflow
        if relative_path.endswith("skills-eval.yml"):
            assert '"$skill_eval_venv_dir/bin/python" .github/skill-eval/skills_eval_agent.py' in workflow
            assert "python3 .github/skill-eval/skills_eval_agent.py" not in workflow
            assert "Assert OpenShell GPU runtime" in workflow
            assert "matrix.local_gpu" in workflow
            assert "rejected OpenShell guest that also has the l40s label" not in workflow
            assert "no GPU visible to this job" in workflow
            assert "openshell-eval" not in workflow
            assert "startsWith(runner.name, 'vss-skill-eval-gpu-a40-')" not in workflow
            assert "uv tool run" in workflow
            assert "base64 -d" in workflow
            assert "python3.12" in workflow
            assert "no skill-eval env file" not in workflow
            assert "/home/ubuntu/eval-coordinator/.env" not in workflow
            assert "secrets.NGC_CLI_API_KEY" in workflow
            assert not any(
                line.startswith("#!/bin/sh") for line in workflow.splitlines()
            )


def test_ci_executes_harness_contracts_on_production_python() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text()
    assert f'SKILL_EVAL_EXPECTED_PYTHON_VERSION: "{PYTHON_VERSION}"' in workflow
    assert f'uvx --python {PYTHON_VERSION} --from "pytest==9.1.1" pytest' in workflow


def test_openshell_sweep_runs_the_daily_jobs_on_openshell_guests() -> None:
    """Daily plans on Ubuntu and runs every trial on an OpenShell guest."""
    daily = (
        REPO_ROOT / ".github/workflows/skills-eval-daily.yml"
    ).read_text()

    for job in ("resolve_ref:", "plan:", "eval:"):
        assert job in daily
    assert "DAILY_RUN: true" in daily
    assert 'export DAILY_RUN="true"' in daily

    assert "EVAL_FLEET: openshell" in daily
    assert 'OPENSHELL_GPU_FLEET: "1"' in daily
    assert "SKIP_SKILLS" not in daily
    assert "TRIGGER_OPENSHELL_EVAL" not in daily
    assert "runs-on: ${{ matrix.runs_on }}" in daily
    assert "Assert OpenShell GPU runtime" in daily
    assert "rejected OpenShell guest that also has the l40s label" not in daily
    assert "no GPU visible to this job" in daily
    assert "openshell-eval" not in daily
    assert "SKILL_EVAL_LOCAL_GPU_INSTANCE=%s" in daily
    assert "unset BREV_INSTANCE" in daily
    assert "/home/ubuntu/eval-coordinator/.env" not in daily
    assert "secrets.NGC_CLI_API_KEY" in daily
    assert daily.count("runs-on: ubuntu-24.04") == 2
    assert "vss-skill-eval-runner" not in daily
    assert "runs-on: [vss-skill-eval-gpu, openshell-runner, openshell]" not in daily
    assert "runner.environment == 'github-hosted'" in daily
    assert not any(line.startswith("#!/bin/sh") for line in daily.splitlines())
    assert "matrix.kind == 'not_run_infra_acquisition'" in daily

    # EVAL_FLEET=openshell alone selects the carrier corpus; a workflow-level
    # count-only switch would put corpus skills back on guests whose cards
    # their adapters cannot size for.
    assert "OPENSHELL_COUNT_ONLY" not in daily

    # A carrier leg carries no SKU, so sizing is resolved from the guest's
    # own card instead of the matrix.
    assert 'if [ -z "$EVAL_PLATFORM" ]; then' in daily
    assert (
        'EVAL_PLATFORM="$(python3 .github/skill-eval/live_platform.py)"' in daily
    )
    assert 'HARDWARE_PROFILE="$EVAL_PLATFORM"' in daily

    # PR eval workflow on this branch is unchanged and has no fleet switch.
    pr = (REPO_ROOT / ".github/workflows/skills-eval.yml").read_text()
    assert "EVAL_FLEET" not in pr
    assert "workflow_call:" not in pr
    assert not (REPO_ROOT / ".github/workflows/skills-eval-openshell.yml").exists()
