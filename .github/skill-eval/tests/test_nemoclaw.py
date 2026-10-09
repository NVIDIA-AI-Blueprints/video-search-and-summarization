# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""NemoClaw gateway isolation, media staging, and operational readiness."""

import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess

import pytest

NEMOCLAW = Path(__file__).resolve().parents[1] / "nemoclaw"


def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, NEMOCLAW / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner():
    return _load_module("nemoclaw_test_runner", "headless_runner.py")


gateway = _load_module("gateway_state", "gateway_state.py")
fixtures = _load_module("fixture_staging", "stage_fixtures.py")



def free_ports():
    listeners = [socket.socket() for _ in range(3)]
    try:
        for listener in listeners:
            listener.bind(("0.0.0.0", 0))
        return [listener.getsockname()[1] for listener in listeners]
    finally:
        for listener in listeners:
            listener.close()



def test_two_jobs_preserve_default_and_previous_registry(tmp_path):
    default = tmp_path / ".nemoclaw" / "sandboxes.json"
    default.parent.mkdir()
    default.write_text('{"sandboxes":{"old-job":{}}}')
    previous = None
    for owner in ("a" * 64, "b" * 64):
        ports = free_ports()
        gateway.claim(owner, ports, tmp_path)
        root = default.parent / "gateways" / str(ports[0])
        registry = root / "sandboxes.json"
        registry.write_text(json.dumps({"sandboxes": {owner: {}}}))
        gateway.claim(owner, ports, tmp_path)  # Later steps keep the same state.
        assert owner in registry.read_text()
        if previous:
            assert previous.read_text() == '{"sandboxes": {"' + "a" * 64 + '": {}}}'
        previous = registry
    assert default.read_text() == '{"sandboxes":{"old-job":{}}}'



def test_namespace_collision_is_not_overwritten(tmp_path):
    ports = free_ports()
    gateway.claim("a" * 64, ports, tmp_path)
    with pytest.raises(ValueError, match="different evaluation"):
        gateway.claim("b" * 64, ports, tmp_path)



def test_unowned_registry_is_not_adopted(tmp_path):
    ports = free_ports()
    root = tmp_path / ".nemoclaw/gateways" / str(ports[0])
    root.mkdir(parents=True)
    (root / "sandboxes.json").write_text("{}")
    with pytest.raises(ValueError, match="unowned"):
        gateway.claim("a" * 64, ports, tmp_path)
    assert not (root / "skill-eval-owner.json").exists()



def test_busy_listener_is_not_stopped_or_claimed(tmp_path):
    ports = free_ports()
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", ports[1]))
        with pytest.raises(OSError):
            gateway.claim("a" * 64, ports, tmp_path)
        assert listener.getsockname()[1] == ports[1]
    assert not (tmp_path / ".nemoclaw/gateways" / str(ports[0])).exists()



@pytest.mark.parametrize("ports", [[8080, 18789, 18790], [45000, 45000, 45002], [45000, 80, 45002]])
def test_invalid_or_shared_ports_are_rejected(tmp_path, ports):
    with pytest.raises(ValueError):
        gateway.claim("a" * 64, ports, tmp_path)



@pytest.mark.parametrize("port", ["8080", "45000"])
def test_notebook_reads_session_from_selected_gateway(monkeypatch, tmp_path, port):
    import os
    notebook = NEMOCLAW.parents[2] / "deploy/docker/scripts/deploy_nemoclaw.ipynb"
    cells = json.loads(notebook.read_text())["cells"]
    source = next("".join(cell["source"]) for cell in cells if "_session_path =" in "".join(cell.get("source", [])))
    block = source[source.index("_gateway_port ="):source.index("_session_path =")]
    block += source[source.index("_session_path ="):].split("\n", 1)[0]
    monkeypatch.setenv("NEMOCLAW_GATEWAY_PORT", port)
    namespace = {"os": os, "HOME_DIR": tmp_path}
    exec(block, namespace)
    root = tmp_path / ".nemoclaw"
    if port != "8080":
        root = root / "gateways" / port
    assert namespace["_session_path"] == root / "onboard-session.json"



