# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bare vLLM discovery, recorded endpoint checks and the first CLI request."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import httpx
import pytest

from vss_cli import config as config_mod
from vss_cli import configure as configure_mod
from vss_cli.exits import Exit
from vss_cli.vlm.group import VLM

if TYPE_CHECKING:
    from pathlib import Path

ORIGIN = "http://my-lease.vllm.example.com"
MODEL = "Qwen/Qwen3.8-27B"
MODELS = {"object": "list", "data": [{"id": MODEL, "object": "model"}]}


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "config"))
    for name in config_mod.VLM_ENV.values():
        monkeypatch.delenv(name, raising=False)


def _responses(monkeypatch: pytest.MonkeyPatch, routes: dict[str, httpx.Response | Exception]) -> list[str]:
    seen: list[str] = []

    def get(url: str, **kwargs: Any) -> httpx.Response:
        seen.append(url)
        assert kwargs.get("follow_redirects", True) is True
        response = routes.get(url, httpx.Response(404))
        if isinstance(response, Exception):
            raise response
        response.request = httpx.Request("GET", url)
        return response

    monkeypatch.setattr(httpx, "get", get)
    return seen


@pytest.mark.usefixtures("isolated_config")
@pytest.mark.parametrize("origin", [ORIGIN, ORIGIN + "/", "my-lease.vllm.example.com"])
def test_discover_standalone_records_origin_and_models(monkeypatch: pytest.MonkeyPatch, origin: str) -> None:
    _responses(monkeypatch, {f"{ORIGIN}/v1/models": httpx.Response(200, json=MODELS)})
    result = CliRunner().invoke(configure_mod.configure, ["--base-url", origin])
    assert result.exit_code == 0, result.output
    deployment = config_mod.load()
    assert deployment.base_url == ORIGIN
    assert deployment.services == {"rt_vlm": config_mod.Service(url=ORIGIN, models=[MODEL])}


@pytest.mark.usefixtures("isolated_config")
def test_standard_vss_vlm_route_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _responses(monkeypatch, {f"{ORIGIN}/rtvi-vlm/v1/models": httpx.Response(200, json=MODELS)})
    result = CliRunner().invoke(configure_mod.configure, ["--base-url", ORIGIN])
    assert result.exit_code == 0, result.output
    assert config_mod.load().endpoint("rt_vlm") == f"{ORIGIN}/rtvi-vlm"
    assert f"{ORIGIN}/v1/models" not in seen


@pytest.mark.usefixtures("isolated_config")
def test_standalone_can_coexist_with_other_services(monkeypatch: pytest.MonkeyPatch) -> None:
    _responses(
        monkeypatch,
        {
            f"{ORIGIN}/vst/api/v1/sensor/version": httpx.Response(200),
            f"{ORIGIN}/v1/models": httpx.Response(200, json=MODELS),
        },
    )
    result = CliRunner().invoke(configure_mod.configure, ["--base-url", ORIGIN])
    assert result.exit_code == 0, result.output
    assert set(config_mod.load().services) == {"rt_vlm", "vst"}


