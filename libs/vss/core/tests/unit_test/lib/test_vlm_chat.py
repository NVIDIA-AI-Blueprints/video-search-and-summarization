# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Python API contracts: ordered payloads, backend limits, telemetry and cleanup."""

from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import threading
import time

import httpx
import pytest

from vss_core import openai as transport_mod
from vss_core._foundation.retry import create_retry_strategy
from vss_core.vlm import ChatError
from vss_core.vlm import ChatMessage
from vss_core.vlm import ChatRequest
from vss_core.vlm import GenerationOptions
from vss_core.vlm import ImageBytes
from vss_core.vlm import ImagePart
from vss_core.vlm import TextPart
from vss_core.vlm import VideoFile
from vss_core.vlm import VideoOptions
from vss_core.vlm import VideoPart
from vss_core.vlm import VLMChatClient
from vss_core.vlm.requests import serialize_request

PNG = b"\x89PNG\r\n\x1a\nimage"


def response(text=" suffix \n", **fields):
    return {
        "id": "cmpl-1",
        "model": "reported",
        "usage": {"prompt_tokens": 12},
        "choices": [{"message": {"content": text, "reasoning_content": "reason"}, "finish_reason": "length"}],
        **fields,
    }


def request(**kwargs):
    return ChatRequest((ChatMessage("user", "hello"),), "requested", **kwargs)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(
        transport_mod,
        "create_retry_strategy",
        lambda retries, exceptions: create_retry_strategy(retries, delay=0, exceptions=exceptions),
    )


@pytest.mark.parametrize("backend", ["openai", "vllm", "cosmos_reason_nim"])
@pytest.mark.asyncio
async def test_ordered_history_images_controls_continuation_and_suffix(backend):
    captured = []
    messages = (
        ChatMessage(
            "user",
            (
                TextPart("reference"),
                ImagePart(ImageBytes(PNG, "image/png")),
                TextPart("target"),
                ImagePart("https://host/target.png", "high"),
            ),
        ),
        ChatMessage("system", "late system stays late"),
        ChatMessage("assistant", "prefix"),
    )
    req = ChatRequest(
        messages,
        "requested",
        GenerationOptions(temperature=1.5, max_tokens=20, seed=7, top_p=0.8, top_k=-1, repetition_penalty=1.2),
        continuation=True,
    )

    def handler(http_request):
        captured.append(json.loads(http_request.content))
        assert http_request.headers["authorization"] == "Bearer private-key"
        return httpx.Response(200, json=response())

    async with VLMChatClient(
        "https://endpoint/v1/chat/completions", backend, "private-key", transport=httpx.MockTransport(handler)
    ) as client:
        result = await client.complete(req)
    payload = captured[0]
    assert [m["role"] for m in payload["messages"]] == ["user", "system", "assistant"]
    assert [p["type"] for p in payload["messages"][0]["content"]] == ["text", "image_url", "text", "image_url"]
    assert payload["messages"][-1]["content"] == "prefix"
    assert (
        payload["messages"][0]["content"][1]["image_url"]["url"]
        == "data:image/png;base64," + base64.b64encode(PNG).decode()
    )
    assert payload["top_k"] == -1 and payload["top_p"] == 0.8 and payload["repetition_penalty"] == 1.2
    assert payload["continue_final_message"] is True and payload["add_generation_prompt"] is False
    assert result.text == " suffix \n" and result.truncated and result.finish_reason == "length"
    assert result.requested_model == "requested" and result.reported_model == "reported"
    assert result.completion_id == "cmpl-1" and result.usage.prompt_tokens == 12 and result.usage.total_tokens is None
    assert result.attempts == 1 and result.latency_s >= 0 and result.reasoning_content == "reason"
    assert req.messages == messages
    with pytest.raises(FrozenInstanceError):
        req.model = "changed"


@pytest.mark.parametrize(
    "options",
    [
        {"top_p": float("nan")},
        {"top_p": 1.1},
        {"top_k": True},
        {"top_k": 2.0},
        {"top_k": 0},
        {"top_k": -2},
        {"repetition_penalty": float("inf")},
        {"repetition_penalty": 0},
        {"temperature": 2.1},
        {"max_tokens": True},
        {"seed": 0},
    ],
)
def test_strict_generation_validation(options):
    with pytest.raises(ChatError):
        GenerationOptions(**options)


