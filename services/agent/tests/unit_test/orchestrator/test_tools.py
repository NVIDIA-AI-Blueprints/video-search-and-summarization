# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for vss_agents/orchestrator/tools.py."""

from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from vss_agents.orchestrator.tools import ComposeDownOperationInput
from vss_agents.orchestrator.tools import GenerateInput
from vss_agents.orchestrator.tools import OrchestratorRuntimeSettings
from vss_agents.orchestrator.tools import _run_deep_clean


def test_generate_input_does_not_expose_runtime_secret_fields():
    fields = GenerateInput.model_fields

    assert "ngc_cli_api_key" not in fields
    assert "nvidia_api_key" not in fields
    assert "hardware_profile" not in fields


def test_generate_input_accepts_modeless_profile_without_profile_mode():
    """A profile that has no modes must be accepted with profile_mode left unset.
    Runtime validation (in _docker_generate) rejects profile_mode for such profiles."""
    inp = GenerateInput(profile="base")
    assert inp.profile_mode is None


def test_generate_input_accepts_profile_mode_as_freeform_string():
    """profile_mode is a plain string at the schema level; per-profile validity
    is checked at runtime against profile_mode_to_env_modes."""
    inp = GenerateInput(profile="alerts", profile_mode="verification")
    assert inp.profile_mode == "verification"


def test_runtime_settings_reads_and_strips_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NGC_CLI_API_KEY", " ngc-from-env ")  # pragma: allowlist secret
    monkeypatch.setenv("NVIDIA_API_KEY", " nvidia-from-env ")  # pragma: allowlist secret
    monkeypatch.setenv("HARDWARE_PROFILE", " RTXPRO6000BW ")

    settings = OrchestratorRuntimeSettings()

    assert settings.ngc_cli_api_key == "ngc-from-env"  # pragma: allowlist secret
    assert settings.nvidia_api_key == "nvidia-from-env"  # pragma: allowlist secret
    assert settings.hardware_profile == "RTXPRO6000BW"


def test_runtime_settings_loads_dotenv_file(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NGC_CLI_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("HARDWARE_PROFILE", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "NGC_CLI_API_KEY=ngc-from-dotenv",  # pragma: allowlist secret
                "NVIDIA_API_KEY=nvidia-from-dotenv",  # pragma: allowlist secret
                "HARDWARE_PROFILE=H100",
            ]
        )
        + "\n"
    )

    settings = OrchestratorRuntimeSettings()

    assert settings.ngc_cli_api_key == "ngc-from-dotenv"  # pragma: allowlist secret
    assert settings.nvidia_api_key == "nvidia-from-dotenv"  # pragma: allowlist secret
    assert settings.hardware_profile == "H100"


def test_runtime_settings_allows_missing_runtime_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NGC_CLI_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("HARDWARE_PROFILE", raising=False)

    settings = OrchestratorRuntimeSettings()

    assert settings.ngc_cli_api_key == ""
    assert settings.nvidia_api_key == ""
    assert settings.hardware_profile == ""


def test_compose_down_input_deep_clean_defaults_to_false():
    inp = ComposeDownOperationInput(docker_compose_id="base")
    assert inp.deep_clean is False
    assert inp.remove_volumes is True
    assert inp.remove_orphans is True


