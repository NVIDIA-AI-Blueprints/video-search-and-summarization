# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""analyze()/list_models() over a mock transport: retries, error contract, base64 streaming."""

from __future__ import annotations

import base64
from collections.abc import Callable
import json
import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr
from pydantic import ValidationError
import pytest

from nvidia_vision_ai import BackendResponseError
from nvidia_vision_ai import BackendUnreachableError
from nvidia_vision_ai import InvalidRequestError
from nvidia_vision_ai.vlm import Endpoint
from nvidia_vision_ai.vlm import VlmRequest
from nvidia_vision_ai.vlm import analyze
from nvidia_vision_ai.vlm import client as client_mod
from nvidia_vision_ai.vlm import list_models

Handler = Callable[[httpx.Request], httpx.Response]


def completion(content: Any = "a person walks", **extra: Any) -> dict[str, Any]:
    return {"id": "cmpl-1", "model": "served-m", "choices": [{"message": {"content": content}}], **extra}


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], list[httpx.Request]]:
    """Route the library's HTTP client through ``handler``; returns the captured requests."""

    def install(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            request.read()
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(
            client_mod, "_new_client", lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(record))
        )
        return seen

    monkeypatch.setattr(client_mod, "create_retry_strategy", _no_wait_retry)
    return install


def _no_wait_retry(retries: int, delay: float = 2, *, exceptions: tuple[type[BaseException], ...]) -> Any:
    from tenacity import AsyncRetrying
    from tenacity import retry_if_exception_type
    from tenacity import stop_after_attempt

    return AsyncRetrying(retry=retry_if_exception_type(exceptions), stop=stop_after_attempt(retries), reraise=True)


def request(**overrides: Any) -> VlmRequest:
    values: dict[str, Any] = {
        "endpoint": Endpoint(base_url="http://vlm:8000", api_key=SecretStr("k")),
        "model": "m",
        "prompt": "q",
        "media_url": "https://example.com/c.mp4",
    }
    values.update(overrides)
    return VlmRequest(**values)


@pytest.mark.parametrize(
    "base_url",
    ["http://vlm:8000", "http://vlm:8000/", "http://vlm:8000/v1", "http://vlm:8000/v1/chat/completions"],
)
async def test_analyze_success_and_url_forms(transport: Any, base_url: str) -> None:
    seen = transport(lambda r: httpx.Response(200, json=completion(usage={"total_tokens": 3})))
    out = await analyze(request(endpoint=Endpoint(base_url=base_url, api_key=SecretStr("k"))))
    assert (out.answer, out.model, out.completion_id, out.usage) == (
        "a person walks",
        "served-m",
        "cmpl-1",
        {"total_tokens": 3},
    )
    assert str(seen[0].url) == "http://vlm:8000/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer k"


async def test_list_content_parts_are_joined(transport: Any) -> None:
    transport(lambda r: httpx.Response(200, json=completion([{"type": "text", "text": "a"}, {"text": "b"}, 3])))
    assert (await analyze(request())).answer == "a\nb"


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (httpx.Response(200, json=completion("   ")), "no text answer"),
        (httpx.Response(200, json={"choices": []}), "no choices"),
        (httpx.Response(200, text="not json"), "not valid JSON"),
        (httpx.Response(400, text="bad fps"), "HTTP 400"),
    ],
)
async def test_unusable_responses_are_backend_errors(transport: Any, response: httpx.Response, match: str) -> None:
    transport(lambda r: response)
    with pytest.raises(BackendResponseError, match=match) as info:
        await analyze(request())
    if response.status_code == 400:
        assert info.value.status_code == 400


async def test_5xx_is_retried_then_succeeds(transport: Any) -> None:
    responses = iter([httpx.Response(503), httpx.Response(429), httpx.Response(200, json=completion())])
    seen = transport(lambda r: next(responses))
    assert (await analyze(request())).answer == "a person walks"
    assert len(seen) == 3


async def test_persistent_5xx_is_unreachable(transport: Any) -> None:
    seen = transport(lambda r: httpx.Response(502, text="gateway"))
    with pytest.raises(BackendUnreachableError, match="HTTP 502"):
        await analyze(request())
    assert len(seen) == 3


