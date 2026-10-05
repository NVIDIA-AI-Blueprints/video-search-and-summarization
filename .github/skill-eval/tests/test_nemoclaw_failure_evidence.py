# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Failed operational prompts must leave evidence for their own trial."""

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "nemoclaw" / "headless_runner.py"


@pytest.mark.parametrize("stage", ["gateway", "agent"])
def test_failure_is_available_to_the_verifier(monkeypatch, tmp_path, stage):
    spec = importlib.util.spec_from_file_location("nemoclaw_headless_runner", SCRIPT)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("This operational task")
    logs = tmp_path / "agent"
    message = f"this trial's {stage} failed"

    def fail(*args):
        raise RuntimeError(message)

    monkeypatch.setattr(runner, "_load_env_file", lambda path: None)
    monkeypatch.setattr(runner, "_ensure_gateway", fail if stage == "gateway" else lambda name: None)
    monkeypatch.setattr(runner, "_run_openclaw", fail)
    assert runner.main(["--prompt-file", str(prompt), "--agent-log-dir", str(logs)]) == 1
    assert message in (logs / "agent.log").read_text()
    assert not (logs / "openclaw.session.jsonl").exists()
    assert not (logs / "trajectory.json").exists()
