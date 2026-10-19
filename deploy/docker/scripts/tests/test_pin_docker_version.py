# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise Docker preparation with fake host tools; never change host packages."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "pin_docker_version.sh"
PACKAGES = (
    "docker-ce docker-ce-cli docker-buildx-plugin docker-compose-plugin containerd.io"
)
NEEDS_SUDO = pytest.mark.skipif(os.geteuid() == 0, reason="root does not need sudo")


def _run(
    tmp_path,
    *,
    held=PACKAGES,
    sudo_ok=True,
    version="28.4.0",
    driver="cgroupfs",
    terminal=False,
):
    state = {
        "holds": "\n".join(held.split()),
        "version": version,
        "driver": driver,
        "os-release": "VERSION_ID=24.04\nVERSION_CODENAME=noble\n",
    }
    for name, value in state.items():
        path = tmp_path / name
        if not path.exists():
            path.write_text(value + "\n")
    commands = {
        "docker": 'if [ "$1" = version ]; then cat "$STATE/version"; else cat "$STATE/driver"; fi',
        "dpkg-query": 'case " $PACKAGES " in *" ${@: -1} "*) printf installed;; esac',
        "apt-mark": 'if [ "$1" = showhold ]; then cat "$STATE/holds"; else printf "%s\\n" "$*" >> "$STATE/calls"; shift; printf "%s\\n" "$@" >> "$STATE/holds"; fi',
        "apt-get": 'printf "%s\\n" "$*" >> "$STATE/apt_calls"; if [ "$1" = install ]; then echo 29.4.3 > "$STATE/version"; fi',
        "sudo": 'printf "%s\\n" "$*" >> "$STATE/sudo_calls"; if [ "$1" = -n ]; then shift; [ "$SUDO_OK" = 1 ] || exit 1; fi; [ "$1" = true ] || exec "$@"',
        "systemctl": 'printf "%s\\n" "$*" >> "$STATE/restarts"; echo cgroupfs > "$STATE/driver"',
    }
    for name, body in commands.items():
        target = tmp_path / name
        target.write_text("#!/bin/bash\n" + body + "\n")
        target.chmod(0o755)
    jq = tmp_path / "jq"
    jq.write_text(
        f"#!{sys.executable}\n"
        + """import json, sys
with open(sys.argv[-1]) as source:
    value = json.load(source)
value["exec-opts"] = [opt for opt in value.get("exec-opts", []) if not opt.startswith("native.cgroupdriver=")] + ["native.cgroupdriver=cgroupfs"]
print(json.dumps(value))
"""
    )
    jq.chmod(0o755)
    script = tmp_path / "setup scripts" / SCRIPT.name
    script.parent.mkdir(exist_ok=True)
    source = SCRIPT.read_text()
    assert source.count('DAEMON_JSON="/etc/docker/daemon.json"') == 1
    source = source.replace(
        'DAEMON_JSON="/etc/docker/daemon.json"',
        f"DAEMON_JSON={shlex.quote(str(tmp_path / 'daemon.json'))}",
    )
    source = source.replace(
        ". /etc/os-release", ". " + shlex.quote(str(tmp_path / "os-release"))
    )
    script.write_text(source)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "PACKAGES": PACKAGES,
        "STATE": str(tmp_path),
        "SUDO_OK": str(int(sudo_ok)),
        "TMPDIR": str(tmp_path),
    }
    master, slave = os.openpty() if terminal else (None, None)
    try:
        return subprocess.run(
            ["bash", str(script)],
            env=env,
            stdin=slave if terminal else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    finally:
        if terminal:
            os.close(master)
            os.close(slave)


def test_already_prepared_host_needs_no_sudo(tmp_path):
    result = _run(tmp_path, sudo_ok=False)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "sudo_calls").exists()
    assert not (tmp_path / "calls").exists()


def test_only_missing_hold_is_applied(tmp_path):
    result = _run(tmp_path, held=PACKAGES.replace("docker-ce-cli", ""))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls").read_text().strip() == "hold docker-ce-cli"


@NEEDS_SUDO
@pytest.mark.parametrize(
    "version,held,driver",
    [
        ("28.4.0", "", "cgroupfs"),
        ("28.4.0", PACKAGES, "systemd"),
        ("27.5.1", PACKAGES, "systemd"),
    ],
)
def test_notebook_handoff_prepares_whole_host_and_rerun_needs_no_timestamp(
    tmp_path, version, held, driver
):
    result = _run(tmp_path, version=version, held=held, driver=driver, sudo_ok=False)
    assert result.returncode != 0
    command = result.stderr.split("Run in the host terminal:\n", 1)[1].splitlines()[0]
    assert shlex.split(command) == [
        "sudo",
        "bash",
        str(tmp_path / "setup scripts" / SCRIPT.name),
    ]
    assert (tmp_path / "sudo_calls").read_text().splitlines() == ["-n true"]
    assert not (tmp_path / "calls").exists()
    assert not (tmp_path / "apt_calls").exists()
    assert not (tmp_path / "daemon.json").exists()

    # Simulate the host terminal completing preparation with its own sudo context.
    prepared = _run(tmp_path, sudo_ok=False, terminal=True)
    assert prepared.returncode == 0, prepared.stderr
    (tmp_path / "sudo_calls").unlink()
    rerun = _run(tmp_path, sudo_ok=False)
    assert rerun.returncode == 0, rerun.stderr
    assert not (tmp_path / "sudo_calls").exists()


@NEEDS_SUDO
@pytest.mark.parametrize("terminal", [False, True])
def test_cgroup_merge_preserves_config_and_privilege_check_is_once(tmp_path, terminal):
    original = {
        "default-runtime": "nvidia",
        "exec-opts": ["other=value", "native.cgroupdriver=systemd"],
    }
    (tmp_path / "daemon.json").write_text(json.dumps(original))
    result = _run(
        tmp_path, held="", driver="systemd", sudo_ok=not terminal, terminal=terminal
    )
    assert result.returncode == 0, result.stderr
    updated = json.loads((tmp_path / "daemon.json").read_text())
    assert updated == {
        "default-runtime": "nvidia",
        "exec-opts": ["other=value", "native.cgroupdriver=cgroupfs"],
    }
    assert json.loads((tmp_path / "daemon.json.bak").read_text()) == original
    assert (tmp_path / "restarts").read_text().strip() == "restart docker"
    calls = (tmp_path / "sudo_calls").read_text().splitlines()
    assert calls.count("-n true") == (0 if terminal else 1)
    if terminal:
        assert all(not call.startswith("-n ") for call in calls)
    assert not any("test -f" in call for call in calls)