async def test_connect_error_is_unreachable(transport: Any) -> None:
    def refuse(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=r)

    seen = transport(refuse)
    with pytest.raises(BackendUnreachableError, match="ConnectError"):
        await analyze(request())
    assert len(seen) == 3


async def test_read_timeout_is_not_retried(transport: Any) -> None:
    def slow(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=r)

    seen = transport(slow)
    with pytest.raises(BackendUnreachableError, match="timed out after 30s"):
        await analyze(request())
    assert len(seen) == 1


async def test_file_is_streamed_inline_as_base64(transport: Any, tmp_path: Path) -> None:
    data = bytes(range(256)) * 3000  # > one read chunk, not a multiple of 3
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(data)
    seen = transport(lambda r: httpx.Response(200, json=completion()))
    await analyze(request(media_url=None, file=clip, backend="vllm", fps=1))
    body = json.loads(seen[0].content)
    url = body["messages"][0]["content"][0]["video_url"]["url"]
    assert url.startswith("data:video/mp4;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == data
    assert body["media_io_kwargs"] == {"video": {"fps": 1, "num_frames": -1}}


async def test_file_body_is_rebuilt_on_retry(transport: Any, tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"abc")
    responses = iter([httpx.Response(503), httpx.Response(200, json=completion())])
    seen = transport(lambda r: next(responses))
    await analyze(request(media_url=None, file=clip))
    assert len(seen) == 2
    assert json.loads(seen[0].content) == json.loads(seen[1].content)


async def test_list_models(transport: Any) -> None:
    seen = transport(lambda r: httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}, {"x": 1}]}))
    assert (await list_models(Endpoint(base_url="http://vlm:8000/v1"))).models == ["a", "b"]
    assert str(seen[0].url) == "http://vlm:8000/v1/models"
    assert "Authorization" not in seen[0].headers


async def test_large_file_body_is_identical_on_retry(transport: Any, tmp_path: Path) -> None:
    data = bytes(range(256)) * 5000  # several read chunks
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(data)
    responses = iter([httpx.Response(503), httpx.Response(200, json=completion())])
    seen = transport(lambda r: next(responses))
    await analyze(request(media_url=None, file=clip))
    assert seen[0].content == seen[1].content
    url = json.loads(seen[1].content)["messages"][0]["content"][0]["video_url"]["url"]
    assert base64.b64decode(url.split(",", 1)[1]) == data


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions (CI containers run as root)")
async def test_unreadable_file_is_invalid_request(transport: Any, tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    req = request(media_url=None, file=clip)
    clip.chmod(0)
    seen = transport(lambda r: httpx.Response(200, json=completion()))
    try:
        with pytest.raises(InvalidRequestError, match="cannot read local file"):
            await analyze(req)
    finally:
        clip.chmod(0o600)
    assert seen == []


@pytest.mark.parametrize("status", [401, 503])
async def test_api_key_echoed_by_backend_is_redacted(transport: Any, status: int) -> None:
    transport(lambda r: httpx.Response(status, text="bad key k-123 for you"))
    req = request(endpoint=Endpoint(base_url="http://vlm:8000", api_key=SecretStr("k-123")))
    with pytest.raises((BackendResponseError, BackendUnreachableError)) as info:
        await analyze(req)
    assert "k-123" not in str(info.value)
    assert "***" in str(info.value)


@pytest.mark.parametrize("base_url", ["not-a-url", "localhost:8000", "ftp://host", "http://"])
def test_endpoint_rejects_non_http_urls(base_url: str) -> None:
    with pytest.raises(ValidationError, match="http"):
        Endpoint(base_url=base_url)


@pytest.mark.parametrize("key", ["k\r", "k\n", "k k", "kë", ""])
def test_endpoint_rejects_unsendable_api_keys(key: str) -> None:
    with pytest.raises(ValidationError) as info:
        Endpoint(base_url="http://vlm:8000", api_key=SecretStr(key))
    if key.strip():
        assert key not in str(info.value)
