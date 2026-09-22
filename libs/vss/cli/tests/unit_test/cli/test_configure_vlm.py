# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for persistent ``vss configure vlm`` policy."""

from __future__ import annotations

from dataclasses import replace
import json
from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import pytest

from vss_cli import config as config_mod
from vss_cli import configure as configure_mod
from vss_cli.exits import Exit

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    config_mod.save(
        config_mod.Deployment(
            base_url="http://example",
            services={"rt_vlm": config_mod.Service(url="http://example/rtvi-vlm")},
        )
    )
    return tmp_path


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(configure_mod.configure, ["vlm", *args])


def _locked_policy() -> config_mod.VlmConfig:
    return config_mod.VlmConfig(
        timeout=600,
        temperature=0,
        max_tokens=8192,
        seed=1,
        enable_reasoning=False,
        chunk_duration=0,
        fps=4,
        shortest_edge=262144,
        longest_edge=16777216,
        locked=True,
    )


def test_configure_vlm_writes_complete_locked_policy(config_home: Path) -> None:
    result = _invoke(
        "--backend",
        "rt-vlm",
        "--timeout",
        "600",
        "--temperature",
        "0",
        "--max-tokens",
        "8192",
        "--seed",
        "1",
        "--disable-reasoning",
        "--chunk-duration",
        "0",
        "--fps",
        "4",
        "--shortest-edge",
        "262144",
        "--longest-edge",
        "16777216",
        "--lock",
    )
    assert result.exit_code == 0, result.output
    assert config_mod.load().vlm == _locked_policy()
    assert config_home.joinpath("config.json").stat().st_mode & 0o777 == 0o600


def test_configure_vlm_writes_standalone_vllm_backend(config_home: Path) -> None:
    result = _invoke("--backend", "vllm", "--chunk-duration", "0", "--fps", "4")

    assert result.exit_code == 0, result.output
    assert config_mod.load().vlm == config_mod.VlmConfig(
        backend="vllm",
        chunk_duration=0,
        fps=4,
    )


def test_configure_vlm_writes_cosmos_reason_nim_backend(config_home: Path) -> None:
    result = _invoke("--backend", "cosmos-reason-nim", "--fps", "4")

    assert result.exit_code == 0, result.output
    assert config_mod.load().vlm == config_mod.VlmConfig(
        backend="cosmos_reason_nim",
        fps=4,
    )


def test_configure_vlm_inherits_environment_defaults(config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.VLM_ENV["backend"], "vllm")

    result = _invoke("--fps", "4")

    assert result.exit_code == 0, result.output
    assert config_mod.load().vlm == config_mod.VlmConfig(
        backend="vllm",
        fps=4,
    )


def test_vlm_config_without_backend_defaults_to_rt_vlm() -> None:
    policy = config_mod.VlmConfig.from_json({"fps": 4, "locked": True})

    assert policy.backend == "rt_vlm"
    assert policy.to_json()["backend"] == "rt_vlm"


def test_standalone_vllm_rejects_positive_chunk_duration(config_home: Path) -> None:
    result = _invoke("--backend", "vllm", "--chunk-duration", "5")

    assert result.exit_code == int(Exit.CONFIGURATION), result.output
    assert "positive chunk_duration is supported only by RT-VLM" in result.output


def test_configure_vlm_updates_only_supplied_values(config_home: Path) -> None:
    config_mod.save(replace(config_mod.load(), vlm=_locked_policy()))

    result = _invoke("--timeout", "300", "--unlock")
    assert result.exit_code == 0, result.output
    assert config_mod.load().vlm == config_mod.VlmConfig(
        timeout=300,
        temperature=0,
        max_tokens=8192,
        seed=1,
        enable_reasoning=False,
        chunk_duration=0,
        fps=4,
        shortest_edge=262144,
        longest_edge=16777216,
        locked=False,
    )


def test_configure_vlm_without_options_shows_policy(config_home: Path) -> None:
    config_mod.save(replace(config_mod.load(), vlm=_locked_policy()))

    result = _invoke()
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == _locked_policy().to_json()