@pytest.mark.usefixtures("isolated_config")
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>Welcome</html>"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"data": [{"id": ""}]}),
        httpx.Response(200, json={"data": [{"id": " "}]}),
        httpx.Response(200, json={"data": [{"id": 123}]}),
        httpx.Response(200, json={"data": ["not a model"]}),
        httpx.Response(200, json={"data": [{"id": MODEL}, {}]}),
        httpx.Response(401),
        httpx.Response(403),
        httpx.Response(404),
        httpx.Response(503),
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
    ],
)
def test_invalid_fallback_does_not_create_config(monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    _responses(monkeypatch, {f"{ORIGIN}/v1/models": response})
    result = CliRunner().invoke(configure_mod.configure, ["--base-url", ORIGIN])
    assert result.exit_code == 1, result.output
    assert "standalone OpenAI-compatible VLM at /v1/models" in result.output
    assert not config_mod.config_path().exists()


@pytest.mark.usefixtures("isolated_config")
@pytest.mark.parametrize(
    ("response", "expected_exit"),
    [
        (httpx.Response(200, json=MODELS), 0),
        (httpx.Response(404), int(Exit.BACKEND_UNREACHABLE)),
        (httpx.Response(200, json={"data": []}), int(Exit.BACKEND_UNREACHABLE)),
        (httpx.ConnectError("connection refused"), int(Exit.BACKEND_UNREACHABLE)),
    ],
)
def test_check_uses_recorded_standalone_endpoint(
    monkeypatch: pytest.MonkeyPatch, response: Any, expected_exit: int
) -> None:
    config_mod.save(config_mod.Deployment(base_url=ORIGIN, services={"rt_vlm": config_mod.Service(ORIGIN, [MODEL])}))
    seen = _responses(monkeypatch, {f"{ORIGIN}/v1/models": response})
    result = CliRunner().invoke(configure_mod.configure, ["check"])
    assert result.exit_code == expected_exit, result.output
    assert f"{ORIGIN}/v1/models" in seen
    assert f"{ORIGIN}/rtvi-vlm/v1/models" not in seen


@pytest.mark.usefixtures("isolated_config")
def test_check_standard_vss_route_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    config_mod.save(
        config_mod.Deployment(base_url=ORIGIN, services={"rt_vlm": config_mod.Service(f"{ORIGIN}/rtvi-vlm", [MODEL])})
    )
    seen = _responses(monkeypatch, {f"{ORIGIN}/rtvi-vlm/v1/models": httpx.Response(200, json=MODELS)})
    result = CliRunner().invoke(configure_mod.configure, ["check"])
    assert result.exit_code == 0, result.output
    assert f"{ORIGIN}/rtvi-vlm/v1/models" in seen
    assert f"{ORIGIN}/v1/models" not in seen


@pytest.mark.usefixtures("isolated_config")
def test_refresh_preserves_vlm_and_memory_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = config_mod.VlmConfig(backend="vllm", timeout=600, locked=True)
    memory = config_mod.MemoryConfig(index="my-memory", enabled=True)
    config_mod.save(
        config_mod.Deployment(
            base_url=ORIGIN,
            services={"rt_vlm": config_mod.Service(f"{ORIGIN}/rtvi-vlm", [MODEL])},
            vlm=policy,
            memory=memory,
        )
    )
    _responses(monkeypatch, {f"{ORIGIN}/v1/models": httpx.Response(200, json=MODELS)})
    result = CliRunner().invoke(configure_mod.configure, ["--base-url", ORIGIN])
    assert result.exit_code == 0, result.output
    assert config_mod.load().vlm == policy
    assert config_mod.load().memory == memory


@pytest.mark.usefixtures("isolated_config")
def test_discover_check_then_first_vllm_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the CLI flow, not just the discovered configuration fields."""
    monkeypatch.setenv("VSS_VLM_BACKEND", "vllm")
    monkeypatch.setenv("VSS_VLM_TIMEOUT", "600")
    _responses(monkeypatch, {f"{ORIGIN}/v1/models": httpx.Response(200, json=MODELS)})
    runner = CliRunner()
    for args in (["--base-url", ORIGIN], ["check"]):
        result = runner.invoke(configure_mod.configure, args)
        assert result.exit_code == 0, result.output

    captured: dict[str, Any] = {}

    def post(url: str, **kwargs: Any) -> httpx.Response:
        captured.update(url=url, **kwargs)
        return httpx.Response(200, json={"choices": [{"message": {"content": "A"}}]})

    monkeypatch.setattr(httpx, "post", post)
    prompt = "What happens?\nA. Loading\nB. Unloading"
    media = "http://rustfs.example.com/video.mp4"
    result = runner.invoke(VLM.cli(), ["run", "--media-url", media, "--prompt", prompt, "--no-persist"])
    assert result.exit_code == 0, result.output
    assert captured["url"] == f"{ORIGIN}/v1/chat/completions"
    assert captured["timeout"] == 600
    assert captured["json"]["model"] == MODEL
    assert captured["json"]["messages"][0]["content"] == [
        {"type": "video_url", "video_url": {"url": media}},
        {"type": "text", "text": prompt},
    ]
    assert "media_io_kwargs" in captured["json"]  # vLLM request schema, not RT-VLM fields.
