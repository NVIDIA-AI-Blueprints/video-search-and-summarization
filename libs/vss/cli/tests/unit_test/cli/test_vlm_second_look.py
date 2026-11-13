# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Second-look CLI boundaries, source time mapping and durable provenance."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from email.utils import formatdate
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from click.testing import CliRunner
import httpx
from pydantic import ValidationError
import pytest

from vss_cli import config as config_mod
from vss_cli.exits import Exit
from vss_cli.group import Context
from vss_cli.group import InvalidInput
from vss_cli.vlm import group as vlm_group
from vss_cli.vlm.group import VLM
from vss_cli.vlm.group import VlmGroup
from vss_cli.vlm.group import VlmInput
from vss_core import vios
from vss_core._foundation.errors import BackendUnreachableError
from vss_core.memory import MemoryNotFoundError

from .test_vlm_group import _completion
from .test_vlm_group import _deployment
from .test_vlm_group import _in_memory

_SEGMENTS = [("2026-10-19T00:00:00Z", "2026-10-19T00:05:00Z")]


@pytest.fixture
def retry_clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    clock = SimpleNamespace(now=100.0, wall=1700000000.0, sleeps=[])

    async def sleep(delay: float) -> None:
        clock.sleeps.append(delay)
        clock.now += delay

    monkeypatch.setattr(vlm_group, "time", SimpleNamespace(time=lambda: clock.wall, monotonic=lambda: clock.now))
    monkeypatch.setattr(vlm_group, "asyncio", SimpleNamespace(run=asyncio.run, timeout=asyncio.timeout, sleep=sleep))
    return clock


def _input(**updates: Any) -> VlmInput:
    return VlmInput(
        **{
            "sensor": "recording",
            "prompt": "What is visible at 02:01?",
            "start_time": "100",
            "end_time": "200",
            "relook_start_time": "120",
            "relook_end_time": "123",
            **updates,
        }
    )


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    kind: str = "video",
    segments: list[tuple[str, str]] | None = None,
    responses: list[httpx.Response] | None = None,
) -> tuple[Context, list[dict[str, Any]], list[dict[str, Any]]]:
    deployment = _deployment()
    ctx = Context(deployment=deployment, memory=_in_memory(deployment))
    requests: list[dict[str, Any]] = []
    clips: list[dict[str, Any]] = []
    responses = (
        responses
        if responses is not None
        else [
            httpx.Response(200, json=_completion("first answer")),
            httpx.Response(200, json=_completion("targeted answer")),
        ]
    )
    ref = vios.SensorRef("recording", "sensor-42", "stream-7", "/archive/video.mp4", kind)
    monkeypatch.setattr(vios, "resolve_sensor", AsyncMock(return_value=ref))
    monkeypatch.setattr(vios, "recorded_segments", AsyncMock(return_value=_SEGMENTS if segments is None else segments))
    monkeypatch.setattr(vios, "warm_media_url", AsyncMock())

    async def clip(**kwargs: Any) -> str:
        clips.append(kwargs)
        return f"http://h:7777/vst/clip/{len(clips)}.{kwargs.get('container') or 'mp4'}"

    def post(_url: str, *, json: dict[str, Any], **_kwargs: Any) -> httpx.Response:
        requests.append(json)
        assert responses, "second look made an unexpected third model request"
        return responses.pop(0)

    async def async_post(_self: Any, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.post(url, **kwargs)

    monkeypatch.setattr(vios, "get_video_clip_url", clip)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx.AsyncClient, "post", async_post)
    return ctx, requests, clips


@pytest.mark.parametrize(
    "updates",
    [
        {"relook_start_time": None},
        {"relook_end_time": None},
        {"sensor": None, "start_time": None, "end_time": None, "media_url": "http://h/video.mp4"},
        {"sensor": None, "start_time": None, "end_time": None, "file": "/tmp/video.mp4"},
        {"chunk_duration": 10},
        {"relook_start_time": None, "relook_end_time": None, "relook_fps": 10},
        {"relook_start_time": None, "relook_end_time": None, "relook_max_frames": 32},
        {"relook_fps": 0},
        {"relook_max_frames": 0},
    ],
)
def test_relook_requires_paired_sensor_windows_without_chunking(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match=r"relook|second look"):
        _input(**updates)


