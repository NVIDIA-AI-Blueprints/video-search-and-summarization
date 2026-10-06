# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Readiness failures identify the failing boundary and never start a turn."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "nemoclaw/headless_runner.py"


@pytest.mark.parametrize("failed_stage", ["sandbox_access", "gateway_health", "gateway_authentication", "vss_configuration", None])
def test_readiness_stages_stop_at_failure(monkeypatch, tmp_path, failed_stage):
    spec = importlib.util.spec_from_file_location("readiness_runner", SCRIPT)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    names = {"true": "sandbox_access", "openclaw gateway call health --json": "gateway_authentication", "vss configure check": "vss_configuration"}
    def probe(sandbox, command, **kwargs):
        rc = 1 if names[command] == failed_stage else 0
        return subprocess.CompletedProcess(command, rc, '{"ok":true}', "secret-must-not-be-recorded")
    def ensure(sandbox):
        if failed_stage == "gateway_health":
            raise RuntimeError("gateway stopped")
    monkeypatch.setattr(runner, "_sandbox_exec", probe)
    monkeypatch.setattr(runner, "_ensure_gateway", ensure)
    evidence = tmp_path / "readiness.json"
    if failed_stage:
        with pytest.raises(RuntimeError):
            runner._check_readiness("se-test", evidence)
    else:
        runner._check_readiness("se-test", evidence)
    rows = json.loads(evidence.read_text())["stages"]
    assert "secret" not in evidence.read_text()
    assert all(row["status"] == "passed" for row in rows[:-1])
    assert rows[-1]["stage"] == (failed_stage or "vss_configuration")
    assert rows[-1]["status"] == ("failed" if failed_stage else "passed")


def test_http_listener_is_not_authenticated_gateway(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("readiness_runner", SCRIPT)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "_ensure_gateway", lambda _: None)
    monkeypatch.setattr(runner, "_sandbox_exec", lambda *a, **kw: subprocess.CompletedProcess(a, 0, '{"ok":false}', ""))
    with pytest.raises(RuntimeError, match="gateway_authentication"):
        runner._check_readiness("se-test", tmp_path / "readiness.json")


@pytest.mark.parametrize("pending", [True, False])
def test_only_pending_pairing_is_retried(monkeypatch, tmp_path, pending):
    spec = importlib.util.spec_from_file_location("readiness_runner", SCRIPT)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    calls = []
    def probe(sandbox, command, **kwargs):
        if "gateway call" in command:
            calls.append(command)
            if len(calls) == 1:
                return subprocess.CompletedProcess(command, 1, "", "scope upgrade pending approval" if pending else "invalid token")
        return subprocess.CompletedProcess(command, 0, '{"ok":true}', "")
    monkeypatch.setattr(runner, "_ensure_gateway", lambda _: None)
    monkeypatch.setattr(runner, "_sandbox_exec", probe)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    if pending:
        runner._check_readiness("se-test", tmp_path / "readiness.json")
        assert len(calls) == 2
    else:
        with pytest.raises(RuntimeError, match="gateway_authentication"):
            runner._check_readiness("se-test", tmp_path / "readiness.json")
        assert len(calls) == 1
