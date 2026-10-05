# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A healthy gateway must authenticate to the current eval leg's NIM adapter."""

import importlib.util
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "nemoclaw" / "headless_runner.py"


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location("nim_route_runner", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def route_env(monkeypatch):
    monkeypatch.setenv("SKILL_EVAL_LOCAL_NIM_API_KEY", "current-leg-key")
    monkeypatch.setenv("COMPATIBLE_API_KEY", "stale-onboard-key")
    monkeypatch.setenv("NEMOCLAW_ENDPOINT_URL", "http://10.229.20.2:18400/v1")
    monkeypatch.setenv("NEMOCLAW_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")


def test_refreshes_stale_gateway_credential_and_verifies_selected_route(runner, monkeypatch):
    route_env(monkeypatch)
    calls = []

    def configure(command, **kwargs):
        calls.append(command)
        assert command[:4] == ["nemoclaw", "se-test", "inference", "set"]
        assert kwargs["env"]["COMPATIBLE_API_KEY"] == "current-leg-key"
        assert "current-leg-key" not in command
        assert "stale-onboard-key" not in command
        assert command[command.index("--model") + 1] == "nvidia/nemotron-3.5-lightning-30b-a3b"
        assert command[command.index("--endpoint-url") + 1] == "http://10.229.20.2:18400/v1"
        assert command[command.index("--inference-api") + 1] == "openai-completions"
        assert "--no-verify" not in command
        return subprocess.CompletedProcess(command, 0, "Route verified", "")

    monkeypatch.setattr(runner.subprocess, "run", configure)
    runner._ensure_local_nim_route("se-test")
    assert len(calls) == 1
    assert runner.os.environ["COMPATIBLE_API_KEY"] == "stale-onboard-key"


def test_hosted_route_does_not_reconfigure_gateway(runner, monkeypatch):
    monkeypatch.delenv("SKILL_EVAL_LOCAL_NIM_API_KEY", raising=False)
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("hosted route mutated"))
    runner._ensure_local_nim_route("se-test")


def test_missing_route_stops_before_provider_mutation(runner, monkeypatch):
    route_env(monkeypatch)
    monkeypatch.delenv("NEMOCLAW_ENDPOINT_URL")
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("incomplete route mutated"))
    with pytest.raises(RuntimeError, match="selected endpoint and model"):
        runner._ensure_local_nim_route("se-test")


def test_failed_authentication_stops_prompt_and_records_current_error(runner, monkeypatch, tmp_path):
    route_env(monkeypatch)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Operate the deployment")
    logs = tmp_path / "logs"
    monkeypatch.setattr(runner, "_load_env_file", lambda path: None)
    monkeypatch.setattr(runner, "_ensure_gateway", lambda sandbox: None)
    monkeypatch.setattr(runner, "_run_openclaw", lambda *args: pytest.fail("prompt ran without inference"))
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "No connected db. current-leg-key stale-onboard-key"))
    assert runner.main(["--prompt-file", str(prompt), "--agent-log-dir", str(logs)]) == 1
    evidence = (logs / "agent.log").read_text()
    assert "Local NIM gateway route verification failed" in evidence
    assert "No connected db." in evidence
    assert "current-leg-key" not in evidence
    assert "stale-onboard-key" not in evidence
    assert not (logs / "openclaw.txt").exists()