def test_cli_help_exposes_relook_flags() -> None:
    result = CliRunner().invoke(VLM.cli(), ["run", "--help"])
    assert result.exit_code == 0
    assert "--relook-start-time" in result.output
    assert "--relook-end-time" in result.output
    assert "--relook-fps" in result.output
    assert "--relook-max-frames" in result.output
    assert "--relook-container" in result.output


@pytest.mark.parametrize("iso_input", [False, True])
def test_two_reads_map_file_source_time_and_persist_selected_job(
    monkeypatch: pytest.MonkeyPatch, iso_input: bool
) -> None:
    ctx, requests, clips = _setup(monkeypatch)
    inputs = _input()
    if iso_input:
        inputs = _input(
            start_time="2026-10-19T00:01:40Z",
            end_time="2026-10-19T00:03:20Z",
            relook_start_time="2026-10-19T00:02:00Z",
            relook_end_time="2026-10-19T00:02:03Z",
        )
    result = VlmGroup().run("", inputs, ctx)
    assert result.exit == Exit.SUCCESS
    assert result.body["answer"] == "targeted answer"
    details = result.body["second_look"]
    assert details["first_answer"] == "first answer"
    assert details["second_answer"] == "targeted answer"
    assert details["model_calls"] == len(requests) == 2
    assert details["time_format"] == "offset"
    assert float(details["start_timestamp"]) == 100
    assert float(details["second_start_timestamp"]) == 120
    assert len(set(details["job_ids"])) == 2
    assert result.job_id == details["job_ids"][1]
    assert [(clip["start_time"], clip["end_time"]) for clip in clips] == [
        ("2026-10-19T00:01:40.000Z", "2026-10-19T00:03:20.000Z"),
        ("2026-10-19T00:02:00.000Z", "2026-10-19T00:02:03.000Z"),
    ]
    assert [clip.get("transcode") for clip in clips] == ["full", "full"]
    for request, expected_window in zip(requests, ["01:40 to 03:20", "02:00 to 02:03"], strict=True):
        assert request["chunk_duration"] == 0
        assert len(request["messages"]) == 1
        prompt = next(part["text"] for part in request["messages"][0]["content"] if part["type"] == "text")
        assert prompt == f"This clip is from {expected_window} in the original video.\n\nWhat is visible at 02:01?"
        assert "first answer" not in prompt
    saved = ctx.memory.get("vlm", result.job_id)
    assert saved["output"]["answer"] == "targeted answer"
    assert saved["output"]["ext"]["second_look"] == {key: value for key, value in details.items() if value is not None}
    assert details["role"] == "targeted" and details["selected"] is True
    first = ctx.memory.get("vlm", details["job_ids"][0])
    assert first["output"]["ext"]["second_look"] == {
        "group_id": details["job_ids"][0],
        "job_ids": details["job_ids"],
        "selected_job_id": result.job_id,
        "role": "first",
        "selected": False,
    }