def test_allocator_skips_previous_job_and_reuses_own_selection(tmp_path):
    preferred = free_ports()
    gateway.claim("a" * 64, preferred, tmp_path)
    previous = tmp_path / ".nemoclaw/gateways" / str(preferred[0])
    (previous / "sandboxes.json").write_text("previous job registry")
    receipt = (previous / "skill-eval-owner.json").read_text()
    selected = gateway.allocate("b" * 64, preferred, tmp_path)
    assert not set(selected).intersection(preferred)
    assert gateway.allocate("b" * 64, free_ports(), tmp_path) == selected
    assert (previous / "sandboxes.json").read_text() == "previous job registry"
    assert (previous / "skill-eval-owner.json").read_text() == receipt



def test_allocator_skips_unowned_state_and_busy_listener(tmp_path):
    preferred = free_ports()
    root = tmp_path / ".nemoclaw/gateways" / str(preferred[0])
    root.mkdir(parents=True)
    (root / "sandboxes.json").write_text("unowned registry")
    selected = gateway.allocate("c" * 64, preferred, tmp_path)
    assert selected[0] != preferred[0]
    assert (root / "sandboxes.json").read_text() == "unowned registry"
    assert not (root / "skill-eval-owner.json").exists()
    preferred = free_ports()
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", preferred[1]))
        selected = gateway.allocate("d" * 64, preferred, tmp_path)
        assert not set(selected).intersection(preferred)
        assert listener.getsockname()[1] == preferred[1]



def test_explicit_ports_remain_exact(tmp_path):
    preferred = free_ports()
    gateway.claim("a" * 64, preferred, tmp_path)
    with pytest.raises(ValueError, match="different evaluation"):
        gateway.allocate("b" * 64, preferred, tmp_path, exact=True)



def test_allocator_exhaustion_and_permission_errors_fail_closed(monkeypatch, tmp_path):
    import errno
    monkeypatch.setattr(gateway, 'claim', lambda *args: (_ for _ in ()).throw(gateway.NamespaceUnavailable('occupied')))
    with pytest.raises(ValueError, match='no unused'):
        gateway.allocate('a' * 64, [25000,25001,25002], tmp_path)
    monkeypatch.setattr(gateway, 'claim', lambda *args: (_ for _ in ()).throw(PermissionError(errno.EACCES, 'denied')))
    with pytest.raises(PermissionError):
        gateway.allocate('a' * 64, [25000,25001,25002], tmp_path)



@pytest.mark.parametrize('bad', [[], ['../secret.mp4'], ['credentials.env'], ['a.mp4', 'a.mp4'], 'a.mp4'])
def test_rejects_non_media_or_ambiguous_declarations(bad):
    with pytest.raises(ValueError):
        fixtures.validate_files(bad)



def test_uploads_only_declared_media_and_checks_hash(monkeypatch, tmp_path):
    (tmp_path / 'warehouse_safety_0001.mp4').write_bytes(b'video')
    (tmp_path / 'credentials.env').write_text('secret')
    digest = hashlib.sha256(b'video').hexdigest()
    calls = []
    def call(args, **kwargs):
        calls.append(args)
        return digest + '  file\n' if 'sha256sum' in args else ''
    monkeypatch.setattr(fixtures, 'call', call)
    rows = fixtures.stage('se-current', ['warehouse_safety_0001.mp4'], tmp_path)
    assert rows[0]['status'] == 'verified'
    assert rows[0]['sha256'] == digest
    assert calls[1] == ['nemoclaw', 'se-current', 'upload', str(tmp_path / 'warehouse_safety_0001.mp4'), fixtures.DESTINATION + '/warehouse_safety_0001.mp4']
    assert all('secret' not in str(args) for args in calls)



def test_missing_host_fixture_never_uploads(monkeypatch, tmp_path):
    monkeypatch.setattr(fixtures, 'call', lambda args, **kwargs: pytest.fail('must validate before transport'))
    with pytest.raises(ValueError, match='missing or empty'):
        fixtures.stage('se-current', ['missing.mp4'], tmp_path)



