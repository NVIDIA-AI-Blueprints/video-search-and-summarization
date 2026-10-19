# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Second-look CLI boundaries, source time mapping and durable provenance."""

from __future__ import annotations

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
from vss_cli.vlm.group import VLM
from vss_cli.vlm.group import VlmGroup
from vss_cli.vlm.group import VlmInput
from vss_core import vios
from vss_core.memory import MemoryNotFoundError

from .test_vlm_group import _completion
from .test_vlm_group import _deployment
from .test_vlm_group import _in_memory

_SEGMENTS = [("2026-10-19T00:00:00Z", "2026-10-19T00:05:00Z")]


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
        return f"http://h:7777/vst/clip/{len(clips)}.mp4"

    def post(_url: str, *, json: dict[str, Any], **_kwargs: Any) -> httpx.Response:
        requests.append(json)
        assert responses, "second look made an unexpected third model request"
        return responses.pop(0)

    monkeypatch.setattr(vios, "get_video_clip_url", clip)
    monkeypatch.setattr(httpx, "post", post)
    return ctx, requests, clips


@pytest.mark.parametrize(
    "updates",
    [
        {"relook_start_time": None},
        {"relook_end_time": None},
        {"sensor": None, "start_time": None, "end_time": None, "media_url": "http://h/video.mp4"},
        {"sensor": None, "start_time": None, "end_time": None, "file": "/tmp/video.mp4"},
        {"chunk_duration": 10},
    ],
)
def test_relook_requires_paired_sensor_windows_without_chunking(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match=r"relook|second look"):
        _input(**updates)


def test_cli_help_exposes_both_relook_flags() -> None:
    result = CliRunner().invoke(VLM.cli(), ["run", "--help"])
    assert result.exit_code == 0
    assert "--relook-start-time" in result.output
    assert "--relook-end-time" in result.output


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
    for request, expected_window in zip(requests, ["100.0 to 200.0", "120.0 to 123.0"], strict=True):
        assert request.get("chunk_duration", 0) == 0
        assert len(request["messages"]) == 1
        prompt = next(part["text"] for part in request["messages"][0]["content"] if part["type"] == "text")
        assert "What is visible at 02:01?" in prompt
        assert f"source-relative seconds {expected_window}" in prompt
        assert "first answer" not in prompt
    saved = ctx.memory.get("vlm", result.job_id)
    assert saved["output"]["answer"] == "targeted answer"
    assert saved["output"]["ext"]["second_look"] == {key: value for key, value in details.items() if value is not None}


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
    assert "absolute ISO 8601 times" in str(requests[0]["messages"])
    assert "2026-10-19T00:02:00.000Z" in str(requests[1]["messages"])


def test_explicit_stream_handle_survives_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _requests, _clips = _setup(monkeypatch)
    result = VlmGroup().run("", _input(sensor="stream-7"), ctx)
    assert result.exit == Exit.SUCCESS
    assert all(call.args[1] == "stream-7" for call in vios.resolve_sensor.await_args_list)


def test_failed_optional_read_keeps_first_answer_and_both_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, requests, _clips = _setup(
        monkeypatch,
        responses=[httpx.Response(200, json=_completion("first answer")), httpx.Response(503, text="unavailable")],
    )
    result = VlmGroup().run("", _input(), ctx)
    assert result.exit == Exit.PARTIAL
    assert result.body["status"] == "completed"
    assert result.body["answer"] == "first answer"
    details = result.body["second_look"]
    assert details["second_answer"] is None
    assert details["fallback_reason"]
    assert len(requests) == len(details["job_ids"]) == 2
    assert result.job_id == details["job_ids"][0]
    saved = ctx.memory.get("vlm", result.job_id)
    assert saved["output"]["ext"]["second_look"] == {key: value for key, value in details.items() if value is not None}
    assert ctx.memory.get("vlm", details["job_ids"][1])["job"]["status"] == "failed"


def test_first_read_failure_returns_original_failure_without_reread(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, requests, clips = _setup(monkeypatch, responses=[httpx.Response(503, text="unavailable")])
    result = VlmGroup().run("", _input(), ctx)
    assert result.exit == Exit.BACKEND_UNREACHABLE
    assert result.body["status"] == "failed"
    assert "second_look" not in result.body
    assert len(requests) == len(clips) == 1
    assert ctx.memory.get("vlm", result.job_id)["job"]["status"] == "failed"


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
