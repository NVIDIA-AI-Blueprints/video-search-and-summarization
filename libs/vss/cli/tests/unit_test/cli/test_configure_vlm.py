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
        max_frames=256,
        total_pixels=16777216,
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
        "--max-frames",
        "256",
        "--total-pixels",
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
        max_frames=256,
        total_pixels=16777216,
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


def test_sampling_fields_combine_in_one_policy(config_home: Path) -> None:
    result = _invoke("--fps", "2", "--max-frames", "64", "--total-pixels", "4194304")

    assert result.exit_code == 0, result.output
    assert config_mod.load().vlm == config_mod.VlmConfig(fps=2, max_frames=64, total_pixels=4194304)


@pytest.mark.parametrize(
    "name", ["VSS_VLM_LOCKED", "VSS_VLM_LONGEST_EDGE", "VSS_VLM_MAX_PIXELS_PER_FRAME", "VSS_VLM_FPSS"]
)
def test_unsupported_vlm_environment_variable_is_an_error(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(name, "1")

    with pytest.raises(config_mod.ConfigError, match=rf"unsupported VLM environment variables: {name}\. Supported: "):
        config_mod.effective_vlm_config(None)


def test_unsupported_vlm_environment_variable_stops_configure(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VSS_VLM_LOCKED", "true")

    result = _configure_all_routes(monkeypatch)

    assert result.exit_code == int(Exit.CONFIGURATION), result.output
    assert "unsupported VLM environment variables: VSS_VLM_LOCKED" in result.output
    assert config_mod.load().base_url == "http://example"


def test_empty_unsupported_vlm_environment_variable_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VSS_VLM_LOCKED", "")

    assert config_mod.effective_vlm_config(None) is None


def _configure_all_routes(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(configure_mod, "_probe", lambda *_args, **_kwargs: (True, "HTTP 200"))
    monkeypatch.setattr(configure_mod, "_describe", lambda *_args, **_kwargs: [])
    return CliRunner().invoke(configure_mod.configure, ["--base-url", "http://new"])


def _report_line(output: str, field_name: str) -> str:
    return next(line for line in output.splitlines() if line.strip().startswith(field_name))


def test_configure_reports_unset_sampling_and_asks_for_it(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for field_name in config_mod.VLM_SAMPLING_FIELDS:
        monkeypatch.delenv(config_mod.VLM_ENV[field_name], raising=False)

    result = _configure_all_routes(monkeypatch)

    assert result.exit_code == 0, result.output
    for field_name in config_mod.VLM_SAMPLING_FIELDS:
        assert _report_line(result.output, field_name).split()[1] == "unset"
    assert "fps, max_frames, total_pixels unset, so the VLM server's own sampling applies" in result.output
    assert "VSS_VLM_FPS, VSS_VLM_MAX_FRAMES, VSS_VLM_TOTAL_PIXELS" in result.output
    assert "vss configure vlm --fps <value> --max-frames <value> --total-pixels <value>`" in result.output


def test_configure_reports_each_sampling_value_with_its_source(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.VLM_ENV["fps"], "2")
    monkeypatch.setenv(config_mod.VLM_ENV["max_frames"], "32")
    config_mod.save(replace(config_mod.load(), vlm=config_mod.VlmConfig(max_frames=128)))

    result = _configure_all_routes(monkeypatch)

    assert result.exit_code == 0, result.output
    assert _report_line(result.output, "fps").split()[1:] == ["2.0", "VSS_VLM_FPS"]
    max_frames = _report_line(result.output, "max_frames")
    assert max_frames.split()[1] == "128"
    assert "(vss configure vlm)" in max_frames
    assert _report_line(result.output, "total_pixels").split()[1] == "unset"
    assert "note: total_pixels unset" in result.output
    assert "export VSS_VLM_TOTAL_PIXELS or run `vss configure vlm --total-pixels <value>`" in result.output


def test_configure_with_all_sampling_set_prints_no_note(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.VLM_ENV["fps"], "2")
    monkeypatch.setenv(config_mod.VLM_ENV["max_frames"], "32")
    monkeypatch.setenv(config_mod.VLM_ENV["total_pixels"], "16777216")

    result = _configure_all_routes(monkeypatch)

    assert result.exit_code == 0, result.output
    assert "own sampling applies" not in result.output


def test_configure_rejects_malformed_sampling_environment(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.VLM_ENV["max_frames"], "many")

    result = _configure_all_routes(monkeypatch)

    assert result.exit_code == int(Exit.CONFIGURATION), result.output
    assert "VSS_VLM_MAX_FRAMES must be an integer" in result.output
    assert "wrote" not in result.output
    assert config_mod.load().base_url == "http://example"


def test_configure_without_vlm_route_still_rejects_bad_vlm_environment(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.VLM_ENV["fps"], "fast")
    monkeypatch.setattr(
        configure_mod,
        "_probe",
        lambda _base_url, probe_path, _timeout: (probe_path.startswith("/vst"), "HTTP 200"),
    )
    monkeypatch.setattr(configure_mod, "_describe", lambda *_args, **_kwargs: [])

    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "http://new"])

    assert result.exit_code == int(Exit.CONFIGURATION), result.output
    assert "VSS_VLM_FPS must be a number" in result.output
    assert config_mod.load().base_url == "http://example"


def test_configure_without_vlm_route_skips_sampling_report(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        configure_mod,
        "_probe",
        lambda _base_url, probe_path, _timeout: (probe_path.startswith("/vst"), "HTTP 200"),
    )
    monkeypatch.setattr(configure_mod, "_describe", lambda *_args, **_kwargs: [])

    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "http://new"])

    assert result.exit_code == 0, result.output
    assert "vlm sampling" not in result.output


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
        max_frames=128,
        total_pixels=8388608,
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
        "max_frames": "256",
        "total_pixels": "16777216",
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
        ("max_tokens", "8.5", "VSS_VLM_MAX_TOKENS must be an integer"),
        ("temperature", "cold", "VSS_VLM_TEMPERATURE must be a number"),
        (
            "backend",
            "rt-vlm",
            "VSS_VLM_BACKEND must be 'rt_vlm', 'vllm', 'cosmos_reason_nim', or 'openai'",
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


def test_empty_vlm_environment_variables_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # An image built with an unset `ARG VSS_VLM_FPS` -> `ENV VSS_VLM_FPS=$VSS_VLM_FPS`.
    for environment_name in config_mod.VLM_ENV.values():
        monkeypatch.setenv(environment_name, "  ")

    assert config_mod.effective_vlm_config(None) is None


def test_vlm_environment_supplies_model_and_openai_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.VLM_ENV["backend"], "openai")
    monkeypatch.setenv(config_mod.VLM_ENV["model"], "nvdev/qwen/qwen3-vl")

    assert config_mod.effective_vlm_config(None) == config_mod.VlmConfig(backend="openai", model="nvdev/qwen/qwen3-vl")


