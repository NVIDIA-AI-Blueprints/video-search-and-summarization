# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for fatal versus transient OpenAI-compatible VLM errors."""

from __future__ import annotations

import httpx
import pytest

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core.vlm.openai import OpenAIVLMAnalyzer


class _VST:
    async def get_video_clip_url(self, **_kwargs: object) -> str:
        return "https://vst.example/clip.mp4"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"base_url": " ", "model": "model"}, "base_url"),
        ({"base_url": "https://vlm.example/v1", "model": " "}, "model"),
        ({"base_url": "https://vlm.example/v1", "model": "model", "timeout_seconds": 0}, "timeout_seconds"),
        ({"base_url": "https://vlm.example/v1", "model": "model", "media_mode": "invalid"}, "media_mode"),
        ({"base_url": "https://vlm.example/v1", "model": "model", "video_url_scope": "invalid"}, "video_url_scope"),
    ),
)
def test_invalid_vlm_configuration_is_rejected(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        OpenAIVLMAnalyzer(vst=_VST(), **kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_empty_vlm_content_is_backend_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]}, request=request)

    analyzer = OpenAIVLMAnalyzer(base_url="https://vlm.example/v1", model="model", vst=_VST())  # type: ignore[arg-type]
    analyzer._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(BackendUnreachableError, match="no text content"):
            await analyzer.analyze(
                sensor_id="sensor-1",
                start_timestamp="2025-01-01T00:00:00Z",
                end_timestamp="2025-01-01T00:00:05Z",
                prompt="is there a forklift?",
            )
    finally:
        await analyzer.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", (400, 401, 403, 404))
async def test_nonretryable_vlm_4xx_is_fatal_configuration_error(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="request rejected", request=request)

    analyzer = OpenAIVLMAnalyzer(
        base_url="https://vlm.example/v1",
        model="model",
        vst=_VST(),  # type: ignore[arg-type]
    )
    analyzer._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ConfigurationError, match=rf"HTTP {status}"):
            await analyzer.analyze(
                sensor_id="sensor-1",
                start_timestamp="2025-01-01T00:00:00Z",
                end_timestamp="2025-01-01T00:00:05Z",
                prompt="is there a forklift?",
            )
    finally:
        await analyzer.aclose()


@pytest.mark.asyncio
async def test_missing_vst_clip_is_backend_error_not_vlm_configuration() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(404, text="clip expired", request=request)

    analyzer = OpenAIVLMAnalyzer(
        base_url="https://vlm.example/v1",
        model="model",
        vst=_VST(),  # type: ignore[arg-type]
        media_mode="video_base64",
    )
    analyzer._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(BackendUnreachableError) as error:
            await analyzer.analyze(
                sensor_id="sensor-1",
                start_timestamp="2025-01-01T00:00:00Z",
                end_timestamp="2025-01-01T00:00:05Z",
                prompt="is there a forklift?",
            )
        assert error.value.backend == "vst"
    finally:
        await analyzer.aclose()


@pytest.mark.asyncio
async def test_vst_clip_download_error_does_not_expose_presigned_query() -> None:
    secret = "super-secret-token"

    class PresignedVST:
        async def get_video_clip_url(self, **_kwargs: object) -> str:
            return f"https://vst.example/clip.mp4?token={secret}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="clip expired", request=request)

    analyzer = OpenAIVLMAnalyzer(
        base_url="https://vlm.example/v1",
        model="model",
        vst=PresignedVST(),  # type: ignore[arg-type]
        media_mode="video_base64",
    )
    analyzer._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(BackendUnreachableError) as error:
            await analyzer.analyze(
                sensor_id="sensor-1",
                start_timestamp="2025-01-01T00:00:00Z",
                end_timestamp="2025-01-01T00:00:05Z",
                prompt="is there a forklift?",
            )
        assert "HTTP 404" in str(error.value)
        assert secret not in str(error.value)
        assert error.value.__cause__ is None
    finally:
        await analyzer.aclose()