def test_configure_vlm_reset_removes_policy_and_preserves_deployment(config_home: Path) -> None:
    config_mod.save(replace(config_mod.load(), vlm=_locked_policy()))

    result = _invoke("--reset")

    assert result.exit_code == 0, result.output
    deployment = config_mod.load()
    assert deployment.vlm is None
    assert deployment.base_url == "http://example"
    assert deployment.services == {"rt_vlm": config_mod.Service(url="http://example/rtvi-vlm")}


def test_configure_vlm_reset_rejects_policy_options(config_home: Path) -> None:
    config_mod.save(replace(config_mod.load(), vlm=_locked_policy()))

    result = _invoke("--reset", "--fps", "2")

    assert result.exit_code != 0
    assert "cannot combine --reset with VLM policy options" in result.output
    assert config_mod.load().vlm == _locked_policy()


def test_main_configure_preserves_vlm_policy(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_mod.save(replace(config_mod.load(), vlm=_locked_policy()))
    monkeypatch.setattr(configure_mod, "_probe", lambda *_args, **_kwargs: (True, "HTTP 200"))
    monkeypatch.setattr(configure_mod, "_describe", lambda *_args, **_kwargs: [])

    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "http://new"])
    assert result.exit_code == 0, result.output
    assert config_mod.load().vlm == _locked_policy()


def test_locked_policy_requires_at_least_one_value(config_home: Path) -> None:
    result = _invoke("--lock")
    assert result.exit_code == int(Exit.CONFIGURATION), result.output
    assert "must configure at least one" in result.output


def test_configure_vlm_rejects_inverted_processor_size(config_home: Path) -> None:
    result = _invoke("--shortest-edge", "16777216", "--longest-edge", "262144")

    assert result.exit_code == int(Exit.CONFIGURATION), result.output
    assert "shortest_edge must be no greater than longest_edge" in result.output


def test_configure_vlm_without_saved_deployment_exits_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))

    result = _invoke("--fps", "4")

    assert result.exit_code == int(Exit.CONFIGURATION), result.output
    assert "vss configure vlm: configuration error:" in result.output


def test_persisted_policy_overrides_every_environment_default(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = config_mod.VlmConfig(
        backend="rt_vlm",
        timeout=30,
        temperature=0.5,
        max_tokens=512,
        seed=2,
        enable_reasoning=True,
        chunk_duration=30,
        fps=2,
        shortest_edge=131072,
        longest_edge=8388608,
        locked=False,
    )
    values = {
        "backend": "vllm",
        "timeout": "600",
        "temperature": "0",
        "max_tokens": "8192",
        "seed": "1",
        "enable_reasoning": "false",
        "chunk_duration": "0",
        "fps": "4",
        "shortest_edge": "262144",
        "longest_edge": "16777216",
        "locked": "true",
    }
    for field_name, value in values.items():
        monkeypatch.setenv(config_mod.VLM_ENV[field_name], value)

    assert config_mod.effective_vlm_config(configured) == configured


def test_persisted_policy_overrides_environment_temperature_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.VLM_ENV["temperature"], "0.5")
    configured = config_mod.VlmConfig(temperature=0, locked=True)

    effective = config_mod.effective_vlm_config(configured)

    assert effective is not None
    assert effective.temperature == 0
    assert effective.locked is True


def test_environment_supplies_default_without_persisted_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.VLM_ENV["temperature"], "0.5")

    effective = config_mod.effective_vlm_config(None)

    assert effective is not None
    assert effective.temperature == 0.5


def test_vlm_environment_accepts_cosmos_reason_nim_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.VLM_ENV["backend"], "cosmos_reason_nim")

    assert config_mod.effective_vlm_config(None) == config_mod.VlmConfig(backend="cosmos_reason_nim")


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("timeout", "", "VSS_VLM_TIMEOUT is set but empty"),
        ("max_tokens", "8.5", "VSS_VLM_MAX_TOKENS must be an integer"),
        ("temperature", "cold", "VSS_VLM_TEMPERATURE must be a number"),
        ("locked", "yes", "VSS_VLM_LOCKED must be true or false"),
        (
            "backend",
            "rt-vlm",
            "VSS_VLM_BACKEND must be 'rt_vlm', 'vllm', or 'cosmos_reason_nim'",
        ),
    ],
)
def test_vlm_environment_rejects_malformed_values(
    field_name: str,
    value: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.VLM_ENV[field_name], value)

    with pytest.raises(config_mod.ConfigError, match=message):
        config_mod.effective_vlm_config(None)
