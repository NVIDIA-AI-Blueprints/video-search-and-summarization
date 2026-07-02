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

from lib.search_core.clients.rtvi_cv_embed import RTVICVEmbedClient
from lib.search_core.errors import BackendUnreachableError


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

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def post(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._payload)


def _install_fake_httpx(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    def factory(*_args: Any, **_kwargs: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(payload)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


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
