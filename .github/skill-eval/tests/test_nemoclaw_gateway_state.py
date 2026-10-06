# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A new eval must not inherit or overwrite another gateway's registry."""

import importlib.util
import json
from pathlib import Path
import socket

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "nemoclaw" / "gateway_state.py"
spec = importlib.util.spec_from_file_location("gateway_state", SCRIPT)
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


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
    notebook = SCRIPT.parents[3] / "deploy/docker/scripts/deploy_nemoclaw.ipynb"
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
