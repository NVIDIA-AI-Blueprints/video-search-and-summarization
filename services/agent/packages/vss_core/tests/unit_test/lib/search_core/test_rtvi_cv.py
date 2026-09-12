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
"""Tests for RTVICVEmbedClient response-shape guarding."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from vss_core.search_core.clients import rtvi_cv_embed
from vss_core.search_core.clients.rtvi_cv_embed import RTVICVEmbedClient
from vss_core.search_core.errors import BackendUnreachableError


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    async def post(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._payload)

    async def aclose(self) -> None:
        self.closed = True


def _install_fake_httpx(monkeypatch: pytest.MonkeyPatch, payload: Any) -> list[_FakeAsyncClient]:
    created: list[_FakeAsyncClient] = []

    def factory(*_args: Any, **_kwargs: Any) -> _FakeAsyncClient:
        client = _FakeAsyncClient(payload)
        client.closed = False
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return created


@pytest.mark.asyncio
async def test_non_dict_result_maps_to_backend_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    # A bare list (not a dict) previously leaked AttributeError from .get().
    _install_fake_httpx(monkeypatch, ["not", "a", "dict"])
    client = RTVICVEmbedClient("http://rtvi")

    with pytest.raises(BackendUnreachableError, match="Invalid RTVI CV response format") as excinfo:
        await client.get_text_embedding("query")

    assert excinfo.value.backend == "rtvi_cv"
    assert excinfo.value.__cause__ is not None


@pytest.mark.asyncio
async def test_valid_dict_result_returns_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_httpx(monkeypatch, {"data": [{"embedding": [1.0, 2.0]}]})
    client = RTVICVEmbedClient("http://rtvi")
    assert await client.get_text_embedding("query") == [1.0, 2.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("embedding", [[], [1.0, "bad"], [float("inf")]])
async def test_invalid_embedding_values_map_to_backend_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    embedding: list[Any],
) -> None:
    _install_fake_httpx(monkeypatch, {"data": [{"embedding": embedding}]})
    client = RTVICVEmbedClient("http://rtvi")
    with pytest.raises(BackendUnreachableError, match="embedding response"):
        await client.get_text_embedding("query")


def _install_sequenced_httpx(
    monkeypatch: pytest.MonkeyPatch,
    payloads: list[Any],
) -> list[str]:
    """Serve *payloads* in order, recording one entry per POST."""
    calls: list[str] = []

    class _SequencedClient:
        async def post(self, url: str, **_kwargs: Any) -> _FakeResponse:
            calls.append(url)
            return _FakeResponse(payloads[min(len(calls) - 1, len(payloads) - 1)])

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(httpx, "AsyncClient", lambda *_a, **_k: _SequencedClient())
    return calls


@pytest.fixture
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record backoff delays instead of waiting them out."""
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(rtvi_cv_embed.asyncio, "sleep", fake_sleep)
    return slept


@pytest.mark.asyncio
async def test_empty_data_is_retried_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
    no_real_sleep: list[float],
) -> None:
    """RTVI CV answers 200 with no data until its engine loads."""
    calls = _install_sequenced_httpx(
        monkeypatch,
        [{"data": []}, {"data": [{"embedding": [1.0, 2.0]}]}],
    )
    client = RTVICVEmbedClient("http://rtvi")

    assert await client.get_text_embedding("query") == [1.0, 2.0]
    assert len(calls) == 2
    assert no_real_sleep == [2.0]


@pytest.mark.asyncio
async def test_persistent_empty_data_fails_after_its_budget(
    monkeypatch: pytest.MonkeyPatch,
    no_real_sleep: list[float],
) -> None:
    calls = _install_sequenced_httpx(monkeypatch, [{"data": []}])
    client = RTVICVEmbedClient("http://rtvi")

    with pytest.raises(BackendUnreachableError) as excinfo:
        await client.get_text_embedding("query")

    assert len(calls) == rtvi_cv_embed._EMPTY_DATA_RETRIES
    assert len(no_real_sleep) == rtvi_cv_embed._EMPTY_DATA_RETRIES - 1
    assert excinfo.value.backend == "rtvi_cv"
    message = str(excinfo.value)
    assert "missing or empty 'data' field" in message
    assert "unchanged over 3 attempts" in message


@pytest.mark.asyncio
async def test_a_malformed_payload_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    no_real_sleep: list[float],
) -> None:
    """Only the warm-up case is transient; a bad shape is a real fault."""
    calls = _install_sequenced_httpx(monkeypatch, [{"data": [{"no_embedding_key": 1}]}])
    client = RTVICVEmbedClient("http://rtvi")

    with pytest.raises(BackendUnreachableError, match="Unexpected embedding data format"):
        await client.get_text_embedding("query")

    assert len(calls) == 1
    assert no_real_sleep == []


@pytest.mark.asyncio
async def test_a_transport_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    no_real_sleep: list[float],
) -> None:
    attempts: list[int] = []

    class _FailingClient:
        async def post(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
            attempts.append(1)
            raise httpx.ConnectError("connection refused")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(httpx, "AsyncClient", lambda *_a, **_k: _FailingClient())
    client = RTVICVEmbedClient("http://rtvi")

    with pytest.raises(BackendUnreachableError, match="connection refused"):
        await client.get_text_embedding("query")

    assert len(attempts) == 1
    assert no_real_sleep == []


@pytest.mark.asyncio
async def test_client_is_reused_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _install_fake_httpx(monkeypatch, {"data": [{"embedding": [1.0, 2.0]}]})
    client = RTVICVEmbedClient("http://rtvi")

    await client.get_text_embedding("one")
    await client.get_text_embedding("two")

    assert len(created) == 1
    await client.aclose()
    assert created[0].closed is True
