# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contracts for the isolated OpenShell Harbor runtime."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location(
    "openshell_run_leg", ROOT / "openshell" / "run_leg.py"
)
run_leg = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = run_leg
SPEC.loader.exec_module(run_leg)


def _command(agent: str) -> list[str]:
    invocation = run_leg.HarborInvocation(
        harbor_root=Path("/tmp/task"),
        include_task_name="step-1",
        chain_key="test",
    )
    return run_leg.build_harbor_command(
        invocation,
        Path("/tmp/results"),
        "test-model",
        "https://example.invalid",
        agent,
    )


def test_claude_uses_openshell_environment() -> None:
    command = _command("claude-code")
    index = command.index("--environment-import-path")
    assert command[index + 1] == "openshell.env:OpenShellEnvironment"


def test_nemoclaw_uses_openshell_environment() -> None:
    command = _command("nemoclaw")
    index = command.index("--environment-import-path")
    assert (
        command[index + 1]
        == "openshell.nemoclaw_env:NemoClawOpenShellEnvironment"
    )


def test_harbor_is_pinned_to_one_trial_and_no_retry() -> None:
    command = _command("claude-code")
    assert command[command.index("-n") + 1] == "1"
    assert command[command.index("--max-retries") + 1] == "0"