@pytest.mark.parametrize(
    "source",
    [
        "file:///secret.png",
        "https://user:pass@host/x",
        "data:image/png;base64,!",
        "data:image/jpeg;base64," + base64.b64encode(PNG).decode(),
        "data:image/gif;base64,YQ==",
        "data:image/png;base64,",
    ],
)
def test_media_reference_validation_redacts_input(source):
    with pytest.raises(ChatError) as error:
        ImagePart(source)
    assert source not in str(error.value)


def test_capacity_and_role_validation():
    with pytest.raises(ChatError):
        ImageBytes(b"", "image/png")
    with pytest.raises(ChatError):
        ImageBytes(PNG + b"x" * (20 * 1024 * 1024), "image/png")
    with pytest.raises(ChatError):
        ChatRequest(tuple(ChatMessage("user", "x") for _ in range(257)), "m")
    with pytest.raises(ChatError):
        ChatRequest((ChatMessage("user", tuple(ImagePart("https://h/i") for _ in range(33))),), "m")
    with pytest.raises(ChatError):
        ChatRequest((ChatMessage("user", tuple(TextPart("x") for _ in range(1025))),), "m")
    with pytest.raises(ChatError):
        ChatRequest((ChatMessage("user", "x" * 300000), ChatMessage("user", "x" * 300000)), "m")
    with pytest.raises(ChatError):
        ChatRequest((ChatMessage("user", "x"),), "m", video_options=VideoOptions(fps=1))
    with pytest.raises(ChatError):
        ChatMessage("assistant", (ImagePart("https://h/i"),))
    with pytest.raises(ChatError):
        request(continuation=True)
    with pytest.raises(TypeError):
        ChatRequest((ChatMessage("user", "x"),), "m", unknown=True)


@pytest.mark.parametrize(
    "messages,options",
    [
        ((ChatMessage("user", (TextPart("x"),)),), {}),
        ((ChatMessage("user", "x"), ChatMessage("system", "late")), {}),
        ((ChatMessage("system", "x"), ChatMessage("system", "y"), ChatMessage("user", "z")), {}),
        ((ChatMessage("user", (ImagePart("https://h/1"), ImagePart("https://h/2"), TextPart("x"))),), {}),
        ((ChatMessage("user", (ImagePart("https://h/1"), TextPart("x"), TextPart("y"))),), {}),
        ((ChatMessage("user", (VideoPart("https://h/v"), TextPart("x"))), ChatMessage("assistant", "history")), {}),
        ((ChatMessage("user", "x"), ChatMessage("assistant", "prefix")), {"continuation": True}),
        ((ChatMessage("user", "x"),), {"generation": GenerationOptions(repetition_penalty=1.1)}),
        ((ChatMessage("user", "x"),), {"generation": GenerationOptions(top_k=-1)}),
    ],
)
@pytest.mark.asyncio
async def test_rt_vlm_static_rejections_do_not_post(messages, options):
    def handler(_request):
        pytest.fail("incompatible request must not reach HTTP")

    async with VLMChatClient("https://h", "rt_vlm", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ChatError) as error:
            await client.complete(ChatRequest(messages, "m", **options))
        assert error.value.attempts == 0


@pytest.mark.parametrize(
    "messages",
    [
        (
            ChatMessage("system", "instruction"),
            ChatMessage("user", "a"),
            ChatMessage("assistant", "b"),
            ChatMessage("user", "c"),
        ),
        (ChatMessage("user", (ImagePart(ImageBytes(PNG, "image/png")), TextPart("question"))),),
    ],
)
def test_rt_vlm_accepted_subsets(messages):
    payload, _ = serialize_request(ChatRequest(messages, "m", GenerationOptions(top_p=0.9, top_k=1000)), "rt_vlm")
    assert [m["role"] for m in payload["messages"]] == [m.role for m in messages]
    assert payload["top_k"] == 1000


