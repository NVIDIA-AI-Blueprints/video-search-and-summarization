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

import os

import pytest

from vss_agents.orchestrator.tools import GenerateInput
from vss_agents.orchestrator.tools import OrchestratorRuntimeSettings


def test_generate_input_does_not_expose_runtime_secret_fields():
    fields = GenerateInput.model_fields

    assert "ngc_cli_api_key" not in fields
    assert "nvidia_api_key" not in fields
    assert "hardware_profile" not in fields


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


def test_runtime_settings_apply_to_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NGC_CLI_API_KEY", "previous-ngc")  # pragma: allowlist secret
    monkeypatch.setenv("NVIDIA_API_KEY", "previous-nvidia")  # pragma: allowlist secret
    monkeypatch.setenv("HARDWARE_PROFILE", "previous-hardware")

    settings = OrchestratorRuntimeSettings(
        NGC_CLI_API_KEY="ngc-from-settings",  # pragma: allowlist secret
        NVIDIA_API_KEY="nvidia-from-settings",  # pragma: allowlist secret
        HARDWARE_PROFILE="L40S",
    )

    settings.apply_to_environment()

    assert os.environ["NGC_CLI_API_KEY"] == "ngc-from-settings"  # pragma: allowlist secret
    assert os.environ["NVIDIA_API_KEY"] == "nvidia-from-settings"  # pragma: allowlist secret
    assert os.environ["HARDWARE_PROFILE"] == "L40S"


def test_runtime_settings_apply_to_environment_preserves_existing_values_when_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NGC_CLI_API_KEY", "previous-ngc")  # pragma: allowlist secret
    monkeypatch.setenv("NVIDIA_API_KEY", "previous-nvidia")  # pragma: allowlist secret
    monkeypatch.setenv("HARDWARE_PROFILE", "previous-hardware")

    settings = OrchestratorRuntimeSettings(
        NGC_CLI_API_KEY="",  # pragma: allowlist secret
        NVIDIA_API_KEY="",  # pragma: allowlist secret
        HARDWARE_PROFILE="",
    )

    settings.apply_to_environment()

    assert os.environ["NGC_CLI_API_KEY"] == "previous-ngc"  # pragma: allowlist secret
    assert os.environ["NVIDIA_API_KEY"] == "previous-nvidia"  # pragma: allowlist secret
    assert os.environ["HARDWARE_PROFILE"] == "previous-hardware"
