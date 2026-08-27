# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for ``vss vlm``.

The group is a thin client over the RT-VLM REST API, so what is worth pinning
is not the vision model's answer but the job shape around it: where the request
is sent, what media the VLM receives, and how a failed or timed-out call
reports itself.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import httpx
import pytest

from vss_cli import config as config_mod
from vss_cli import memory as memory_mod
from vss_cli.exits import Exit
from vss_cli.vlm.group import VLM
from vss_cli.vlm.group import VlmInput
from vss_cli.vlm.group import VlmOptions
from vss_core.memory import InMemoryStore
from vss_core.memory import MemoryService

if TYPE_CHECKING:
    from pathlib import Path

BASE_URL = "http://h:7777"


# --------------------------------------------------------------------------
# fixtures and doubles
# --------------------------------------------------------------------------


def _deployment(*, rt_vlm_models: list[str] | None = None) -> config_mod.Deployment:
    models = rt_vlm_models if rt_vlm_models is not None else ["cosmos-reason1-7b"]
    return config_mod.Deployment(
        base_url=BASE_URL,
        services={
            "rt_vlm": config_mod.Service(url=f"{BASE_URL}/rtvi-vlm", models=models),
            "elasticsearch": config_mod.Service(url=f"{BASE_URL}/elasticsearch"),
        },
        memory=config_mod.MemoryConfig(),
    )


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> config_mod.Deployment:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    deployment = _deployment()
    config_mod.save(deployment)
    return deployment