@pytest.mark.parametrize("backend", ["vllm", "openai", "cosmos_reason_nim"])
def test_direct_servers_preserve_text_arrays_and_multiple_videos(backend):
    req = ChatRequest(
        (ChatMessage("user", (TextPart("a"), VideoPart("https://h/1"), TextPart("b"), VideoPart("https://h/2"))),), "m"
    )
    payload, _ = serialize_request(req, backend)
    assert [p["type"] for p in payload["messages"][0]["content"]] == ["text", "video_url", "text", "video_url"]
    assert "continue_final_message" not in payload


@pytest.mark.parametrize(
    "req",
    [
        request(),
        ChatRequest((ChatMessage("user", (ImagePart("https://h/i"), TextPart("x"))),), "m"),
        ChatRequest(
            (ChatMessage("user", (VideoPart("https://h/v"), TextPart("x"))),), "m", GenerationOptions(top_p=0.5)
        ),
    ],
)
def test_nim_accepts_text_image_and_video_generation_extensions(req):
    payload, body = serialize_request(req, "cosmos_reason_nim")
    assert body is None and payload["model"] == req.model
    assert [m["role"] for m in payload["messages"]] == [m.role for m in req.messages]
    assert payload.get("top_p") == req.generation.top_p


@pytest.mark.parametrize(
    "status,retry_statuses,expected_attempts",
    [(429, None, 2), (503, (), 1), (408, (408,), 2), (401, None, 1), (403, None, 1)],
)
@pytest.mark.asyncio
async def test_typed_failure_status_attempts_and_safe_details(status, retry_statuses, expected_attempts):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, text="echo prefix secret signed https://host/?token=abc")

    async with VLMChatClient(
        "https://h", "openai", attempts=2, retry_statuses=retry_statuses, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ChatError) as error:
            await client.complete(request())
    assert error.value.status_code == status and error.value.attempts == expected_attempts
    assert len(calls) == expected_attempts and error.value.latency_s >= 0
    assert "secret" not in str(error.value) and "prefix" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": 3}}]},
        response(model=8),
        response(id={}),
        {"choices": [{"message": {"content": "ok"}, "finish_reason": 7}]},
    ],
)
@pytest.mark.asyncio
async def test_malformed_response_is_structured(payload):
    async with VLMChatClient(
        "https://h", "openai", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ChatError) as error:
            await client.complete(request())
        assert error.value.kind == "response_format" and error.value.attempts == 1


@pytest.mark.parametrize(
    "usage", [None, [], {"total_tokens": None}, {"total_tokens": True}, {"total_tokens": -1}, {"total_tokens": "12"}]
)
@pytest.mark.asyncio
async def test_optional_usage_does_not_discard_valid_answer(usage):
    payload = response(" answer \n", usage=usage)
    if isinstance(usage, dict):
        payload["usage"]["prompt_tokens"] = 12
    async with VLMChatClient(
        "https://h", "openai", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        result = await client.complete(request())
    assert result.text == " answer \n"
    if isinstance(usage, dict):
        assert result.usage.prompt_tokens == 12 and result.usage.total_tokens is None
    else:
        assert result.usage is None


@pytest.mark.asyncio
async def test_reuse_overlapping_failure_then_success_injected_ownership_and_clock():
    async def handler(req):
        text = json.loads(req.content)["messages"][0]["content"]
        await asyncio.sleep(0)
        return httpx.Response(400) if text == "fail" else httpx.Response(200, json=response(text))

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ticks = iter(range(100))
    client = VLMChatClient("https://h", "openai", client=http_client, clock=lambda: next(ticks))
    try:
        with pytest.raises(ChatError):
            await client.complete(ChatRequest((ChatMessage("user", "fail"),), "m"))
        results = await asyncio.gather(
            *(client.complete(ChatRequest((ChatMessage("user", str(i)),), "m")) for i in range(3))
        )
        assert [r.text for r in results] == ["0", "1", "2"]
        assert all(r.attempts == 1 and r.latency_s > 0 for r in results)
        assert (await client.complete(request())).text == "hello"
        await client.aclose()
        await client.aclose()
        assert not http_client.is_closed
        with pytest.raises(ChatError) as error:
            await client.complete(request())
        assert error.value.kind == "lifecycle" and error.value.attempts == 0
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_busy_shutdown_cancellation_and_owned_cleanup():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(_req):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=response())

    client = VLMChatClient("https://h", "openai", transport=httpx.MockTransport(handler))
    task = asyncio.create_task(client.complete(request()))
    await entered.wait()
    with pytest.raises(ChatError, match="outstanding"):
        await client.aclose()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client._active == 0 and not client._transport._client.is_closed
    release.set()
    assert (await client.complete(request())).attempts == 1
    await client.aclose()
    assert client._transport._client.is_closed