def test_live_stream_retains_absolute_time_context(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, requests, _clips = _setup(monkeypatch, kind="stream")
    result = VlmGroup().run(
        "",
        _input(
            start_time="2026-10-19T00:01:40Z",
            end_time="2026-10-19T00:03:20Z",
            relook_start_time="2026-10-19T00:02:00Z",
            relook_end_time="2026-10-19T00:02:03Z",
        ),
        ctx,
    )
    assert result.exit == Exit.SUCCESS
    assert result.body["second_look"]["time_format"] == "iso"
    assert "This clip is from 2026-10-19T00:01:40.000Z to 2026-10-19T00:03:20.000Z" in str(requests[0]["messages"])
    assert "2026-10-19T00:02:00.000Z" in str(requests[1]["messages"])


def test_whole_original_segment_skips_transcode_and_container_only_changes_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, requests, clips = _setup(monkeypatch)
    ctx.extra["relook_container"] = "mkv"
    result = VlmGroup().run("", _input(start_time="0", end_time="300"), ctx)
    assert result.exit == Exit.SUCCESS
    assert [clip.get("transcode") for clip in clips] == [None, "full"]
    assert [clip.get("container") for clip in clips] == [None, "mkv"]
    urls = [request["messages"][0]["content"][0]["video_url"]["url"] for request in requests]
    assert urls[0].endswith(".mp4") and urls[1].endswith(".mkv")


def test_relook_container_requires_second_window(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, requests, clips = _setup(monkeypatch)
    ctx.extra["relook_container"] = "mkv"
    with pytest.raises(InvalidInput, match="--relook-container requires a relook window"):
        VlmGroup().run("", _input(relook_start_time=None, relook_end_time=None), ctx)
    assert not requests and not clips


def test_explicit_stream_handle_survives_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _requests, _clips = _setup(monkeypatch)
    result = VlmGroup().run("", _input(sensor="stream-7"), ctx)
    assert result.exit == Exit.SUCCESS
    assert all(call.args[1] == "stream-7" for call in vios.resolve_sensor.await_args_list)


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_failed_optional_read_keeps_first_answer_and_both_jobs(
    monkeypatch: pytest.MonkeyPatch, retry_clock: SimpleNamespace, status: int
) -> None:
    ctx, requests, _clips = _setup(
        monkeypatch,
        responses=[httpx.Response(200, json=_completion("first answer"))]
        + [httpx.Response(status, text="unavailable") for _ in range(3)],
    )
    result = VlmGroup().run("", _input(timeout=200), ctx)
    assert result.exit == Exit.PARTIAL
    assert result.body["status"] == "completed"
    assert result.body["answer"] == "first answer"
    details = result.body["second_look"]
    assert details["second_answer"] is None
    assert details["fallback_reason"]
    assert len(requests) == 4 and len(details["job_ids"]) == 2
    assert retry_clock.sleeps == [2, 4]
    assert requests[1] == requests[2] == requests[3]
    assert result.job_id == details["job_ids"][0]
    saved = ctx.memory.get("vlm", result.job_id)
    assert saved["output"]["ext"]["second_look"] == {key: value for key, value in details.items() if value is not None}
    failed = ctx.memory.get("vlm", details["job_ids"][1])
    assert failed["job"]["status"] == "failed" and failed["error"]
    assert details["role"] == "first" and details["selected"] is True
    assert failed["output"]["ext"]["second_look"] == {
        "group_id": result.job_id,
        "job_ids": details["job_ids"],
        "selected_job_id": result.job_id,
        "role": "targeted",
        "selected": False,
    }


@pytest.mark.parametrize("consume_all", [True, False])
def test_retry_recreates_inline_body_and_keeps_successful_first_answer(
    monkeypatch: pytest.MonkeyPatch, retry_clock: SimpleNamespace, consume_all: bool
) -> None:
    ctx, _requests, _clips = _setup(monkeypatch)
    ctx.extra["use_base64"] = True
    monkeypatch.setattr(
        httpx,
        "stream",
        lambda _method, url, **_kw: nullcontext(
            httpx.Response(200, content=b"video bytes", request=httpx.Request("GET", url))
        ),
    )
    bodies = []
    timeouts = []

    async def post(_self: Any, _url: str, *, content: Any, timeout: float, **_kwargs: Any) -> httpx.Response:
        timeouts.append(timeout)
        bodies.append(
            b"".join([chunk async for chunk in content]) if consume_all or len(bodies) != 1 else await anext(content)
        )
        if len(bodies) == 2:
            return httpx.Response(429, headers={"Retry-After": "45"})
        return httpx.Response(200, json=_completion("first" if len(bodies) == 1 else "target"))

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = VlmGroup().run("", _input(timeout=200), ctx)
    assert result.exit == Exit.SUCCESS and result.body["answer"] == "target"
    assert len(bodies) == 3 and bodies[0] != bodies[2]
    assert bodies[1] == bodies[2] if consume_all else bodies[2].startswith(bodies[1])
    assert json.loads(bodies[2])["messages"][0]["content"][0]["video_url"]["url"].endswith("dmlkZW8gYnl0ZXM=")
    assert timeouts == [200, 200, 155] and retry_clock.sleeps == [45]
    assert len(result.body["second_look"]["job_ids"]) == 2


@pytest.mark.parametrize("retry_after", [None, "NaN", "inf", "-1", "not a delay", "300"])
def test_retry_waits_and_attempts_share_original_timeout(
    monkeypatch: pytest.MonkeyPatch, retry_clock: SimpleNamespace, retry_after: str | None
) -> None:
    ctx, _requests, _clips = _setup(monkeypatch)
    timeouts = []

    def post(_url: str, *, timeout: float, **_kwargs: Any) -> httpx.Response:
        timeouts.append(timeout)
        if len(timeouts) == 1:
            return httpx.Response(200, json=_completion("first"))
        retry_clock.now += 3
        return httpx.Response(429, headers={} if retry_after is None else {"Retry-After": retry_after})

    monkeypatch.setattr(httpx, "post", post)
    result = VlmGroup().run("", _input(timeout=10), ctx)
    assert result.exit == Exit.PARTIAL and result.body["answer"] == "first"
    assert timeouts == ([10, 10] if retry_after == "300" else [10, 10, 5])
    assert retry_clock.sleeps == ([] if retry_after == "300" else [2])


@pytest.mark.parametrize(("offset", "wait"), [(5, 5), (-5, 2), (60, None)])
def test_retry_after_http_date_respects_request_budget(
    monkeypatch: pytest.MonkeyPatch, retry_clock: SimpleNamespace, offset: int, wait: int | None
) -> None:
    ctx, requests, _clips = _setup(
        monkeypatch,
        responses=[
            httpx.Response(200, json=_completion("first")),
            httpx.Response(429, headers={"Retry-After": formatdate(retry_clock.wall + offset, usegmt=True)}),
            httpx.Response(200, json=_completion("target")),
        ],
    )
    result = VlmGroup().run("", _input(timeout=20), ctx)
    assert result.exit == (Exit.PARTIAL if wait is None else Exit.SUCCESS)
    assert result.body["answer"] == ("first" if wait is None else "target")
    assert len(requests) == (2 if wait is None else 3)
    assert retry_clock.sleeps == ([] if wait is None else [wait])


@pytest.mark.parametrize("phase", ["upload", "response"])
def test_request_deadline_cancels_io_and_persists_first_answer_fallback(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    client_type, original_post = httpx.AsyncClient, httpx.AsyncClient.post
    ctx, _requests, _clips = _setup(monkeypatch)
    monkeypatch.setattr(client_type, "post", original_post)
    state = SimpleNamespace(calls=0, cancelled=False, stream_closed=False, clients_closed=0, chunks=0)
    generators = []
    if phase == "upload":
        ctx.extra["use_base64"] = True
        monkeypatch.setattr(
            httpx,
            "stream",
            lambda _method, url, **_kw: nullcontext(
                httpx.Response(200, content=b"video bytes", request=httpx.Request("GET", url))
            ),
        )
        original_body = vlm_group._iter_base64_json

        def body(**kwargs: Any) -> Any:
            generator = original_body(**kwargs)
            generators.append(generator)
            return generator

        monkeypatch.setattr(vlm_group, "_iter_base64_json", body)

    class SlowResponse(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            try:
                for _ in range(6):
                    await asyncio.sleep(0.25)
                    state.chunks += 1
                    yield b" "
            except asyncio.CancelledError:
                state.cancelled = True
                raise

        async def aclose(self) -> None:
            state.stream_closed = True

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            state.calls += 1
            if state.calls == 1:
                await request.aread()
                return httpx.Response(200, json=_completion("first"))
            if phase == "upload":
                await anext(request.stream.__aiter__())
                try:
                    await asyncio.sleep(2)
                except asyncio.CancelledError:
                    state.cancelled = True
                    raise
                pytest.fail("upload was not cancelled by the request deadline")
            return httpx.Response(200, stream=SlowResponse())

        async def aclose(self) -> None:
            state.clients_closed += 1

    monkeypatch.setattr(httpx, "AsyncClient", lambda: client_type(transport=Transport()))
    result = VlmGroup().run("", _input(timeout=1), ctx)
    assert result.exit == Exit.PARTIAL and result.body["answer"] == "first"
    assert state.calls == 2 and state.cancelled and state.clients_closed == 2
    if phase == "upload":
        assert len(generators) == 2 and all(generator.gi_frame is None for generator in generators)
    else:
        assert 0 < state.chunks < 6 and state.stream_closed
    details = result.body["second_look"]
    assert len(details["job_ids"]) == 2 and result.job_id == details["job_ids"][0]
    assert ctx.memory.get("vlm", details["job_ids"][1])["job"]["status"] == "timeout"
    assert ctx.memory.get("vlm", result.job_id)["output"]["answer"] == "first"


@pytest.mark.parametrize("failure", [401, 500, 200, "timeout"])
def test_retry_does_not_redraw_ambiguous_or_nonretryable_target(
    monkeypatch: pytest.MonkeyPatch, retry_clock: SimpleNamespace, failure: int | str
) -> None:
    ctx, _requests, _clips = _setup(monkeypatch)
    calls = []

    def post(_url: str, **_kwargs: Any) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json=_completion("first"))
        if failure == "timeout":
            raise httpx.ReadTimeout("ambiguous delivery")
        return httpx.Response(failure, text="invalid response")

    monkeypatch.setattr(httpx, "post", post)
    result = VlmGroup().run("", _input(timeout=200), ctx)
    assert result.exit == Exit.PARTIAL and result.body["answer"] == "first"
    assert len(calls) == 2 and not retry_clock.sleeps


def test_ordinary_read_does_not_retry_rejection(monkeypatch: pytest.MonkeyPatch, retry_clock: SimpleNamespace) -> None:
    ctx, requests, _clips = _setup(monkeypatch, responses=[httpx.Response(429)])
    result = VlmGroup().run("", _input(relook_start_time=None, relook_end_time=None, timeout=200), ctx)
    assert result.exit == Exit.INVALID_INPUT
    assert len(requests) == 1 and not retry_clock.sleeps


def test_first_read_failure_returns_original_failure_without_reread(
    monkeypatch: pytest.MonkeyPatch, retry_clock: SimpleNamespace
) -> None:
    ctx, requests, clips = _setup(monkeypatch, responses=[httpx.Response(503, text="unavailable") for _ in range(3)])
    result = VlmGroup().run("", _input(), ctx)
    assert result.exit == Exit.BACKEND_UNREACHABLE
    assert result.body["status"] == "failed"
    assert "second_look" not in result.body
    assert len(requests) == 3 and len(clips) == 1
    assert retry_clock.sleeps == [2, 4]
    assert ctx.memory.get("vlm", result.job_id)["job"]["status"] == "failed"


def test_targeted_transcode_failure_retains_first_without_second_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, requests, _clips = _setup(monkeypatch)
    clip = vios.get_video_clip_url

    async def fail_transcode(**kwargs: Any) -> str:
        if kwargs.get("start_time") == "2026-10-19T00:02:00.000Z":
            raise vios.VIOSTimeoutError("transcode timed out")
        return await clip(**kwargs)

    monkeypatch.setattr(vios, "get_video_clip_url", fail_transcode)
    result = VlmGroup().run("", _input(), ctx)
    assert result.exit == Exit.PARTIAL
    assert result.body["answer"] == "first answer"
    assert result.body["second_look"]["fallback_reason"]
    assert len(requests) == 1
    failed_job = result.body["second_look"]["job_ids"][1]
    assert ctx.memory.get("vlm", failed_job)["job"]["status"] == "timeout"


@pytest.mark.parametrize(
    "updates",
    [
        {"relook_start_time": "90", "relook_end_time": "93"},
        {"relook_start_time": "100", "relook_end_time": "200"},
        {"relook_start_time": "123", "relook_end_time": "123"},
    ],
)
def test_non_narrowing_windows_fail_before_any_model_call(
    monkeypatch: pytest.MonkeyPatch, updates: dict[str, str]
) -> None:
    ctx, requests, clips = _setup(monkeypatch)
    with pytest.raises(InvalidInput):
        VlmGroup().run("", _input(**updates), ctx)
    assert not requests and not clips


@pytest.mark.parametrize(
    ("segments", "target_start"),
    [
        ([], "120"),
        (_SEGMENTS, "nan"),
        (_SEGMENTS, "not-a-time"),
        (
            [("2026-10-19T00:00:00Z", "2026-10-19T00:01:50Z"), ("2026-10-19T00:04:00Z", "2026-10-19T00:05:00Z")],
            "120",
        ),
    ],
)
def test_missing_invalid_or_gapped_recording_fails_preflight(
    monkeypatch: pytest.MonkeyPatch, segments: list[tuple[str, str]], target_start: str
) -> None:
    ctx, requests, clips = _setup(monkeypatch, segments=segments)
    result = VlmGroup().run("", _input(relook_start_time=target_start), ctx)
    assert result.exit == Exit.INVALID_INPUT
    assert not requests and not clips


def test_configured_chunking_is_rejected_before_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, requests, clips = _setup(monkeypatch)
    ctx.deployment = _deployment(vlm=config_mod.VlmConfig(chunk_duration=10))
    with pytest.raises(InvalidInput, match="chunk-duration"):
        VlmGroup().run("", _input(), ctx)
    assert not requests and not clips


def test_second_look_metadata_failure_preserves_selected_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, requests, _clips = _setup(monkeypatch)

    def missing_record(*_args: Any, **_kwargs: Any) -> None:
        raise MemoryNotFoundError("record disappeared")

    monkeypatch.setattr(ctx.memory.service, "get", missing_record)
    result = VlmGroup().run("", _input(), ctx)
    assert result.exit == Exit.PARTIAL
    assert result.body["answer"] == "targeted answer"
    assert result.body["persisted"] is False
    assert result.body["persist_error"]
    assert len(requests) == len(result.body["second_look"]["job_ids"]) == 2


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"relook_fps": 10}, {"fps": 10, "num_frames": 16, "max_frames": 16}),
        ({"relook_max_frames": 32}, {"fps": 2, "num_frames": 32, "max_frames": 32}),
        ({"relook_fps": 10, "relook_max_frames": 32}, {"fps": 10, "num_frames": 32, "max_frames": 32}),
    ],
)
def test_vllm_reread_sampling_changes_only_second_request(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, int], expected: dict[str, int]
) -> None:
    ctx, requests, _clips = _setup(monkeypatch)
    ctx.deployment = _deployment(vlm=config_mod.VlmConfig(backend="vllm", fps=2, max_frames=16))
    result = VlmGroup().run("", _input(**overrides), ctx)
    assert result.exit == Exit.SUCCESS
    assert [request["media_io_kwargs"]["video"] for request in requests] == [
        {"fps": 2, "num_frames": 16, "max_frames": 16},
        expected,
    ]
    params = [ctx.memory.get("vlm", job_id)["input"]["params"] for job_id in result.body["second_look"]["job_ids"]]
    assert (params[0]["fps"], params[0]["max_frames"]) == (2, 16)
    assert (params[1]["fps"], params[1]["max_frames"]) == (expected["fps"], expected["max_frames"])