def _completion(answer: str = "I see a forklift in aisle 3.") -> dict[str, Any]:
    return {
        "id": "cmpl-abc123",
        "object": "chat.completion",
        "model": "cosmos-reason1-7b",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def _fake_post(response: Any) -> Any:
    """Return a callable that patches httpx.post with a fixed response."""
    if isinstance(response, Exception):

        def _raise(*_args: Any, **_kwargs: Any) -> httpx.Response:
            raise response

        return _raise

    def _return(*_args: Any, **_kwargs: Any) -> httpx.Response:
        return response

    return _return


def _in_memory(deployment: config_mod.Deployment) -> memory_mod.Memory:
    store = InMemoryStore()
    index = deployment.memory.index if deployment.memory else "vss-memory"
    return memory_mod.Memory(MemoryService(store), index=index)


# --------------------------------------------------------------------------
# input model validation
# --------------------------------------------------------------------------


def test_vlm_input_requires_exactly_one_source() -> None:
    with pytest.raises(Exception, match="exactly one"):
        VlmInput(prompt="What do you see?")

    with pytest.raises(Exception, match="exactly one"):
        VlmInput(prompt="What?", sensor="cam1", media_url="http://h/clip.mp4")


def test_vlm_input_start_end_require_sensor() -> None:
    with pytest.raises(Exception, match="start-time"):
        VlmInput(prompt="What?", media_url="http://h/clip.mp4", start_time="2025-01-01T00:00:00Z")


def test_vlm_input_valid_sensor_path() -> None:
    inp = VlmInput(prompt="What?", sensor="cam1", start_time="2025-01-01T00:00:00Z", end_time="2025-01-01T00:00:30Z")
    assert inp.sensor == "cam1"
    assert inp.start_time == "2025-01-01T00:00:00Z"


def test_vlm_input_valid_url_path() -> None:
    inp = VlmInput(prompt="What?", media_url="http://h/clip.mp4")
    assert inp.media_url == "http://h/clip.mp4"
    assert inp.sensor is None


def test_vlm_options_defaults() -> None:
    opts = VlmOptions()
    assert opts.no_persist is False
    assert opts.use_base64 is False


# --------------------------------------------------------------------------
# happy path: --media-url
# --------------------------------------------------------------------------


def test_run_media_url_persists_answer(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = "I see a person carrying a box."
    monkeypatch.setattr(httpx, "post", _fake_post(httpx.Response(200, json=_completion(answer))))

    from vss_cli.group import Context
    from vss_cli.vlm.group import VlmGroup

    ctx = Context(deployment=configured, memory=_in_memory(configured))
    group = VlmGroup()
    inputs = VlmInput(prompt="What is happening?", media_url="http://h/clip.mp4", intent="qa")
    result = group.run("", inputs, ctx)

    assert result.exit == Exit.SUCCESS
    assert result.body["answer"] == answer
    assert result.body["persisted"] is True
    assert result.body["intent"] == "qa"
    assert result.job_id.startswith("vlm-")


def test_run_no_persist_skips_memory(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "post", _fake_post(httpx.Response(200, json=_completion())))

    from vss_cli.group import Context
    from vss_cli.vlm.group import VlmGroup

    ctx = Context(deployment=configured)
    ctx.extra = {"no_persist": True}
    group = VlmGroup()
    inputs = VlmInput(prompt="What?", media_url="http://h/clip.mp4")
    result = group.run("", inputs, ctx)

    assert result.exit == Exit.SUCCESS
    assert result.body["persisted"] is False


def test_run_request_carries_video_url(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _capture(url: str, *, json: Any, **_kwargs: Any) -> httpx.Response:
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(200, json=_completion())

    monkeypatch.setattr(httpx, "post", _capture)

    from vss_cli.group import Context
    from vss_cli.vlm.group import VlmGroup

    ctx = Context(deployment=configured)
    ctx.extra = {"no_persist": True}
    group = VlmGroup()
    media = "http://vios/clip.mp4"
    inputs = VlmInput(prompt="Count the people.", media_url=media, model="my-vlm")
    group.run("", inputs, ctx)

    assert captured["url"].endswith("/v1/chat/completions")
    payload = captured["json"]
    assert payload["model"] == "my-vlm"
    content = payload["messages"][0]["content"]
    video_parts = [c for c in content if c.get("type") == "video_url"]
    assert video_parts[0]["video_url"]["url"] == media


def test_run_model_defaults_from_deployment(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _capture(_url: str, *, json: Any, **_kw: Any) -> httpx.Response:
        captured["model"] = json["model"]
        return httpx.Response(200, json=_completion())

    monkeypatch.setattr(httpx, "post", _capture)

    from vss_cli.group import Context
    from vss_cli.vlm.group import VlmGroup

    ctx = Context(deployment=configured)
    ctx.extra = {"no_persist": True}
    group = VlmGroup()
    inputs = VlmInput(prompt="What?", media_url="http://h/clip.mp4")
    group.run("", inputs, ctx)

    assert captured["model"] == "cosmos-reason1-7b"


# --------------------------------------------------------------------------
# failure paths
# --------------------------------------------------------------------------


def test_run_timeout_returns_timeout_exit(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "post", _fake_post(httpx.TimeoutException("timed out")))

    from vss_cli.group import Context
    from vss_cli.vlm.group import VlmGroup

    ctx = Context(deployment=configured)
    ctx.extra = {"no_persist": True}
    group = VlmGroup()
    inputs = VlmInput(prompt="What?", media_url="http://h/clip.mp4", timeout=10)
    result = group.run("", inputs, ctx)

    assert result.exit == Exit.TIMEOUT
    assert result.body["status"] == "timeout"
    assert result.job_id.startswith("vlm-")


def test_run_5xx_returns_backend_unreachable(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "post", _fake_post(httpx.Response(503, text="Service Unavailable")))

    from vss_cli.group import Context
    from vss_cli.vlm.group import VlmGroup

    ctx = Context(deployment=configured)
    ctx.extra = {"no_persist": True}
    group = VlmGroup()
    inputs = VlmInput(prompt="What?", media_url="http://h/clip.mp4")
    result = group.run("", inputs, ctx)

    assert result.exit == Exit.BACKEND_UNREACHABLE
    assert result.body["status"] == "failed"


def test_run_4xx_returns_invalid_input(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "post", _fake_post(httpx.Response(400, text="Bad Request")))

    from vss_cli.group import Context
    from vss_cli.vlm.group import VlmGroup

    ctx = Context(deployment=configured)
    ctx.extra = {"no_persist": True}
    group = VlmGroup()
    inputs = VlmInput(prompt="What?", media_url="http://h/clip.mp4")
    result = group.run("", inputs, ctx)

    assert result.exit == Exit.INVALID_INPUT


def test_run_network_error_returns_backend_unreachable(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "post", _fake_post(httpx.ConnectError("refused")))

    from vss_cli.group import Context
    from vss_cli.vlm.group import VlmGroup

    ctx = Context(deployment=configured)
    ctx.extra = {"no_persist": True}
    group = VlmGroup()
    inputs = VlmInput(prompt="What?", media_url="http://h/clip.mp4")
    result = group.run("", inputs, ctx)

    assert result.exit == Exit.BACKEND_UNREACHABLE


# --------------------------------------------------------------------------
# CLI surface
# --------------------------------------------------------------------------


def test_cli_help_shows_required_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(VLM.cli(), ["run", "--help"])
    assert result.exit_code == 0
    assert "--prompt" in result.output
    assert "--sensor" in result.output
    assert "--media-url" in result.output
    assert "--intent" in result.output
    assert "--no-persist" in result.output
    assert "--use-base64" in result.output
    assert "--num-frames" in result.output


def test_cli_mutually_exclusive_sensor_url(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    config_mod.save(configured)
    runner = CliRunner()
    result = runner.invoke(VLM.cli(), ["run", "--prompt", "What?", "--sensor", "cam1", "--media-url", "http://x/v.mp4"])
    assert result.exit_code == Exit.INVALID_INPUT


def test_cli_missing_media_source(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    config_mod.save(configured)
    runner = CliRunner()
    result = runner.invoke(VLM.cli(), ["run", "--prompt", "What?"])
    assert result.exit_code == Exit.INVALID_INPUT


def test_cli_run_success(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    config_mod.save(configured)

    answer = "Nothing unusual."
    monkeypatch.setattr(httpx, "post", _fake_post(httpx.Response(200, json=_completion(answer))))

    runner = CliRunner()
    result = runner.invoke(
        VLM.cli(),
        ["run", "--prompt", "What do you see?", "--media-url", "http://h/clip.mp4", "--no-persist"],
    )
    assert result.exit_code == 0, result.output
    # The framework emits the body then a completion marker on separate lines.
    body = json.loads(result.output.splitlines()[0])
    assert body["answer"] == answer
    assert body["status"] == "completed"
    assert body["persisted"] is False


def test_cli_intent_stored_in_body(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    config_mod.save(configured)
    monkeypatch.setattr(httpx, "post", _fake_post(httpx.Response(200, json=_completion())))

    runner = CliRunner()
    result = runner.invoke(
        VLM.cli(),
        ["run", "--prompt", "Report?", "--media-url", "http://h/clip.mp4", "--intent", "report", "--no-persist"],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output.splitlines()[0])
    assert body.get("intent") == "report"


def test_run_request_carries_num_frames(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _capture(_url: str, *, json: Any, **_kw: Any) -> httpx.Response:
        captured["json"] = json
        return httpx.Response(200, json=_completion())

    monkeypatch.setattr(httpx, "post", _capture)

    from vss_cli.group import Context
    from vss_cli.vlm.group import VlmGroup

    ctx = Context(deployment=configured)
    ctx.extra = {"no_persist": True}
    group = VlmGroup()
    inputs = VlmInput(prompt="What?", media_url="http://h/clip.mp4", num_frames=16)
    group.run("", inputs, ctx)

    assert captured["json"].get("num_frames_per_second_or_fixed_frames_chunk") == 16


def test_run_request_num_frames_default(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _capture(_url: str, *, json: Any, **_kw: Any) -> httpx.Response:
        captured["json"] = json
        return httpx.Response(200, json=_completion())

    monkeypatch.setattr(httpx, "post", _capture)

    from vss_cli.group import Context
    from vss_cli.vlm.group import VlmGroup

    ctx = Context(deployment=configured)
    ctx.extra = {"no_persist": True}
    group = VlmGroup()
    inputs = VlmInput(prompt="What?", media_url="http://h/clip.mp4")
    group.run("", inputs, ctx)

    assert captured["json"].get("num_frames_per_second_or_fixed_frames_chunk") == 8


def test_use_base64_with_sensor_is_invalid(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """--use-base64 combined with --sensor must be rejected before VIOS resolution."""
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    config_mod.save(configured)

    runner = CliRunner()
    result = runner.invoke(
        VLM.cli(),
        ["run", "--prompt", "What?", "--sensor", "cam1", "--use-base64"],
    )
    assert result.exit_code == Exit.INVALID_INPUT


def test_sensor_path_uses_resolved_window_bounds(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Memory record must store the bounds VIOS actually served, not the raw CLI inputs."""
    from vss_cli.group import Context
    from vss_cli.vlm import group as vlm_group_mod
    from vss_cli.vlm.group import VlmGroup

    resolved_url = "http://vios/clip.mp4"
    resolved_start = "2025-01-01T00:00:00Z"
    resolved_end = "2025-01-01T00:00:30Z"

    monkeypatch.setattr(
        vlm_group_mod,
        "_resolve_vios_clip",
        lambda *_args, **_kwargs: (resolved_url, resolved_start, resolved_end),
    )
    monkeypatch.setattr(httpx, "post", _fake_post(httpx.Response(200, json=_completion("ok"))))

    store = _in_memory(configured)
    ctx = Context(deployment=configured, memory=store)
    group = VlmGroup()
    # Supply only --start-time; VIOS fills in the end bound.
    inputs = VlmInput(prompt="What?", sensor="cam1", start_time="2025-01-01T00:00:05Z")
    result = group.run("", inputs, ctx)

    assert result.exit == Exit.SUCCESS
    records = store.service.list_jobs()
    assert records, "expected one persisted record"
    rec = records[0]
    assert rec.input.window is not None
    assert rec.input.window.start.timestamp.isoformat().startswith("2025-01-01T00:00:00")
    assert rec.input.window.end is not None
    assert rec.input.window.end.timestamp.isoformat().startswith("2025-01-01T00:00:30")
