# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Operational prompts use the onboarded route without replacing its provider."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "nemoclaw" / "headless_runner.py"


@pytest.mark.parametrize("local_nim", [False, True])
def test_prompt_uses_native_inference_without_mutating_provider(monkeypatch, tmp_path, local_nim):
    spec = importlib.util.spec_from_file_location("nim_route_runner", SCRIPT)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setenv("NEMOCLAW_SANDBOX_NAME", "se-test")
    monkeypatch.setenv("COMPATIBLE_API_KEY", "stale-onboard-key")
    if local_nim:
        monkeypatch.setenv("SKILL_EVAL_LOCAL_NIM_API_KEY", "local-nim")
    else:
        monkeypatch.delenv("SKILL_EVAL_LOCAL_NIM_API_KEY", raising=False)
    monkeypatch.setattr(runner, "_load_env_file", lambda path: None)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Operate the deployment")
    logs = tmp_path / "logs"
    session_path = "/sandbox/.openclaw/agents/main/sessions/test.jsonl"
    calls = []

    def sandbox_exec(sandbox, script, **kwargs):
        assert sandbox == "se-test"
        calls.append(script)
        if script in ("true", "vss configure check"):
            output = ""
        elif script == "openclaw gateway call health --json":
            output = '{"ok":true}'
        elif "/health" in script:
            output = ""
        elif "openclaw agent" in script:
            assert "Operate the deployment" in script
            assert ". /tmp/nemoclaw-proxy-env.sh" in script
            output = json.dumps({"meta": {"agentMeta": {"sessionFile": session_path}}})
        elif script == f"cat -- {session_path}":
            output = json.dumps({"message": {
                "role": "assistant", "content": [{"type": "text", "text": "Done"}],
                "usage": {"input": 5, "output": 2},
            }})
        else:
            pytest.fail(f"Unexpected sandbox command: {script}")
        return subprocess.CompletedProcess(script, 0, output, "")

    monkeypatch.setattr(runner, "_sandbox_exec", sandbox_exec)
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("host provider mutated"))
    assert runner.main(["--prompt-file", str(prompt), "--agent-log-dir", str(logs)]) == 0
    assert len(calls) == 6
    envelope = json.loads((logs / "openclaw.txt").read_text())
    assert envelope["meta"]["agentMeta"]["usage"]["input"] == 5
    assert envelope["meta"]["agentMeta"]["usage"]["output"] == 2
    assert (logs / "openclaw.session.jsonl").exists()
    assert not (logs / "agent.log").exists()
