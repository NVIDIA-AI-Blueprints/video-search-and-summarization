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
"""Warm-up retry behaviour for RTVICVEmbedClient text embeddings.

RTVI CV serves HTTP before its DeepStream text-embedder engine is loaded and
answers 200 with an empty ``data`` list until it is. Without a retry a single
such response failed the whole search, which is how `attribute_search` came to
report ``Invalid RTVI CV response format`` on an otherwise healthy deployment.
"""

from typing import Any

import httpx
import pytest

from vss_agents.embed import rtvi_cv_embed
from vss_agents.embed.rtvi_cv_embed import RTVICVEmbedClient


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def _install_httpx(monkeypatch: pytest.MonkeyPatch, payloads: list[Any]) -> list[str]:
    """Serve *payloads* in order, recording one entry per POST."""
    calls: list[str] = []

    class _FakeAsyncClient:
        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def post(self, url: str, **_kwargs: Any) -> _FakeResponse:
            calls.append(url)
            return _FakeResponse(payloads[min(len(calls) - 1, len(payloads) - 1)])

    monkeypatch.setattr(httpx, "AsyncClient", lambda *_a, **_k: _FakeAsyncClient())
    return calls


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record backoff delays instead of waiting them out."""
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(rtvi_cv_embed.asyncio, "sleep", fake_sleep)
    return slept


@pytest.mark.asyncio
async def test_empty_data_is_retried_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
    _no_real_sleep: list[float],
) -> None:
    calls = _install_httpx(
        monkeypatch,
        [{"data": []}, {"data": []}, {"data": [{"embedding": [1.0, 2.0]}]}],
    )
    client = RTVICVEmbedClient("http://rtvi")

    assert await client.get_text_embedding("query") == [1.0, 2.0]
    assert len(calls) == 3
    # Linear backoff, so the two waits differ.
    assert _no_real_sleep == [2.0, 4.0]


@pytest.mark.asyncio
async def test_persistent_empty_data_fails_after_its_budget(
    monkeypatch: pytest.MonkeyPatch,
    _no_real_sleep: list[float],
) -> None:
    calls = _install_httpx(monkeypatch, [{"data": []}])
    client = RTVICVEmbedClient("http://rtvi")

    with pytest.raises(ValueError) as excinfo:
        await client.get_text_embedding("query")

    assert len(calls) == rtvi_cv_embed._EMPTY_DATA_RETRIES
    assert len(_no_real_sleep) == rtvi_cv_embed._EMPTY_DATA_RETRIES - 1
    message = str(excinfo.value)
    # The reason survives, so this stops reading as a bare parse failure.
    assert "missing or empty 'data' field" in message
    assert "unchanged over 3 attempts" in message


@pytest.mark.asyncio
async def test_a_malformed_payload_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    _no_real_sleep: list[float],
) -> None:
    """Only the warm-up case is transient; a bad shape is a real fault."""
    calls = _install_httpx(monkeypatch, [{"data": [{"no_embedding_key": 1}]}])
    client = RTVICVEmbedClient("http://rtvi")

    with pytest.raises(ValueError, match="Unexpected embedding data format"):
        await client.get_text_embedding("query")

    assert len(calls) == 1
    assert _no_real_sleep == []


@pytest.mark.asyncio
async def test_a_transport_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    _no_real_sleep: list[float],
) -> None:
    attempts: list[int] = []

    class _FailingClient:
        async def __aenter__(self) -> "_FailingClient":
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
            attempts.append(1)
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", lambda *_a, **_k: _FailingClient())
    client = RTVICVEmbedClient("http://rtvi")

    with pytest.raises(httpx.HTTPError):
        await client.get_text_embedding("query")

    assert len(attempts) == 1
    assert _no_real_sleep == []