def test_checksum_mismatch_fails(monkeypatch, tmp_path):
    (tmp_path / 'a.mp4').write_bytes(b'video')
    monkeypatch.setattr(fixtures, 'call', lambda args, **kwargs: 'incorrect  file\n' if 'sha256sum' in args else '')
    with pytest.raises(ValueError, match='checksum mismatch'):
        fixtures.stage('se-current', ['a.mp4'], tmp_path)



def test_multiple_files_are_verified_individually(monkeypatch, tmp_path):
    names = ['warehouse_sample.mp4', 'sample-warehouse-ladder.mp4']
    for name in names:
        (tmp_path / name).write_bytes(name.encode())
    calls = []
    def call(args, **kwargs):
        calls.append(args)
        if 'sha256sum' in args:
            name = Path(args[-1]).name
            return hashlib.sha256(name.encode()).hexdigest() + '  file\n'
        return ''
    monkeypatch.setattr(fixtures, 'call', call)
    rows = fixtures.stage('se-current', names, tmp_path)
    assert [row['file'] for row in rows] == names
    assert all(row['status'] == 'verified' for row in rows)
    assert len(calls) == 5



@pytest.mark.parametrize("failed_stage", ["sandbox_access", "gateway_health", "gateway_authentication", "vss_configuration", None])
def test_readiness_stages_stop_at_failure(runner, monkeypatch, tmp_path, failed_stage):
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
    artifact = evidence.parent.parent / "artifacts/nemoclaw/readiness.json"
    assert artifact.read_text() == evidence.read_text()
    assert "secret" not in evidence.read_text()
    assert all(row["status"] == "passed" for row in rows[:-1])
    assert rows[-1]["stage"] == (failed_stage or "vss_configuration")
    assert rows[-1]["status"] == ("failed" if failed_stage else "passed")



def test_http_listener_is_not_authenticated_gateway(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_ensure_gateway", lambda _: None)
    monkeypatch.setattr(runner, "_sandbox_exec", lambda *a, **kw: subprocess.CompletedProcess(a, 0, '{"ok":false}', ""))
    with pytest.raises(RuntimeError, match="gateway_authentication"):
        runner._check_readiness("se-test", tmp_path / "readiness.json")



@pytest.mark.parametrize("pending", [True, False])
def test_only_pending_pairing_is_retried(runner, monkeypatch, tmp_path, pending):
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



def test_pairing_deadline_preserves_failure_when_final_probe_times_out(runner, monkeypatch, tmp_path):
    clock = [0.0]
    timeouts = []
    def probe(sandbox, command, **kwargs):
        if "gateway call" in command:
            timeouts.append(kwargs["timeout"])
            if len(timeouts) == 3:
                clock[0] += kwargs["timeout"]
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            clock[0] += 30
            return subprocess.CompletedProcess(command, 1, "", "scope upgrade pending approval token=secret")
        return subprocess.CompletedProcess(command, 0, '{"ok":true}', "")
    monkeypatch.setattr(runner, "_ensure_gateway", lambda _: None)
    monkeypatch.setattr(runner, "_sandbox_exec", probe)
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner.time, "sleep", lambda duration: clock.__setitem__(0, clock[0] + duration))
    evidence = tmp_path / "readiness.json"
    with pytest.raises(RuntimeError, match="gateway_authentication"):
        runner._check_readiness("se-test", evidence)
    row = json.loads(evidence.read_text())["stages"][-1]
    assert timeouts == [30, 30, 24]
    assert row["reason"] == "pairing_deadline"
    assert row["attempts"] == 3
    assert row["exit_code"] == 1
    assert "secret" not in evidence.read_text()



@pytest.mark.parametrize("stage", ["gateway", "agent"])
def test_failure_is_available_to_the_verifier(runner, monkeypatch, tmp_path, stage):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("This operational task")
    logs = tmp_path / "agent"
    message = f"this trial's {stage} failed"

    def fail(*args):
        raise RuntimeError(message)

    monkeypatch.setattr(runner, "_load_env_file", lambda path: None)
    monkeypatch.setattr(runner, "_sandbox_exec", lambda *a, **kw: __import__("subprocess").CompletedProcess(a, 0, '{"ok":true}', ""))
    monkeypatch.setattr(runner, "_ensure_gateway", fail if stage == "gateway" else lambda name: None)
    monkeypatch.setattr(runner, "_operational_prompt", lambda sandbox, prompt: prompt)
    monkeypatch.setattr(runner, "_run_openclaw", fail)
    assert runner.main(["--prompt-file", str(prompt), "--agent-log-dir", str(logs)]) == 1
    assert message in (logs / "agent.log").read_text()
    assert not (logs / "openclaw.session.jsonl").exists()
    assert not (logs / "trajectory.json").exists()