def test_cross_loop_calls_fail_locally():
    client = VLMChatClient(
        "https://h", "openai", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response()))
    )
    owner = asyncio.new_event_loop()
    other = asyncio.new_event_loop()
    try:
        owner.run_until_complete(client.complete(request()))
        with pytest.raises(ChatError) as error:
            other.run_until_complete(client.complete(request()))
        assert error.value.kind == "lifecycle" and error.value.attempts == 0
        owner.run_until_complete(client.aclose())
    finally:
        owner.close()
        other.close()


@pytest.mark.asyncio
async def test_streamed_video_reopens_on_retry_preserves_hashes_and_escaping(tmp_path):
    data = bytes(range(256)) * 2500
    path = tmp_path / "video.mp4"
    path.write_bytes(data)
    req = ChatRequest((ChatMessage("user", (VideoPart(VideoFile(path)), TextPart('quote" slash\\ newline\n'))),), "m")
    payload, factory = serialize_request(req, "vllm")
    chunks = [chunk async for chunk in factory()]
    assert payload is None and max(map(len, chunks)) <= 4 * 65536
    bodies = []

    async def handler(http_req):
        bodies.append(json.loads(await http_req.aread()))
        return httpx.Response(503) if len(bodies) == 1 else httpx.Response(200, json=response())

    async with VLMChatClient("https://h", "vllm", attempts=2, transport=httpx.MockTransport(handler)) as client:
        result = await client.complete(req)
    assert result.attempts == 2 and bodies[0] == bodies[1]
    parts = bodies[0]["messages"][0]["content"]
    assert base64.b64decode(parts[0]["video_url"]["url"].split(",")[1]) == data
    assert parts[1]["text"] == 'quote" slash\\ newline\n'


@pytest.mark.asyncio
async def test_slow_video_reads_leave_event_loop_responsive(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    original = Path.open
    loop = asyncio.get_running_loop()
    progressed = threading.Event()
    observations = []

    class SlowReader:
        def __init__(self, stream):
            self.stream = stream

        def seek(self, offset):
            return self.stream.seek(offset)

        def read(self, size):
            loop.call_soon_threadsafe(progressed.set)
            time.sleep(0.05)
            observations.append(progressed.is_set())
            return self.stream.read(size)

    @contextmanager
    def slow_open(self, *args, **kwargs):
        with original(self, *args, **kwargs) as stream:
            yield SlowReader(stream)

    monkeypatch.setattr(Path, "open", slow_open)
    req = ChatRequest((ChatMessage("user", (VideoPart(VideoFile(path)), TextPart("x"))),), "m")
    _, factory = serialize_request(req, "vllm")
    body = b"".join([chunk async for chunk in factory()])
    assert observations and all(observations)
    assert base64.b64decode(json.loads(body)["messages"][0]["content"][0]["video_url"]["url"].split(",")[1]) == b"video"


@pytest.mark.parametrize("read_failure", [False, True])
@pytest.mark.asyncio
async def test_cancelled_video_read_does_not_hold_completion_or_client_shutdown(tmp_path, monkeypatch, read_failure):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    original = Path.open
    entered = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    loop = asyncio.get_running_loop()
    opened = []

    class BlockingReader:
        def __init__(self, stream):
            self.stream = stream

        def seek(self, offset):
            return self.stream.seek(offset)

        def read(self, size):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            assert not self.stream.closed
            if read_failure:
                raise OSError("read failed")
            return self.stream.read(size)

    @contextmanager
    def blocking_open(self, *args, **kwargs):
        try:
            with original(self, *args, **kwargs) as stream:
                opened.append(stream)
                yield BlockingReader(stream)
        finally:
            finished.set()

    monkeypatch.setattr(Path, "open", blocking_open)
    req = ChatRequest((ChatMessage("user", (VideoPart(VideoFile(path)), TextPart("x"))),), "m")
    client = VLMChatClient(
        "https://h", "vllm", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response("ok")))
    )
    task = asyncio.create_task(client.complete(req))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        finished.clear()
        task.cancel()
        done, _ = await asyncio.wait((task,), timeout=0.5)
        assert task in done
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not opened[-1].closed
        assert (await asyncio.wait_for(client.complete(request()), 0.5)).text == "ok"
        await asyncio.wait_for(client.aclose(), 0.5)
        assert not release.is_set() and not finished.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 5)
        await client.aclose()
    assert all(stream.closed for stream in opened)


