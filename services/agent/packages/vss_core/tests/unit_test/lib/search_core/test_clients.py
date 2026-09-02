# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Client error-surface tests for lib.search_core."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import aiohttp
import pytest

from vss_core._foundation.retry import create_retry_strategy
from vss_core.search_core.clients.cosmos_embed import CosmosEmbedClient
from vss_core.search_core.errors import BackendUnreachableError
from vss_core.vios import VSTClient
from vss_core.vios import VSTError
import vss_core.vios.client as vst_module


def test_vst_sessions_honor_operator_proxy_environment() -> None:
    timeout = aiohttp.ClientTimeout(total=5)
    with patch.object(vst_module.aiohttp, "ClientSession") as session:
        assert vst_module._client_session(timeout) is session.return_value
    session.assert_called_once_with(timeout=timeout, trust_env=True)


class _MalformedResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"data": [{}]}


class _MalformedHttpClient:
    async def post(self, *_args: Any, **_kwargs: Any) -> _MalformedResponse:
        return _MalformedResponse()

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("get_image_embedding", ("http://vst/image.jpg",)),
        ("get_text_embedding", ("red forklift",)),
        ("get_video_embeddings_from_urls", (["http://vst/video.mp4"],)),
    ],
)
async def test_cosmos_embed_malformed_response_is_backend_unreachable(
    method_name: str,
    args: tuple[Any, ...],
) -> None:
    client = CosmosEmbedClient("http://embed")
    client._client = _MalformedHttpClient()

    with pytest.raises(BackendUnreachableError, match="Invalid Cosmos Embed response format"):
        await getattr(client, method_name)(*args)


class _EmptyDataResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"data": []}


class _EmptyDataHttpClient:
    async def post(self, *_args: Any, **_kwargs: Any) -> _EmptyDataResponse:
        return _EmptyDataResponse()

    async def aclose(self) -> None:
        return None


class _PayloadResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _PayloadHttpClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def post(self, *_args: Any, **_kwargs: Any) -> _PayloadResponse:
        return _PayloadResponse(self._payload)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_cosmos_get_video_embedding_empty_data_is_backend_unreachable() -> None:
    client = CosmosEmbedClient("http://embed")
    client._client = _EmptyDataHttpClient()

    with pytest.raises(BackendUnreachableError, match="empty embedding response"):
        await client.get_video_embedding("http://vst/video.mp4")


@pytest.mark.asyncio
@pytest.mark.parametrize("embedding", [[], [1.0, "bad"], [float("nan")]])
async def test_cosmos_rejects_invalid_numeric_embedding(embedding: list[Any]) -> None:
    client = CosmosEmbedClient("http://embed")
    client._client = _PayloadHttpClient({"data": [{"embeddings": embedding}]})
    with pytest.raises(BackendUnreachableError, match="embedding response"):
        await client.get_text_embedding("query")


def test_cosmos_endpoint_trailing_slash_normalized() -> None:
    client = CosmosEmbedClient("http://embed/")
    assert client.endpoint == "http://embed"
    assert client.text_embeddings_url == "http://embed/v1/generate_text_embeddings"
    assert client.video_embeddings_url == "http://embed/v1/generate_video_embeddings"


@pytest.mark.asyncio
async def test_vst_client_external_clip_url_preserves_query_and_fragment(monkeypatch) -> None:
    async def fake_resolve_stream_id(self: VSTClient, sensor_id: str) -> str:
        return f"stream-{sensor_id}"

    async def fake_get_video_clip_url(**_kwargs: Any) -> str:
        return "http://internal:30888/vst/api/v1/storage/file/stream-cam01.mp4?token=abc#frag"

    monkeypatch.setattr(VSTClient, "resolve_stream_id", fake_resolve_stream_id)
    monkeypatch.setattr(vst_module, "get_video_clip_url", fake_get_video_clip_url)

    client = VSTClient(internal_url="http://internal:30888", external_url="https://vst.example.test")

    url = await client.get_video_clip_url(
        sensor_id="cam01",
        start_timestamp="2026-01-01T00:00:00Z",
        end_timestamp="2026-01-01T00:00:10Z",
        time_format="iso",
        internal=False,
    )

    assert url == "https://vst.example.test/vst/api/v1/storage/file/stream-cam01.mp4?token=abc#frag"