@pytest.mark.parametrize("local_nim", [False, True])
def test_prompt_uses_native_inference_without_mutating_provider(runner, monkeypatch, tmp_path, local_nim):
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
        elif script.startswith("find /sandbox/.openclaw/extensions/vss/skills"):
            output = "/sandbox/.openclaw/extensions/vss/skills/vss-manage-alerts/SKILL.md\n"
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
    assert len(calls) == 7
    envelope = json.loads((logs / "openclaw.txt").read_text())
    assert envelope["meta"]["agentMeta"]["usage"]["input"] == 5
    assert envelope["meta"]["agentMeta"]["usage"]["output"] == 2
    assert (logs / "openclaw.session.jsonl").exists()
    assert not (logs / "agent.log").exists()


@pytest.mark.parametrize("phase,model,access", [
    ("Ready", "local-model", 0),
    ("Error", "local-model", 0),
    ("Ready", "wrong-model", 0),
    ("Ready", "local-model", 1),
])
def test_setup_checks_phase_and_native_model_before_allowing_handoff(runner, monkeypatch, tmp_path, phase, model, access):
    monkeypatch.setenv("NEMOCLAW_MODEL", "local-model")
    monkeypatch.setenv("SKILL_EVAL_LOCAL_NIM_API_KEY", "local-nim")
    monkeypatch.setattr(runner, "_load_env_file", lambda _: None)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps({"phase": phase}), "secret"))
    monkeypatch.setattr(runner, "_sandbox_exec", lambda *a, **kw: subprocess.CompletedProcess(a, access, '{"ok":true}', "secret"))
    monkeypatch.setattr(runner, "_ensure_gateway", lambda _: pytest.fail("setup must not recover or recreate the sandbox"))
    calls = []
    def probe(sandbox, prompt, timeout):
        calls.append(prompt)
        return {"payloads": [{"text": "OK"}], "meta": {"aborted": False, "agentMeta": {"model": model, "usage": {"input": 10, "output": 1}}}}, "probe-session"
    monkeypatch.setattr(runner, "_run_openclaw", probe)
    logs = tmp_path / "agent"
    assert runner.main(["--setup-check", "--agent-log-dir", str(logs)]) == (0 if phase == "Ready" and model == "local-model" and access == 0 else 1)
    report = json.loads((logs / "setup-readiness.json").read_text())
    failed = next((row["stage"] for row in report["stages"] if row["status"] == "failed"), None)
    assert failed == ("sandbox_phase" if phase == "Error" else "sandbox_access" if access else "local_inference" if model != "local-model" else None)
    assert len(calls) == (1 if phase == "Ready" and access == 0 else 0)
    assert "secret" not in json.dumps(report)
    assert not (logs / "openclaw.txt").exists()
    assert not (logs / "openclaw.session.jsonl").exists()


def test_setup_waits_for_ready_with_a_bounded_deadline(runner, monkeypatch):
    clock = [0.0]
    phases = iter(["Pending", "Ready"])
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps({"phase": next(phases)}), ""))
    row = {}
    runner._wait_sandbox_ready("se-test", row)
    assert row["phase"] == "Ready"
    assert row["attempts"] == 2
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, '{"phase":"Pending"}', ""))
    with pytest.raises(RuntimeError, match="deadline"):
        runner._wait_sandbox_ready("se-test", {})
    assert clock[0] == 183