@pytest.mark.asyncio
async def test_stream_generator_closes_file_when_cancelled(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"x" * 300000)
    opened = []
    original = Path.open

    def track(self, *args, **kwargs):
        stream = original(self, *args, **kwargs)
        opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", track)
    req = ChatRequest((ChatMessage("user", (VideoPart(VideoFile(path)), TextPart("x"))),), "m")

    async def post(_self, _url, *, content, **kwargs):
        await anext(content)
        await anext(content)
        raise asyncio.CancelledError

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    async with VLMChatClient("https://h", "vllm") as client:
        with pytest.raises(asyncio.CancelledError):
            await client.complete(req)
    assert opened and all(stream.closed for stream in opened)


@pytest.mark.asyncio
async def test_empty_and_text_array_responses_are_representable():
    for content, expected in [("", ""), ([{"text": " a "}, {"text": "b"}], " a \nb")]:
        async with VLMChatClient(
            "https://h",
            "openai",
            transport=httpx.MockTransport(lambda _, content=content: httpx.Response(200, json=response(content))),
        ) as client:
            assert (await client.complete(request())).text == expected


@pytest.mark.asyncio
async def test_transport_failure_retries_without_logging_secrets(caplog):
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            raise httpx.ConnectError("echo private-prefix https://host/?token=secret", request=req)
        return httpx.Response(200, json=response())

    async with VLMChatClient("https://h", "openai", attempts=2, transport=httpx.MockTransport(handler)) as client:
        assert (await client.complete(request())).attempts == 2
    assert "secret" not in caplog.text and "private-prefix" not in caplog.text


@pytest.mark.asyncio
async def test_transport_timeout_and_missing_video_are_typed(tmp_path):
    def handler(req):
        raise httpx.ReadTimeout("private-input", request=req)

    async with VLMChatClient("https://h", "vllm", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ChatError) as error:
            await client.complete(request())
        assert error.value.kind == "timeout" and error.value.attempts == 1
        req = ChatRequest((ChatMessage("user", (VideoPart(VideoFile(tmp_path / "private-path")), TextPart("x"))),), "m")
        with pytest.raises(ChatError) as error:
            await client.complete(req)
        assert error.value.attempts == 0 and "private-path" not in str(error.value)


def test_embedded_image_aggregate_limit_and_formats():
    images = tuple(ImagePart(ImageBytes(PNG + b"x" * (17 * 1024 * 1024), "image/png")) for _ in range(4))
    with pytest.raises(ChatError, match="limits"):
        ChatRequest((ChatMessage("user", images),), "m")
    ImageBytes(b"\xff\xd8\xffjpeg", "image/jpeg")
    ImageBytes(b"RIFF\x00\x00\x00\x00WEBP", "image/webp")


@pytest.mark.asyncio
async def test_multiple_local_videos_can_share_ordered_request(tmp_path):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    req = ChatRequest(
        (ChatMessage("user", (VideoPart(VideoFile(first)), TextPart("between"), VideoPart(VideoFile(second)))),), "m"
    )
    payload, factory = serialize_request(req, "vllm")
    result = json.loads(b"".join([chunk async for chunk in factory()]))
    parts = result["messages"][0]["content"]
    assert base64.b64decode(parts[0]["video_url"]["url"].split(",")[1]) == b"first"
    assert base64.b64decode(parts[2]["video_url"]["url"].split(",")[1]) == b"second"
    assert parts[1]["text"] == "between"
    assert payload is None


