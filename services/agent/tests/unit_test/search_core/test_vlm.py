# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for OpenAIVLMAnalyzer error surfacing and retries."""

from __future__ import annotations

from typing import Any

import pytest

import lib.search_core.clients.vlm_openai as vlm_module
from lib.search_core.clients.vlm_openai import OpenAIVLMAnalyzer
from lib.search_core.clients.vlm_openai import _FrameExtractionError
from lib.search_core.errors import BackendUnreachableError


class _RaisingVST:
    async def get_video_clip_url(self, **_kwargs: Any) -> str:
        raise BackendUnreachableError("vst", "vst is down")

    def build_screenshot_url(self, **_kwargs: Any) -> str:
        return ""

    async def resolve_stream_id(self, _sensor_id: str) -> str:
        return ""

    async def get_timeline(self, _sensor_id: str) -> tuple[str, str]:
        return "", ""


class _OkVST(_RaisingVST):
    async def get_video_clip_url(self, **_kwargs: Any) -> str:
        return "http://clip.example/v.mp4"


class _FakeVLMResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeVLMClient:
    def __init__(self, response: _FakeVLMResponse) -> None:
        self._response = response
        self.calls = 0

    async def request(self, _method: str, _url: str, **_kwargs: Any) -> _FakeVLMResponse:
        self.calls += 1
        return self._response

    async def aclose(self) -> None:
        return None


def _analyzer(vst: Any) -> OpenAIVLMAnalyzer:
    return OpenAIVLMAnalyzer(base_url="http://vlm", model="test-model", vst=vst)


@pytest.mark.asyncio
async def test_vst_backend_error_propagates_with_backend() -> None:
    analyzer = _analyzer(_RaisingVST())

    with pytest.raises(BackendUnreachableError) as excinfo:
        await analyzer.analyze(
            sensor_id="cam",
            start_timestamp="0",
            end_timestamp="10",
            prompt="what happened?",
            time_format="offset",
        )

    # The VST error must surface unmasked — backend stays "vst", not "vlm".
    assert excinfo.value.backend == "vst"


@pytest.mark.asyncio
async def test_invalid_vlm_response_maps_to_backend_unreachable() -> None:
    analyzer = _analyzer(_OkVST())
    analyzer._client = _FakeVLMClient(_FakeVLMResponse({}))  # type: ignore[assignment]

    with pytest.raises(BackendUnreachableError, match="Invalid VLM response format") as excinfo:
        await analyzer.analyze(
            sensor_id="cam",
            start_timestamp="0",
            end_timestamp="10",
            prompt="what happened?",
            time_format="offset",
        )

    assert excinfo.value.backend == "vlm"


@pytest.mark.asyncio
async def test_frame_extraction_error_has_distinct_message(monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer = _analyzer(_OkVST())

    async def boom(**_kwargs: Any) -> list[dict[str, Any]]:
        raise _FrameExtractionError("Video has no readable frames")

    monkeypatch.setattr(analyzer, "_build_content", boom)

    with pytest.raises(BackendUnreachableError, match="Frame extraction from VST clip failed") as excinfo:
        await analyzer.analyze(
            sensor_id="cam",
            start_timestamp="0",
            end_timestamp="10",
            prompt="what happened?",
            time_format="offset",
        )

    assert excinfo.value.backend == "vlm"


@pytest.mark.asyncio
async def test_5xx_is_retried_then_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    real = vlm_module.create_retry_strategy

    def fast_retry(retries: int, delay: float = 2, exceptions: tuple = ()) -> Any:
        return real(retries, delay=0, exceptions=exceptions)

    monkeypatch.setattr(vlm_module, "create_retry_strategy", fast_retry)

    analyzer = _analyzer(_OkVST())
    fake = _FakeVLMClient(_FakeVLMResponse({}, status=503, text="upstream boom"))
    analyzer._client = fake  # type: ignore[assignment]

    with pytest.raises(BackendUnreachableError) as excinfo:
        await analyzer.analyze(
            sensor_id="cam",
            start_timestamp="0",
            end_timestamp="10",
            prompt="what happened?",
            time_format="offset",
        )

    assert excinfo.value.backend == "vlm"
    # 5xx is retryable: three attempts before giving up.
    assert fake.calls == 3