@pytest.mark.parametrize("recover", [True, False])
def test_setup_phase_lookup_timeout_uses_remaining_deadline(runner, monkeypatch, recover):
    clock = [0.0]
    calls = []
    def probe(*args, **kwargs):
        calls.append(kwargs["timeout"])
        if recover and len(calls) == 2:
            return subprocess.CompletedProcess(args, 0, '{"phase":"Ready"}', "")
        clock[0] += kwargs["timeout"]
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(runner.subprocess, "run", probe)
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    row = {}
    if recover:
        runner._wait_sandbox_ready("se-test", row)
        assert row["phase"] == "Ready"
        assert row["timeouts"] == 1
        assert row["attempts"] == 2
    else:
        with pytest.raises(RuntimeError, match="deadline"):
            runner._wait_sandbox_ready("se-test", row)
        assert clock[0] == 180
        assert calls[-1] == 15



@pytest.mark.parametrize("approve", [True, False])
def test_pairing_uses_final_remaining_window(runner, monkeypatch, tmp_path, approve):
    clock = [0.0]
    calls = []
    def probe(sandbox, command, **kwargs):
        if "gateway call" not in command:
            return subprocess.CompletedProcess(command, 0, '{"ok":true}', "")
        calls.append(kwargs["timeout"])
        clock[0] += kwargs["timeout"]
        if len(calls) == 3 and approve:
            return subprocess.CompletedProcess(command, 0, '{"ok":true}', "")
        return subprocess.CompletedProcess(command, 1, "", "pairing required")
    monkeypatch.setattr(runner, "_ensure_gateway", lambda _: None)
    monkeypatch.setattr(runner, "_sandbox_exec", probe)
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    if approve:
        runner._check_readiness("se-test", tmp_path / "readiness.json")
    else:
        with pytest.raises(RuntimeError, match="gateway_authentication"):
            runner._check_readiness("se-test", tmp_path / "readiness.json")
    assert calls == [30, 30, 24]


def test_media_commands_share_remaining_staging_deadline(monkeypatch):
    clock = [0.0]
    timeouts = []
    monkeypatch.setattr(fixtures.time, "monotonic", lambda: clock[0])
    def run(args, **kwargs):
        timeouts.append(kwargs["timeout"])
        clock[0] += 100
        return subprocess.CompletedProcess(args, 0, "done", "")
    monkeypatch.setattr(fixtures.subprocess, "run", run)
    for command in ("upload", "sha256sum"):
        fixtures.call([command], deadline=300)
    assert timeouts == [300, 200]
    clock[0] = 300
    with pytest.raises(TimeoutError, match="deadline"):
        fixtures.call(["upload"], deadline=300)
    assert len(timeouts) == 2


def test_operational_prompt_uses_installed_paths_and_foreground_session(runner, monkeypatch):
    path = "/sandbox/.openclaw/extensions/vss/skills/vss-manage-alerts/SKILL.md"
    monkeypatch.setattr(runner, "_sandbox_exec", lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 1, path + "\n" + path + "\n/usr/local/lib/fake/SKILL.md\n", ""))
    prompt = runner._operational_prompt("se-test", "Stop monitoring warehouse_sample")
    assert prompt.count(path) == 1
    assert "/usr/local/lib/fake" not in prompt
    assert "Do not spawn subagents" in prompt
    assert "sessions_spawn/sessions_yield" in prompt
    assert prompt.endswith("Stop monitoring warehouse_sample")


def test_operational_prompt_rejects_missing_skill_install(runner, monkeypatch):
    monkeypatch.setattr(runner, "_sandbox_exec", lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 1, "", "missing directory"))
    with pytest.raises(RuntimeError, match="No installed VSS skill"):
        runner._operational_prompt("se-test", "Operate")


def test_yielded_parent_is_not_reported_as_completed(runner, monkeypatch):
    session_path = "/sandbox/.openclaw/agents/main/sessions/test.jsonl"
    monkeypatch.setattr(runner, "_nemoclaw_exec", lambda *args, **kwargs:
        subprocess.CompletedProcess([], 0, json.dumps({"meta": {"agentMeta": {"sessionFile": session_path}}}), ""))
    session = json.dumps({"message": {
        "role": "assistant", "content": [{"type": "toolCall", "name": "sessions_yield", "arguments": {}}],
        "usage": {"input": 5, "output": 2},
    }})
    monkeypatch.setattr(runner, "_sandbox_exec", lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 0, session, ""))
    with pytest.raises(RuntimeError, match="detached work"):
        runner._run_openclaw("se-test", "Operate", 60)
