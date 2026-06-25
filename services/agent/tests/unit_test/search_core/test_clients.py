# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Client error-surface tests for lib.search_core."""

from __future__ import annotations

from typing import Any

import pytest

from lib.search_core.clients import vst as vst_module
from lib.search_core.clients.cosmos_embed import CosmosEmbedClient
from lib.search_core.clients.vst import VSTClient
from lib.search_core.clients.vst import VSTError
from lib.search_core.errors import BackendUnreachableError


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
async def test_vst_clip_rejects_mixed_iso_and_offset_inputs() -> None:
    with pytest.raises(VSTError, match="both be ISO strings or both be second offsets"):
        await vst_module.get_video_clip_url(
            stream_id="stream-cam01",
            start_time="2026-01-01T00:00:00Z",
            end_time=10.0,
            vst_internal_url="http://internal:30888",
        )