@pytest.mark.parametrize("backend", ["rt_vlm", "cosmos_reason_nim"])
@pytest.mark.parametrize(
    ("defaults", "overrides", "first", "second"),
    [
        ({"fps": 2}, {"relook_max_frames": 32}, {"fps": 2}, {"num_frames": 32}),
        ({"max_frames": 16}, {"relook_fps": 10}, {"num_frames": 16}, {"fps": 10}),
    ],
)
def test_reread_sampling_switches_one_mode_backends(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    defaults: dict[str, int],
    overrides: dict[str, int],
    first: dict[str, int],
    second: dict[str, int],
) -> None:
    ctx, requests, _clips = _setup(monkeypatch)
    ctx.deployment = _deployment(vlm=config_mod.VlmConfig(backend=backend, **defaults))
    result = VlmGroup().run("", _input(**overrides), ctx)
    assert result.exit == Exit.SUCCESS
    assert [request["media_io_kwargs"]["video"] for request in requests] == [first, second]


@pytest.mark.parametrize(
    ("policy", "overrides", "error"),
    [
        (config_mod.VlmConfig(backend="openai"), {"relook_fps": 10}, "not supported"),
        (config_mod.VlmConfig(backend="openai"), {"relook_max_frames": 32}, "not supported"),
        (config_mod.VlmConfig(backend="rt_vlm"), {"relook_fps": 10, "relook_max_frames": 32}, "not both"),
        (config_mod.VlmConfig(backend="cosmos_reason_nim"), {"relook_fps": 10, "relook_max_frames": 32}, "not both"),
        (config_mod.VlmConfig(backend="vllm", fps=2, locked=True), {"relook_fps": 10}, "--fps is locked"),
        (
            config_mod.VlmConfig(backend="vllm", max_frames=16, locked=True),
            {"relook_max_frames": 32},
            "--max-frames is locked",
        ),
        (config_mod.VlmConfig(backend="rt_vlm", fps=2, locked=True), {"relook_max_frames": 32}, "--fps is locked"),
        (
            config_mod.VlmConfig(backend="cosmos_reason_nim", max_frames=16, locked=True),
            {"relook_fps": 10},
            "--max-frames is locked",
        ),
    ],
)
def test_invalid_reread_policy_fails_before_either_read(
    monkeypatch: pytest.MonkeyPatch, policy: config_mod.VlmConfig, overrides: dict[str, int], error: str
) -> None:
    ctx, requests, clips = _setup(monkeypatch)
    ctx.deployment = _deployment(vlm=policy)
    with pytest.raises(InvalidInput, match=error):
        VlmGroup().run("", _input(**overrides), ctx)
    assert not requests and not clips
    vios.resolve_sensor.assert_not_awaited()


