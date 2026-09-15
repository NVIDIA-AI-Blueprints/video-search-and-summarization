# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
from typing import Any

import httpx
import pytest

from vss_agents.embed.rtvi_cv_embed import RTVICVEmbedClient


class _Response:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Any:
        return self.payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payloads, expected_calls",
    [
        ([{"data": []}, {"data": [{"embedding": [1.0]}]}], 2),
        ([{"data": []}], 3),
    ],
)
async def test_empty_data_retry_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    payloads: list[Any],
    expected_calls: int,
) -> None:
    calls = 0

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            pass

        async def post(self, *_args: Any, **_kwargs: Any) -> _Response:
            nonlocal calls
            response = _Response(payloads[min(calls, len(payloads) - 1)])
            calls += 1
            return response

    async def no_sleep(_delay: float) -> None:
        pass

    monkeypatch.setattr(httpx, "AsyncClient", lambda *_args, **_kwargs: _Client())
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    client = RTVICVEmbedClient("http://rtvi")
    if len(payloads) == 1:
        with pytest.raises(ValueError):
            await client.get_text_embedding("query")
    else:
        assert await client.get_text_embedding("query") == [1.0]
    assert calls == expected_calls