@pytest.mark.asyncio
async def test_vst_client_can_rebase_internal_clip_url_to_host_forward(monkeypatch) -> None:
    async def fake_resolve_stream_id(self: VSTClient, sensor_id: str) -> str:
        return f"stream-{sensor_id}"

    async def fake_get_video_clip_url(**_kwargs: Any) -> str:
        return "http://vss-vios-ingress:30888/vst/api/v1/storage/file/stream-cam01.mp4?token=abc"

    monkeypatch.setattr(VSTClient, "resolve_stream_id", fake_resolve_stream_id)
    monkeypatch.setattr(vst_module, "get_video_clip_url", fake_get_video_clip_url)
    client = VSTClient(
        internal_url="http://127.0.0.1:43123",
        external_url="https://vst.example.test",
        rewrite_internal_clip_url=True,
    )

    url = await client.get_video_clip_url(
        sensor_id="cam01",
        start_timestamp="2026-01-01T00:00:00Z",
        end_timestamp="2026-01-01T00:00:10Z",
        time_format="iso",
        internal=True,
    )

    assert url == "http://127.0.0.1:43123/vst/api/v1/storage/file/stream-cam01.mp4?token=abc"


@pytest.mark.asyncio
async def test_vst_clip_rejects_mixed_iso_and_offset_inputs() -> None:
    with pytest.raises(VSTError, match="both be ISO strings or both be second offsets"):
        await vst_module.get_video_clip_url(
            stream_id="stream-cam01",
            start_time="2026-01-01T00:00:00Z",
            end_time=10.0,
            vst_internal_url="http://internal:30888",
        )


class _FailingRequest:
    def __init__(self, attempts: list[int]) -> None:
        self._attempts = attempts

    async def __aenter__(self) -> None:
        self._attempts.append(1)
        raise aiohttp.ClientConnectionError("connection refused")

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FailingSession:
    def __init__(self, attempts: list[int]) -> None:
        self._attempts = attempts

    async def __aenter__(self) -> _FailingSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, _url: str) -> _FailingRequest:
        return _FailingRequest(self._attempts)


class _PayloadFailingResponse:
    def __init__(self, attempts: list[int]) -> None:
        self._attempts = attempts
        self.status = 200

    async def __aenter__(self) -> _PayloadFailingResponse:
        self._attempts.append(1)
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def text(self) -> str:
        raise aiohttp.ClientPayloadError("truncated response body")


class _PayloadFailingSession(_FailingSession):
    def get(self, _url: str) -> _PayloadFailingResponse:
        return _PayloadFailingResponse(self._attempts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("helper", "kwargs"),
    [
        (
            vst_module.get_video_clip_url,
            {
                "stream_id": "stream-1",
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-01-01T00:00:10Z",
                "vst_internal_url": "http://vst:30888",
            },
        ),
        (vst_module.get_name_to_stream_id_map, {"vst_internal_url": "http://vst:30888"}),
        (vst_module.get_streams_info, {"vst_internal_url": "http://vst:30888"}),
        (
            vst_module.get_timeline,
            {"stream_id": "stream-1", "vst_internal_url": "http://vst:30888"},
        ),
    ],
)
async def test_vst_helpers_wrap_retry_exhausted_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
    helper: Any,
    kwargs: dict[str, Any],
) -> None:
    attempts: list[int] = []
    monkeypatch.setattr(vst_module.aiohttp, "ClientSession", lambda **_kwargs: _FailingSession(attempts))
    monkeypatch.setattr(
        vst_module,
        "create_retry_strategy",
        lambda retries, exceptions: create_retry_strategy(retries, delay=0, exceptions=exceptions),
    )

    with pytest.raises(VSTError) as excinfo:
        await helper(**kwargs)

    assert len(attempts) == 3
    assert isinstance(excinfo.value.__cause__, aiohttp.ClientConnectionError)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("helper", "kwargs"),
    [
        (
            vst_module.get_video_clip_url,
            {
                "stream_id": "stream-1",
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-01-01T00:00:10Z",
                "vst_internal_url": "http://vst:30888",
            },
        ),
        (vst_module.get_name_to_stream_id_map, {"vst_internal_url": "http://vst:30888"}),
        (vst_module.get_streams_info, {"vst_internal_url": "http://vst:30888"}),
        (
            vst_module.get_timeline,
            {"stream_id": "stream-1", "vst_internal_url": "http://vst:30888"},
        ),
    ],
)
async def test_vst_helpers_wrap_retry_exhausted_payload_errors(
    monkeypatch: pytest.MonkeyPatch,
    helper: Any,
    kwargs: dict[str, Any],
) -> None:
    attempts: list[int] = []
    monkeypatch.setattr(
        vst_module.aiohttp,
        "ClientSession",
        lambda **_kwargs: _PayloadFailingSession(attempts),
    )
    monkeypatch.setattr(
        vst_module,
        "create_retry_strategy",
        lambda retries, exceptions: create_retry_strategy(retries, delay=0, exceptions=exceptions),
    )

    with pytest.raises(VSTError) as excinfo:
        await helper(**kwargs)

    assert len(attempts) == 3
    assert isinstance(excinfo.value.__cause__, aiohttp.ClientPayloadError)