@pytest.mark.parametrize(
    "url", ["file:///private", "https://token@host", "http://h:bad", "https://h/?key=private", "", "bare-host"]
)
def test_invalid_endpoint_is_a_local_safe_failure(url):
    with pytest.raises(ChatError) as error:
        VLMChatClient(url, "openai")
    assert error.value.attempts == 0 and "private" not in str(error.value)


def test_huge_generation_number_is_a_local_validation_error():
    with pytest.raises(ChatError):
        GenerationOptions(top_p=10**1000)


@pytest.mark.asyncio
async def test_video_disappearing_after_validation_retains_attempts_and_safe_error(tmp_path, monkeypatch):
    path = tmp_path / "private-name.mp4"
    path.write_bytes(b"video")
    req = ChatRequest((ChatMessage("user", (VideoPart(VideoFile(path)), TextPart("x"))),), "m")
    original = Path.open
    count = 0

    def open_file(self, *args, **kwargs):
        nonlocal count
        count += 1
        if count > 1:
            raise OSError("private-path and credential")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    async with VLMChatClient(
        "https://h", "vllm", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response()))
    ) as client:
        with pytest.raises(ChatError) as error:
            await client.complete(req)
        assert error.value.kind == "validation" and error.value.attempts == 1
        assert "private" not in str(error.value)


def test_video_data_uri_validation_decodes_bounded_blocks(monkeypatch):
    data = bytes(range(256)) * 2500
    source = "data:video/mp4;base64," + base64.b64encode(data).decode()
    original = base64.b64decode
    lengths = []

    def decode(block, **kwargs):
        lengths.append(len(block))
        return original(block, **kwargs)

    monkeypatch.setattr(base64, "b64decode", decode)
    part = VideoPart(source)
    assert part.source is source
    assert len(lengths) > 1 and max(lengths) <= 4 * 65536


@pytest.mark.parametrize("encoded", ["", "AAAA!", "YQ==" + "A" * (4 * 65536), "A" * (4 * 65536) + "!"])
def test_video_data_uri_validation_rejects_empty_invalid_and_nonfinal_padding(encoded):
    with pytest.raises(ChatError):
        VideoPart("data:video/mp4;base64," + encoded)


@pytest.mark.asyncio
async def test_nim_http_rejection_does_not_retry_with_stripped_controls():
    payloads = []

    def handler(req):
        payloads.append(json.loads(req.content))
        return httpx.Response(400)

    req = ChatRequest(
        (ChatMessage("user", "question"), ChatMessage("assistant", "prefix")),
        "m",
        GenerationOptions(max_tokens=64, seed=7, top_p=0.8, top_k=20, repetition_penalty=1.1),
        continuation=True,
    )
    async with VLMChatClient(
        "https://h", "cosmos_reason_nim", attempts=3, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ChatError) as error:
            await client.complete(req)
    assert error.value.status_code == 400 and error.value.attempts == 1
    assert len(payloads) == 1
    assert payloads[0]["messages"][-1]["content"] == "prefix"
    assert payloads[0]["continue_final_message"] is True
    assert payloads[0]["add_generation_prompt"] is False
    assert payloads[0]["top_k"] == 20 and payloads[0]["seed"] == 7


@pytest.mark.parametrize("backend", ["rt_vlm", "cosmos_reason_nim"])
@pytest.mark.asyncio
async def test_legacy_streamed_video_option_field_order(backend, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    req = ChatRequest(
        (ChatMessage("user", (VideoPart(VideoFile(video)), TextPart("question"))),),
        "m",
        GenerationOptions(temperature=0.3, max_tokens=128, seed=7),
        enable_reasoning=False,
        video_options=VideoOptions(fps=2, max_frames=4, total_pixels=262144, chunk_duration=0),
    )
    payload, body = serialize_request(req, backend)
    assert payload is None
    raw = b"".join([chunk async for chunk in body()])
    expected = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,dmlkZW8="}},
                    {"type": "text", "text": "question"},
                ],
            }
        ],
        "temperature": 0.3,
        "max_tokens": 128,
        "seed": 7,
        "media_io_kwargs": {"video": {"fps": 2}},
        "enable_reasoning": False,
        "chunk_duration": 0,
        "mm_processor_kwargs": {"size": {"shortest_edge": 131072, "longest_edge": 262144}},
    }
    assert raw == json.dumps(expected).encode()