def test_reread_can_keep_locked_sampling_values(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, requests, _clips = _setup(monkeypatch)
    ctx.deployment = _deployment(vlm=config_mod.VlmConfig(backend="vllm", fps=2, max_frames=16, locked=True))
    result = VlmGroup().run("", _input(relook_fps=2, relook_max_frames=16), ctx)
    assert result.exit == Exit.SUCCESS
    assert requests[0]["media_io_kwargs"] == requests[1]["media_io_kwargs"]


def test_metadata_write_failure_does_not_prevent_tagging_other_job(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _requests, _clips = _setup(monkeypatch)
    upsert = ctx.memory.service.upsert
    calls = 0

    def fail_first_metadata_write(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise BackendUnreachableError("memory", "unavailable")
        return upsert(*args, **kwargs)

    monkeypatch.setattr(ctx.memory.service, "upsert", fail_first_metadata_write)
    result = VlmGroup().run("", _input(), ctx)
    assert result.exit == Exit.PARTIAL
    assert result.body["answer"] == "targeted answer"
    assert result.body["persisted"] is False
    assert result.body["persist_error"]
    assert calls == 4
    selected = ctx.memory.get("vlm", result.job_id)
    assert selected["output"]["ext"]["second_look"]["selected"] is True


def test_first_save_failure_remains_explained_when_second_save_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _requests, _clips = _setup(monkeypatch)
    upsert = ctx.memory.service.upsert
    calls = 0

    def fail_first_save(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BackendUnreachableError("memory", "first save unavailable")
        return upsert(*args, **kwargs)

    monkeypatch.setattr(ctx.memory.service, "upsert", fail_first_save)
    result = VlmGroup().run("", _input(), ctx)
    assert result.exit == Exit.PARTIAL
    assert result.body["answer"] == "targeted answer"
    assert result.body["persisted"] is True
    details = result.body["second_look"]
    first, second = details["reads"]
    assert first["job_id"] == details["job_ids"][0]
    assert first["persisted"] is False
    assert "first save unavailable" in first["persist_error"]
    assert second == {"job_id": result.job_id, "persisted": True}
    saved = ctx.memory.get("vlm", result.job_id)
    assert saved["output"]["ext"]["second_look"]["reads"] == details["reads"]


def test_no_persist_reports_each_unsaved_read_without_a_save_error(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _requests, _clips = _setup(monkeypatch)
    ctx.extra["no_persist"] = True
    result = VlmGroup().run("", _input(), ctx)
    assert result.exit == Exit.SUCCESS
    assert result.body["persisted"] is False
    details = result.body["second_look"]
    assert details["reads"] == [{"job_id": job_id, "persisted": False} for job_id in details["job_ids"]]