def test_configure_vlm_writes_model_and_openai_backend(config_home: Path) -> None:
    result = _invoke("--backend", "openai", "--model", "nvdev/qwen/qwen3-vl")

    assert result.exit_code == 0, result.output
    assert config_mod.load().vlm == config_mod.VlmConfig(backend="openai", model="nvdev/qwen/qwen3-vl")


def test_openai_backend_rejects_positive_chunk_duration(config_home: Path) -> None:
    result = _invoke("--backend", "openai", "--chunk-duration", "5")

    assert result.exit_code == int(Exit.CONFIGURATION), result.output
    assert "positive chunk_duration is supported only by RT-VLM" in result.output


# --------------------------------------------------------------------------
# `vss configure --base-url` on a bare VLM endpoint
# --------------------------------------------------------------------------


def _bare_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int = 200,
    models: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """No ingress route answers; ``<root>/v1/models`` answers as given. Returns the requests seen."""
    import httpx

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(configure_mod, "_probe", lambda *_args, **_kwargs: (False, "HTTP 404"))
    body = {"object": "list", "data": models if models is not None else [{"id": "Qwen/Qwen3-VL", "owned_by": "vllm"}]}

    def _get(url: str, **kwargs: Any) -> httpx.Response:
        seen.append({"url": url, "headers": kwargs.get("headers") or {}})
        if not url.endswith("/v1/models"):
            return httpx.Response(404)
        return httpx.Response(status, json=body if status == 200 else {"error": "denied"}, headers=headers or {})

    monkeypatch.setattr(httpx, "get", _get)
    return seen


def _configure(base_url: str) -> Any:
    return CliRunner().invoke(configure_mod.configure, ["--base-url", base_url])