def test_compose_down_input_deep_clean_can_be_enabled():
    inp = ComposeDownOperationInput(docker_compose_id="base", deep_clean=True)
    assert inp.deep_clean is True


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_deep_clean_skips_missing_data_directory(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    logs: list[str] = []

    with patch("vss_agents.orchestrator.tools.subprocess.run") as mock_run:
        mock_run.return_value = _make_completed(stdout="Total reclaimed space: 0B\n")
        _run_deep_clean(missing, logs.append)

    # docker volume prune ran exactly once; rm was NOT invoked because the dir is missing.
    assert mock_run.call_count == 1
    assert mock_run.call_args_list[0].args[0] == ["docker", "volume", "prune", "-f"]
    assert any("Data directory does not exist" in line for line in logs)


def test_run_deep_clean_removes_existing_data_directory(tmp_path: Path):
    data_dir = tmp_path / "data-dir"
    data_dir.mkdir()
    (data_dir / "marker").write_text("x")
    logs: list[str] = []

    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        # docker volume prune: stub. rm -rf: defer to real subprocess so the dir actually goes away.
        if cmd[:1] == ["docker"]:
            return _make_completed(stdout="Total reclaimed space: 0B\n")
        return real_run(cmd, *args, **kwargs)

    with (
        patch("vss_agents.orchestrator.tools.subprocess.run", side_effect=fake_run),
        patch("vss_agents.orchestrator.tools.os.geteuid", return_value=1000),
        patch("vss_agents.orchestrator.tools.shutil.which", return_value=None),
    ):
        _run_deep_clean(data_dir, logs.append)

    assert not data_dir.exists()
    assert any("Data directory deleted" in line for line in logs)


def test_run_deep_clean_uses_sudo_when_non_root_and_available(tmp_path: Path):
    data_dir = tmp_path / "data-dir"
    data_dir.mkdir()
    logs: list[str] = []

    captured_cmds: list[list[str]] = []

    def fake_run(cmd, *_args, **_kwargs):
        captured_cmds.append(list(cmd))
        if cmd[:1] == ["docker"]:
            return _make_completed(stdout="")
        # Simulate sudo rm -rf success without actually deleting (we'll assert command shape).
        import shutil as _sh

        _sh.rmtree(data_dir, ignore_errors=True)
        return _make_completed(returncode=0)

    with (
        patch("vss_agents.orchestrator.tools.subprocess.run", side_effect=fake_run),
        patch("vss_agents.orchestrator.tools.os.geteuid", return_value=1000),
        patch("vss_agents.orchestrator.tools.shutil.which", return_value="/usr/bin/sudo"),
    ):
        _run_deep_clean(data_dir, logs.append)

    rm_cmd = next(cmd for cmd in captured_cmds if cmd[:1] != ["docker"])
    assert rm_cmd[:3] == ["sudo", "-n", "rm"]
    assert rm_cmd[3] == "-rf"
    assert rm_cmd[4] == str(data_dir)


def test_run_deep_clean_skips_sudo_when_root(tmp_path: Path):
    data_dir = tmp_path / "data-dir"
    data_dir.mkdir()
    logs: list[str] = []

    captured_cmds: list[list[str]] = []

    def fake_run(cmd, *_args, **_kwargs):
        captured_cmds.append(list(cmd))
        if cmd[:1] == ["docker"]:
            return _make_completed(stdout="")
        import shutil as _sh

        _sh.rmtree(data_dir, ignore_errors=True)
        return _make_completed(returncode=0)

    with (
        patch("vss_agents.orchestrator.tools.subprocess.run", side_effect=fake_run),
        patch("vss_agents.orchestrator.tools.os.geteuid", return_value=0),
        patch("vss_agents.orchestrator.tools.shutil.which", return_value="/usr/bin/sudo"),
    ):
        _run_deep_clean(data_dir, logs.append)

    rm_cmd = next(cmd for cmd in captured_cmds if cmd[:1] != ["docker"])
    assert rm_cmd[0] == "rm"
    assert "sudo" not in rm_cmd


def test_run_deep_clean_logs_failure_when_rm_returns_nonzero(tmp_path: Path):
    data_dir = tmp_path / "data-dir"
    data_dir.mkdir()
    logs: list[str] = []

    def fake_run(cmd, *_args, **_kwargs):
        if cmd[:1] == ["docker"]:
            return _make_completed(stdout="")
        # Simulate permission denied: dir remains, exit code 1.
        return _make_completed(returncode=1, stderr="rm: cannot remove ...: Permission denied\n")

    with (
        patch("vss_agents.orchestrator.tools.subprocess.run", side_effect=fake_run),
        patch("vss_agents.orchestrator.tools.os.geteuid", return_value=1000),
        patch("vss_agents.orchestrator.tools.shutil.which", return_value=None),
    ):
        _run_deep_clean(data_dir, logs.append)

    assert data_dir.exists()
    assert any("Failed to delete data directory" in line for line in logs)
    assert any("root-owned" in line for line in logs)


def test_run_deep_clean_handles_missing_docker_binary(tmp_path: Path):
    data_dir = tmp_path / "data-dir"
    data_dir.mkdir()
    logs: list[str] = []

    def fake_run(cmd, *_args, **_kwargs):
        if cmd[:1] == ["docker"]:
            raise FileNotFoundError("docker")
        import shutil as _sh

        _sh.rmtree(data_dir, ignore_errors=True)
        return _make_completed(returncode=0)

    with (
        patch("vss_agents.orchestrator.tools.subprocess.run", side_effect=fake_run),
        patch("vss_agents.orchestrator.tools.os.geteuid", return_value=0),
        patch("vss_agents.orchestrator.tools.shutil.which", return_value=None),
    ):
        _run_deep_clean(data_dir, logs.append)

    assert any("docker command not found" in line for line in logs)
    # rm path still runs and removes the directory.
    assert not data_dir.exists()
