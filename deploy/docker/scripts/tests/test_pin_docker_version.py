# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise Docker preparation with fake host tools; never change host packages."""

import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "pin_docker_version.sh"
PACKAGES = "docker-ce docker-ce-cli docker-buildx-plugin docker-compose-plugin containerd.io"


def _run(tmp_path, *, held=PACKAGES, sudo_ok=True):
    commands = {
        "docker": 'if [ "$1" = version ]; then echo 28.4.0; else echo cgroupfs; fi',
        "dpkg-query": 'case " $PACKAGES " in *" ${@: -1} "*) printf installed;; esac',
        "apt-mark": 'if [ "$1" = showhold ]; then printf "%s\\n" $HELD; else printf "%s\\n" "$*" >> "$CALLS"; fi',
        "sudo": 'printf "%s\\n" "$*" >> "$SUDO_CALLS"; [ "$SUDO_OK" = 1 ] || exit 1; shift; [ "$1" = true ] || exec "$@"',
    }
    for name, body in commands.items():
        target = tmp_path / name
        target.write_text("#!/bin/bash\n" + body + "\n")
        target.chmod(0o755)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "PACKAGES": PACKAGES, "HELD": held, "CALLS": str(tmp_path / "calls"),
           "SUDO_CALLS": str(tmp_path / "sudo_calls"), "SUDO_OK": str(int(sudo_ok))}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)


def test_already_prepared_host_needs_no_sudo(tmp_path):
    result = _run(tmp_path, sudo_ok=False)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "sudo_calls").exists()
    assert not (tmp_path / "calls").exists()


def test_only_missing_hold_is_applied(tmp_path):
    result = _run(tmp_path, held=PACKAGES.replace("docker-ce-cli", ""))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls").read_text().strip() == "hold docker-ce-cli"


@pytest.mark.skipif(os.geteuid() == 0, reason="root does not need sudo")
def test_password_required_fails_immediately_with_host_command(tmp_path):
    result = _run(tmp_path, held="", sudo_ok=False)
    assert result.returncode != 0
    assert "Run in the host terminal:" in result.stderr
    assert "sudo apt-mark hold docker-ce" in result.stderr
    assert (tmp_path / "sudo_calls").read_text().splitlines() == ["-n true"]
    assert not (tmp_path / "calls").exists()