@pytest.mark.parametrize(
    "given",
    ["http://vllm:8000", "http://vllm:8000/v1", "http://vllm:8000/v1/", "http://vllm:8000/v1/chat/completions"],
)
def test_configure_records_a_bare_vlm_endpoint_at_its_root(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    given: str,
) -> None:
    seen = _bare_endpoint(monkeypatch)

    result = _configure(given)

    assert result.exit_code == 0, result.output
    deployment = config_mod.load()
    assert deployment.base_url == "http://vllm:8000"
    assert deployment.services == {
        "rt_vlm": config_mod.Service(url="http://vllm:8000", models=["Qwen/Qwen3-VL"]),
    }
    assert deployment.is_direct_vlm
    assert seen[-1]["url"] == "http://vllm:8000/v1/models"
    assert "direct VLM endpoint, 1 models" in result.output
    assert "Qwen/Qwen3-VL (the endpoint's only model)" in result.output


def test_bare_vllm_endpoint_is_reported_not_switched(config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # RT-VLM can front a vLLM engine and answer the same way, and it rejects
    # vLLM's request shape, so owned_by=vllm is a hint, never a switch.
    _bare_endpoint(monkeypatch)

    result = _configure("http://vllm:8000")

    assert result.exit_code == 0, result.output
    assert config_mod.load().vlm is None
    assert "the endpoint reports vLLM" in result.output
    assert "vss configure vlm --backend vllm" in result.output


def test_inference_hub_endpoint_selects_openai_backend_and_sends_the_key(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.VLM_API_KEY_ENV, "sk-test")
    seen = _bare_endpoint(
        monkeypatch,
        models=[{"id": "nvdev/a", "owned_by": "openai"}, {"id": "nvdev/b"}, {"id": "nvdev/c"}],
        headers={"x-litellm-version": "1.84.10"},
    )

    result = _configure("https://inference-api.nvidia.com/v1/chat/completions")

    assert result.exit_code == 0, result.output
    deployment = config_mod.load()
    assert deployment.base_url == "https://inference-api.nvidia.com"
    assert deployment.vlm == config_mod.VlmConfig(backend="openai")
    assert seen[-1]["headers"] == {"Authorization": "Bearer sk-test"}
    assert "identified by the endpoint" in result.output
    assert "the endpoint lists 3 models" in result.output
    assert "vss configure vlm --model <id>" in result.output
    assert "not sent -- the openai backend sends a plain chat completion" in result.output
    assert "sk-test" not in config_home.joinpath("config.json").read_text()


def test_backend_environment_overrides_endpoint_identification(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.VLM_ENV["backend"], "cosmos_reason_nim")
    _bare_endpoint(monkeypatch, headers={"x-litellm-version": "1.84.10"})

    result = _configure("http://hub")

    assert result.exit_code == 0, result.output
    assert config_mod.load().vlm is None
    assert _report_line(result.output, "backend").split()[1:] == ["cosmos_reason_nim", "VSS_VLM_BACKEND"]


def test_endpoint_needing_a_key_asks_for_it_and_writes_nothing(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(config_mod.VLM_API_KEY_ENV, raising=False)
    _bare_endpoint(monkeypatch, status=401)

    result = _configure("https://inference-api.nvidia.com/v1")

    assert result.exit_code != 0
    assert "needs an API key. Export VSS_VLM_API_KEY" in result.output
    assert config_mod.load().base_url == "http://example"


def test_endpoint_rejecting_the_key_says_so(config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.VLM_API_KEY_ENV, "sk-wrong")
    _bare_endpoint(monkeypatch, status=401)

    result = _configure("https://inference-api.nvidia.com/v1")

    assert result.exit_code != 0
    assert "rejected VSS_VLM_API_KEY (HTTP 401)" in result.output


def test_origin_that_is_neither_ingress_nor_vlm_endpoint_fails(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bare_endpoint(monkeypatch, status=404)

    result = _configure("http://nothing")

    assert result.exit_code != 0
    assert "is not an OpenAI-compatible VLM endpoint (/v1/models: HTTP 404)" in result.output
    assert config_mod.load().base_url == "http://example"


def test_check_reprobes_a_bare_vlm_endpoint(config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_mod.save(
        config_mod.Deployment(
            base_url="http://vllm:8000",
            services={"rt_vlm": config_mod.Service(url="http://vllm:8000", models=["m"])},
        )
    )
    _bare_endpoint(monkeypatch)

    result = CliRunner().invoke(configure_mod.configure, ["check"])

    assert result.exit_code == 0, result.output
    assert "rt_vlm" in result.output
    assert "ok" in result.output
    assert "commands:" in result.output
    assert "vlm" in result.output


def test_ingress_deployment_is_not_a_direct_vlm_endpoint(config_home: Path) -> None:
    assert not config_mod.load().is_direct_vlm
